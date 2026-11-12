"""Reading `no_factor.csv`: flows a database emits that the method has no factor for, acknowledged.

Example: the T-shirt emits 0.002 kg of COD to water, and toy-climate has no factor for COD. A
row for `tshirt:cod` and `toy-climate` says that the flow counts 0, why, who decided and when.
Whether a row is needed depends on the method, and whether `flow_name` is the flow's name
depends on the flows the database can see, so validation checks those, not reading.
"""

from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from headwater.model import Problem
from headwater.study._cut_offs import WHO_AND_WHEN
from headwater.study._files import (
    Row,
    blank_to_none,
    problems_from_validation,
    read_csv,
    read_when,
)

FILE = "no_factor.csv"
COLUMNS = ("flow", "method", "reason", "who", "when")
OPTIONAL = ("flow_name",)

_WHAT = {
    "flow": "the flow's id, as a process file gives it",
    "method": "the method's id, for example EF-3.1",
    "reason": "why the flow counts 0",
    **WHO_AND_WHEN,
}


class NoFactor(BaseModel):
    """A flow that the database's own processes emit or take and that `method` has no factor for
    in any indicator, acknowledged by a person: it counts 0.

    Example: `tshirt:cod` in `toy-climate`, "not characterised in toy-climate", Sam,
    2026-10-12.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    flow_id: str = Field(min_length=1)
    flow_name: str | None = None
    """The flow's name written next to its id, which validation checks, as in a process file."""
    method: str = Field(min_length=1)
    """The method's id."""
    reason: str = Field(min_length=1)
    who: str = Field(min_length=1)
    when: datetime
    """In UTC without a zone, as in cut_offs.csv."""


def read_no_factor(root: Path, problems: list[Problem]) -> tuple[NoFactor, ...]:
    """The rows of `no_factor.csv`, in the order of the file. A missing file has none.

    Every column but `flow_name` is required in every row, and a flow is written once per
    method. `when` is read as in cut_offs.csv.
    """
    path = root / FILE
    if not path.is_file():
        return ()
    found: list[NoFactor] = []
    seen: dict[tuple[str, str], str] = {}
    for row in read_csv(path, root, required=COLUMNS, optional=OPTIONAL, problems=problems):
        entry = _entry(row, seen, problems)
        if entry is not None:
            found.append(entry)
    return tuple(found)


def _entry(row: Row, seen: dict[tuple[str, str], str], problems: list[Problem]) -> NoFactor | None:
    item = f"flow {row.get('flow')}"

    def problem(code: str, message: str) -> None:
        problems.append(Problem(code, item, message, location=row.location))

    empty = [c for c in COLUMNS if not row.get(c)]
    for column in empty:
        problem("invalid-field", f"{column} is empty. Say {_WHAT[column]}.")
    if empty:
        return None
    key = (row.get("flow"), row.get("method"))
    if key in seen:
        problem(
            "no-factor-duplicate", f"is already acknowledged for method {key[1]} at {seen[key]}"
        )
        return None
    seen[key] = row.location
    when = read_when(row.get("when"), problem, naive=True)
    if when is None:
        return None
    try:
        return NoFactor(
            flow_id=row.get("flow"),
            flow_name=blank_to_none(row.get("flow_name")),
            method=row.get("method"),
            reason=row.get("reason"),
            who=row.get("who"),
            when=when,
        )
    except ValidationError as error:
        problems.extend(problems_from_validation(error, item=item, location=row.location))
        return None
