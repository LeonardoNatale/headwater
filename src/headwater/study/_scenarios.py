"""Reading the scenarios of a study: named sets of changes to the lines and parameters of the
databases its products reach, and to the background.

Example: the study `tshirt-footprint` reaches the databases `tshirt` and `dyes`. Its scenario
`cleaner-dye` sets line e03 of `dyes:reactive-dye` from 4 to 3.5: a line of a database the
T-shirt uses, so it changes every product that buys the dye.
"""

from collections.abc import Collection, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

from pydantic import ValidationError

from headwater.model import (
    Exchange,
    Formula,
    FormulaError,
    InvalidDataError,
    LineChange,
    ParameterChange,
    ParameterSet,
    Problem,
    Process,
    Role,
    Scenario,
    did_you_mean,
)
from headwater.study._files import number_or_formula, problems_from_validation, read_yaml, relative
from headwater.study.folders import DatabaseFolder, in_used

FOLDER = "scenarios"
TOP_KEYS = ("description", "changes")
LINE_KEYS = ("process", "line", "amount", "unit", "supplier")
PARAMETER_KEYS = ("parameter", "process", "database", "value")


def read_scenarios(
    root: Path,
    databases: Mapping[str, DatabaseFolder],
    siblings: Collection[str],
    problems: list[Problem],
) -> tuple[Scenario, ...]:
    """Every scenario of the study in the folder `root`, by name.

    `databases` are the databases the study's products reach, by name, and `siblings` the names
    of the database folders next to the study. A change to a process of a database is checked
    here: its line, its sign and its formula, with the parameters as the scenario has them. A
    change to a process of the background can only be checked against that process, so
    validation does it.
    """
    folder = root / FOLDER
    if not folder.is_dir():
        return ()
    reader = _Reader(databases, siblings, problems)
    scenarios: list[Scenario] = []
    for path in sorted(folder.iterdir()):
        if path.name.startswith("."):
            continue
        if path.suffix == ".yaml" and path.is_file():
            reader.name, reader.location = path.stem, relative(path, root)
            data = read_yaml(path, root, problems)
            scenario = reader.read(data) if data is not None else None
            if scenario is not None:
                scenarios.append(scenario)
            continue
        rel = relative(path, root)
        problems.append(
            Problem(
                "unknown-file",
                rel,
                "is not a scenario file. A scenario is a .yaml file, named like the scenario: "
                "scenarios/clean-grid.yaml.",
                location=rel,
            )
        )
    return tuple(scenarios)


def scenarios_of(
    given: Mapping[str, Mapping[str, Any]],
    databases: Mapping[str, DatabaseFolder],
    siblings: Collection[str],
) -> tuple[Scenario, ...]:
    """The scenarios of a study in memory, each given as its file says it:
    `{"cleaner-dye": {"changes": [{"process": "dyes:reactive-dye", "line": "e03",
    "amount": 3.5}]}}`. Raises InvalidDataError naming every problem."""
    problems: list[Problem] = []
    reader = _Reader(databases, siblings, problems)
    found: list[Scenario] = []
    for name, data in given.items():
        reader.name, reader.location = name, f"scenarios/{name}.yaml"
        if not isinstance(data, Mapping):  # pyright: ignore[reportUnnecessaryIsInstance]
            reader.add("invalid-field", "scenario", "must be a mapping with `changes`")
            continue
        scenario = reader.read({str(k): v for k, v in data.items()})
        if scenario is not None:
            found.append(scenario)
    if problems:
        raise InvalidDataError(problems)
    return tuple(found)


