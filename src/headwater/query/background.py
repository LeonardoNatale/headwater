import difflib
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import replace
from functools import cached_property
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.dataset as ds
from scipy import sparse

from headwater.calc import (
    CalculationError,
    Factorization,
    Solution,
    factorize,
    scores,
    supply,
    weighted_scores,
    weighted_sum,
)
from headwater.calc import explain as calc_explain
from headwater.calc.arrays import Vector
from headwater.calc.cancellation import process_parts
from headwater.model import (
    DEFAULT_UNITS,
    Catalog,
    Flow,
    FlowProperty,
    Indicator,
    InvalidDataError,
    Method,
    NwSet,
    Pin,
    Problem,
    Process,
    Scenario,
    SetIndicator,
    Units,
    combine,
    did_you_mean,
    set_indicators,
)
from headwater.query._hash import records_of, sha256_of
from headwater.query._open import open_source
from headwater.query._release import assemble_release
from headwater.query._system import (
    Assembled,
    Part,
    Solved,
    System,
    assemble,
    characterisation,
    ids_in_errors,
    matvec,
    require_global_factors,
)
from headwater.query.result import (
    Contribution,
    Locations,
    NotCharacterised,
    Sensitivity,
    Where,
    contribution_of,
    counted,
    rank_sensitivity,
    where_of,
)
from headwater.query.search import Match, SearchTable, table_of_folder
from headwater.release import Release, ReleaseRef, method_hash, methods_hash, set_hash
from headwater.release.indicators import write_indicators
from headwater.release.manifest import ReleaseKind
from headwater.release.schema import read_processes
from headwater.study import (
    DatabaseFolder,
    ProcessFolder,
    build_catalog,
    read_method,
    read_process_folder,
    read_set,
    validate,
)
from headwater.study.validate import flows_without_factor, id_prefixes


