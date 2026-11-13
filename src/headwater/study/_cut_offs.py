"""Reading `cut_offs.csv`: inputs a database leaves out on purpose, checked against
database.yaml."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from headwater.model import (
    CutOff,
    CutOffCriterion,
    Database,
    Exchange,
    Issue,
    IssueStatus,
    LinkStatus,
    Problem,
    Process,
    Role,
    did_you_mean,
)
from headwater.study._files import (
    Report,
    Row,
    problems_from_validation,
    read_csv,
    read_number,
    read_when,
)

FILE = "cut_offs.csv"
COLUMNS = ("process", "line", "flow", "criterion", "share", "reason", "who", "when")
ROLES_THAT_CAN_BE_CUT_OFF = (Role.input, Role.waste_to_treatment)
_EPSILON = 1e-12


@dataclass(frozen=True)
class _Entry:
    position: tuple[str, int]  # the process, and where the line is in it
    exchange: Exchange
    row: Row
    criterion: CutOffCriterion
    share: float
    reason: str
    who: str
    when: datetime


def read_cut_offs(
    root: Path, database: Database, processes: tuple[Process, ...], problems: list[Problem]
) -> tuple[tuple[Issue, ...], tuple[Process, ...]]:
    """The cut-offs as issues, and the processes with those lines marked as cut off.

    The cumulative share is worked out here, by criterion, in the order of the lines: the file
    holds the share of each line only. It must stay within `max_cumulative` of database.yaml for
    every criterion.
    """
    path = root / FILE
    if not path.is_file():
        return (), processes
    rows = read_csv(path, root, required=COLUMNS, problems=problems)
    if not rows:
        return (), processes
    cut_off = database.cut_off
    if cut_off is None:
        problems.append(
            Problem(
                "cut-off-no-limit",
                FILE,
                f"has {len(rows)} row{'s' if len(rows) > 1 else ''}, and database.yaml has no "
                "cut_off to check them against. Add the criteria and the largest share that may "
                "be left out in all, such as `cut_off: {criteria: [mass, energy], "
                "max_cumulative: 0.05}`.",
                location=FILE,
            )
        )
        return (), processes
    by_id = {p.id: p for p in processes}
    entries: list[_Entry] = []
    seen: dict[tuple[str, str], str] = {}
    for row in rows:
        entry = _entry(row, by_id, cut_off, seen, problems)
        if entry is not None:
            entries.append(entry)
    entries.sort(key=lambda e: e.position)
    totals: dict[CutOffCriterion, float] = defaultdict(float)
    cumulative: list[float] = []
    for entry in entries:
        totals[entry.criterion] += entry.share
        cumulative.append(totals[entry.criterion])
    limit = cut_off.max_cumulative
    over = [c for c in sorted(totals, key=lambda c: c.value) if totals[c] > limit + _EPSILON]
    for criterion in over:
        lines = ", ".join(
            f"{e.exchange.label} ({e.share:.4g})" for e in entries if e.criterion is criterion
        )
        problems.append(
            Problem(
                "cut-off-over-limit",
                f"{FILE} criterion {criterion.value}",
                f"the cut-offs add up to {totals[criterion]:.4g} of {criterion.value}, above "
                f"the {limit:.4g} that database.yaml allows (max_cumulative). Lines: {lines}",
                location=FILE,
            )
        )
    if over:
        return (), processes
    issues: list[Issue] = []
    for entry, running in zip(entries, cumulative, strict=True):
        try:
            issues.append(
                Issue(
                    process_id=entry.exchange.process_id,
                    line_id=entry.exchange.line_id,
                    flow_id=entry.exchange.flow_id,
                    status=IssueStatus.cut_off,
                    reason=entry.reason,
                    criterion=entry.criterion,
                    share=entry.share,
                    cumulative_share=running,
                    resolved_by=entry.who,
                    resolved_at=entry.when,
                )
            )
        except ValidationError as error:
            problems.extend(
                problems_from_validation(
                    error, item=entry.exchange.label, location=entry.row.location
                )
            )
    return tuple(issues), _mark_cut_off(processes, {e.exchange.label for e in entries})


def _entry(
    row: Row,
    by_id: dict[str, Process],
    cut_off: CutOff,
    seen: dict[tuple[str, str], str],
    problems: list[Problem],
) -> _Entry | None:
    process_id, line_id = row.get("process"), row.get("line")
    item = f"{process_id} line {line_id}"

    def problem(code: str, message: str) -> None:
        problems.append(Problem(code, item, message, location=row.location))

    process = by_id.get(process_id)
    if process is None:
        problem(
            "cut-off-unknown-line",
            f"process {process_id!r} is not a process of the database."
            f"{did_you_mean(process_id, by_id)}",
        )
        return None
    lines = [e.line_id for e in process.exchanges]
    if line_id not in lines:
        problem(
            "cut-off-unknown-line",
            f"{process_id} has no line {line_id!r}.{did_you_mean(line_id, lines)} "
            f"Its lines: {', '.join(lines)}",
        )
        return None
    exchange = process.exchanges[lines.index(line_id)]
    if (process_id, line_id) in seen:
        problem("cut-off-duplicate", f"is already cut off at {seen[(process_id, line_id)]}")
        return None
    seen[(process_id, line_id)] = row.location
    if exchange.role not in ROLES_THAT_CAN_BE_CUT_OFF:
        allowed = " and ".join(r.value for r in ROLES_THAT_CAN_BE_CUT_OFF)
        problem(
            "cut-off-role",
            f"is a {exchange.role.value} line. Only {allowed} lines can be cut off.",
        )
        return None
    if exchange.supplier_id is not None:
        problem(
            "cut-off-supplier",
            f"has the supplier {exchange.supplier_id!r}, so it is not left out. Clear the "
            "supplier in exchanges.csv, or remove this row.",
        )
        return None
    if row.get("flow") != exchange.flow_id:
        problem(
            "cut-off-flow",
            f"the line buys {exchange.flow_id!r}, but this row says {row.get('flow')!r}",
        )
        return None
    criterion = _criterion(row.get("criterion"), cut_off, problem)
    if criterion is None:
        return None
    empty = [c for c in ("reason", "who", "when") if not row.get(c)]
    for column in empty:
        problem("invalid-field", f"{column} is empty. Say {_WHAT[column]}.")
    share = read_number(row, "share", item, problems)
    if share is not None and not 0 <= share <= 1:
        problem("invalid-field", f"share {share:g} must be between 0 and 1")
        share = None
    when = None if "when" in empty else read_when(row.get("when"), problem, naive=True)
    if empty or share is None or when is None:
        return None
    return _Entry(
        position=(process_id, lines.index(line_id)),
        exchange=exchange,
        row=row,
        criterion=criterion,
        share=share,
        reason=row.get("reason"),
        who=row.get("who"),
        when=when,
    )


WHO_AND_WHEN = {"who": "who decided it", "when": "when, as 2026-10-11"}
"""What to write in an empty `who` or `when`, here and in no_factor.csv."""

_WHAT = {"reason": "why it is left out", **WHO_AND_WHEN}


def _criterion(text: str, cut_off: CutOff, report: Report) -> CutOffCriterion | None:
    options = [c.value for c in CutOffCriterion]
    try:
        criterion = CutOffCriterion(text)
    except ValueError:
        report(
            "cut-off-criterion",
            f"unknown criterion {text!r}.{did_you_mean(text, options)} "
            f"Criteria: {', '.join(options)}",
        )
        return None
    if criterion not in cut_off.criteria:
        used = ", ".join(c.value for c in cut_off.criteria)
        report(
            "cut-off-criterion",
            f"{text!r} is not a criterion of this database. database.yaml lists only {used}.",
        )
        return None
    return criterion


def _mark_cut_off(processes: tuple[Process, ...], labels: set[str]) -> tuple[Process, ...]:
    """The processes where each line named in `labels` has the link status `cut_off`."""
    marked: list[Process] = []
    for process in processes:
        if not any(e.label in labels for e in process.exchanges):
            marked.append(process)
            continue
        marked.append(
            Process.model_validate(
                {
                    **process.model_dump(exclude={"exchanges"}),
                    "exchanges": [
                        Exchange.model_validate(
                            {**e.model_dump(), "link_status": LinkStatus.cut_off}
                        )
                        if e.label in labels
                        else e
                        for e in process.exchanges
                    ],
                }
            )
        )
    return tuple(marked)
