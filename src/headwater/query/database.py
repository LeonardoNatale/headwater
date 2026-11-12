from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
import pyarrow as pa
from scipy import sparse

import headwater
from headwater.calc import Factorization, LowRankUpdate, factorize, solve_study
from headwater.calc import scores as solve_scores
from headwater.calc.arrays import Vector
from headwater.calc.cancellation import cancellation, cancellation_of, process_parts
from headwater.model import (
    Catalog,
    Exchange,
    InvalidDataError,
    LineChange,
    Method,
    ParameterSet,
    Problem,
    Process,
    Report,
    Scenario,
    did_you_mean,
)
from headwater.query._git import git_state
from headwater.query._hash import records_of, sha256_of
from headwater.query._internals import internals
from headwater.query._scenario import Changed, change
from headwater.query._system import (
    Block,
    Part,
    StackedIndex,
    System,
    assemble,
    ids_in_errors,
    matvec,
)
from headwater.query.background import Background
from headwater.query.result import (
    DatabaseRef,
    FormulaLine,
    MethodRef,
    NotCharacterised,
    Provenance,
    Result,
    SetRef,
    StudyRef,
    counted,
)
from headwater.study import (
    DatabaseEditor,
    DatabaseFolder,
    NoFactor,
    in_used,
    owner_of,
    read_database,
)
from headwater.study.edit import new_database
from headwater.study.validate import check_scenarios, flows_without_factor, validate_database


@dataclass(frozen=True, eq=False)
class _Prepared:
    """What `calc` and `scores` work out before they look at an indicator: the checked database, its
    processes with formulas worked out, the copies its overrides make, and its block of the
    stacked system. Nothing here depends on the indicator."""

    catalog: Catalog
    parameters: ParameterSet
    processes: tuple[Process, ...]
    """The database's processes, those of the databases it uses, then the copies their overrides
    make."""
    copy_of: Mapping[str, str]
    """The id of the background process each copy copies, by the copy's id."""
    copy_rows: Mapping[int, str]
    """The same, by the copy's index among `processes`."""
    used_rows: Mapping[int, str]
    """The database each process of a database it uses belongs to, by its index among
    `processes`: the processes of `dyes`, and the copies `dyes` makes, are `dyes`."""
    mine: tuple[int, ...]
    """The indices among `processes` of this database's own processes and copies: the lines whose
    formulas use its parameters."""
    everyone: list[Process]
    """The background processes a scenario changes, with every line."""
    block: Block
    A_ss: sparse.csc_array
    A_bs: sparse.csc_array
    target: int
    f_s: Vector
    uncovered: Mapping[str, tuple[str, ...]]
    """The database's own elementary flows that no indicator of the method covers, with the
    processes that use them: `flows_without_factor`, worked out once for validation and calc."""

    @property
    def n(self) -> int:
        return len(self.processes)