class Internals:
    """What this package's calculations read from a background: the factorized matrices, the row
    of each process, the records a study is checked against, and the hashes of its provenance.

    Integer indices live here and never leave a calculation (AGENTS.md, rule 1), so none of it is
    on `Background`. The package's own code reaches it with `internals(bg)`, from
    `headwater.query._internals`.
    """

    def __init__(
        self,
        source: ProcessFolder | Release,
        method: Method | Sequence[Method],
        sets: Sequence[NwSet] = (),
    ) -> None:
        """Validate, build A and B, factorize A, and solve the scores of every indicator, as
        `Background` says."""
        methods = (method,) if isinstance(method, Method) else tuple(method)
        method = combine(methods)
        if isinstance(source, Release):
            _require_source(source)
            require_global_factors(method)
            assembled = assemble_release(source, method)
            release = source
        else:
            assembled = _assemble_folder(source, method)
            release = None
        hashes: dict[str, str] = {}  # of the methods the sets pin, kept for `method_hashes`
        derived = _derived(methods, sets, hashes)  # checked before the long factorization
        block = assembled.block
        c: dict[str, Vector] = {}
        h: dict[str, Vector] = {}
        x: dict[str, Solution] = {}
        with ids_in_errors(block.ids):
            factorization = factorize(block.A)
            for indicator in method.indicators:
                c[indicator.id] = characterisation(block.flows, method, indicator.id)
                h[indicator.id] = matvec(block.B.T, c[indicator.id])
                x[indicator.id] = scores(factorization, h[indicator.id])
                fallback = x[indicator.id].fallback
                if fallback is not None:  # the solve needed pivoting: keep those factors
                    factorization = fallback
            for d in derived:
                c[d.id] = weighted_sum([(k, c[i]) for i, k in d.terms])
                h[d.id] = weighted_sum([(k, h[i]) for i, k in d.terms])
                x[d.id] = weighted_scores(factorization, [(k, x[i]) for i, k in d.terms], h[d.id])
                fallback = x[d.id].fallback
                if fallback is not None:
                    factorization = fallback
        self.folder = assembled.folder
        self.method = method
        """The methods the background was loaded with, as one: with several, their ids joined by
        `+`, such as `EF-3.1+IPCC-2021`, and all their indicators and factors."""
        self.methods = methods
        """The methods the background was loaded with, in order, each as it is."""
        self.sets = tuple(sets)
        """The normalisation and weighting sets loaded with it, in order."""
        self._derived = {d.id: d for d in derived}
        self._hashes = hashes
        self._process_parts: dict[str, tuple[Vector, Vector]] = {}
        self.release = release
        """The release this background was read from, or None for a folder."""
        self.solved = Solved(block, factorization, c, h, x)
        """The factorized matrices, for this package's calculations. Their indices mean nothing
        outside them. The factorization is replaced by the pivoted one when a solve needed it
        (see `keep`)."""
        self.not_characterised: tuple[NotCharacterised, ...] = assembled.uncovered
        """Elementary flows of the background that no indicator of the method covers, with every
        process that uses them. They count 0 in every score."""

    @property
    def namespace(self) -> str:
        return self.folder.namespace

    @cached_property
    def id_prefixes(self) -> frozenset[str]:
        """What the ids of its processes start with, up to the first colon: `{"toy"}` for the
        toy background, `{"ecoinvent"}` for an ecoinvent release. A study's line may buy from a
        process whose id starts with one of them."""
        return id_prefixes(self.index)

    def require_pin(self, pin: Pin, study: str) -> None:
        """Raise InvalidDataError unless the `background:` pin of `study` names the release this
        background was read from.

        The pin is `name@version`. For a release the name must be the release's name and the
        version a prefix of its hash, at least 12 characters, as `find` reads it: a label such as
        `fixture` or `latest` names no release in particular and is refused. A background read
        from a folder has no hash, so there is nothing to check.
        """
        release = self.release
        if release is None:
            return
        version = pin.version
        if pin.name == release.name and len(version) >= 12 and release.hash.startswith(version):
            return
        short = f"{release.name}@{release.hash[:12]}"
        raise InvalidDataError(
            [
                Problem(
                    "background-pin-mismatch",
                    f"study {study} background {pin}",
                    f"is not the release given, {short}. Pin the release by its name and at "
                    f"least 12 characters of its hash: background: {short}",
                )
            ]
        )

    def keep(self, fallback: Factorization | None) -> None:
        """Keep the pivoted factors that a solve had to make, for every solve after it.

        Example: the forward solve of a study fails its check with the unpivoted factors and falls
        back to pivoting. Without this, the next study would try the failing factors first and
        factorize again. `None`, no fallback, changes nothing.
        """
        if fallback is not None:
            self.solved = replace(self.solved, factorization=fallback)

    @cached_property
    def index(self) -> Mapping[str, int]:
        """The row of every process in A, by id. Built once: a study stacks its own rows above
        these without copying them."""
        return {pid: i for i, pid in enumerate(self.solved.ids)}

    @cached_property
    def _flows(self) -> Mapping[str, Flow]:
        return {flow.id: flow for flow in self.folder.flows}

    @cached_property
    def _properties(self) -> Mapping[str, tuple[FlowProperty, ...]]:
        found: defaultdict[str, list[FlowProperty]] = defaultdict(list)
        for prop in self.folder.flow_properties:
            found[prop.flow_id].append(prop)
        return {flow_id: tuple(props) for flow_id, props in found.items()}

    @cached_property
    def _units(self) -> Units:
        return DEFAULT_UNITS.merge(Units(self.folder.units))

    @cached_property
    def _processes(self) -> Mapping[str, Process]:
        """The records of `folder` by id. For a release, each holds its reference line only."""
        return {process.id: process for process in self.folder.processes}

    def processes(self, process_ids: Iterable[str]) -> dict[str, Process]:
        """The processes of this background with every one of their lines, by id.

        Example: `processes(["toy:fabric-production"])` holds its four lines, e01 to e04, from
        the folder, or from the release's exchanges table. What a study overrides or a scenario
        changes is built from these. Each one is read once. Raises ValueError for an id this
        background does not hold.

        For a release, each call with ids not read yet scans the exchanges table: give every id
        in one call (`Background.processes` has the times).
        """
        wanted = sorted(set(process_ids))
        unknown = [i for i in wanted if i not in self.index]
        if unknown:
            raise ValueError(
                f"{unknown[0]!r} is not the id of a process of this background."
                f"{did_you_mean(unknown[0], self.index)}"
            )
        missing = [i for i in wanted if i not in self._full]
        if missing:
            self._full.update(self._read_processes(missing))
        return {i: self._full[i] for i in wanted}

    @cached_property
    def _full(self) -> dict[str, Process]:
        return {} if self.release is not None else dict(self._processes)

    def _read_processes(self, process_ids: list[str]) -> dict[str, Process]:
        """The processes from the release's exchanges and processes tables, each line with the
        name of its flow from `_flows`, which holds every flow of the release: the exchanges
        table does not store it, and the flows table is not read again."""
        assert self.release is not None
        path = self.release.path

        def rows(table: str, key: str, values: list[str]) -> pa.Table:
            data = ds.dataset(path / f"{table}.parquet", format="parquet")  # pyright: ignore[reportUnknownMemberType]
            return data.to_table(filter=pc.field(key).isin(pa.array(values, pa.string())))  # pyright: ignore[reportUnknownMemberType]

        flows = self._flows

        def flow_name(flow_id: str) -> str | None:
            flow = flows.get(flow_id)
            return flow.name if flow is not None else None

        found = read_processes(
            rows("processes", "id", process_ids), rows("exchanges", "process_id", process_ids)
        )
        return {
            p.id: p.model_copy(
                update={
                    "exchanges": tuple(
                        e.model_copy(update={"flow_name": flow_name(e.flow_id)})
                        if e.flow_name is None
                        else e
                        for e in p.exchanges
                    )
                }
            )
            for p in found
        }

    @cached_property
    def geographies(self) -> frozenset[str]:
        """The codes of the places the release names, such as `GLO` and `RoW`: what a study's
        locations are checked against. Empty for a folder of process files, and for a release
        built without geographies: there is then nothing to check against."""
        if self.release is None:
            return frozenset()
        return frozenset(
            str(code) for code in self.release.table("geographies").column("code").to_pylist()
        )

    def study_catalog(self, study: ProcessFolder, scenarios: Sequence[Scenario] = ()) -> Catalog:
        """What a study needs from this background to be checked and turned into matrix entries.

        It holds the study's own records, the background processes it names (the suppliers of
        its lines and of its overrides, and the processes its overrides and scenarios change),
        the flows all their lines name, with their properties, and the units. Each process an
        override or a scenario changes holds every one of its lines: the overrides and scenarios
        are checked against them, and the study's copies are built from them. The others hold
        their reference line only. For a supplier the background does not hold, the closest id
        it does hold is added, so the error can suggest it.

        Not the whole background: building a catalog of every flow costs more than the rest of
        a calc. A flow the study defines and the background holds too is in it twice, so a
        difference between the two raises InvalidDataError (`duplicate-flow`).

        The databases a database uses are part of it: their records, and what they name in the
        background, are in the catalog too. So are the processes `scenarios`, a study's, change
        and the suppliers they name.
        """
        chain = study.chain if isinstance(study, DatabaseFolder) else (study,)
        processes_of = [p for folder in chain for p in folder.processes]
        own = {p.id for p in processes_of}
        lines = [e for p in processes_of for e in p.exchanges]
        changed: set[str] = set()
        suppliers: set[str] = set()
        for folder in chain:
            if isinstance(folder, DatabaseFolder):
                for override in folder.overrides:
                    changed.add(override.process_id)
                    lines.extend(override.lines)
        if isinstance(study, DatabaseFolder):
            for scenario in scenarios:
                for line_change in scenario.line_changes:
                    changed.add(line_change.process_id)
                    if line_change.supplier_id is not None:
                        suppliers.add(line_change.supplier_id)
        complete = self.processes(i for i in changed - own if i in self.index)
        lines.extend(e for p in complete.values() for e in p.exchanges)
        suppliers |= {e.supplier_id for e in lines if e.supplier_id is not None}
        processes = dict(complete)
        for process_id in sorted((suppliers | changed) - own - processes.keys()):
            found = process_id if process_id in self.index else _closest(process_id, self.index)
            if found is not None:
                processes.setdefault(found, self._processes[found])
        flows = [f for folder in chain for f in folder.flows]
        properties = [prop for folder in chain for prop in folder.flow_properties]
        flow_ids = {e.flow_id for e in lines} | {f.id for f in flows}
        flow_ids |= {prop.flow_id for prop in properties}
        wanted = [f for f in sorted(flow_ids) if f in self._flows]
        units = self._units
        for folder in chain:
            units = units.merge(Units(folder.units))
        return Catalog(
            flows=[*flows, *(self._flows[f] for f in wanted)],
            processes=[*processes_of, *(processes[i] for i in sorted(processes))],
            properties=[
                *properties,
                *(prop for f in wanted for prop in self._properties.get(f, ())),
            ],
            units=units,
        )

    @cached_property
    def locations(self) -> Locations:
        """The location of every process, in the order of its row: `GLO` for each toy process.
        From the process records of the folder, or the release's processes table."""
        return Locations.of([self._processes[pid].location for pid in self.solved.ids])

    @cached_property
    def search(self) -> SearchTable:
        """The table `Background.find` reads, built on its first call."""
        return table_of_folder(self.folder)

    def supply(self, indicator: str, row: int) -> System:
        """The background as a system for one unit of product `row`, with how much each process
        runs for it, from one forward solve. Keeps pivoted factors if the solve needs them."""
        solved = self.solved
        block = solved.block
        demand = np.zeros(len(block.ids))
        demand[row] = block.reference_sign[row]
        with ids_in_errors(block.ids):
            runs = supply(solved.factorization, demand)
        self.keep(runs.fallback)
        n = len(block.ids)
        return System(
            ids=block.ids,
            names=block.names,
            flow_ids=block.flow_ids,
            units=block.units,
            A_ss=sparse.csc_array((0, 0)),
            A_bs=sparse.csc_array((n, 0)),
            A_bb=block.A,
            h=solved.h[indicator],
            x=solved.x[indicator].values,
            s=runs.values,
            f=demand,
            reference_sign=block.reference_sign,
            parts=(Part(block, solved.c[indicator], 0),),
            target=row,
        )

    def row(self, process_id: str) -> int:
        """The row of `process_id`. Raises ValueError, with the closest id, for an id this
        background does not hold."""
        row = self.index.get(process_id)
        if row is None:
            raise ValueError(
                f"{process_id!r} is not the id of a process of this background."
                f"{did_you_mean(process_id, self.index)}"
            )
        return row

    def score(self, indicator: str, row: int) -> float:
        """The stored score of one unit of what process `row` makes, or of one unit it treats: a
        treatment's positive burden per kg, as `contributions`, `score_many` and an indicators
        release give it."""
        solved = self.solved
        return float(solved.x[indicator].values[row] * solved.block.reference_sign[row])

    @property
    def releases(self) -> tuple[ReleaseRef, ...]:
        """The release the background was read from, for provenance. Empty for a folder."""
        return (self.release.ref,) if self.release is not None else ()

    @cached_property
    def method_hash(self) -> str:
        """The hash of the method, or of several from their own hashes (`methods_hash`)."""
        return methods_hash(list(self.method_hashes.values()))

    @cached_property
    def method_hashes(self) -> dict[str, str]:
        """The hash of each method of `methods`, by id, in order."""
        return {m.id: self._hashes.get(m.id) or method_hash(m) for m in self.methods}

    @cached_property
    def fingerprint(self) -> str:
        """What the background was made from: the release's hash, or the records of the folder,
        and the method. Part of the input hash of every result."""
        if self.release is not None:
            source = {"release": self.release.ref.model_dump()}
        else:
            source = {"records": records_of(self.folder)}
        return sha256_of({"background": source, "method": self.method_hash})

    def process_parts(self, indicator: str) -> tuple[Vector, Vector]:
        """For each background process, the sums of its positive and of its negative direct
        terms for `indicator`, per unit it runs (`calc.cancellation.process_parts`). Worked out
        once per indicator, for the cancellation flag of every result."""
        if indicator not in self._process_parts:
            solved = self.solved
            self._process_parts[indicator] = process_parts(solved.block.B, solved.c[indicator])
        return self._process_parts[indicator]

    def method_of(self, indicator: str) -> Method:
        """The method of `methods` that has `indicator`. For an indicator of a set, the method
        the set is for."""
        nw_set = self.set_of(indicator)
        if nw_set is not None:
            indicator = self._derived[indicator].terms[0][0]
        for method in self.methods:
            if any(i.id == indicator for i in method.indicators):
                return method
        raise ValueError(f"method {self.method.id} has no indicator {indicator!r}")

    @cached_property
    def indicators(self) -> tuple[Indicator, ...]:
        """Every indicator it scores: those of its methods, then those of its sets, each set's
        normalised scores, weighted scores and single score. Without a set, the methods'."""
        return (*self.method.indicators, *self._derived.values())

    def set_of(self, indicator: str) -> NwSet | None:
        """The set that `indicator` comes from, or None for an indicator of a method."""
        derived = self._derived.get(indicator)
        if derived is None:
            return None
        return next(s for s in self.sets if s.id == derived.nw_set)

    def left_out(self, indicator: str) -> tuple[str, ...]:
        """The indicators of its method that the set of `indicator` has no row for: they count
        in none of the set's scores. Empty for an indicator of a method."""
        derived = self._derived.get(indicator)
        return derived.left_out if derived is not None else ()

    @cached_property
    def set_hashes(self) -> dict[str, str]:
        """The hash of each set of `sets`, by id."""
        return {s.id: set_hash(s) for s in self.sets}

    def characterisation(self, flows: Sequence[str], indicator: str) -> Vector:
        """The factor of each of `flows` for `indicator`, as `query._system.characterisation`
        gives it. For an indicator of a set, the same weighted sum of its method's factors."""
        derived = self._derived.get(indicator)
        if derived is None:
            return characterisation(flows, self.method, indicator)
        return weighted_sum(
            [(k, characterisation(flows, self.method, i)) for i, k in derived.terms]
        )

    def indicator(self, indicator: str | None = None) -> str:
        """The id of `indicator`, or of the method's only indicator when it is None. An indicator
        of a set is never chosen by default."""
        ids = [i.id for i in self.method.indicators]
        if indicator is None:
            if len(ids) == 1:
                return ids[0]
            raise ValueError(
                f"method {self.method.id} has {len(ids)} indicators ({', '.join(ids)}); "
                "name one with indicator="
            )
        if indicator not in ids and indicator not in self._derived:
            hint = did_you_mean(indicator, [*ids, *self._derived])
            raise ValueError(f"method {self.method.id} has no indicator {indicator!r}.{hint}")
        return indicator


