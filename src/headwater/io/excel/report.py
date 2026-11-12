"""What an import did with each row of the workbook, and how to say it.

Every exchange row has exactly one `Outcome`, so the counts add up to the rows read and nothing
is lost without a trace:

- `mapped`: written with its supplier or flow, and its formula if it had one.
- `unmapped`: could not be linked. It is still written, with no supplier and the workbook's own
  words in its comment, so the study does not validate until a person decides.
- `waiting`: buys from another foreground database, which the mapping file sends to a study folder
  that is not there yet. Written like an unmapped line.
- `unexpressible`: linked, but its formula is not in headwater's syntax. The amount is kept as the
  number Excel stored, and the formula is in the line's comment.
"""

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum


class Status(StrEnum):
    mapped = "mapped"
    unmapped = "unmapped"
    waiting = "waiting"
    unexpressible = "unexpressible"


@dataclass(frozen=True)
class Outcome:
    """What became of one row of an `Exchanges` table."""

    source: str
    """Where the row is: `inventory!A42`."""
    activity: str
    line: str
    """The row as a person finds it in the sheet: `name | reference product | location`."""
    status: Status
    code: str
    """Why, as a stable word: `release`, `not-in-release`, `negative-to-product`..."""
    message: str
    suggestions: tuple[str, ...] = ()
    """Processes or flows a person might have meant. Only to read, never applied."""
    database: str | None = None
    """For a `waiting` line: the database folder it will buy from, `textile-chemicals`."""
    by_name: str | None = None
    """For a line linked with no reference product or no location, because exactly one process
    fits: the supplier, and what the workbook left out. Accepted, and listed to be reviewed."""
    process_id: str | None = None
    line_id: str | None = None
    """The line this row became in the database, when it was written."""


@dataclass(frozen=True)
class ParameterOutcome:
    """What became of one row of a table of parameters."""

    source: str
    name: str
    scope: str | None
    copied: bool
    code: str
    message: str


@dataclass(frozen=True)
class Note:
    """Something a person should know that is not a failure: a formula that disagrees with the
    number next to it, a column that was not read, a skipped sheet."""

    message: str
    source: str | None = None


@dataclass(frozen=True)
class DatabaseUse:
    """A database the workbook's lines name, and what the import took it for."""

    name: str
    rows: int
    treated_as: str


@dataclass(frozen=True)
class ImportReport:
    """The report of one import."""

    workbook: str
    source_database: str
    """The workbook's own name for its database, as Brightway names it: `textiles`."""
    name: str
    """The name of the headwater database written, the prefix of its process ids: `textiles`."""
    release: str
    activities: int
    exchange_rows: int
    outcomes: tuple[Outcome, ...]
    parameter_rows: int
    parameter_outcomes: tuple[ParameterOutcome, ...]
    skipped_activities: tuple[Outcome, ...] = ()
    """One per activity that was not converted, with the reason."""
    notes: tuple[Note, ...] = ()
    databases: tuple[DatabaseUse, ...] = ()
    uses: tuple[str, ...] = ()
    unused_mapping: tuple[str, ...] = ()
    """Rows of the mapping file that no line of the workbook needed."""
    mapped_by: Mapping[str, int] = field(default_factory=dict[str, int])
    """How the mapped lines were linked, by code."""

    def count(self, status: Status) -> int:
        return sum(1 for o in self.outcomes if o.status is status)

    @property
    def linked_by_name(self) -> tuple[Outcome, ...]:
        """The lines linked on less than name, reference product and location, to be reviewed."""
        return tuple(o for o in self.outcomes if o.by_name is not None)

    @property
    def parameters_copied(self) -> int:
        return sum(1 for p in self.parameter_outcomes if p.copied)

    @property
    def parameters_failed(self) -> tuple[ParameterOutcome, ...]:
        return tuple(p for p in self.parameter_outcomes if not p.copied)

    @property
    def clean(self) -> bool:
        """Every line was mapped and every formula and parameter was copied. Notes do not count:
        they are for reading."""
        return (
            all(o.status is Status.mapped for o in self.outcomes)
            and not self.skipped_activities
            and not self.parameters_failed
        )

    def codes(self, status: Status) -> Counter[str]:
        return Counter(o.code for o in self.outcomes if o.status is status)


