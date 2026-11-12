"""From an inventory as its source writes it to a release: the stages every format shares.

The order is fixed, and the same for every format:

1. The unit check: every line's unit converts to its flow's unit.
2. Allocation: a process that makes several products is split by its source's factors.
3. Linking: a line that names its supplier by id is linked by id, one that names it by name is
   linked by name. A row of the supplier mapping decides first.

Every line that does not link is an open issue with its reason, and `freeze` refuses while there
is one. A reader never calls a stage: it only reads.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc

from headwater.io.allocate import allocate
from headwater.io.source import ALLOCATION, LINES, PROCESSES_READ, InventorySource
from headwater.io.units import check_units
from headwater.link import SupplierMapping, Suppliers, link_by_id, link_by_name
from headwater.link.lines import Linked, buys, label
from headwater.link.mapping import SUPPLIER_KEY, SupplierRow
from headwater.model import (
    Flow,
    FlowProperty,
    InvalidDataError,
    Issue,
    Problem,
    Process,
    Severity,
    Units,
)
from headwater.release import Release, ReleaseInfo, build_tables
from headwater.release.content import conform, sort_rows
from headwater.release.manifest import FOLDER_NAME
from headwater.release.schema import (
    EXCHANGES,
    FLOW_NAME,
    flow_properties_table,
    flows_table,
    issues_table,
    read_processes,
)

compute: Any = pc


@dataclass(frozen=True)
class Inventory:
    """An inventory linked and ready to freeze.

    `process_table` and `exchange_table` hold the processes and their lines as the release stores
    them, plus each line's `flow_name`; `processes` makes records of them when asked. `issues`
    holds one issue per line that did not link: open, or resolved as a data gap. `problems` gives
    the reason of each open issue; while there is one, `freeze` refuses. `warnings` are for people
    to read.
    """

    info: ReleaseInfo
    process_table: pa.Table
    """Sorted by id."""
    exchange_table: pa.Table
    flows: tuple[Flow, ...]
    """Sorted by id."""
    flow_properties: tuple[FlowProperty, ...]
    units: Units
    geographies: pa.Table
    issues: tuple[Issue, ...]
    problems: tuple[Problem, ...]
    warnings: tuple[Problem, ...]

    @cached_property
    def processes(self) -> tuple[Process, ...]:
        """Every process with its lines, as records, sorted by id. Made on the first call: for
        ecoinvent that is about 750,000 records."""
        return read_processes(self.process_table, self.exchange_table)

    def freeze(self, *, name: str | None = None, into: Path | str | None = None) -> Release:
        """Write the release with `headwater.release.build_tables`, by default into the cache.
        `name` replaces the default name the reader gave."""
        if self.problems:
            raise InvalidDataError(self.problems)
        info = self.info
        if name is not None:
            if not re.fullmatch(FOLDER_NAME, name):
                raise InvalidDataError(
                    [
                        Problem(
                            "bad-name",
                            f"release {name}",
                            "a release name is a folder name: letters, digits, '.', '_' and '-' "
                            "only",
                        )
                    ]
                )
            info = info.model_copy(update={"name": name})
        return build_tables(
            info,
            processes=self.process_table,
            exchanges=self.exchange_table,
            flows=flows_table(self.flows),
            flow_properties=flow_properties_table(self.flow_properties),
            issues=issues_table(self.issues),
            geographies=self.geographies,
            units=self.units,
            into=into,
        )


def link_inventory(
    source: InventorySource, *, suppliers: SupplierMapping | None = None
) -> Inventory:
    """Run the stages on `source`, in order. Raises `InvalidDataError` for what a reader must not
    give (a flow defined twice, a line whose flow is not a flow, a process with no product, a line
    that names its supplier two ways, a unit that does not convert); every line that does not
    link is an issue on the result instead."""
    processes = conform(source.processes, PROCESSES_READ, "processes")
    lines = conform(source.lines, LINES, "lines")
    named = _named(lines)
    _raise_if_any(
        _flow_problems(lines, source.flows)
        + _without_product(processes, lines)
        + (_key_problems(lines, named) if compute.any(named).as_py() else [])
    )
    _raise_if_any(check_units(lines, source.flows, source.flow_properties, source.units))
    allocated = allocate(processes, lines, conform(source.allocation, ALLOCATION, "allocation"))
    processes, lines = allocated.processes, allocated.lines
    named = _named(lines)
    by_name: Linked | None = None
    if compute.any(named).as_py():
        offered = Suppliers.of_tables(processes, lines, source.flows, source.units)
        by_name = link_by_name(lines.filter(named), offered, decided=suppliers)
        lines = lines.filter(compute.invert(named))
    by_id = link_by_id(
        lines,
        processes.column("id"),
        left_out=source.left_out,
        decided=suppliers,
        hint=source.supplier_hint,
    )
    parts = [by_id] if by_name is None else [by_id, by_name]
    columns = [*EXCHANGES.schema.names, FLOW_NAME]
    exchanges = pa.concat_tables([p.lines.select(columns) for p in parts]).combine_chunks()
    unused_rows: list[SupplierRow] = list(by_id.unused_rows)
    if suppliers is not None:
        unused_rows += by_name.unused_rows if by_name else suppliers.by_name.values()
    warnings = [
        Problem(
            "excluded-unused",
            source.left_out[supplier].source_ref or f"supplier {supplier}",
            "is excluded, but no line buys from it, so the release does not record it",
            severity=Severity.warning,
        )
        for supplier in by_id.unused_gaps
    ]
    warnings += [_row_not_needed(row) for row in unused_rows]
    issues = [*allocated.issues, *(i for p in parts for i in p.issues)]
    return Inventory(
        info=source.info,
        process_table=sort_rows(_with_reference_flows(processes, exchanges), ("id",)),
        exchange_table=exchanges,
        flows=tuple(sorted(source.flows, key=lambda f: f.id)),
        flow_properties=source.flow_properties,
        units=source.units,
        geographies=source.geographies,
        issues=tuple(sorted(issues, key=lambda i: (i.process_id, i.line_id))),
        problems=(*allocated.problems, *(p for part in parts for p in part.problems)),
        warnings=(*source.warnings, *warnings),
    )


def _named(lines: pa.Table) -> Any:
    """True for each line that names its supplier by name."""
    named: Any = compute.is_valid(lines.column(SUPPLIER_KEY[0]))
    for column in SUPPLIER_KEY[1:]:
        named = compute.or_(named, compute.is_valid(lines.column(column)))
    return named


def _references(lines: pa.Table, *columns: str) -> pa.Table:
    """The reference lines, with only these columns."""
    table = lines.select(["role", *columns])
    return table.filter(compute.equal(table.column("role"), "reference"))


def _flow_problems(lines: pa.Table, flows: Sequence[Flow]) -> list[Problem]:
    """Flows defined twice, and lines whose flow is not one of the source's flows."""
    seen: set[str] = set()
    problems: list[Problem] = []
    for flow in flows:
        if flow.id in seen:
            problems.append(Problem("duplicate-flow", f"flow {flow.id}", "is defined twice"))
        seen.add(flow.id)
    ids = pa.array(sorted(seen), pa.string())
    unknown = lines.select(["process_id", "line_id", "flow_id"])
    unknown = unknown.filter(
        compute.invert(compute.is_in(unknown.column("flow_id"), value_set=ids))
    )
    problems += [
        Problem("unknown-flow", label(r), f"flow {r['flow_id']} is not a flow of the source")
        for r in unknown.to_pylist()
    ]
    return problems


