"""The two mapping files: decisions a person made about suppliers and flows, reviewed in git.

A line or a factor that does not match by itself is never matched by a guess. A person looks it
up and writes the decision here, with who and when, like a row of `cut_offs.csv`. The next
import reads the same decision again.

The supplier mapping decides a line's supplier, or makes the line a data gap:

```
supplier_id,name,product,location,unit,supplier,reason,who,when,comment
,"market for | Cut-off, U","Electricity, medium voltage",FR,,agribalyse:3.2:p7,,leo,2026-10-24,
,market for,Tap water,CH,,,Not in the package,leo,2026-10-24,out of scope
```

A row names the line's supplier as the line does: by id in `supplier_id`, or by name with at
least one of `name`, `product`, `location` and `unit`. Then either `supplier`, the process to
buy from, or `reason`, which makes the line a data gap.

The flow mapping finds the flow of a method's factor in a release:

```
source_id,source_name,source_compartment,target,who,when,comment
,"Carbon dioxide, fossil",air/high. pop.,ecoinvent:flow:349b...,leo,2026-10-24,
```

A row names the factor's flow as the method does, by `source_id` or by `source_name` and
`source_compartment`, and gives the flow of the release in `target`.

`who` and `when` (an ISO date or time) are required on every row. A key decided twice is an
error. One row is one line, and the file is read as `cut_offs.csv` is.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from datetime import datetime
from pathlib import Path

from headwater.model import InvalidDataError, Problem
from headwater.study._files import Report, read_csv, read_when


@dataclass(frozen=True)
class SupplierKey:
    """How a line names its supplier by name: what it gives of the supplier's process name,
    reference product, location and reference unit. At least one is given.

    Example: `SupplierKey(product="electricity", location="GLO", unit="kWh")`.
    """

    name: str | None = None
    product: str | None = None
    location: str | None = None
    unit: str | None = None

    def __str__(self) -> str:
        given = ((f.name, getattr(self, f.name)) for f in fields(self))
        return ", ".join(f"{name} {value!r}" for name, value in given if value is not None)


SUPPLIER_KEY = tuple(f"supplier_{f.name}" for f in fields(SupplierKey))
"""The columns of a line that name its supplier by name, in the order of `SupplierKey`:
`supplier_name`, `supplier_product`, `supplier_location`, `supplier_unit`."""


@dataclass(frozen=True)
class FlowKey:
    """How a factor names its flow: by id, or by name and compartment.

    Example: `FlowKey(name="Methane, fossil", compartment="air/urban air close to ground")`.
    """

    id: str | None = None
    name: str | None = None
    compartment: str | None = None

    def __str__(self) -> str:
        if self.id is not None:
            return self.id
        return f"{self.name} ({self.compartment or ''})"


@dataclass(frozen=True)
class Decision:
    """Who decided, when, and where the row is: `suppliers.csv:3`."""

    who: str
    when: datetime
    """In UTC."""
    comment: str | None
    source: str


@dataclass(frozen=True)
class SupplierRow:
    """One row of the supplier mapping: the supplier to buy from, or the reason for a gap."""

    key: str | SupplierKey
    """The supplier's id, or its key by name."""
    supplier: str | None
    reason: str | None
    decision: Decision


@dataclass(frozen=True)
class FlowRow:
    """One row of the flow mapping: the flow of the release a source's key maps to."""

    key: FlowKey
    target: str
    decision: Decision


@dataclass(frozen=True)
class SupplierMapping:
    by_id: Mapping[str, SupplierRow] = field(default_factory=dict[str, SupplierRow])
    by_name: Mapping[SupplierKey, SupplierRow] = field(
        default_factory=dict[SupplierKey, SupplierRow]
    )


@dataclass(frozen=True)
class FlowMapping:
    rows: Mapping[FlowKey, FlowRow] = field(default_factory=dict[FlowKey, FlowRow])
    file: str = ""
    """The file's name, for messages."""


_SUPPLIER_REQUIRED = ("supplier", "reason", "who", "when")
_SUPPLIER_OPTIONAL = ("supplier_id", "name", "product", "location", "unit", "comment")
_FLOW_REQUIRED = ("target", "who", "when")
_FLOW_OPTIONAL = ("source_id", "source_name", "source_compartment", "comment")

