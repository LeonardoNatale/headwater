"""The tables of a release: their Arrow schema, their key, and how records become rows and back.

Every numeric column that holds an amount, a factor or a score is float64. Pedigree scores are
int8: they are codes from 1 to 5, not quantities.
"""

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pyarrow as pa

from headwater.model import (
    Factor,
    Flow,
    FlowProperty,
    Indicator,
    InvalidDataError,
    Issue,
    Method,
    Problem,
    Process,
    Unit,
)
from headwater.model.process import Exchange


@dataclass(frozen=True)
class TableSpec:
    """The columns of one table, and the columns that identify a row. Rows are sorted by key."""

    schema: pa.Schema
    key: tuple[str, ...]


def _required(name: str, type_: pa.DataType) -> "pa.Field[Any]":
    return pa.field(name, type_, nullable=False)


_TEXT = pa.string()
_FLOAT = pa.float64()

PROCESSES = TableSpec(
    pa.schema(
        [
            _required("id", _TEXT),
            _required("name", _TEXT),
            _required("reference_flow_id", _TEXT),
            _required("location", _TEXT),
            pa.field("time_start", pa.date32()),
            pa.field("time_end", pa.date32()),
            _required("kind", _TEXT),
            pa.field("source_ref", _TEXT),
        ]
    ),
    ("id",),
)

FLOWS = TableSpec(
    pa.schema(
        [
            _required("id", _TEXT),
            _required("name", _TEXT),
            _required("kind", _TEXT),
            _required("unit", _TEXT),
            pa.field("compartment", _TEXT),
            pa.field("cas", _TEXT),
        ]
    ),
    ("id",),
)

FLOW_PROPERTIES = TableSpec(
    pa.schema(
        [
            _required("flow_id", _TEXT),
            _required("property", _TEXT),
            _required("value", _FLOAT),
            _required("unit", _TEXT),
        ]
    ),
    ("flow_id", "property"),
)

UNITS = TableSpec(
    pa.schema(
        [
            _required("unit", _TEXT),
            _required("quantity", _TEXT),
            _required("factor_to_reference", _FLOAT),
        ]
    ),
    ("unit",),
)

# Fields inside a struct are nullable: other Parquet writers leave nulls under a null struct.
# The records require them.
UNCERTAINTY = pa.struct(
    [
        pa.field("distribution", _TEXT),
        pa.field("loc", _FLOAT),
        pa.field("scale", _FLOAT),
        pa.field("shape", _FLOAT),
        pa.field("minimum", _FLOAT),
        pa.field("maximum", _FLOAT),
    ]
)

_PEDIGREE_SCORES = (
    "reliability",
    "completeness",
    "temporal_correlation",
    "geographical_correlation",
    "further_technological_correlation",
)

PEDIGREE = pa.struct([pa.field(name, pa.int8()) for name in _PEDIGREE_SCORES])

PROPERTIES = pa.map_(_TEXT, pa.struct([pa.field("value", _FLOAT), pa.field("unit", _TEXT)]))
"""Property name to value and unit, entries sorted by name."""

EXCHANGES = TableSpec(
    pa.schema(
        [
            _required("process_id", _TEXT),
            _required("line_id", _TEXT),
            _required("role", _TEXT),
            _required("flow_id", _TEXT),
            _required("amount", _FLOAT),
            _required("unit", _TEXT),
            pa.field("supplier_id", _TEXT),
            pa.field("link_status", _TEXT),
            pa.field("uncertainty", UNCERTAINTY),
            pa.field("pedigree", PEDIGREE),
            _required("properties", PROPERTIES),
            pa.field("comment", _TEXT),
        ]
    ),
    ("process_id", "line_id"),
)

ISSUES = TableSpec(
    pa.schema(
        [
            _required("process_id", _TEXT),
            _required("line_id", _TEXT),
            _required("flow_id", _TEXT),
            _required("status", _TEXT),
            pa.field("supplier_id", _TEXT),
            pa.field("reason", _TEXT),
            pa.field("criterion", _TEXT),
            pa.field("share", _FLOAT),
            pa.field("cumulative_share", _FLOAT),
            pa.field("resolved_by", _TEXT),
            pa.field("resolved_at", pa.timestamp("us")),
        ]
    ),
    ("process_id", "line_id"),
)
"""`resolved_at` is UTC, stored with no time zone: DuckDB then reads it without pytz."""