class Background:
    """A background and a method, factorized once, with one score vector per indicator.

    Example, the toy: `load("tests/fixtures/toy/background", method=..., namespace="toy")` gives
    the cotton fabric a score of 3.7086 kg CO2-eq per kg: `bg.score("toy:fabric-production")`.
    Studies are then scored on it with no new factorization. The same background read from a
    release: `load(release_path)`.

    A flow with no factor for an indicator counts 0 for it, whether the method covers the flow
    for other indicators or for none. The flows that no indicator covers are listed in
    `not_characterised`. A result lists the ones its study reaches.
    """

    def __init__(
        self,
        source: ProcessFolder | Release,
        method: Method | Sequence[Method],
        sets: Sequence[NwSet] = (),
    ) -> None:
        """Validate, build A and B, factorize A, and solve the scores of every indicator.

        `source` is a folder of process files, read with `read_process_folder`, or a release.
        `method` is one method, or several used together, such as EF-3.1 and IPCC-2021: the
        background then scores the indicators of all of them. Raises InvalidDataError for bad
        links or units, for factors of one location, and for an indicator id that two methods
        share, and a calc error naming the processes when the matrix cannot be solved. A flow
        that the methods do not cover is not an error: ecoinvent has many.

        Each of `sets`, normalisation and weighting sets, adds its normalised and weighted scores
        and its single score as indicators of their own, worked out from the scores of its
        method with no new solve ([`set_indicators`]). A set must pin a method loaded here by
        its hash: InvalidDataError otherwise, naming both.
        """
        self._internals = Internals(source, method, sets)

    @property
    def method(self) -> Method:
        """The methods the background was loaded with, as one: with several, their ids joined by
        `+`, such as `EF-3.1+IPCC-2021`, and all their indicators and factors."""
        return self._internals.method

    @property
    def methods(self) -> tuple[Method, ...]:
        """The methods the background was loaded with, in order, each as it is."""
        return self._internals.methods

    @property
    def sets(self) -> tuple[NwSet, ...]:
        """The normalisation and weighting sets loaded with it, in order."""
        return self._internals.sets

    @property
    def release(self) -> Release | None:
        """The release this background was read from, or None for a folder."""
        return self._internals.release

    @property
    def not_characterised(self) -> tuple[NotCharacterised, ...]:
        """Elementary flows of the background that no indicator of the method covers, with every
        process that uses them. They count 0 in every score."""
        return self._internals.not_characterised

    @property
    def indicators(self) -> tuple[Indicator, ...]:
        """Every indicator it scores: those of its methods, then those of its sets, each set's
        normalised scores, weighted scores and single score. Without a set, the methods'."""
        return self._internals.indicators

    def processes(self, process_ids: Iterable[str]) -> dict[str, Process]:
        """The processes of this background with every one of their lines, by id.

        Example: `processes(["toy:fabric-production"])` holds its four lines, e01 to e04, from
        the folder, or from the release's exchanges table. Raises ValueError for an id this
        background does not hold.

        On a release, one call with every id is much faster than one call per id: with 50 ids in a
        call, about 4.6 ms per id against 42 ms per id alone, on ecoinvent 3.12 cut-off
        (bench/processes_read.py, results and machine in
        docs/appendix/benchmarks.md#processes-read-from-a-release). A process already read is not
        read again.
        """
        return self._internals.processes(process_ids)

    def score(self, process_id: str, *, indicator: str | None = None) -> float:
        """The score of one unit of what a background process makes, with no solve.

        Example: `score("toy:fabric-production")` is 3.7086 kg CO2-eq per kg of cotton fabric. For a
        waste treatment it is per unit treated, a positive burden: 1.2 per kg for
        `toy:incineration-textile-waste`. It is the stored score that `contributions` starts from
        and `score_many` multiplies. `indicator` may be left out when the method has one. Raises
        ValueError for an id this background does not hold, with the closest id.
        """
        inner = self._internals
        return inner.score(inner.indicator(indicator), inner.row(process_id))

    def contributions(
        self,
        process_id: str,
        *,
        indicator: str | None = None,
        min_share: float = 0.03,
        max_depth: int = 3,
    ) -> Contribution:
        """Contribution analysis of one unit of a background product, with no solve: what its
        score is made of, as a tree.

        `process_id` is the id of the process that makes the product, such as
        `toy:fabric-production`: 1 kg of cotton fabric is 3.7086 kg CO2-eq, of which 2.1192 is
        direct and 3 kWh of electricity × 0.5298 is the rest. For a waste treatment it is 1 kg
        treated, and the score is the positive burden per kg. `min_share` and `max_depth` are as
        for `Result.contributions`. Raises ValueError for an id this background does not hold.
        """
        inner = self._internals
        indicator = inner.indicator(indicator)
        row = inner.row(process_id)
        solved = inner.solved
        block = solved.block
        x = solved.x[indicator].values
        with ids_in_errors(block.ids):
            root = calc_explain(
                block.A,
                solved.h[indicator],
                x,
                row,
                amount=float(block.reference_sign[row]),
                min_share=min_share,
                max_depth=max_depth,
            )
        return contribution_of(root, block, x, inner.locations, max_depth=max_depth)

    def sensitivity(
        self,
        process_id: str,
        *,
        indicator: str | None = None,
        top: int | None = None,
        include_reference: bool = False,
    ) -> tuple[Sensitivity, ...]:
        """Sensitivity analysis of one unit of a background product: how much its score moves with
        each line of its process and of every process upstream, largest elasticity first.

        As `Result.sensitivity`, with one forward solve for how much each process runs. For 1 kg of
        cotton fabric, the largest is its own 2.0 kg of CO2 (elasticity 0.5393), then the 3 kWh
        of electricity it buys (3/7). Raises ValueError for an id this background does not hold,
        and a calc error naming the product when its score is exactly 0.
        """
        if top is not None and top < 0:
            raise ValueError(f"top must be zero or positive, got {top}")
        inner = self._internals
        indicator = inner.indicator(indicator)
        row = inner.row(process_id)
        score = inner.score(indicator, row)
        if score == 0:
            raise CalculationError(f"score of {process_id} is zero, so elasticities are undefined")
        system = inner.supply(indicator, row)
        return rank_sensitivity(system, score, top=top, include_reference=include_reference)

    def where(
        self, process_id: str, *, indicator: str | None = None, top: int | None = 10
    ) -> Where:
        """Where the score of one unit of a background product is emitted, by process and by
        location, largest first.

        Each process upstream emits its direct emissions times how much it runs for the product,
        from one forward solve, as for `sensitivity`. For 1 kg of cotton fabric: 2.1192 by fabric
        production itself and 1.5894 by electricity production, which runs 3 kWh. The `top`
        largest processes and locations are kept, and the others summed into a rest; `top=None`
        keeps every process that emits something. Raises ValueError for an id this background
        does not hold.
        """
        if top is not None and top < 0:
            raise ValueError(f"top must be zero or positive, got {top}")
        inner = self._internals
        system = inner.supply(inner.indicator(indicator), inner.row(process_id))
        return where_of(system, inner.locations, top=top)

    def direct_and_upstream(
        self, process_id: str, *, indicator: str | None = None
    ) -> tuple[float, float]:
        """The score of one unit of a background product split as Brightway splits it: what the
        process itself emits, and the rest, upstream.

        `direct` is the process's own row of `where(process_id, top=None)`: its direct emissions
        times how much it runs for the unit, including the extra runs a loop through other
        processes adds. `upstream` is `score(process_id)` minus `direct`. With no loop, `direct`
        is `contributions(process_id).direct`. On the toy, 1 kg of cotton fabric is 2.1192
        direct and 1.5894 upstream. One forward solve, as for `where`. Raises ValueError for an
        id this background does not hold.
        """
        inner = self._internals
        indicator = inner.indicator(indicator)
        row = inner.row(process_id)
        system = inner.supply(indicator, row)
        direct = float(system.h[row] * system.s[row])
        return direct, self.score(process_id, indicator=indicator) - direct

    def get_process(
        self, name: str, *, product: str | None = None, location: str | None = None
    ) -> Match:
        """The one background process with this name, and this reference product and location
        when given, as Brightway's `get_node(name=..., product=..., location=...)`.

        Example, the toy: `get_process("electricity production", product="electricity",
        location="GLO")` is the `Match` of `toy:electricity-production`, whose `flow_id`,
        `toy:electricity`, is the flow a study's line names. Each is matched exactly, case
        included: `find` is for searching. Raises ValueError when no process fits, naming the
        processes of that name or the closest names, and when several fit, naming them.
        """
        return self._internals.search.get(name, product=product, location=location)

    def find(
        self,
        text: str,
        *,
        location: str | None = None,
        product: str | None = None,
        kind: str | None = None,
        limit: int | None = 20,
    ) -> tuple[Match, ...]:
        """The background processes that match `text`, best first: the id to write in a study.

        Example, the toy: `find("electricity")` is one `Match`, `toy:electricity-production`,
        `why` `name starts with`. The rules, first to last, and a process is listed once at the
        first it meets:

        1. the name is the text
        2. the name starts with the text
        3. the name has every word of the text, in any order, each as a part of a word
        4. to 6. the same three on the name of the reference product

        Case is ignored and nothing is fuzzy: no synonyms, no spelling repair. Words are split on
        spaces, so `steel low-alloyed` finds `market for steel, low-alloyed`. Ties go by name
        (without case), location, product and id.

        `location` keeps the processes in that place (its whole code, `RER`, not `RE`), `product`
        those whose reference product has every word of it, and `kind` those of a `ProcessKind`
        such as `market_activity`. `limit` keeps the best ones, `None` all. The table is read
        once and nothing is stored on disk. Raises ValueError for no text, a negative limit, a
        `location` that no process has or a `kind` that does not exist, naming the choices.
        """
        search = self._internals.search
        return search.find(text, location=location, product=product, kind=kind, limit=limit)

    def write_indicators(
        self, *, name: str | None = None, into: Path | str | None = None
    ) -> Release:
        """Freeze the score vectors of every indicator into an indicators release.

        It goes beside the release this background was read from, or into `into`, named
        `<release>.<method>` or `name`, and pins that release by hash. With several methods it
        holds each of them, and is named with their ids joined by dots:
        `<release>.EF-3.1.IPCC-2021`. The release itself is not touched. Scores of the same
        background and methods always give the same release.
        """
        inner = self._internals
        if inner.release is None:
            raise ValueError(
                "only a background read from a release can write indicators: "
                "freeze the folder with release.build, then load the release"
            )
        solved = inner.solved
        # the methods' own scores only: the scores of a set are worked out from them
        x = {i.id: solved.x[i.id] for i in inner.method.indicators}
        return write_indicators(
            inner.release,
            inner.methods,
            product_ids=solved.ids,
            x={i: s.values for i, s in x.items()},
            reference_sign=solved.block.reference_sign,
            solver=solved.factorization.settings,
            steps={i: s.path for i, s in x.items()},
            residual=max(s.residual for s in x.values()),
            exact_zeros={i: int(s.exact_zeros.sum()) for i, s in x.items()},
            name=name,
            into=into,
        )

    def __repr__(self) -> str:
        """Example: `Background(release toy-background@6864b7dbb65d, method toy-climate 1, 1
        indicator, 3 processes)`. A folder is named by its last part and its namespace, so a
        printed background shows no path of the machine it runs on."""
        inner = self._internals
        release = inner.release
        if release is not None:
            source = f"release {release.name}@{release.hash[:12]}"
        else:
            source = f"folder {inner.folder.path.resolve().name}, namespace {inner.namespace}"
        parts = [source, f"method {inner.method.id} {inner.method.version}"]
        parts += [f"set {s.id} {s.version}" for s in inner.sets]
        parts.append(counted(len(inner.indicators), "indicator", "indicators"))
        parts.append(counted(len(inner.solved.ids), "process", "processes"))
        return f"Background({', '.join(parts)})"