class Database:
    """A database read from its folder: processes, each a product scored per unit of what it
    makes. `calc` scores one of them on a background.

    Example: `open("tests/fixtures/toy/tshirt/tshirt").calc(background, "tshirt:tshirt").score`
    is 1.19205.
    """

    def __init__(self, folder: DatabaseFolder) -> None:
        self.folder = folder
        self._commit: tuple[DatabaseFolder, DatabaseRef] | None = None

    @property
    def name(self) -> str:
        return self.folder.database.name

    def __repr__(self) -> str:
        """Example: `Database(tshirt: 1 process, background toy-background@fixture, methods
        toy-climate)`, then the databases it uses, if any: `uses dyes`. A database with no
        methods says `no methods`."""
        database = self.folder.database
        methods = " ".join(database.methods)
        parts = [
            counted(len(self.folder.processes), "process", "processes"),
            f"background {database.background}",
            f"methods {methods}" if methods else "no methods",
        ]
        if database.uses:
            parts.append(f"uses {' '.join(database.uses)}")
        return f"Database({self.name}: {', '.join(parts)})"

    @classmethod
    def new(
        cls,
        path: Path | str,
        background: Background,
        *,
        name: str | None = None,
        pin: str | None = None,
        methods: Sequence[str] = (),
        uses: Sequence[str] = (),
        description: str | None = None,
    ) -> DatabaseEditor:
        """Start a database that does not exist yet, in the empty folder `path`: an editor that
        `save` writes from, as a Brightway user starts with `Database("name")`.

        The fields are those of database.yaml ([design.md](design.md#a-database)). `name` is the
        folder's name by default, `pin` names the release `background` was read from
        (`toy-background@fixture` for a folder), and `methods` the methods the database is scored
        with by default, none when left out. A field that does not read raises InvalidDataError
        at once.

        Example, one purchase: `Database.new("databases/shirt", bg)`, then `new_process` and
        `new_exchange`, then `save()`.
        """
        fields = {"name": name, "background": pin, "methods": list(methods)}
        fields |= {"uses": list(uses), "description": description}
        return new_database(path, background, fields)

    def edit(self, background: Background) -> DatabaseEditor:
        """An editor of this database's files: add, change and remove processes, exchanges,
        parameters, cut-offs and overrides, then `save`, which checks the database against
        `background` and writes only what changed.

        Example: `hw.open("databases/tshirt").edit(bg).process("tshirt").set_exchange("e02",
        amount=0.3)`, then `save()`.
        """
        return DatabaseEditor(self.folder.path, background)

    def validate(self, background: Background) -> Report:
        """Check the database against `background` as `calc` does, without calculating.

        Only the database's own records are checked, against what they name in the background:
        its lines, its overrides and its scenarios, against every line of the processes they
        change, and its locations, against the places the release names. The background's
        records are not checked again. Errors stop a calc, warnings are for people to read.
        """
        return self._checked(background)[0]

    def calc(
        self,
        background: Background,
        process_id: str,
        *,
        indicator: str | None = None,
    ) -> Result:
        """Score one unit of what the database's process `process_id` makes on `background`, as
        Brightway's `LCA({act: 1})`: `calc(bg, "tshirt:tshirt")`.

        The background is not solved again, and its records are not checked again: it was
        validated when it was loaded, or is a release. Each process of the database scores its
        direct emissions plus amount × stored score of what it buys. The database's own processes
        are solved as one small system, so they may buy from each other in any order, loops
        included. If the forward solve of the background needs pivoted factors, the background
        keeps them.

        Formulas are worked out again with the database's parameters. An override gives the
        database its own copy of a background process: the database's purchases from that process
        use the copy, and the copy buys from the background as it is. Lines cut off buy nothing.
        A scenario is a study's: `hw.Study(...).calc(bg, scenario=...)`.

        A flow with no factor for `indicator` counts 0 for it. The flows of the background that
        the database reaches and the method covers for no indicator are listed in
        `Result.not_characterised`. A flow that the database itself emits and the method covers
        for no indicator is an error, as `validate` reports it, until a row of the database's
        `no_factor.csv` acknowledges it. Then it counts 0 and is listed too, with the row's
        reason.

        The provenance names the database and the commit of the repository that holds it, with
        `changed` true when its files or those of a database it uses differ from that commit.
        Both are checked at this database's first calc, not when it was opened, and kept for its
        later calcs: the records it scores were read when it was opened. Neither is part of the
        input hash.

        Raises ValueError for a process the database does not have. Raises InvalidDataError
        when the database's `background:` pin does not name the release `background` was read
        from, and for every problem `validate` reports as an
        error, and a calc error naming the processes when a matrix cannot be solved. `indicator`
        may be left out when the method has only one.
        """
        return self._calc(background, process_id, indicator, database_ref=self._ref())

    def _ref(self) -> DatabaseRef:
        """The database and where its files stand in git, checked once per folder read: at the
        first calc, then kept."""
        folder = self.folder
        if self._commit is None or self._commit[0] is not folder:
            paths = [folder.path, *(used.path for used in folder.used)]
            commit, changed = git_state(paths)
            self._commit = (folder, DatabaseRef(folder.database.name, commit, changed))
        return self._commit[1]

    def _calc(
        self,
        background: Background,
        process_id: str,
        indicator: str | None,
        chosen: Scenario | None = None,
        amount: float = 1.0,
        acknowledged: Sequence[NoFactor] = (),
        hashed: Callable[[str | None], str] | None = None,
        study_ref: StudyRef | None = None,
        database_ref: DatabaseRef | None = None,
    ) -> Result:
        """`calc`, for `amount` units, in a study's scenario `chosen`, with a study's own
        no_factor rows `acknowledged`. `hashed` gives the input hash from the hash of the set an
        indicator comes from, `study_ref` names the study, for a product of a study, and
        `database_ref` the database and its commit, for a database scored on its own."""
        inner = internals(background)
        indicator = inner.indicator(indicator)
        method = background.method
        scenario = chosen.name if chosen is not None else None
        prepared = self._prepare(background, chosen, process_id, amount, acknowledged)
        catalog, parameters, uncovered = prepared.catalog, prepared.parameters, prepared.uncovered
        everyone, study, n, block = (
            prepared.everyone,
            prepared.processes,
            prepared.n,
            prepared.block,
        )
        copy_of, target, f_s = prepared.copy_of, prepared.target, prepared.f_s
        A_ss, A_bs = prepared.A_ss, prepared.A_bs

        bg = inner.solved
        changed: Changed | None = None
        if everyone:
            changed = change(bg, inner.index, everyone, catalog, indicator)
            inner.keep(changed.fallback)
            bg = inner.solved
        bg_block = changed.block if changed is not None else bg.block
        x_b = changed.x if changed is not None else bg.x[indicator]
        h_b = changed.h if changed is not None else bg.h[indicator]
        solver: Factorization | LowRankUpdate = (
            changed.solver if changed is not None else bg.factorization
        )

        c_s = inner.characterisation(block.flows, indicator)
        h_s = matvec(block.B.T, c_s)

        ids = tuple(p.id for p in study) + bg.ids
        with ids_in_errors(ids):
            solved = solve_study(solver, x_b.values, A_ss, A_bs, h_s, f_s)
        if changed is not None and changed.refactorized:
            # factors of the scenario's A: they are not the background's to keep
            solver = solved.background_supply.fallback or changed.solver
        elif isinstance(solver, Factorization):  # factors of the background's own A, not an update
            inner.keep(solved.background_supply.fallback)
            solver = inner.solved.factorization  # the factors this result was solved with
        x_s, s_s, s_b = solved.scores, solved.supply, solved.background_supply

        system = System(
            ids=ids,
            names=block.names + bg_block.names,
            flow_ids=block.flow_ids + bg_block.flow_ids,
            units=block.units + bg_block.units,
            A_ss=A_ss,
            A_bs=A_bs,
            A_bb=bg_block.A,
            h=np.concatenate([h_s, h_b]),
            x=np.concatenate([x_s.values, x_b.values]),
            s=np.concatenate([s_s.values, s_b.values]),
            f=np.concatenate([f_s, np.zeros(len(bg.ids))]),
            reference_sign=np.concatenate([block.reference_sign, bg_block.reference_sign]),
            parts=(Part(block, c_s, 0), Part(bg_block, bg.c[indicator], n)),
            target=target,
            copies=prepared.copy_rows,
            used=prepared.used_rows,
        )
        mine = prepared.mine
        formula_lines = (
            *_formula_lines([study[i] for i in mine], mine, block, 0, catalog, copy_of),
            *(
                _formula_lines(everyone, changed.columns.tolist(), bg_block, 1, catalog)
                if changed is not None
                else ()
            ),
        )
        solutions = (x_b, x_s, s_s, s_b)
        # The background's terms per process are kept on it, unless a scenario changed them.
        bg_terms = (
            inner.process_parts(indicator)
            if changed is None
            else process_parts(bg_block.B, bg.c[indicator])
        )
        parts = cancellation(block.B, c_s, s_s.values) + cancellation_of(*bg_terms, s_b.values)
        defines = inner.method_of(indicator)
        hashes = inner.method_hashes
        nw_set = _set_ref(background, indicator)
        provenance = Provenance(
            input_hash=(
                hashed(nw_set.hash if nw_set is not None else None)
                if hashed is not None
                else _input_hash(
                    self.folder,
                    background,
                    process_id,
                    None,
                    nw_set.hash if nw_set is not None else None,
                )
            ),
            engine_version=headwater.__version__,
            method=method.id,
            method_version=method.version,
            method_hash=inner.method_hash,
            releases=inner.releases,
            indicator=indicator,
            indicator_method=MethodRef(defines.id, defines.version, hashes[defines.id]),
            methods=tuple(MethodRef(m.id, m.version, hashes[m.id]) for m in background.methods),
            pin=str(self.folder.database.background),
            solver=solver.settings,
            condition=solver.condition,
            database_condition=solved.factorization.condition,
            path=tuple(step for solution in solutions for step in solution.path),
            residual=max(solution.residual for solution in solutions),
            exact_zeros=int(x_b.exact_zeros.sum()),
            cancellation=parts,
            scenario=scenario,
            update_route=str(changed.route) if changed is not None else None,
            cut_offs=self.folder.cut_offs,
            used=tuple(used.database.name for used in self.folder.used),
            used_cut_offs=tuple(i for used in self.folder.used for i in used.cut_offs),
            nw_set=nw_set,
            study=study_ref,
            database=database_ref,
        )
        unit = next(i.unit for i in background.indicators if i.id == indicator)
        return Result(
            score=float(x_s.values[target] * f_s[target]),
            forward_score=float(system.h @ system.s),
            indicator=indicator,
            unit=unit,
            provenance=provenance,
            system=system,
            uncovered=_with_own(
                background.not_characterised,
                _acknowledged(self.folder, catalog, method, uncovered, acknowledged),
            ),
            locations=inner.locations.under([p.location for p in study]),
            parameters=parameters,
            formula_lines=formula_lines,
            amount=amount,
        )

    def scores(
        self,
        background: Background,
        process_id: str,
        *,
        indicators: Sequence[str] | None = None,
    ) -> dict[str, float]:
        """Score one unit of what the process `process_id` makes for every indicator of the
        method at once, or for `indicators` only.

        Each score is the one `calc` gives for that indicator, with no scenario: the same checks
        refuse the same databases, and the database is assembled and factorized once. It is for
        the many calculations of a diff, where each difference is made to the old database alone
        and scored for every indicator. The stored scores of the background are used as `calc`
        uses them for `Result.score`, so nothing is solved against the background's matrix. What
        is left out is the rest of a result: the forward solve, `contributions`, `sensitivity`
        and provenance. Raises what `calc` raises. Example, the T-shirt on a method with two
        indicators: `{"climate-change": 1.19205, "methane": 0.00225}`.
        """
        return scores_with_residual(self, background, process_id, indicators)[0]

    def _require_process(self, process_id: str) -> None:
        """Raise ValueError naming the database's processes for an id it does not have."""
        own = [p.id for p in self.folder.processes]
        if process_id not in own:
            raise ValueError(
                f"database {self.name} has no process {process_id!r}."
                f"{did_you_mean(process_id, own)} Its processes: {', '.join(own)}"
            )

    def _prepare(
        self,
        background: Background,
        chosen: Scenario | None,
        process_id: str,
        amount: float = 1.0,
        acknowledged: Sequence[NoFactor] = (),
    ) -> _Prepared:
        """Check the database and build its block of the stacked system, scored for `amount`
        units of what `process_id` makes, as far as it does not depend on an indicator. `calc`
        and `scores` both start here, so a database they refuse, and the numbers they build, are
        the same.

        `chosen` is a study's scenario: a change to a line of this database, or of a database it
        uses, is made in the block, and a change to a background line changes that process for
        everything. Its parameter changes are made to the parameters of the database that holds
        each, and that database's formulas worked out again. `acknowledged` are a study's own
        `no_factor.csv` rows, which count as the database's own."""
        self._require_process(process_id)
        inner = internals(background)
        inner.require_pin(self.folder.database.background, self.name)
        report, catalog, uncovered = self._checked(
            background, () if chosen is None else (chosen,), acknowledged
        )
        report.raise_if_errors()  # exactly what validate reports as errors, no more
        assert catalog is not None  # a catalog that cannot be built is an error of the report
        chain = self.folder.chain
        clash = sorted(p.id for f in chain for p in f.processes if p.id in inner.index)
        if clash:
            raise InvalidDataError(
                Problem(
                    "duplicate-process",
                    f"process {i}",
                    "is both in the database and in the background",
                )
                for i in clash
            )

        # each database's parameters, with the scenario's changes made
        by_name = {
            f.database.name: (
                f.parameters.with_changes(chosen.parameter_values(f.database.name))
                if chosen is not None and chosen.parameter_values(f.database.name)
                else f.parameters
            )
            for f in chain
        }
        parameters = by_name[self.name]
        mine = {p.id for f in chain for p in f.processes}
        changes = chosen.line_changes if chosen is not None else ()
        local = [c for c in changes if c.process_id in mine]
        global_ = [c for c in changes if c.process_id not in mine]
        processes = tuple(
            _worked_out(_changed(p, local), parameters)
            for p in sorted(self.folder.processes, key=lambda p: p.id)
        )
        # A used database's amounts were worked out with its own parameters when it was read: a
        # scenario's changes to its lines and parameters are made here.
        used = tuple(
            _worked_out(_changed(p, local), by_name[u.database.name]) if chosen is not None else p
            for u in self.folder.used
            for p in sorted(u.processes, key=lambda p: p.id)
        )
        world = self._world(background, global_, parameters)
        overrides = [
            (folder.database.name, o)
            for folder in chain
            for o in sorted(folder.overrides, key=lambda o: o.process_id)
        ]
        copies = tuple(
            _copy(
                _worked_out(o.apply_to(world[o.process_id]), by_name[name])
                if name == self.name or chosen is not None
                else o.apply_to(world[o.process_id]),
                name,
            )
            for name, o in overrides
        )
        copy_of = {c.id: o.process_id for c, (_, o) in zip(copies, overrides, strict=True)}
        everyone = [world[i] for i in sorted({c.process_id for c in global_})]

        study = (*processes, *used, *copies)
        n = len(study)
        rows = {p.id: i for i, p in enumerate(study)}
        # The database each process of a used database, and each of its copies, belongs to.
        owners = {
            **{p.id: u.database.name for u in self.folder.used for p in u.processes},
            **{
                c.id: name
                for c, (name, _) in zip(copies, overrides, strict=True)
                if name != self.name
            },
        }
        # Each database's purchases from a process it overrides go to its own copy: the T-shirt's
        # to the T-shirt's, the dye's to the dye's. The copies buy from the background as it is.
        index = StackedIndex(rows, inner.index, shift=n)
        redirected = {
            f.database.name: StackedIndex(
                {
                    **rows,
                    **{
                        copy_of[c.id]: rows[c.id]
                        for c in copies
                        if owners.get(c.id, self.name) == f.database.name
                    },
                },
                inner.index,
                shift=n,
            )
            for f in chain
        }
        suppliers = {
            **{p.id: redirected[owners.get(p.id, self.name)] for p in (*processes, *used)},
            **{c.id: index for c in copies},
        }
        block = assemble(study, catalog, index, suppliers=suppliers)
        # Rows: the database's processes and its copies, then the background's. The background buys
        # nothing from the database, so its columns are the background's A below rows of zeros.
        A_ss = sparse.csc_array(block.A[:n, :])
        A_bs = sparse.csc_array(block.A[n:, :])
        target = next(i for i, p in enumerate(processes) if p.id == process_id)
        f_s = np.zeros(n)
        f_s[target] = amount * block.reference_sign[target]
        return _Prepared(
            catalog=catalog,
            parameters=parameters,
            processes=study,
            copy_of=copy_of,
            copy_rows={rows[c.id]: copy_of[c.id] for c in copies},
            used_rows={rows[i]: name for i, name in owners.items()},
            mine=tuple(i for i, p in enumerate(study) if p.id not in owners),
            everyone=everyone,
            block=block,
            A_ss=A_ss,
            A_bs=A_bs,
            target=target,
            f_s=f_s,
            uncovered=uncovered,
        )

    def _checked(
        self,
        background: Background,
        scenarios: Sequence[Scenario] = (),
        acknowledged: Sequence[NoFactor] = (),
    ) -> tuple[Report, Catalog | None, Mapping[str, tuple[str, ...]]]:
        """The report of `validate`, the catalog it was made with, and the database's flows with no
        factor, which it needed. `calc` reuses the last two. A study's `scenarios` are checked
        against what they change, and its own no_factor rows, `acknowledged`, count as the
        database's: a flow they name is no error."""
        inner = internals(background)
        try:
            catalog = inner.study_catalog(self.folder, scenarios)
        except InvalidDataError as error:
            return Report(error.problems), None, {}
        uncovered = flows_without_factor(self.folder.processes, catalog, background.method)
        report = validate_database(
            self.folder,
            inner.folder,
            method=background.method,
            catalog=catalog,
            lines_complete=True,
            uncovered=uncovered,
            prefixes=inner.id_prefixes,
        )
        problems = list(report.problems)
        named = {r.flow_id for r in acknowledged if r.method in background.method.ids}
        if named:
            problems = [
                p
                for p in problems
                if not (p.code == "no-factor" and p.item.removeprefix("flow ") in named)
            ]
        if scenarios:
            owners = owner_of(self.folder.chain)
            problems += check_scenarios(scenarios, catalog, True, owners, inner.id_prefixes)
        return Report((*problems, *self._locations(background))), catalog, uncovered

    def _locations(self, background: Background) -> list[Problem]:
        """The processes of the database, and of the databases it uses, whose location is not a
        place the release names."""
        codes = internals(background).geographies
        if not codes:
            return []
        problems: list[Problem] = []
        for folder in self.folder.chain:
            name = folder.database.name
            for p in folder.processes:
                if p.location in codes:
                    continue
                where = f"processes/{p.id.removeprefix(f'{name}:')}.yaml"
                problems.append(
                    Problem(
                        "unknown-location",
                        f"process {p.id}",
                        f"location {p.location!r} is not a geography of the background."
                        f"{did_you_mean(p.location, codes)}",
                        location=where if folder is self.folder else in_used(name, where),
                    )
                )
        return problems

    def _world(
        self, background: Background, changes: Sequence[LineChange], parameters: ParameterSet
    ) -> Mapping[str, Process]:
        """Every background process the database overrides or the scenario changes, with every
        line, as the scenario has them."""
        overridden = {o.process_id for f in self.folder.chain for o in f.overrides}
        wanted = overridden | {c.process_id for c in changes}
        world = background.processes(wanted)
        for process_id, process in world.items():
            if any(c.process_id == process_id for c in changes):
                world[process_id] = _worked_out(_changed(process, changes), parameters)
        return world