FACTORS = TableSpec(
    pa.schema(
        [
            _required("indicator", _TEXT),
            _required("flow_id", _TEXT),
            pa.field("location", _TEXT),
            _required("factor", _FLOAT),
        ]
    ),
    ("indicator", "flow_id", "location"),
)


GEOGRAPHIES = TableSpec(
    pa.schema([_required("code", _TEXT), _required("name", _TEXT)]),
    ("code",),
)
"""The places a source names, by their short code: `GLO`, `RoW`, `CH`. Empty when the source
gives none."""


def indicators_spec(indicator_ids: Iterable[str]) -> TableSpec:
    """`product_id`, the id of the process that makes the product, then one float64 column per
    indicator, sorted by indicator id."""
    columns = [_required(i, _FLOAT) for i in sorted(indicator_ids)]
    return TableSpec(pa.schema([_required("product_id", _TEXT), *columns]), ("product_id",))


SOURCE_TABLES: dict[str, TableSpec] = {
    "processes.parquet": PROCESSES,
    "flows.parquet": FLOWS,
    "flow_properties.parquet": FLOW_PROPERTIES,
    "units.parquet": UNITS,
    "exchanges.parquet": EXCHANGES,
    "issues.parquet": ISSUES,
    "geographies.parquet": GEOGRAPHIES,
}
"""The tables every source release has, by file. Methods add `methods/<id>/factors.parquet`."""


def factors_path(method_id: str) -> str:
    return f"methods/{method_id}/factors.parquet"


def indicators_path(method_id: str) -> str:
    return f"indicators/{method_id}.parquet"


# --- records to tables ----------------------------------------------------------------------


def _table(spec: TableSpec, rows: list[dict[str, Any]]) -> pa.Table:
    return pa.Table.from_pylist(rows, schema=spec.schema)


def processes_table(processes: Iterable[Process]) -> pa.Table:
    rows = [
        {
            "id": p.id,
            "name": p.name,
            "reference_flow_id": p.reference_exchange.flow_id,
            "location": p.location,
            "time_start": p.time_start,
            "time_end": p.time_end,
            "kind": p.kind.value,
            "source_ref": p.source_ref,
        }
        for p in processes
    ]
    return _table(PROCESSES, rows)


FLOW_NAME = "flow_name"
"""An optional column of the exchanges given to `build_tables`: the flow's name as the source
writes it next to the line. It is checked against the flow, then not stored."""


def exchanges_table(processes: Iterable[Process], *, flow_names: bool = False) -> pa.Table:
    """Every line of every process. `flow_name` is not stored: in a release, a flow's name is in
    `flows.parquet`, and a name written next to an id is only a check for files people edit.
    With `flow_names`, the table has a last column `flow_name`, for `build_tables` to check."""
    lines = [e for p in processes for e in p.exchanges]
    columns: dict[str, list[Any]] = {
        "process_id": [e.process_id for e in lines],
        "line_id": [e.line_id for e in lines],
        "role": [e.role.value for e in lines],
        "flow_id": [e.flow_id for e in lines],
        "amount": [e.amount for e in lines],
        "unit": [e.unit for e in lines],
        "supplier_id": [e.supplier_id for e in lines],
        "link_status": [e.link_status.value if e.link_status else None for e in lines],
        "uncertainty": [_uncertainty_row(e) for e in lines],
        "pedigree": [_pedigree_row(e) for e in lines],
        "properties": [
            [(p.name, {"value": p.value, "unit": p.unit}) for p in e.properties] for e in lines
        ],
        "comment": [e.comment for e in lines],
    }
    schema: Any = EXCHANGES.schema
    if flow_names:
        columns[FLOW_NAME] = [e.flow_name for e in lines]
        schema = schema.append(pa.field(FLOW_NAME, _TEXT))
    return pa.Table.from_pydict(columns, schema=schema)


def _uncertainty_row(e: Exchange) -> dict[str, Any] | None:
    u = e.uncertainty
    if u is None:
        return None
    return {
        "distribution": u.distribution.value,
        "loc": u.loc,
        "scale": u.scale,
        "shape": u.shape,
        "minimum": u.minimum,
        "maximum": u.maximum,
    }


