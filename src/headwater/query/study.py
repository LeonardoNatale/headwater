"""A study: the question asked of databases. It names products with amounts, the methods, and
the scenarios, and holds no processes (D46).

Example: `hw.open("tests/fixtures/toy/tshirt-dyed/tshirt-footprint").calc(bg)` scores one
T-shirt at 1.242646, and 1.237646 in the scenario `cleaner-dye`, which changes a line of the
database `dyes` that the T-shirt uses.
"""

import re
import shutil
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import yaml

from headwater.model import (
    InvalidDataError,
    Problem,
    Product,
    Report,
    Scenario,
    Severity,
    did_you_mean,
)
from headwater.model import Study as StudyRecord
from headwater.query._git import git_state
from headwater.query._hash import records_of, sha256_of
from headwater.query._internals import internals
from headwater.query.background import Background
from headwater.query.database import (
    Database,
    _fields,  # pyright: ignore[reportPrivateUsage]
    _set_ref,  # pyright: ignore[reportPrivateUsage]
    read_database,
)
from headwater.query.result import (
    MethodRef,
    NotCharacterised,
    Provenance,
    Result,
    StudyRef,
    counted,
)
from headwater.study import StudyFolder, in_used, read_study, study_in_memory
from headwater.study._files import problems_from_validation
from headwater.study._tables import EXCHANGES, PROCESSES
from headwater.study._write import number, yaml_value
from headwater.study.validate import flows_without_factor

STUDY_YAML = "study.yaml"
_EPSILON = 1e-12