def scores_with_residual(
    database: Database,
    background: Background,
    process_id: str,
    indicators: Sequence[str] | None = None,
    *,
    amount: float = 1.0,
    acknowledged: Sequence[NoFactor] = (),
) -> tuple[dict[str, float], float]:
    """`database.scores(background, process_id, indicators=indicators)`, and the largest residual
    of the solves behind them: the background's stored scores and the database's own, row by
    row. What `headwater calc` reports for each product it scores this way. `amount` and
    `acknowledged` are a study's, for a product of a study."""
    inner = internals(background)
    wanted = (
        [inner.indicator(i) for i in indicators]
        if indicators is not None
        else [i.id for i in background.indicators]
    )
    prepared = database._prepare(  # pyright: ignore[reportPrivateUsage]
        background, None, process_id, amount, acknowledged
    )
    bg = inner.solved
    settings = bg.factorization.settings
    residual = 0.0
    with ids_in_errors(tuple(p.id for p in prepared.processes)):
        factors = factorize(
            prepared.A_ss,
            tolerance=settings.tolerance,
            condition_limit=settings.condition_limit,
        )
        found: dict[str, float] = {}
        for indicator in wanted:
            c_s = inner.characterisation(prepared.block.flows, indicator)
            h_s = matvec(prepared.block.B.T, c_s)
            x_b = bg.x[indicator]
            x_s = solve_scores(factors, h_s - matvec(prepared.A_bs.T, x_b.values))
            found[indicator] = float(x_s.values[prepared.target] * prepared.f_s[prepared.target])
            residual = max(residual, x_b.residual, x_s.residual)
    return found, residual