def format_report(report: ImportReport) -> str:
    """The report as text for a person: counts first, then every line that needs a decision, with
    where it is in the workbook, why, and what it might have been."""
    lines = [
        f"Read {report.workbook}: database {report.source_database}, {report.activities} "
        "activities, "
        f"{report.exchange_rows} exchange rows, {report.parameter_rows} parameter rows.",
        f"Linked to release {report.release}. Database {report.name}.",
        "",
        "Lines",
    ]
    how = ", ".join(f"{code} {n}" for code, n in sorted(report.mapped_by.items()))
    mapped = report.count(Status.mapped)
    lines.append(f"  mapped         {mapped:>5}" + (f"  ({how})" if how else ""))
    if report.linked_by_name:
        lines.append(f"  linked by name only, to review: {len(report.linked_by_name)}")
    for status in (Status.unmapped, Status.waiting, Status.unexpressible):
        codes = ", ".join(f"{c} {n}" for c, n in sorted(report.codes(status).items()))
        lines.append(
            f"  {status.value:<14} {report.count(status):>5}" + (f"  ({codes})" if codes else "")
        )
    if report.databases:
        lines += ["", "Databases named by the lines"]
        for db in report.databases:
            lines.append(f"  {db.name}: {_lines(db.rows)}, {db.treated_as}")
    sections: list[tuple[str, list[Outcome], str]] = [
        (
            "Linked by name only",
            [
                replace(
                    o, status=Status.mapped, code="by-name-only", message=f"linked to {o.by_name}"
                )
                for o in report.linked_by_name
            ],
            "to review",
        ),
        (
            "Not mapped",
            [o for o in report.outcomes if o.status is Status.unmapped],
            "to decide",
        ),
    ]
    waited = sorted({o.database or "" for o in report.outcomes if o.status is Status.waiting})
    sections += [
        (
            f"Waiting for database {database}",
            [
                o
                for o in report.outcomes
                if o.status is Status.waiting and (o.database or "") == database
            ],
            "to decide",
        )
        for database in waited
    ]
    sections.append(
        (
            "Formulas not copied",
            [o for o in report.outcomes if o.status is Status.unexpressible],
            "to decide",
        )
    )
    for title, found, verb in sections:
        if not found:
            continue
        groups = _groups(found)
        lines += ["", f"{title}: {_lines(len(found))}, {len(groups)} {verb}"]
        for group in groups:
            lines += _group_lines(group)
    if report.skipped_activities:
        lines += ["", f"Activities not converted: {len(report.skipped_activities)}"]
        lines += [f"- [{o.code}] {o.source}: {o.message}" for o in report.skipped_activities]
    if report.parameter_rows:
        lines += [
            "",
            f"Parameters: {report.parameters_copied} copied, "
            f"{len(report.parameters_failed)} not copied",
        ]
        lines += [
            f"- [{p.code}] {p.source}, {p.name}: {p.message}" for p in report.parameters_failed
        ]
    if report.uses:
        lines += ["", "uses: " + ", ".join(report.uses)]
    if report.unused_mapping:
        lines += ["", "Mapping rows that no line needed:"]
        lines += [f"- {row}" for row in report.unused_mapping]
    if report.notes:
        lines += ["", "Notes"]
        lines += [
            f"- {n.source}: {n.message}" if n.source else f"- {n.message}" for n in report.notes
        ]
    return "\n".join(lines)


def _lines(n: int) -> str:
    return f"{n} line{'' if n == 1 else 's'}"


def _groups(outcomes: list[Outcome]) -> list[list[Outcome]]:
    """Lines that say the same thing, together: the same line as the workbook writes it, and the
    same reason. A mapping file decides each group once."""
    by_key: dict[tuple[str, str, str], list[Outcome]] = {}
    for o in outcomes:
        by_key.setdefault((o.code, o.line, o.message), []).append(o)
    return sorted(by_key.values(), key=lambda g: (g[0].code, -len(g), g[0].source))


def _group_lines(group: list[Outcome]) -> list[str]:
    first = group[0]
    activities = sorted({o.activity for o in group})
    where = f"in {activities[0]}" if len(activities) == 1 else f"in {len(activities)} activities"
    lines = [
        f"- [{first.code}] {first.line}  ({_lines(len(group))}, {where})",
        f"    {first.message}",
    ]
    lines += [f"    maybe: {s}" for s in first.suggestions]
    sources = [o.source for o in group]
    for start in range(0, len(sources), 8):
        lines.append("    at " + ", ".join(sources[start : start + 8]))
    return lines