def load(
    path: Path | str,
    *,
    method: Path | str | Sequence[Path | str] | None = None,
    namespace: str | None = None,
    sets: Path | str | Sequence[Path | str] | None = None,
) -> Background:
    """Read a release or a background folder, and factorize the background once.

    `path` is one of:

    - a release folder, which holds `release.json`
    - a release in the local cache, written `name@hash` with at least 12 characters of the hash
    - a folder of process files, such as `tests/fixtures/toy/background`

    For a release, `method` is the id of a method the release holds, or a method folder (a
    `Path` is always a folder), and may be left out when the release holds one method. For a
    folder it is the method folder, and
    `namespace` prefixes the process ids (`toy` for `toy:fabric-production`; it defaults to the
    folder name).

    Several methods are used together when `method` is a list of them:
    `["methods/EF-3.1", "methods/IPCC-2021"]`, or the ids of methods the release holds. The
    background then scores the indicators of all of them (see `Background`).

    `sets` is a set folder, or a list of them: normalisation and weighting sets, each pinned to
    one of the methods by its hash. Each adds its normalised and weighted scores and its single
    score as indicators, such as `toy-nw:single-score`.
    """
    source = open_source(path)
    chosen = _methods(method)
    nw_sets = [read_set(s) for s in _folders(sets)]
    if isinstance(source, Release):
        if namespace is not None:
            raise ValueError(
                f"namespace is for a folder of process files: the ids of release {source.name} "
                "are fixed"
            )
        _require_source(source)
        if chosen is None:
            return Background(source, _release_method(source, None), nw_sets)
        return Background(source, [_release_method(source, m) for m in chosen], nw_sets)
    if chosen is None:
        raise ValueError(
            "method is required for a folder of process files: the path of a method folder"
        )
    return Background(
        read_process_folder(source, namespace=namespace), [read_method(m) for m in chosen], nw_sets
    )