def open(path: Path | str) -> Database:
    """Read a database folder: database.yaml and its processes. Raises InvalidDataError."""
    return Database(read_database(path))


def _acknowledged(
    folder: DatabaseFolder,
    catalog: Catalog,
    method: Method,
    uncovered: Mapping[str, tuple[str, ...]],
    acknowledged: Sequence[NoFactor] = (),
) -> tuple[NotCharacterised, ...]:
    """The database's own flows with no factor (`uncovered`, as validation worked them out), each
    with the reason of its row in `no_factor.csv`, then those of the databases it uses, each with
    the reason of its own row. Validation refused the database if one had no row. A flow two of
    them use is one entry, with the processes of both and the first database's reason."""
    by_flow: dict[str, NotCharacterised] = {}
    for study in folder.chain:
        found = (
            uncovered if study is folder else flows_without_factor(study.processes, catalog, method)
        )
        rows = (*acknowledged, *study.no_factor)
        reasons = {row.flow_id: row.reason for row in rows if row.method in method.ids}
        for flow_id, process_ids in found.items():
            known = by_flow.get(flow_id)
            if known is not None:
                processes = tuple(sorted({*known.processes, *process_ids}))
                by_flow[flow_id] = replace(known, processes=processes)
                continue
            flow = catalog.flow(flow_id)
            name = flow.name if flow is not None else flow_id
            by_flow[flow_id] = NotCharacterised(
                flow_id, name, process_ids, in_database=True, reason=reasons[flow_id]
            )
    return tuple(by_flow[flow_id] for flow_id in sorted(by_flow))