def _without_product(processes: pa.Table, lines: pa.Table) -> list[Problem]:
    """Processes with no reference line: a process makes at least one product."""
    references = _references(lines, "process_id")
    made = compute.is_in(processes.column("id"), value_set=references.column("process_id"))
    return [
        Problem("no-reference-line", f"process {p}", "has no reference line: it makes nothing")
        for p in processes.filter(compute.invert(made)).column("id").to_pylist()
    ]


def _key_problems(lines: pa.Table, named: Any) -> list[Problem]:
    """Lines that name their supplier two ways, or by name on a role that buys nothing. A
    supplier id on such a role is refused when freezing."""
    both = compute.and_(named, compute.is_valid(lines.column("supplier_id")))
    idle = compute.and_(named, compute.invert(buys(lines)))
    keys = lines.select(["process_id", "line_id", "role"])
    problems = [
        Problem(
            "two-supplier-keys",
            label(r),
            "names its supplier both by id and by name. A reader gives one.",
        )
        for r in keys.filter(both).to_pylist()
    ]
    problems += [
        Problem(
            "supplier-on-role",
            label(r),
            f"is a {r['role']} line, which buys from no supplier, but names one",
        )
        for r in keys.filter(idle).to_pylist()
    ]
    return problems


def _with_reference_flows(processes: pa.Table, exchanges: pa.Table) -> pa.Table:
    """`processes` with `reference_flow_id`, the flow of each one's reference line."""
    references = _references(exchanges, "process_id", "flow_id")
    found = compute.take(
        references.column("flow_id"),
        compute.index_in(processes.column("id"), value_set=references.column("process_id")),
    )
    return processes.add_column(2, "reference_flow_id", found)


def _row_not_needed(row: SupplierRow) -> Problem:
    return Problem(
        "row-not-needed",
        f"mapping {row.decision.source}",
        f"decides {row.key}, but no line buys from it",
        severity=Severity.warning,
    )


def _raise_if_any(problems: list[Problem] | tuple[Problem, ...]) -> None:
    if problems:
        raise InvalidDataError(problems)