type _Row = Mapping[str, str | None]


def read_supplier_mapping(path: Path | str) -> SupplierMapping:
    """Read a supplier mapping file. Raises `InvalidDataError` naming every bad row."""
    file, rows = _read(path, _SUPPLIER_REQUIRED, _SUPPLIER_OPTIONAL)
    problems: list[Problem] = []
    by_id: dict[str, SupplierRow] = {}
    by_name: dict[SupplierKey, SupplierRow] = {}
    for where, row in rows:
        report = _reporter(problems, file, where)
        decision = _decision(row, f"{file}:{where}", report)
        supplier_id = row["supplier_id"]
        named = SupplierKey(row["name"], row["product"], row["location"], row["unit"])
        if (supplier_id is None) == (named == SupplierKey()):
            report(
                "bad-key",
                "name the supplier either by supplier_id, or by name, product, location and "
                "unit (at least one), as the line does",
            )
            continue
        if (row["supplier"] is None) == (row["reason"] is None):
            report(
                "bad-decision",
                "give either a supplier to buy from, or a reason, which makes the line a data gap",
            )
            continue
        if decision is None:
            continue
        if supplier_id is not None:
            decided = SupplierRow(supplier_id, row["supplier"], row["reason"], decision)
            known = by_id.setdefault(supplier_id, decided)
        else:
            decided = SupplierRow(named, row["supplier"], row["reason"], decision)
            known = by_name.setdefault(named, decided)
        if known is not decided:
            report(
                "duplicate-row",
                f"{decided.key} is decided twice: also on line {known.decision.source}",
            )
    if problems:
        raise InvalidDataError(problems)
    return SupplierMapping(by_id=by_id, by_name=by_name)


def read_flow_mapping(path: Path | str) -> FlowMapping:
    """Read a flow mapping file. Raises `InvalidDataError` naming every bad row."""
    file, rows = _read(path, _FLOW_REQUIRED, _FLOW_OPTIONAL)
    problems: list[Problem] = []
    found: dict[FlowKey, FlowRow] = {}
    for where, row in rows:
        report = _reporter(problems, file, where)
        decision = _decision(row, f"{file}:{where}", report)
        source_id, name = row["source_id"], row["source_name"]
        if (source_id is None) == (name is None):
            report("bad-key", "name the flow either by source_id, or by source_name")
            continue
        if source_id is not None and row["source_compartment"] is not None:
            report("bad-key", "a flow named by source_id has no source_compartment")
            continue
        target = row["target"]
        if target is None:
            report("missing-field", "target is required")
            continue
        if decision is None:
            continue
        key = FlowKey(id=source_id, name=name, compartment=row["source_compartment"])
        decided = FlowRow(key, target, decision)
        known = found.setdefault(key, decided)
        if known is not decided:
            report("duplicate-row", f"{key} is mapped twice: also on line {known.decision.source}")
    if problems:
        raise InvalidDataError(problems)
    return FlowMapping(rows=found, file=file)


def _read(
    path: Path | str, required: tuple[str, ...], optional: tuple[str, ...]
) -> tuple[str, list[tuple[int, _Row]]]:
    """The file's name and its rows, each with its line number and its cells by column, blank
    cells as None."""
    root = Path(path)
    problems: list[Problem] = []
    rows = read_csv(root, root.parent, required=required, optional=optional, problems=problems)
    if problems:
        raise InvalidDataError(problems)
    columns = (*required, *optional)
    return root.name, [(row.line, {c: row.get(c) or None for c in columns}) for row in rows]


def _reporter(problems: list[Problem], file: str, where: int) -> Report:
    def report(code: str, message: str) -> None:
        problems.append(Problem(code, f"mapping {file} line {where}", message))

    return report


def _decision(row: _Row, source: str, report: Report) -> Decision | None:
    """Who and when of a row, with `when` in UTC. A time with no zone is UTC."""
    who, text = row["who"], row["when"]
    if who is None:
        report("missing-field", "who is required: a mapping is a decision somebody made")
    if text is None:
        report("missing-field", "when is required, an ISO date or time")
        return None
    when = read_when(text, report)
    if when is None or who is None:
        return None
    return Decision(who=who, when=when, comment=row["comment"], source=source)