def _with_own(
    background: tuple[NotCharacterised, ...], own: tuple[NotCharacterised, ...]
) -> tuple[NotCharacterised, ...]:
    """The background's flows with no factor and the database's own, sorted by flow id. A flow both
    use is one entry, the database's own, with the processes of both."""
    if not own:
        return background
    by_flow = {entry.flow_id: entry for entry in background}
    for entry in own:
        theirs = by_flow.get(entry.flow_id)
        if theirs is not None:
            entry = replace(entry, processes=tuple(sorted({*entry.processes, *theirs.processes})))
        by_flow[entry.flow_id] = entry
    return tuple(by_flow[flow_id] for flow_id in sorted(by_flow))


def _changed(process: Process, changes: Iterable[LineChange]) -> Process:
    """`process` with the scenario's changes to its own lines made: `changes` may hold changes
    to other processes too. Validation checked each line."""
    by_line = {c.line_id: c for c in changes if c.process_id == process.id}
    if not by_line:
        return process
    missing = sorted(by_line.keys() - {e.line_id for e in process.exchanges})
    if missing:
        raise InvalidDataError(
            Problem(
                "scenario-unknown-line",
                f"{process.id} line {line}",
                f"{process.id} has no line {line!r}",
            )
            for line in missing
        )
    exchanges = [
        by_line[e.line_id].apply_to(e) if e.line_id in by_line else e for e in process.exchanges
    ]
    return process.model_copy(update={"exchanges": tuple(exchanges)})


