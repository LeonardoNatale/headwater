"""Reading `parameters.yaml`: named values and formulas of a database."""

from collections.abc import Collection
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

from pydantic import ValidationError

from headwater.model import InvalidDataError, Parameter, ParameterSet, Problem, did_you_mean
from headwater.study._files import number_or_formula, problems_from_validation, read_yaml
from headwater.study._uncertainty import COLUMNS as DISTRIBUTION_COLUMNS
from headwater.study._uncertainty import read_uncertainty

FILE = "parameters.yaml"
SECTIONS = ("database", "processes")
ENTRY_KEYS = ("value", "formula", "unit", "comment", *DISTRIBUTION_COLUMNS)


def read_parameters(
    root: Path, process_ids: Collection[str], problems: list[Problem]
) -> ParameterSet | None:
    """The parameters of a database, or an empty set when there is no file.

    `process_ids` are the processes a parameter may be scoped to. Returns None after adding
    problems, so the lines that use parameters are not reported again for the same mistake.
    """
    path = root / FILE
    if not path.is_file():
        return ParameterSet()
    before = len(problems)
    data = read_yaml(path, root, problems)
    if data is None:
        return None
    parameters: list[Parameter] = []
    for key, section in data.items():
        if key not in SECTIONS:
            problems.append(
                Problem(
                    "invalid-field",
                    f"{FILE}: {key}",
                    "is not a section. Parameters of the database go under `database:`, and "
                    "parameters of one process under `processes:` and then the process id."
                    f"{did_you_mean(key, SECTIONS)}",
                    location=FILE,
                )
            )
        elif key == "database":
            parameters.extend(_section(section, None, problems))
        else:
            parameters.extend(_processes(section, process_ids, problems))
    if len(problems) > before:
        return None
    try:
        return ParameterSet(parameters)
    except InvalidDataError as error:
        problems.extend(_located(p) for p in error.problems)
        return None


def _located(problem: Problem) -> Problem:
    return replace(problem, location=problem.location or FILE)


def _mapping(raw: object, item: str, problems: list[Problem]) -> dict[str, Any] | None:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        problems.append(
            Problem(
                "invalid-field",
                item,
                "must be a mapping of parameter names to values or formulas",
                location=FILE,
            )
        )
        return None
    return {str(k): v for k, v in cast(dict[Any, Any], raw).items()}


def _processes(
    raw: object, process_ids: Collection[str], problems: list[Problem]
) -> list[Parameter]:
    found: list[Parameter] = []
    by_process = _mapping(raw, f"{FILE}: processes", problems)
    for process_id, section in (by_process or {}).items():
        item = f"{FILE}: processes: {process_id}"
        if process_id not in process_ids:
            problems.append(
                Problem(
                    "unknown-scope",
                    item,
                    "is not a process of this database, or a background process it overrides."
                    f"{did_you_mean(process_id, process_ids)}",
                    location=FILE,
                )
            )
            continue
        found.extend(_section(section, process_id, problems))
    return found


def _section(raw: object, scope: str | None, problems: list[Problem]) -> list[Parameter]:
    item = f"{FILE}: database" if scope is None else f"{FILE}: processes: {scope}"
    entries = _mapping(raw, item, problems)
    found: list[Parameter] = []
    for name, entry in (entries or {}).items():
        parameter = _parameter(name, entry, scope, problems)
        if parameter is not None:
            found.append(parameter)
    return found


def _with_distribution(
    fields: dict[str, Any], cells: dict[str, Any], item: str, problems: list[Problem]
) -> bool:
    """Put the distribution written in `cells` around the parameter's value into `fields`.
    False when it cannot be: a formula, or a mistake in the cells, each added to `problems`."""
    if "formula" in fields:
        problems.append(
            Problem(
                "formula-with-distribution",
                item,
                f"is the formula {fields['formula']!r}, so its spread comes from the parameters "
                "it uses: give the distribution to them",
                location=FILE,
            )
        )
        return False
    value = fields.get("value")
    if not isinstance(value, int | float) or isinstance(value, bool):
        return True  # the value itself is refused below, with its own message
    before = len(problems)
    found = read_uncertainty(
        cells,
        float(value),
        what="value",
        item=item,
        location=FILE,
        number=_number,
        problems=problems,
    )
    if len(problems) > before:
        return False
    fields["uncertainty"] = found
    return True


def _number(raw: object, what: str) -> float:
    number, _ = number_or_formula(raw, what)
    if number is None:
        raise ValueError(f"{what} {raw!r} is not a number")
    return number


def _parameter(
    name: str, raw: object, scope: str | None, problems: list[Problem]
) -> Parameter | None:
    item = f"parameter {name}" if scope is None else f"parameter {scope}/{name}"
    fields: dict[str, Any] = {}
    if isinstance(raw, dict):
        entry: dict[str, Any] = {str(k): v for k, v in cast(dict[Any, Any], raw).items()}
        unknown = [k for k in entry if k not in ENTRY_KEYS]
        for key in unknown:
            problems.append(
                Problem(
                    "invalid-field",
                    item,
                    f"unknown key {key!r}.{did_you_mean(key, ENTRY_KEYS)} "
                    f"Keys: {', '.join(ENTRY_KEYS)}",
                    location=FILE,
                )
            )
        if unknown:
            return None
        fields = dict(entry)
        if "value" in fields and "formula" in fields:
            problems.append(
                Problem(
                    "invalid-field",
                    item,
                    "needs a value or a formula, and only one of them",
                    location=FILE,
                )
            )
            return None
        if isinstance(fields.get("value"), str) and "formula" not in fields:
            number, formula = number_or_formula(fields.pop("value"), "value")
            fields["value" if number is not None else "formula"] = (
                number if number is not None else formula
            )
        cells = {c: fields.pop(c) for c in DISTRIBUTION_COLUMNS if c in fields}
        if cells and not _with_distribution(fields, cells, item, problems):
            return None
    else:
        try:
            number, formula = number_or_formula(raw, "value")
        except ValueError as exc:
            problems.append(Problem("invalid-field", item, str(exc), location=FILE))
            return None
        fields = {"value": number} if number is not None else {"formula": formula}
    try:
        return Parameter.model_validate({**fields, "name": name, "scope": scope})
    except ValidationError as error:
        problems.extend(problems_from_validation(error, item=item, location=FILE, model=Parameter))
        return None