def _folders(sets: Path | str | Sequence[Path | str] | None) -> list[Path | str]:
    """The set folders asked for, one by one."""
    if sets is None:
        return []
    return [sets] if isinstance(sets, Path | str) else list(sets)


def _derived(
    methods: Sequence[Method], sets: Sequence[NwSet], hashes: dict[str, str]
) -> tuple[SetIndicator, ...]:
    """The indicators of every set, after checking that each pins a method loaded, by its hash.
    The hash of each method a set pins is put in `hashes`, by id, so it is worked out once.

    Raises InvalidDataError naming the set and both hashes when its method was loaded with other
    content, the methods loaded when its method was not, and a set given twice.
    """
    problems: list[Problem] = []
    by_id = {m.id: m for m in methods}
    found: list[SetIndicator] = []
    seen: set[str] = set()
    for nw_set in sets:
        item = f"set {nw_set.id}"
        if nw_set.id in seen:
            problems.append(Problem("duplicate-set", item, "is given twice"))
            continue
        seen.add(nw_set.id)
        pinned = nw_set.method
        method = by_id.get(pinned.id)
        if method is None:
            problems.append(
                Problem(
                    "set-method-missing",
                    item,
                    f"is for method {pinned.id}, which is not loaded (loaded: "
                    f"{', '.join(by_id)}). Load it with the set.",
                )
            )
            continue
        if method.id not in hashes:
            hashes[method.id] = method_hash(method)
        loaded = hashes[method.id]
        if loaded != pinned.hash:
            problems.append(
                Problem(
                    "set-method-mismatch",
                    item,
                    f"pins method {pinned.id} {pinned.version} with hash {pinned.hash[:12]}, but "
                    f"the method loaded, version {method.version}, has hash {loaded[:12]}: its "
                    "factors or indicators are not those the set was made for. Make the set again "
                    "for this method, with headwater method import-set.",
                )
            )
            continue
        try:
            found.extend(set_indicators(nw_set, method))
        except InvalidDataError as error:
            problems.extend(error.problems)
    ids = [i.id for i in found]
    problems.extend(
        Problem("duplicate-indicator", f"indicator {i}", "is given by two sets")
        for i in sorted({i for i in ids if ids.count(i) > 1})
    )
    owners = {i.id: m.id for m in methods for i in m.indicators}
    problems.extend(
        Problem(
            "duplicate-indicator",
            f"indicator {i.id}",
            f"is an indicator of method {owners[i.id]} and of set {i.nw_set}",
        )
        for i in found
        if i.id in owners
    )
    if problems:
        raise InvalidDataError(problems)
    return tuple(found)