def _copy(process: Process, study: str) -> Process:
    """The database's own copy of a background process, with its own id: the copy of
    `toy:electricity-production` in database `tshirt` is `tshirt:copy:toy:electricity-production`.
    Its formulas are worked out before, with the parameters of the process it copies."""
    copy_id = f"{study}:copy:{process.id}"
    exchanges = tuple(e.model_copy(update={"process_id": copy_id}) for e in process.exchanges)
    return process.model_copy(update={"id": copy_id, "exchanges": exchanges})


def _worked_out(process: Process, parameters: ParameterSet) -> Process:
    """`process` with the amount of every line that is a formula worked out with `parameters`."""
    if not any(e.formula for e in process.exchanges):
        return process
    exchanges = [
        Exchange.model_validate(
            {**e.model_dump(), "amount": parameters.evaluate(e.formula, e.process_id)}
        )
        if e.formula
        else e
        for e in process.exchanges
    ]
    return process.model_copy(update={"exchanges": tuple(exchanges)})


def _formula_lines(
    processes: Sequence[Process],
    columns: Iterable[int],
    block: Block,
    part: int,
    catalog: Catalog,
    copy_of: Mapping[str, str] | None = None,
) -> list[FormulaLine]:
    """Where each line whose amount is a formula sits in `block`. A line with no matrix entry,
    such as one cut off, has none, and its parameters do not move the score through it. A line
    of a copy sees the parameters of the background process it copies (`copy_of`)."""
    copy_of = copy_of or {}
    wanted = {
        (col, e.line_id): e
        for col, p in zip(columns, processes, strict=True)
        for e in p.exchanges
        if e.formula
    }
    if not wanted:
        return []
    found: list[FormulaLine] = []
    lines, emissions = block.lines, block.emissions
    for biosphere, cols, line_ids in (
        (False, lines.col, lines.line_id),
        (True, emissions.col, emissions.line_id),
    ):
        at = np.flatnonzero(np.isin(cols, [col for col, _ in wanted]))
        named = line_ids.take(pa.array(at, pa.int64())).to_pylist()
        for position, col, line_id in zip(at.tolist(), cols[at].tolist(), named, strict=True):
            exchange = wanted.get((col, str(line_id)))
            if exchange is None or exchange.formula is None:
                continue
            one = catalog.convert(exchange.model_copy(update={"amount": 1.0}))
            found.append(
                FormulaLine(
                    label=exchange.label,
                    formula=exchange.formula,
                    scope=copy_of.get(exchange.process_id, exchange.process_id),
                    part=part,
                    biosphere=biosphere,
                    position=position,
                    factor=one,
                )
            )
    return found