class Study:
    """A study, read from its folder with `hw.open`, or made in memory:

        hw.Study(products={"tshirt:tshirt": 1}, methods=["toy-climate"], databases="toy/dyed")

    `products` maps the id of a process to the amount of what it makes. `databases` is the folder
    that holds the database folders of the products, as the folder that holds a saved study; a
    product whose prefix names none of them is a product of the background, and so is every
    product when `databases` is None. `scenarios` maps a scenario's name to what its file says,
    `{"changes": [{"process": "dyes:reactive-dye", "line": "e03", "amount": 3.5}]}`. `cut_off`
    is the largest share of inputs the databases may leave out, `{"criteria": ["mass"],
    "max_cumulative": 0.03}`; None checks no limit. `save` writes the study next to its
    databases, with its goal and scope.
    """

    def __init__(
        self,
        products: Mapping[str, float],
        methods: Sequence[str],
        *,
        databases: Path | str | None = None,
        scenarios: Mapping[str, Mapping[str, Any]] | None = None,
        cut_off: Mapping[str, Any] | None = None,
        background: str | None = None,
        name: str | None = None,
    ) -> None:
        self.folder = study_in_memory(
            products,
            methods,
            databases=databases,
            scenarios=scenarios,
            cut_off=cut_off,
            background=background,
            name=name,
        )
        self._databases = Path(databases) if databases is not None else None
        self._given = {k: dict(v) for k, v in (scenarios or {}).items()}

    @classmethod
    def _of(cls, folder: StudyFolder) -> "Study":
        """The study read from its folder."""
        study = cls.__new__(cls)
        study.folder = folder
        study._databases = folder.path.parent if folder.path is not None else None
        study._given = {}
        return study

    @property
    def name(self) -> str | None:
        return self.folder.name

    @property
    def products(self) -> tuple[Product, ...]:
        return self.folder.products

    def __repr__(self) -> str:
        """Example: `Study(tshirt-footprint: 1 product, methods toy-climate, scenarios
        cleaner-dye)`. A study in memory is `not saved`."""
        folder = self.folder
        parts = [
            counted(len(folder.products), "product", "products"),
            f"methods {' '.join(folder.methods)}",
        ]
        if folder.scenarios:
            parts.append(f"scenarios {' '.join(s.name for s in folder.scenarios)}")
        if folder.path is None:
            parts.append("not saved")
        return f"Study({folder.name or 'in memory'}: {', '.join(parts)})"

    # --- calculate -----------------------------------------------------------------------------

    def calc(
        self,
        background: Background,
        *,
        indicator: str | None = None,
        scenario: str | None = None,
    ) -> tuple[Result, ...]:
        """Score each product of the study, its amount of it, on `background`: one `Result` per
        product, in the order of `products`. Each is what `Database.calc` gives for that
        process, for its amount: the amount is scored in the system, so `contributions` adds up
        to the score.

        With `scenario`, the name of one of the study's scenarios, its changes are made first: a
        change to a line of a database a product reaches is made for every product that reaches
        it, and a change to a background line changes that process for everything. Each
        result's provenance names the study, saved or not, and its input hash, which is the
        products and amounts, the methods, the scenario, the study's own no_factor rows and the
        records of the databases the study reaches.

        Raises what `validate` reports as errors, ValueError for a scenario the study does not
        have, and a calc error naming the processes when a matrix cannot be solved.
        """
        chosen = self._scenario(scenario)
        self._study_problems(background).raise_if_errors()
        self._refuse_background_products(chosen)
        ref = self._ref()
        results: list[Result] = []
        for product in self.folder.products:
            hashed = _hasher(self.folder, background, product.process, chosen)
            database = self.folder.database_of(product.process)
            if database is None:
                results.append(
                    _background_product(background, product, indicator, chosen, hashed, ref)
                )
                continue
            results.append(
                Database(database)._calc(  # pyright: ignore[reportPrivateUsage]
                    background,
                    product.process,
                    indicator,
                    None if chosen is None else self.folder.scenario_for(chosen, database),
                    product.amount,
                    self.folder.no_factor,
                    hashed,
                    ref,
                )
            )
        return tuple(results)

    def scores(
        self,
        background: Background,
        *,
        indicators: Sequence[str] | None = None,
        scenario: str | None = None,
    ) -> dict[str, dict[str, float]]:
        """The score of each product, its amount of it, for every indicator of the method, or
        for `indicators`, by process id then indicator: `{"tshirt:tshirt": {"climate-change":
        2.485292}}`. The numbers of `calc`, worked out with one assembly per product."""
        from headwater.query.database import scores_with_residual

        chosen = self._scenario(scenario)
        self._study_problems(background).raise_if_errors()
        self._refuse_background_products(chosen)
        found: dict[str, dict[str, float]] = {}
        for product in self.folder.products:
            database = self.folder.database_of(product.process)
            if database is None:
                found[product.process] = {
                    i.id: product.amount * background.score(product.process, indicator=i.id)
                    for i in background.indicators
                    if indicators is None or i.id in indicators
                }
                continue
            if chosen is not None:  # a scenario may change the background: calc solves it
                wanted = indicators or [i.id for i in background.indicators]
                one = Database(database)
                found[product.process] = {
                    i: one._calc(  # pyright: ignore[reportPrivateUsage]
                        background,
                        product.process,
                        i,
                        self.folder.scenario_for(chosen, database),
                        product.amount,
                        self.folder.no_factor,
                    ).score
                    for i in wanted
                }
                continue
            found[product.process] = scores_with_residual(
                Database(database),
                background,
                product.process,
                indicators,
                amount=product.amount,
                acknowledged=self.folder.no_factor,
            )[0]
        return found

    # --- check ---------------------------------------------------------------------------------

    def validate(self, background: Background) -> Report:
        """Check the study against `background` as `calc` does, without calculating: its own
        rules (its products, its methods, the cut-off limit of the databases it reaches, its
        no_factor rows) and every database its products reach, with its scenarios. A problem in
        a database is at its place there, as `../tshirt/exchanges.csv`."""
        problems = list(self._study_problems(background).problems)
        needed: set[str] = set()
        method = background.method
        for database in self.folder.databases:
            name = database.database.name
            try:
                internals(background).require_pin(database.database.background, name)
            except InvalidDataError as error:
                problems.extend(error.problems)
            scenarios = [self.folder.scenario_for(s, database) for s in self.folder.scenarios]
            report, catalog, uncovered = Database(database)._checked(  # pyright: ignore[reportPrivateUsage]
                background, scenarios, self.folder.no_factor
            )
            for p in report.problems:
                at = p.location
                if not p.item.startswith("scenario "):
                    at = in_used(name, p.location) if p.location is not None else None
                problems.append(replace(p, location=at))
            needed |= uncovered.keys()
            if catalog is not None:
                for used in database.used:
                    needed |= flows_without_factor(used.processes, catalog, method).keys()
        for row in self.folder.no_factor:
            why = None
            if row.method not in method.ids:
                why = f"it is for method {row.method}, which the study is not scored with"
            elif row.flow_id not in needed:
                why = "no database the study reaches emits or takes the flow without a factor"
            if why is not None:
                problems.append(
                    Problem(
                        "no-factor-not-needed",
                        f"flow {row.flow_id}",
                        f"row not needed: {why}. Remove the row.",
                        severity=Severity.warning,
                        location="no_factor.csv",
                    )
                )
        return Report(tuple(dict.fromkeys(problems)))

    def _study_problems(self, background: Background) -> Report:
        """The rules of the study itself: its products are found, its methods are those
        `background` is loaded with, and no database it reaches allows more cut-offs."""
        problems: list[Problem] = []
        inner = internals(background)
        folder = self.folder
        for product in folder.products:
            if folder.database_of(product.process) is None and product.process not in inner.index:
                prefix = product.process.split(":", 1)[0]
                problems.append(
                    Problem(
                        "study-unknown-product",
                        f"product {product.process}",
                        f"is in no database {prefix} next to the study, and is not a process of "
                        f"the background either.{did_you_mean(product.process, inner.index)}",
                        location=STUDY_YAML if folder.path is not None else None,
                    )
                )
        if tuple(folder.methods) != background.method.ids:
            problems.append(
                Problem(
                    "method-mismatch",
                    f"study {folder.name or 'in memory'}",
                    f"asks for {', '.join(folder.methods)}, but the background is loaded with "
                    f"{', '.join(background.method.ids)}",
                )
            )
        if folder.background is not None and not folder.databases:
            try:
                inner.require_pin(folder.background, folder.name or "study")
            except InvalidDataError as error:
                problems.extend(error.problems)
        limit = folder.cut_off
        if limit is not None:
            for name, database in folder.reached.items():
                theirs = database.database.cut_off
                if theirs is not None and theirs.max_cumulative > limit.max_cumulative + _EPSILON:
                    problems.append(
                        Problem(
                            "study-cut-off-limit",
                            f"database {name}",
                            f"the database {name} allows {theirs.max_cumulative:g} of inputs to "
                            "be cut off, above "
                            f"the {limit.max_cumulative:g} the study allows (cut_off in "
                            "study.yaml). Raise the study's limit, or cut off less in the "
                            "database.",
                            location=in_used(name, "database.yaml"),
                        )
                    )
        return Report(tuple(problems))

    def _scenario(self, name: str | None) -> Scenario | None:
        if name is None:
            return None
        scenarios = {s.name: s for s in self.folder.scenarios}
        found = scenarios.get(name)
        if found is None:
            held = ", ".join(scenarios)
            raise ValueError(
                f"study {self.name} has no scenario {name!r}."
                + (
                    f"{did_you_mean(name, scenarios)} Its scenarios: {held}."
                    if scenarios
                    else " It has no scenarios: add scenarios/<name>.yaml."
                )
            )
        return found

    def _refuse_background_products(self, chosen: Scenario | None) -> None:
        """A product of the background is not scored yet in a scenario that changes a
        background line: that needs the background solved again. A scenario that changes only
        the lines and parameters of databases leaves it as it is."""
        if chosen is None or not self.folder.changes_background(chosen):
            return
        problems = [
            Problem(
                "study-scenario-background-product",
                f"product {product.process}",
                f"is a process of the background, and the scenario {chosen.name} changes "
                "background lines: a product of the background is not scored in such a "
                "scenario yet.",
            )
            for product in self.folder.products
            if self.folder.database_of(product.process) is None
        ]
        if problems:
            raise InvalidDataError(problems)

    def _ref(self) -> StudyRef:
        folder = self.folder
        if folder.path is None:
            return StudyRef(name=folder.name, saved=False, commit=None, changed=False)
        paths = [folder.path, *(d.path for d in folder.reached.values())]
        commit, changed = git_state(paths)
        return StudyRef(name=folder.name, saved=True, commit=commit, changed=changed)

    # --- save ----------------------------------------------------------------------------------

    def save(
        self,
        path: Path | str,
        *,
        goal: str,
        functional_unit: str,
        boundary: str,
        cut_off: Mapping[str, Any] | None = None,
        **optional: Any,
    ) -> "Study":
        """Write the study into the folder `path`, which must not exist or be empty, next to its
        databases: `path`'s parent is the folder `databases` names. The goal and scope ISO 14044
        asks for are given here: `goal`, `functional_unit`, `boundary`, and `cut_off` when the
        study has none; `optional` are the optional fields of study.yaml, such as `audience`.
        The files are written to a copy first and read back as `hw.open` reads them: nothing is
        written when they do not read. Returns the study as saved."""
        target = Path(path)
        if self._databases is not None and target.resolve().parent != self._databases.resolve():
            raise ValueError(
                f"a study is saved next to its databases, so it finds them: {path} is not in "
                f"{self._databases}. Save it as {self._databases / target.name}."
            )
        if target.exists() and (not target.is_dir() or any(target.iterdir())):
            raise ValueError(f"{target} already holds files: a study is saved to a new folder")
        limit = cut_off if cut_off is not None else self._cut_off()
        if limit is None:
            raise TypeError("save() needs cut_off: the study has none")
        data: dict[str, Any] = {
            "name": self.folder.name or target.name,
            "goal": goal,
            "functional_unit": functional_unit,
            "products": [p.model_dump(mode="json") for p in self.folder.products],
            "boundary": boundary,
            "methods": list(self.folder.methods),
            "cut_off": limit,
        }
        if self.folder.background is not None:
            data["background"] = str(self.folder.background)
        data.update(optional)
        try:
            StudyRecord.model_validate(data)
        except Exception as error:  # a field that does not read: nothing is written
            from pydantic import ValidationError

            if isinstance(error, ValidationError):
                raise InvalidDataError(
                    problems_from_validation(error, prefix=STUDY_YAML, model=StudyRecord)
                ) from None
            raise
        target.parent.mkdir(parents=True, exist_ok=True)
        holder = Path(tempfile.mkdtemp(prefix=f".tmp-{target.name}-", dir=target.parent))
        temp = holder / target.name
        try:
            temp.mkdir()
            (temp / STUDY_YAML).write_text(_study_yaml(data), encoding="utf-8")
            self._write_scenarios(temp)
            if self.folder.no_factor and self.folder.path is not None:
                shutil.copy(self.folder.path / "no_factor.csv", temp / "no_factor.csv")
            read_study(temp, databases=target.parent)  # as `hw.open` will read it
            if target.is_dir():
                target.rmdir()
            temp.rename(target)
        finally:
            shutil.rmtree(holder, ignore_errors=True)
        return Study._of(read_study(target))

    def _cut_off(self) -> dict[str, Any] | None:
        limit = self.folder.cut_off
        return None if limit is None else limit.model_dump(mode="json")

    def _write_scenarios(self, folder: Path) -> None:
        if self.folder.path is not None and (self.folder.path / "scenarios").is_dir():
            shutil.copytree(self.folder.path / "scenarios", folder / "scenarios")
            return
        if not self._given:
            return
        (folder / "scenarios").mkdir()
        for name, data in self._given.items():
            text = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100)
            (folder / "scenarios" / f"{name}.yaml").write_text(text, encoding="utf-8")


