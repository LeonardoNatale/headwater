"""From the raw datasets of a source to the typed tables of an `InventorySource`.

What a dataset says alone is read in `dataset`. What needs the whole source is decided here:

- **Ids.** A process id is a uuid5 of what its reference function says (name, location, unit,
  category, subcategory, infrastructure), never of a file path, so moving or renaming the files
  keeps every id. Two datasets that say the same are refused, naming both files.
- **Flows.** ecoSpold1 names a flow, not identifies it. A product is one flow by name and the
  quantity of its unit, whatever its location or category: a buyer does not give the supplier's
  category, and the flow of a line must be the flow its supplier makes. The same product counted
  in kWh and in MJ is one flow, and in `m3` and in kg two. An elementary flow is named by name,
  category and subcategory, and counted in one quantity: the unit check refuses it otherwise. Ids
  come from `flow_id` and `product_flow_id`. A flow is a waste when any dataset says so (see
  `roles`). Its unit is the unit of the line that makes it, else of the first line that uses it.
- **Roles and signs**, from `roles`, once every flow's kind is known.
- **Suppliers.** A line that buys names its supplier by product, location and unit, for the link
  stage by name. A line that buys the process's own product is `own_use`. A line that buys in an
  aggregated dataset (type 2, cumulative results) names a stand-in supplier that is left out on
  purpose, so the stage resolves it as a data gap.
- **Allocation.** The percent of each `allocation` is a factor for its co-product, on the lines it
  names. The factor most lines share is the product's, and the lines that differ get a row.
"""

import uuid
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pyarrow as pa

from headwater.io.ecospold1.dataset import Dataset, FlowKey, Line
from headwater.io.ecospold1.roles import Refused, assign, is_waste_signal
from headwater.io.ecospold1.uncertainty import negate
from headwater.io.ecospold1.units import unit_table
from headwater.io.source import ALLOCATION, LINES, PROCESSES_READ, flow_id, product_flow_id
from headwater.link import DataGap
from headwater.model import (
    ROLES_WITH_SUPPLIER,
    Flow,
    FlowKind,
    Problem,
    ProcessKind,
    Role,
    Units,
)

AGGREGATED = DataGap(
    reason="aggregated dataset: upstream already included",
    resolved_by="headwater ecospold1 reader",
    resolved_at=datetime(2026, 10, 25, tzinfo=UTC),
)
"""What a line that buys, in a dataset of cumulative results, is resolved as. Its emissions already
include its suppliers', so linking the line would count them twice. A rule of the format, decided
on the date given, not a person's decision about one line."""


@dataclass(frozen=True)
class Assembled:
    processes: pa.Table
    lines: pa.Table
    allocation: pa.Table
    flows: tuple[Flow, ...]
    units: Units
    left_out: dict[str, DataGap]
    """The aggregated datasets' stand-in supplier, when a line of one buys."""
    problems: tuple[Problem, ...]


@dataclass
class _FlowInfo:
    id: str
    unit: str
    made: bool
    """The unit is that of a line that makes the flow."""
    cas: str | None
    waste_at: str | None = None
    """Where a dataset first says the flow is a waste, for the message of a line that disagrees."""


def assemble(found: Sequence[tuple[str, Dataset]], source: str, version: str) -> Assembled:
    """The tables for these datasets, each with the path of its file. Problems come back with
    the tables, which are then incomplete."""
    namespace = f"{source}-{version}"
    problems: list[Problem] = []
    ids = _process_ids(found, f"{source}:{version}", problems)
    flows = _flows(found, namespace)
    stand_in = f"{source}:{version}:aggregated"
    process_rows: list[dict[str, Any]] = []
    line_rows: list[dict[str, Any]] = []
    allocation_rows: list[dict[str, Any]] = []
    for file, ds in found:
        pid = ids[file, ds.number]
        process_rows.append(_process(pid, file, ds))
        own = {(line.name, ds.location, line.unit) for line in ds.lines if line.slot.makes}
        for line in ds.lines:
            info = flows[line.key]
            decided = assign(line.slot, line.amount, info.waste_at is not None)
            if isinstance(decided, Refused):
                label = f"dataset {ds.number} line {line.id}"
                message = f"{decided.message}. The flow is {line.name!r}"
                if decided.code == "waste-and-product":
                    message += f", a waste since {info.waste_at}"
                problems.append(Problem(decided.code, label, message, location=file))
                continue
            role = decided.role
            if role is Role.input and (line.name, line.location, line.unit) in own:
                role = Role.own_use
            stand = stand_in if ds.aggregated else None
            line_rows.append(_line(pid, line, role, decided.negate, info.id, stand))
        allocation_rows += _allocation(pid, ds)
    return Assembled(
        processes=pa.Table.from_pylist(process_rows, schema=PROCESSES_READ.schema),
        lines=pa.Table.from_pylist(line_rows, schema=LINES.schema),
        allocation=pa.Table.from_pylist(allocation_rows, schema=ALLOCATION.schema),
        flows=tuple(_flow(key, info) for key, info in flows.items()),
        units=unit_table(line.unit for _, ds in found for line in ds.lines),
        left_out=(
            {stand_in: AGGREGATED} if any(r["supplier_id"] == stand_in for r in line_rows) else {}
        ),
        problems=tuple(problems),
    )


# --- processes -----------------------------------------------------------------------------