def _input_hash(
    folder: DatabaseFolder,
    background: Background,
    process_id: str,
    scenario: Scenario | None,
    nw_set: str | None = None,
) -> str:
    """sha256 of the database's records, those of the databases it uses, the process scored, the
    scenario it is calculated in, if any, and the background's fingerprint: the same content
    always gives the same hash. A database's scenarios count only when one is calculated, and
    then that one alone. For an indicator of a set, `nw_set` is the set's hash, and counts too;
    for an indicator of a method nothing is added, so loading a set does not change its hash."""
    records = {"database": _fields(folder), **records_of(folder)}
    if folder.used:
        records["used"] = [{"database": _fields(used), **records_of(used)} for used in folder.used]
    fingerprint = internals(background).fingerprint
    inputs: dict[str, object] = {
        "database": records,
        "process": process_id,
        "background": fingerprint,
    }
    if scenario is not None:
        inputs["scenario"] = scenario.model_dump(mode="json")
    if nw_set is not None:
        inputs["set"] = nw_set
    return sha256_of(inputs)


def _fields(folder: DatabaseFolder) -> dict[str, object]:
    """The fields of database.yaml that are set: an optional field left out, or added to the
    format later, does not change the hash."""
    return folder.database.model_dump(mode="json", exclude_none=True)


def _set_ref(background: Background, indicator: str) -> SetRef | None:
    """The set `indicator` comes from, for provenance, or None for an indicator of a method."""
    inner = internals(background)
    nw_set = inner.set_of(indicator)
    if nw_set is None:
        return None
    pinned = nw_set.method
    return SetRef(
        id=nw_set.id,
        version=nw_set.version,
        hash=inner.set_hashes[nw_set.id],
        method=MethodRef(pinned.id, pinned.version, pinned.hash),
        unit=nw_set.unit,
        left_out=inner.left_out(indicator),
    )