def open(path: Path | str) -> "Database | Study":
    """Read a database folder, which holds database.yaml, or a study folder, which holds
    study.yaml. A folder of the format before D46, with study.yaml and processes, is read as a
    database, which says what it needs. Raises InvalidDataError."""
    folder = Path(path)
    study = folder / STUDY_YAML
    if study.is_file() and not (folder / "database.yaml").is_file() and not _old_format(folder):
        return Study._of(read_study(folder))  # pyright: ignore[reportPrivateUsage]
    return Database(read_database(folder))


def _old_format(folder: Path) -> bool:
    """True for a study folder of the format before D46: study.yaml with no products, and
    processes, as processes/ or as the two tables."""
    if not any((folder / name).exists() for name in ("processes", PROCESSES, EXCHANGES)):
        return False
    try:
        data = yaml.safe_load((folder / STUDY_YAML).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError, UnicodeDecodeError):
        return False
    return isinstance(data, dict) and "products" not in data


def _study_yaml(data: Mapping[str, Any]) -> str:
    lines: list[str] = []
    for key, value in data.items():
        if key == "products":
            lines.append("products:\n")
            lines += [
                f"  - {{process: {p['process']}, amount: {number(p['amount'])}}}\n"
                if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]*", p["process"])
                else f"  - {yaml_value(p)}\n"
                for p in value
            ]
        else:
            lines.append(f"{key}: {yaml_value(value)}\n")
    return "".join(lines)


