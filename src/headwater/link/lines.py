"""What the link stages share: a supplier left out on purpose, what a row of the supplier mapping
does to a line, and how a stage collects what it decided into its result."""

from collections.abc import Container, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc

from headwater.link.mapping import SUPPLIER_KEY, SupplierRow
from headwater.model import ROLES_WITH_SUPPLIER, Issue, IssueStatus, Problem
from headwater.model.uncertainty import LinkStatus

compute: Any = pc

BUYING_ROLES = pa.array(sorted(r.value for r in ROLES_WITH_SUPPLIER), pa.string())
"""The roles that buy from, or send to, a supplier."""

LINE_COLUMNS = ["process_id", "line_id", "role", "flow_id"]
"""The columns a stage reads from each line it has to decide."""

type Line = Mapping[str, Any]
"""One row of a lines table, with at least `LINE_COLUMNS`."""


@dataclass(frozen=True)
class DataGap:
    """A supplier left out on purpose, why, who decided and when.

    `source_ref` names what was left out in the source, for example `datasets/<file>.spold`.
    """

    reason: str
    resolved_by: str
    resolved_at: datetime
    source_ref: str | None = None


@dataclass(frozen=True)
class Linked:
    """Lines after a link stage."""

    lines: pa.Table
    """The same lines, in the same order, with `supplier_id` and `link_status` set, and without
    the columns that named a supplier by name."""
    issues: tuple[Issue, ...]
    """One per line that did not link by itself: open, linked or a data gap by a person's
    decision, or a data gap. Sorted by line."""
    problems: tuple[Problem, ...]
    """One per open issue, with its reason. Sorted by line."""
    unused_gaps: tuple[str, ...] = ()
    """Suppliers left out on purpose that no line buys from, sorted."""
    unused_rows: tuple[SupplierRow, ...] = ()
    """Rows of the supplier mapping no line needed, in file order."""


def buys(lines: pa.Table) -> Any:
    """True for each line whose role buys from a supplier."""
    return compute.is_in(lines.column("role"), value_set=BUYING_ROLES)


def label(line: Line) -> str:
    return f"{line['process_id']} line {line['line_id']}"


def unlinked(line: Line) -> Problem:
    """The problem of a line that buys but names no supplier."""
    what = f"{line['role']} of flow {line['flow_id']}"
    return Problem("unlinked-line", label(line), f"{what} names no supplier. It is an open issue.")


def issue(line: Line, status: IssueStatus, **resolution: Any) -> Issue:
    return Issue(
        process_id=line["process_id"],
        line_id=line["line_id"],
        flow_id=line["flow_id"],
        status=status,
        **resolution,
    )


def gap_issue(line: Line, gap: DataGap) -> Issue:
    return issue(
        line,
        IssueStatus.data_gap,
        reason=gap.reason,
        resolved_by=gap.resolved_by,
        resolved_at=gap.resolved_at,
    )


class Outcomes:
    """What a stage decided for each line it looked at, in the order of those lines."""

    def __init__(self) -> None:
        self.suppliers: list[str | None] = []
        self.statuses: list[str | None] = []
        self.issues: list[Issue] = []
        self.problems: list[Problem] = []
        self.used_rows: set[str] = set()

    def add(
        self, supplier: str | None, found: Issue | None = None, problem: Problem | None = None
    ) -> None:
        self.suppliers.append(supplier)
        gap = found is not None and found.status is IssueStatus.data_gap
        status = LinkStatus.linked if supplier else LinkStatus.data_gap if gap else None
        self.statuses.append(status.value if status is not None else None)
        if found is not None:
            self.issues.append(found)
        if problem is not None:
            self.problems.append(problem)

    def decide(self, line: Line, row: SupplierRow, known: Container[str]) -> None:
        """What a row of the supplier mapping gives a line. The issue keeps who decided and when:
        resolved as linked, or as a data gap, or open when the row names a process that is not
        there."""
        self.used_rows.add(row.decision.source)
        decision = row.decision
        if row.supplier is None:
            assert row.reason is not None
            self.add(None, gap_issue(line, DataGap(row.reason, decision.who, decision.when)))
        elif row.supplier in known:
            linked = issue(
                line,
                IssueStatus.linked,
                supplier_id=row.supplier,
                reason=decision.comment,
                resolved_by=decision.who,
                resolved_at=decision.when,
            )
            self.add(row.supplier, linked)
        else:
            missing = Problem(
                "mapping-target-missing",
                label(line),
                f"{decision.source} sends it to {row.supplier}, which is not a process of the "
                "source. It is an open issue.",
            )
            self.add(None, issue(line, IssueStatus.open), missing)

    def linked(
        self,
        lines: pa.Table,
        decided: Any,
        suppliers: Any,
        statuses: Any,
        rows: Mapping[Any, SupplierRow],
        unused_gaps: tuple[str, ...] = (),
    ) -> Linked:
        """`lines` with the rows where `decided` is true given these outcomes, in order."""
        return Linked(
            lines=with_links(
                lines,
                _replace(suppliers, decided, self.suppliers),
                _replace(statuses, decided, self.statuses),
            ),
            issues=tuple(sorted(self.issues, key=lambda i: (i.process_id, i.line_id))),
            problems=tuple(sorted(self.problems, key=lambda p: p.item)),
            unused_gaps=unused_gaps,
            unused_rows=tuple(r for r in rows.values() if r.decision.source not in self.used_rows),
        )


def with_links(lines: pa.Table, supplier: Any, status: Any) -> pa.Table:
    """`lines` with these supplier and link status columns, Arrow arrays, and without the
    supplier name columns."""
    kept = [c for c in lines.column_names if c not in SUPPLIER_KEY and c != "link_status"]
    table = lines.select(kept)
    index = table.column_names.index("supplier_id")
    table = table.set_column(index, "supplier_id", supplier)
    return table.append_column("link_status", status)


def _replace(values: Any, mask: Any, replacements: list[str | None]) -> Any:
    """`values` with the rows where `mask` is true replaced, in order, by `replacements`."""
    if not replacements:
        return values
    return compute.replace_with_mask(
        _whole(values), _whole(mask), pa.array(replacements, pa.string())
    )


def _whole(array: Any) -> Any:
    """One array: `replace_with_mask` takes no chunks. A single chunk is used as it is, since
    `combine_chunks` would copy it."""
    chunked: Any = pa.ChunkedArray
    if not isinstance(array, chunked):
        return array
    return array.chunk(0) if array.num_chunks == 1 else array.combine_chunks()
