"""Reading one line of a process: a row of exchanges.csv, or of an override of a background
process."""

import re
from dataclasses import dataclass
from typing import Literal

from pydantic import ValidationError

from headwater.model import (
    Exchange,
    Formula,
    FormulaError,
    ParameterSet,
    Problem,
    Role,
    Uncertainty,
    did_you_mean,
)
from headwater.study._files import (
    Row,
    blank_to_none,
    parse_number,
    problems_from_validation,
)
from headwater.study._uncertainty import COLUMNS as DISTRIBUTION_COLUMNS
from headwater.study._uncertainty import read_uncertainty

EXCHANGE_COLUMNS = ("line", "role", "flow", "amount", "unit", "supplier")
EXCHANGE_OPTIONAL = ("flow_name", "comment", *DISTRIBUTION_COLUMNS)

# Record field names that differ from the column a person sees in the file.
EXCHANGE_COLUMN = {"line_id": "line", "flow_id": "flow", "supplier_id": "supplier"}

_DECIMAL_COMMA = re.compile(r"[+-]?\d+,\d+")


@dataclass(frozen=True)
class Formulas:
    """What an amount may be besides a number: a formula of the database's parameters.

    `parameters` is None when `parameters.yaml` was refused. A formula is then checked for its
    syntax only, because the problem with the parameters has already been reported.
    """

    parameters: ParameterSet | None


def read_exchange(
    row: Row, process_id: str, formulas: Formulas | None, problems: list[Problem]
) -> Exchange | None:
    """One line of `process_id`. Without `formulas`, the amount must be a number."""
    line = row.get("line")
    if not line:
        problems.append(
            Problem(
                "bad-row", f"process {process_id}", "a row has no line id", location=row.location
            )
        )
        return None
    item = f"{process_id} line {line}"
    role = _read_role(row, item, problems)
    amount = read_amount(row, item, process_id, formulas, problems)
    if role is None or amount is None:
        return None
    value, formula = amount
    uncertainty = _read_distribution(row, item, value, formula, problems)
    if uncertainty is False:
        return None
    try:
        return Exchange(
            process_id=process_id,
            line_id=line,
            role=role,
            flow_id=row.get("flow"),
            flow_name=blank_to_none(row.get("flow_name")),
            amount=value,
            formula=formula,
            unit=row.get("unit"),
            supplier_id=blank_to_none(row.get("supplier")),
            comment=blank_to_none(row.get("comment")),
            uncertainty=uncertainty,
        )
    except ValidationError as error:
        problems.extend(
            problems_from_validation(
                error, item=item, location=row.location, rename=EXCHANGE_COLUMN
            )
        )
        return None


def _read_distribution(
    row: Row, item: str, amount: float, formula: str | None, problems: list[Problem]
) -> Uncertainty | None | Literal[False]:
    """The line's distribution around its amount, None for none, or False when it is wrong."""
    before = len(problems)
    found = read_uncertainty(
        {c: row.get(c) for c in DISTRIBUTION_COLUMNS},
        amount,
        what="amount",
        item=item,
        location=row.location,
        number=lambda raw, what: parse_number(str(raw), what),
        problems=problems,
    )
    if len(problems) > before:
        return False
    if found is not None and formula is not None:
        problems.append(
            Problem(
                "formula-with-distribution",
                item,
                f"amount {formula!r} is a formula, so its spread comes from the parameters it "
                "uses: give the distribution to them, in parameters.yaml",
                location=row.location,
            )
        )
        return False
    return found


def read_amount(
    row: Row, item: str, scope: str, formulas: Formulas | None, problems: list[Problem]
) -> tuple[float, str | None] | None:
    """The `amount` cell: a number, or a formula and what it gives with the database's parameters.

    `scope` is the process the line belongs to, so its own parameters can be used.
    """
    text = row.get("amount")
    if formulas is None or not text or _is_number(text) or _DECIMAL_COMMA.fullmatch(text):
        try:
            return parse_number(text, "amount"), None
        except ValueError as exc:
            hint = ""
            if formulas is None and any(c.isalpha() for c in text) and "," not in text:
                hint = " A formula can be used in a database's processes, with a parameters.yaml."
            problems.append(Problem("bad-number", item, f"{exc}{hint}", location=row.location))
            return None
    return _formula_amount(text, row, item, scope, formulas, problems)


def _formula_amount(
    text: str, row: Row, item: str, scope: str, formulas: Formulas, problems: list[Problem]
) -> tuple[float, str] | None:
    try:
        formula = Formula.parse(text)
    except FormulaError as exc:
        problems.append(Problem("bad-formula", item, f"amount: {exc}", location=row.location))
        return None
    parameters = formulas.parameters
    if parameters is None:
        return None
    for name in formula.names:
        unknown = parameters.unknown_name(name, scope)
        if unknown is None:
            continue
        if len(parameters) == 0:
            # Most likely a mistyped number, in a database that has no parameters at all.
            problems.append(
                Problem(
                    "bad-number",
                    item,
                    f"amount {text!r} is not a number. A formula can use the parameters of "
                    "parameters.yaml, and this database has none.",
                    location=row.location,
                )
            )
        else:
            problems.append(
                Problem(
                    "unknown-parameter",
                    item,
                    f"amount {text!r} uses {name!r}, which is not a parameter it can see.{unknown}",
                    location=row.location,
                )
            )
        return None
    try:
        return parameters.evaluate(text, scope), formula.text
    except FormulaError as exc:
        problems.append(Problem("bad-formula", item, f"amount: {exc}", location=row.location))
        return None


def _is_number(text: str) -> bool:
    try:
        float(text)
    except ValueError:
        return False
    return True


def _read_role(row: Row, item: str, problems: list[Problem]) -> Role | None:
    text = row.get("role")
    try:
        return Role(text)
    except ValueError:
        roles = [r.value for r in Role]
        problems.append(
            Problem(
                "unknown-role",
                item,
                f"unknown role {text!r}.{did_you_mean(text, roles)} Roles: {', '.join(roles)}",
                location=row.location,
            )
        )
        return None