class _Reader:
    def __init__(
        self,
        databases: Mapping[str, DatabaseFolder],
        siblings: Collection[str],
        problems: list[Problem],
    ) -> None:
        self.databases = databases
        self.siblings = frozenset(siblings)
        self.processes: dict[str, Process] = {
            p.id: p for d in databases.values() for p in d.processes
        }
        self.owners = {p.id: name for name, d in databases.items() for p in d.processes}
        self.override_sources = {
            label: in_used(name, source)
            for name, d in databases.items()
            for label, source in d.override_sources
        }
        self.problems = problems
        self.name = ""
        self.location = ""

    def add(self, code: str, item: str, message: str) -> None:
        self.problems.append(
            Problem(code, f"scenario {self.name}: {item}", message, location=self.location)
        )

    def add_record_problems(self, error: ValidationError, item: str) -> None:
        for problem in problems_from_validation(error, item=f"scenario {self.name}: {item}"):
            if not problem.item.startswith(f"scenario {self.name}"):
                problem = replace(problem, item=f"scenario {self.name}: {problem.item}")
            self.problems.append(replace(problem, location=self.location))

    # --- one scenario --------------------------------------------------------------------

    def read(self, data: dict[str, Any]) -> Scenario | None:
        before = len(self.problems)
        description = data.get("description")
        if description is not None and not isinstance(description, str):
            self.add(
                "invalid-field",
                "description",
                f"must be text, but is {type(description).__name__}",  # pyright: ignore[reportUnknownArgumentType]
            )
            description = None
        changes = self._changes(data)
        if changes is None or len(self.problems) > before:
            return None
        built = self._build(changes)
        if built is None:
            return None
        try:
            return Scenario(name=self.name, description=description, changes=tuple(built))
        except ValidationError as error:
            for problem in problems_from_validation(error, prefix=f"scenario {self.name}"):
                self.problems.append(replace(problem, location=self.location))
            return None

    def _changes(self, data: dict[str, Any]) -> list[tuple[int, dict[str, Any]]] | None:
        """The changes of the file, numbered from 1, with their keys checked."""
        unknown = [k for k in data if k not in TOP_KEYS]
        for key in unknown:
            self.add(
                "invalid-field",
                key,
                f"unknown key {key!r}.{did_you_mean(key, TOP_KEYS)} Keys: {', '.join(TOP_KEYS)}",
            )
        raw = data.get("changes")
        if raw is None:
            if not unknown:
                self.add(
                    "invalid-field", "changes", "is missing. List the changes of the scenario."
                )
            return None
        if not isinstance(raw, list):
            self.add("invalid-field", "changes", "must be a list of changes")
            return None
        if not raw:
            self.add("invalid-field", "changes", "needs at least one change")
            return None
        found: list[tuple[int, dict[str, Any]]] = []
        for number, change in enumerate(raw, start=1):  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
            item = f"change {number}"
            if not isinstance(change, dict):
                self.add(
                    "invalid-field", item, "must be a mapping, such as {parameter: x, value: 1}"
                )
                continue
            entry: dict[str, Any] = {str(k): v for k, v in cast(dict[Any, Any], change).items()}
            if "parameter" in entry:
                allowed = PARAMETER_KEYS
            elif "line" in entry or "process" in entry:
                allowed = LINE_KEYS
            else:
                self.add(
                    "invalid-field",
                    item,
                    "needs `line` with `process` to change a line, or `parameter` to change a "
                    "parameter",
                )
                continue
            if allowed is LINE_KEYS and not ("process" in entry and "line" in entry):
                self.add("invalid-field", item, "needs both `process` and `line`")
                continue
            bad = [k for k in entry if k not in allowed]
            for key in bad:
                self.add(
                    "invalid-field",
                    item,
                    f"unknown key {key!r}.{did_you_mean(key, allowed)} Keys: {', '.join(allowed)}",
                )
            if not bad:
                found.append((number, entry))
        return found

    # --- the changes -----------------------------------------------------------------------

    def _build(
        self, changes: list[tuple[int, dict[str, Any]]]
    ) -> list[LineChange | ParameterChange] | None:
        before = len(self.problems)
        parameter_changes = self._parameter_changes(changes)
        parameters: dict[str, ParameterSet] = {}
        for name, database in self.databases.items():
            values = ParameterChange.as_values(
                c for _, c in parameter_changes if c.database == name
            )
            try:
                parameters[name] = database.parameters.with_changes(values)
            except InvalidDataError as error:
                for problem in error.problems:
                    self.add(problem.code, problem.item, problem.message)
        lines = self._line_changes(changes, parameters)
        if parameter_changes and len(self.problems) == before:
            self._recheck_amounts(parameters, {c.label for _, c in lines})
        if len(self.problems) > before:
            return None
        numbered: list[tuple[int, LineChange | ParameterChange]] = [*lines, *parameter_changes]
        return [change for _, change in sorted(numbered, key=lambda pair: pair[0])]

    def _parameter_changes(
        self, changes: list[tuple[int, dict[str, Any]]]
    ) -> list[tuple[int, ParameterChange]]:
        found: list[tuple[int, ParameterChange]] = []
        seen: set[tuple[str, str]] = set()
        for number, entry in changes:
            if "parameter" not in entry:
                continue
            scope = entry.get("process")
            name = entry["parameter"]
            parameter_id = f"{scope}/{name}" if scope else str(name)
            database = self._holder(parameter_id, entry.get("database"))
            if database is None:
                continue
            if (database, parameter_id) in seen:
                self.add(
                    "scenario-duplicate-change", f"parameter {parameter_id}", "is changed twice"
                )
                continue
            seen.add((database, parameter_id))
            try:
                value, formula = number_or_formula(entry.get("value"), "value")
            except ValueError as exc:
                self.add("invalid-field", f"parameter {parameter_id}", str(exc))
                continue
            try:
                change = ParameterChange(
                    parameter=parameter_id, database=database, value=value, formula=formula
                )
            except ValidationError as error:
                self.add_record_problems(error, f"parameter {parameter_id}")
                continue
            found.append((number, change))
        return found

    def _holder(self, parameter_id: str, given: object) -> str | None:
        """The database whose parameter `parameter_id` is: the one `database:` names, else the
        one database the study reaches that has it."""
        item = f"parameter {parameter_id}"
        holders = [
            name
            for name, d in self.databases.items()
            if any(p.id == parameter_id for p in d.parameters)
        ]
        if given is not None:
            name = str(given)
            if name not in self.databases:
                self.add(
                    "scenario-not-reached",
                    item,
                    f"names the database {name!r}, which no product of the study reaches."
                    f"{did_you_mean(name, self.databases)}",
                )
                return None
            if name not in holders:
                self.add("unknown-parameter", item, f"the database {name} has no such parameter.")
                return None
            return name
        if len(holders) == 1:
            return holders[0]
        if holders:
            self.add(
                "scenario-ambiguous-parameter",
                item,
                f"is a parameter of the databases {', '.join(holders)}: say which with `database:`",
            )
            return None
        known = [p.id for d in self.databases.values() for p in d.parameters]
        self.add(
            "unknown-parameter",
            item,
            f"no database the study reaches has this parameter.{did_you_mean(parameter_id, known)}",
        )
        return None

    def _line_changes(
        self, changes: list[tuple[int, dict[str, Any]]], parameters: Mapping[str, ParameterSet]
    ) -> list[tuple[int, LineChange]]:
        found: list[tuple[int, LineChange]] = []
        seen: set[str] = set()
        for number, entry in changes:
            if "parameter" in entry:
                continue
            label = f"{entry.get('process')} line {entry.get('line')}"
            if label in seen:
                self.add("scenario-duplicate-change", label, "is changed twice")
                continue
            seen.add(label)
            change = self._line_change(entry, label, parameters)
            if change is not None and self._fits(change):
                found.append((number, change))
        return found

    def _line_change(
        self, entry: dict[str, Any], label: str, parameters: Mapping[str, ParameterSet]
    ) -> LineChange | None:
        amount: float | None = None
        formula: str | None = None
        if "amount" in entry:
            try:
                amount, formula = number_or_formula(entry["amount"], "amount")
            except ValueError as exc:
                self.add("invalid-field", label, str(exc))
                return None
            if formula is not None:
                process_id = str(entry.get("process"))
                owner = self.owners.get(process_id)
                if owner is None and len(self.databases) == 1:
                    owner = next(iter(self.databases))  # a line of the background
                if owner is None or owner not in parameters:
                    self.add(
                        "scenario-formula-scope",
                        label,
                        f"amount {formula!r} is a formula, and no one database's parameters can "
                        "work it out: the line is of the background, and the study reaches "
                        f"{len(self.databases)} databases. Write the number.",
                    )
                    return None
                amount = self._worked_out(formula, process_id, label, parameters[owner])
                if amount is None:
                    return None
        try:
            return LineChange.model_validate(
                {
                    "process_id": entry.get("process"),
                    "line_id": entry.get("line"),
                    "amount": amount,
                    "formula": formula,
                    "unit": entry.get("unit"),
                    "supplier_id": entry.get("supplier"),
                }
            )
        except ValidationError as error:
            self.add_record_problems(error, label)
            return None

    def _worked_out(
        self, text: str, scope: str, label: str, parameters: ParameterSet
    ) -> float | None:
        try:
            formula = Formula.parse(text)
        except FormulaError as exc:
            self.add("bad-formula", label, f"amount: {exc}")
            return None
        for name in formula.names:
            unknown = parameters.unknown_name(name, scope)
            if unknown is not None:
                self.add(
                    "unknown-parameter",
                    label,
                    f"amount {text!r} uses {name!r}, which is not a parameter it can see.{unknown}",
                )
                return None
        try:
            return parameters.evaluate(text, scope)
        except FormulaError as exc:
            self.add("bad-formula", label, f"amount: {exc}")
            return None

    def _fits(self, change: LineChange) -> bool:
        """Checks of a change that need only the databases: its line, the overrides, the sign.
        A change to a database the study does not reach is refused."""
        label = change.label
        if label in self.override_sources:
            self.add(
                "scenario-overlaps-override",
                label,
                f"is also changed by the override at {self.override_sources[label]}. "
                "Change it in one place.",
            )
            return False
        process = self.processes.get(change.process_id)
        if process is None:
            prefix = change.process_id.split(":", 1)[0]
            if prefix in self.databases:
                known = [p.id for p in self.databases[prefix].processes]
                self.add(
                    "scenario-unknown-process",
                    label,
                    f"the database {prefix} has no process {change.process_id!r}."
                    f"{did_you_mean(change.process_id, known)}",
                )
                return False
            if prefix in self.siblings:
                self.add(
                    "scenario-not-reached",
                    label,
                    f"changes a line of the database {prefix}, which no product of the study "
                    "reaches. A scenario changes the databases its products are in or use, "
                    "and the background.",
                )
                return False
            return True  # a background process: validation checks it
        by_id = {e.line_id: e for e in process.exchanges}
        line = by_id.get(change.line_id)
        if line is None:
            self.add(
                "scenario-unknown-line",
                label,
                f"{change.process_id} has no line {change.line_id!r}."
                f"{did_you_mean(change.line_id, by_id)} Its lines: {', '.join(by_id)}",
            )
            return False
        if line.role is Role.reference:
            self.add(
                "scenario-reference-line",
                label,
                "is the reference line, which is what the process makes. A scenario changes "
                "the other lines.",
            )
            return False
        try:
            change.apply_to(line)
        except ValidationError as error:
            self.add_record_problems(error, label)
            return False
        return True

    def _recheck_amounts(self, parameters: Mapping[str, ParameterSet], changed: set[str]) -> None:
        """Work out every formula amount again with the parameters of the scenario. A scenario
        that makes an amount negative, or divides by zero, is refused here, not at calculation."""
        for name, database in self.databases.items():
            if name not in parameters:
                continue
            exchanges: list[Exchange] = [e for p in database.processes for e in p.exchanges]
            exchanges.extend(e for o in database.overrides for e in o.lines)
            for exchange in exchanges:
                if exchange.formula is None or exchange.label in changed:
                    continue
                try:
                    value = parameters[name].evaluate(exchange.formula, exchange.process_id)
                    Exchange.model_validate({**exchange.model_dump(), "amount": value})
                except FormulaError as exc:
                    self.add("bad-formula", exchange.label, f"amount: {exc}")
                except ValidationError as error:
                    self.add_record_problems(error, exchange.label)