def _methods(method: Path | str | Sequence[Path | str] | None) -> list[Path | str] | None:
    """The methods asked for, one by one."""
    if method is None or isinstance(method, Path | str):
        return None if method is None else [method]
    if not method:
        raise ValueError("method is an empty list: give at least one method")
    return list(method)


def _require_source(release: Release) -> None:
    if release.kind is ReleaseKind.indicators:
        raise InvalidDataError(
            [
                Problem(
                    "indicators-release",
                    f"release {release.name}",
                    "holds scores only, with no processes to calculate on. Load its source "
                    f"release {release.parent}, or score SKUs from it with "
                    "hw.score_many(release, table).",
                )
            ]
        )


def _release_method(release: Release, method: Path | str | None) -> Method:
    methods = {m.id: m for m in release.methods()}
    if method is None:
        if len(methods) == 1:
            return next(iter(methods.values()))
        held = ", ".join(methods) or "none"
        raise ValueError(
            f"release {release.name} holds {len(methods)} methods ({held}); name one with method="
        )
    if isinstance(method, str) and method in methods:
        return methods[method]
    folder = Path(method)
    if not folder.is_dir():
        held = ", ".join(methods) or "none"
        raise ValueError(
            f"release {release.name} has no method {str(method)!r}, and it is not a method "
            f"folder, one that holds method.yaml and factors.csv. The release holds: "
            f"{held}.{did_you_mean(str(method), methods)}"
        )
    found = read_method(folder)
    if found.flow_list != release.info.flow_list:
        raise InvalidDataError(
            [
                Problem(
                    "flow-list-mismatch",
                    f"method {found.id}",
                    f"is mapped to flow list {found.flow_list!r}, but release {release.name} "
                    f"uses {release.info.flow_list!r}",
                )
            ]
        )
    return found


def _assemble_folder(folder: ProcessFolder, method: Method) -> Assembled:
    validate(folder).raise_if_errors()
    require_global_factors(method)
    catalog = build_catalog(folder)
    processes = tuple(sorted(folder.processes, key=lambda p: p.id))
    block = assemble(processes, catalog, {p.id: i for i, p in enumerate(processes)})
    uncovered = tuple(
        NotCharacterised(flow_id, _flow_name(catalog, flow_id), process_ids)
        for flow_id, process_ids in flows_without_factor(processes, catalog, method).items()
    )
    return Assembled(block, folder, uncovered)


def _closest(process_id: str, ids: Iterable[str]) -> str | None:
    close = difflib.get_close_matches(process_id, list(ids), n=1, cutoff=0.6)
    return close[0] if close else None


def _flow_name(catalog: Catalog, flow_id: str) -> str:
    flow = catalog.flow(flow_id)
    return flow.name if flow is not None else flow_id