def _pedigree_row(e: Exchange) -> dict[str, int] | None:
    p = e.pedigree
    if p is None:
        return None
    return {name: getattr(p, name) for name in _PEDIGREE_SCORES}


def flows_table(flows: Iterable[Flow]) -> pa.Table:
    return _table(FLOWS, [f.model_dump(mode="json") for f in flows])


def flow_properties_table(properties: Iterable[FlowProperty]) -> pa.Table:
    return _table(FLOW_PROPERTIES, [p.model_dump(mode="json") for p in properties])


def units_table(units: Iterable[Unit]) -> pa.Table:
    rows = [
        {"unit": u.name, "quantity": u.quantity, "factor_to_reference": u.factor_to_reference}
        for u in units
    ]
    return _table(UNITS, rows)


def issues_table(issues: Iterable[Issue]) -> pa.Table:
    """Issues as rows, with `resolved_at` in UTC and no time zone. A time with no zone is
    refused before this, by `build`."""
    rows = [i.model_dump() for i in issues]
    for row in rows:
        at: datetime | None = row["resolved_at"]
        if at is not None and at.utcoffset() is not None:
            row["resolved_at"] = at.astimezone(UTC).replace(tzinfo=None)
    return _table(ISSUES, rows)


def factors_table(factors: Iterable[Factor]) -> pa.Table:
    return _table(FACTORS, [f.model_dump() for f in factors])


def indicators_table(product_ids: Sequence[str], scores: Mapping[str, Sequence[float]]) -> pa.Table:
    spec = indicators_spec(scores)
    columns: dict[str, Any] = {"product_id": list(product_ids)}
    columns.update({i: scores[i] for i in sorted(scores)})
    return pa.Table.from_pydict(columns, schema=spec.schema)


# --- tables to records ----------------------------------------------------------------------


def read_processes(processes: pa.Table, exchanges: pa.Table) -> tuple[Process, ...]:
    """Processes sorted by id, each with its lines sorted by line id."""
    lines: dict[str, list[Exchange]] = defaultdict(list)
    for row in exchanges.to_pylist():
        lines[row["process_id"]].append(read_exchange(row))
    found: list[Process] = []
    for row in processes.to_pylist():
        del row["reference_flow_id"]
        made = lines.pop(row["id"], [])
        found.append(Process.model_validate({**row, "exchanges": made}))
    if lines:
        raise InvalidDataError(
            Problem(
                "unknown-process",
                f"process {process_id}",
                "has lines in exchanges.parquet but no row in processes.parquet",
            )
            for process_id in sorted(lines)
        )
    return tuple(found)


def read_exchange(row: dict[str, Any]) -> Exchange:
    """One row of the exchanges table as a record. Changes `row`."""
    properties: list[tuple[str, dict[str, Any]]] = row.pop("properties")
    row["properties"] = [{"name": name, **value} for name, value in properties]
    return Exchange.model_validate(row)


def read_flows(table: pa.Table) -> tuple[Flow, ...]:
    return tuple(Flow.model_validate(row) for row in table.to_pylist())


def read_flow_properties(table: pa.Table) -> tuple[FlowProperty, ...]:
    return tuple(FlowProperty.model_validate(row) for row in table.to_pylist())


def read_units(table: pa.Table) -> tuple[Unit, ...]:
    return tuple(
        Unit(
            name=row["unit"],
            quantity=row["quantity"],
            factor_to_reference=row["factor_to_reference"],
        )
        for row in table.to_pylist()
    )


def read_issues(table: pa.Table) -> tuple[Issue, ...]:
    """Issues with `resolved_at` in UTC, as stored."""
    return tuple(Issue.model_validate(issue_row(row)) for row in table.to_pylist())


def issue_row(row: dict[str, Any]) -> dict[str, Any]:
    """A row of the issues table with its time zone back, ready for `Issue`. Changes `row`."""
    at: datetime | None = row["resolved_at"]
    if at is not None:
        row["resolved_at"] = at.replace(tzinfo=UTC)
    return row


def read_method(
    *,
    id: str,
    version: str,
    variant: str | None,
    flow_list: str,
    indicators: Sequence[Indicator],
    factors: pa.Table,
) -> Method:
    return Method(
        id=id,
        version=version,
        variant=variant,
        flow_list=flow_list,
        indicators=tuple(indicators),
        factors=tuple(Factor.model_validate(row) for row in factors.to_pylist()),
    )