def _process_ids(
    found: Sequence[tuple[str, Dataset]], prefix: str, problems: list[Problem]
) -> dict[tuple[str, str], str]:
    """The id of each dataset, by its file and number. Datasets that say the same are refused."""
    ids: dict[tuple[str, str], str] = {}
    first: dict[str, tuple[str, str]] = {}
    for file, ds in found:
        if (file, ds.number) in ids:
            problems.append(
                Problem(
                    "duplicate-dataset",
                    f"{file}#{ds.number}",
                    f"{file} has two datasets with the number {ds.number}",
                    location=file,
                )
            )
            continue
        key = "\x00".join(
            [ds.name, ds.location, ds.unit, ds.category, ds.sub_category, str(ds.infrastructure)]
        )
        pid = f"{prefix}:{uuid.uuid5(uuid.NAMESPACE_URL, key)}"
        ids[file, ds.number] = pid
        if pid in first:
            other_file, other_number = first[pid]
            problems.append(
                Problem(
                    "duplicate-process",
                    f"{file}#{ds.number}",
                    f"{other_file} dataset {other_number} and {file} dataset {ds.number} have "
                    "the same name, location, unit, category and subcategory, so they would "
                    "have one id",
                    location=file,
                )
            )
        first.setdefault(pid, (file, ds.number))
    return ids


def _process(pid: str, file: str, ds: Dataset) -> dict[str, Any]:
    return {
        "id": pid,
        "name": ds.name,
        "location": ds.location,
        "time_start": ds.time_start,
        "time_end": ds.time_end,
        "kind": ProcessKind.ordinary_transforming_activity.value,
        "source_ref": f"{file}#{ds.number}",
    }


# --- flows ---------------------------------------------------------------------------------


def _flows(found: Iterable[tuple[str, Dataset]], namespace: str) -> dict[FlowKey, _FlowInfo]:
    flows: dict[FlowKey, _FlowInfo] = {}
    for file, ds in found:
        for line in ds.lines:
            info = flows.get(line.key)
            if info is None:
                info = flows[line.key] = _FlowInfo(
                    _flow_id(namespace, line.key), line.unit, line.slot.makes, line.cas
                )
            if line.slot.makes and not info.made:
                info.unit, info.made = line.unit, True
            info.cas = info.cas or line.cas
            if info.waste_at is None and is_waste_signal(line.slot, line.amount):
                info.waste_at = f"{file} dataset {ds.number} line {line.id}"
    return flows


def _flow_id(namespace: str, key: FlowKey) -> str:
    name, compartment, quantity = key
    if compartment is not None:
        return flow_id(namespace, name, compartment)
    assert quantity is not None
    return product_flow_id(namespace, name, quantity)


def _flow(key: FlowKey, info: _FlowInfo) -> Flow:
    name, compartment, _ = key
    if compartment is not None:
        kind = FlowKind.elementary
    else:
        kind = FlowKind.waste if info.waste_at is not None else FlowKind.product
    return Flow(
        id=info.id, name=name, kind=kind, unit=info.unit, compartment=compartment, cas=info.cas
    )


# --- lines ---------------------------------------------------------------------------------


def _line(
    pid: str, line: Line, role: Role, negated: bool, flow: str, stand_in: str | None
) -> dict[str, Any]:
    """The row of a line. A line that buys names its supplier by product, location and unit, or
    by the stand-in supplier id when it sits in an aggregated dataset."""
    uncertainty = line.uncertainty
    if uncertainty is not None and negated:
        uncertainty = negate(uncertainty)
    buys = role in ROLES_WITH_SUPPLIER
    by_name = buys and stand_in is None
    return {
        "process_id": pid,
        "line_id": line.id,
        "role": role.value,
        "flow_id": flow,
        "flow_name": line.name,
        "amount": abs(line.amount) if negated else line.amount,
        "unit": line.unit,
        "supplier_id": stand_in if buys else None,
        "supplier_name": None,
        "supplier_product": line.name if by_name else None,
        "supplier_location": line.location if by_name else None,
        "supplier_unit": line.unit if by_name else None,
        "uncertainty": uncertainty.model_dump() if uncertainty is not None else None,
        "pedigree": line.pedigree.model_dump() if line.pedigree is not None else None,
        "properties": [],
        "comment": line.comment,
    }


# --- allocation ----------------------------------------------------------------------------


def _allocation(pid: str, ds: Dataset) -> list[dict[str, Any]]:
    """The `ALLOCATION` rows of a dataset that gives allocations. A reference to a product line
    is no factor for a line, so it was dropped when the allocation was read."""
    if not ds.shares:
        return []
    products = [line.id for line in ds.lines if line.slot.makes]
    others = [line.id for line in ds.lines if not line.slot.makes]
    factors: defaultdict[tuple[str, str], float] = defaultdict(float)
    for share in ds.shares:
        for line in share.lines:
            factors[share.product, line] += share.fraction / 100
    rows: list[dict[str, Any]] = []
    for product in products:
        shares = [s for s in ds.shares if s.product == product]
        if not shares:
            continue
        by_line = {line: factors.get((product, line), 0.0) for line in others}
        usual = (
            Counter(by_line.values()).most_common(1)[0][0]
            if by_line
            else sum(s.fraction for s in shares) / 100
        )
        rows.append({"process_id": pid, "product": product, "line_id": None, "factor": usual})
        rows += [
            {"process_id": pid, "product": product, "line_id": line, "factor": factor}
            for line, factor in by_line.items()
            if factor != usual
        ]
    return rows