def _hasher(
    folder: StudyFolder, background: Background, process_id: str, scenario: Scenario | None
):  # -> Callable[[str | None], str]
    def hashed(nw_set: str | None) -> str:
        return study_input_hash(folder, background, process_id, scenario, nw_set)

    return hashed


def study_input_hash(
    folder: StudyFolder,
    background: Background,
    process_id: str,
    scenario: Scenario | None,
    nw_set: str | None = None,
) -> str:
    """sha256 of what a product of a study is scored from: the study's products and amounts,
    methods and own no_factor rows, the product scored, the records of every database the study
    reaches, the scenario, and the background's fingerprint. The study's name and text are not
    part of it, so a study in memory and the same study saved hash alike."""
    inputs: dict[str, Any] = {
        "study": {
            "products": [p.model_dump(mode="json") for p in folder.products],
            "methods": list(folder.methods),
            "no_factor": [
                r.model_dump(mode="json")
                for r in sorted(folder.no_factor, key=lambda r: (r.flow_id, r.method))
            ],
        },
        "product": process_id,
        "databases": [
            {"database": _fields(d), **records_of(d)} for _, d in sorted(folder.reached.items())
        ],
        "background": internals(background).fingerprint,
    }
    if scenario is not None:
        inputs["scenario"] = scenario.model_dump(mode="json")
    if nw_set is not None:
        inputs["set"] = nw_set
    return sha256_of(inputs)


