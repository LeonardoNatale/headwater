"""Link lines to suppliers by id, the way ecoinvent links by UUID.

A line that buys from, or sends to, a supplier names it by id. Example: the natural gas market buys
from `ecoinvent:3.12:cutoff:<activity>_<product>`.

- A row of the supplier mapping decides first: buy from another process, or a data gap.
- The supplier is a process: the line is linked.
- The supplier was left out on purpose: the line is a data gap, with a resolved issue saying why.
- Anything else: the line is unlinked, with an open issue and a problem naming both ends.

Works on Arrow tables: only the few lines that do not link become Python objects.
"""

from collections.abc import Callable, Mapping
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc

from headwater.link.lines import (
    LINE_COLUMNS,
    DataGap,
    Line,
    Linked,
    Outcomes,
    buys,
    gap_issue,
    issue,
    label,
    unlinked,
)
from headwater.link.mapping import SupplierMapping, SupplierRow
from headwater.model import IssueStatus, Problem
from headwater.model.uncertainty import LinkStatus

compute: Any = pc


def link_by_id(
    lines: pa.Table,
    known: Any,
    *,
    left_out: Mapping[str, DataGap] | None = None,
    decided: SupplierMapping | None = None,
    hint: Callable[[str], str] | None = None,
) -> Linked:
    """Check every line's `supplier_id` against `known`, the ids of every process a line may buy
    from. `hint` adds a sentence about a missing supplier to its problem, for example the file it
    would come from. Lines that buy nothing keep their supplier and get no link status."""
    left_out = left_out or {}
    rows: Mapping[str, SupplierRow] = decided.by_id if decided is not None else {}
    supplier = lines.column("supplier_id")
    found = compute.fill_null(compute.is_in(supplier, value_set=known), False)
    if rows:
        keys = pa.array(sorted(rows), pa.string())
        found = compute.and_(found, compute.invert(compute.is_in(supplier, value_set=keys)))
    buying = buys(lines)
    rest = compute.and_(buying, compute.invert(found))
    statuses = compute.if_else(
        compute.and_(buying, found),
        LinkStatus.linked.value,
        pa.nulls(lines.num_rows, pa.string()),
    )
    known_ids = set(compute.unique(known).to_pylist()) if rows else set[str]()
    used_gaps: set[str] = set()
    outcomes = Outcomes()
    for line in lines.filter(rest).select([*LINE_COLUMNS, "supplier_id"]).to_pylist():
        named: str | None = line["supplier_id"]
        row = rows.get(named) if named is not None else None
        gap = left_out.get(named) if named is not None else None
        if row is not None:
            outcomes.decide(line, row, known_ids)
        elif named is not None and gap is not None:
            used_gaps.add(named)
            outcomes.add(None, gap_issue(line, gap))
        else:
            outcomes.add(None, issue(line, IssueStatus.open), _unlinked(line, hint))
    unused_gaps = tuple(sorted(set(left_out) - used_gaps))
    return outcomes.linked(lines, rest, supplier, statuses, rows, unused_gaps)


def _unlinked(line: Line, hint: Callable[[str], str] | None) -> Problem:
    supplier: str | None = line["supplier_id"]
    if supplier is None:
        return unlinked(line)
    extra = f" {hint(supplier)}" if hint else ""
    return Problem(
        "missing-supplier",
        label(line),
        f"{line['role']} of flow {line['flow_id']} comes from {supplier}, which is not in the "
        f"source.{extra} It is an open issue.",
    )