def _background_product(
    background: Background,
    product: Product,
    indicator: str | None,
    chosen: Scenario | None,
    hashed: Any,
    ref: StudyRef,
) -> Result:
    """A product of the background, its amount of it, as a result."""
    inner = internals(background)
    indicator = inner.indicator(indicator)
    row = inner.row(product.process)
    system = inner.supply(indicator, row)
    system = replace(system, f=system.f * product.amount, s=system.s * product.amount)
    solved = inner.solved
    method = background.method
    defines = inner.method_of(indicator)
    hashes = inner.method_hashes
    nw_set = _set_ref(background, indicator)
    provenance = Provenance(
        input_hash=hashed(nw_set.hash if nw_set is not None else None),
        engine_version=_engine_version(),
        method=method.id,
        method_version=method.version,
        method_hash=inner.method_hash,
        releases=inner.releases,
        indicator=indicator,
        indicator_method=MethodRef(defines.id, defines.version, hashes[defines.id]),
        methods=tuple(MethodRef(m.id, m.version, hashes[m.id]) for m in background.methods),
        pin=str(background_pin) if (background_pin := _pin_of(ref, background)) else "",
        solver=solved.factorization.settings,
        condition=solved.factorization.condition,
        database_condition=1.0,
        path=(),
        residual=solved.x[indicator].residual,
        exact_zeros=int(solved.x[indicator].exact_zeros.sum()),
        cancellation=_one_sign(product.amount * inner.score(indicator, row)),
        scenario=chosen.name if chosen is not None else None,
        nw_set=nw_set,
        study=ref,
    )
    unit = next(i.unit for i in background.indicators if i.id == indicator)
    score = product.amount * inner.score(indicator, row)
    reached: tuple[NotCharacterised, ...] = background.not_characterised
    return Result(
        score=score,
        forward_score=float(system.h @ system.s),
        indicator=indicator,
        unit=unit,
        provenance=provenance,
        system=system,
        uncovered=reached,
        locations=inner.locations,
        amount=product.amount,
    )


def _engine_version() -> str:
    import headwater

    return headwater.__version__


def _one_sign(score: float):  # -> Cancellation
    """The parts of a stored score, which keeps no terms: all of it on one side."""
    from headwater.calc.cancellation import Cancellation

    return Cancellation(positive=max(score, 0.0), negative=min(score, 0.0))


def _pin_of(ref: StudyRef, background: Background) -> str | None:
    """The release a background product is scored on, `name@hash`, or None for a folder."""
    release = background.release
    return f"{release.name}@{release.hash[:12]}" if release is not None else None
