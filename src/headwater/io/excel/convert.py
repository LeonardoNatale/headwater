"""Turn a Brightway workbook into the records of a study, and say what happened to each row.

The work is done by four parts, each in its own module, and this one puts them together and
writes the report:

1. `activities`: each activity becomes the head of a process, with an id of its own.
2. `parameters`: project, database and activity parameters become one `ParameterSet`.
3. `linking`: who supplies a line, and which flow of nature it is. Nothing is guessed.
4. `lines`: each row of an activity becomes a line, with a role, an amount, and a supplier or a
   reason it has none.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from headwater.io.excel.activities import ActivityReader
from headwater.io.excel.index import ReleaseIndex, Supplier
from headwater.io.excel.lines import LineReader
from headwater.io.excel.linking import Linker
from headwater.io.excel.mapping import ImportMapping
from headwater.io.excel.parameters import ParameterReader
from headwater.io.excel.records import Draft, Head, database_name
from headwater.io.excel.report import (
    DatabaseUse,
    ImportReport,
    Note,
    Outcome,
)
from headwater.io.excel.workbook import BrightwayWorkbook
from headwater.io.files import one_line
from headwater.model import (
    Exchange,
    Flow,
    InvalidDataError,
    Parameter,
    Problem,
    Process,
    ProcessKind,
)
from headwater.model.database import DATABASE_NAME


@dataclass(frozen=True)
class DatabaseDraft:
    """What `database.yaml` will say."""

    name: str
    background: str
    methods: tuple[str, ...]
    """The ids of the methods the import was given, if any."""
    uses: tuple[str, ...]


@dataclass(frozen=True)
class Conversion:
    """A database as records, not yet written, and the report of how it was made."""

    processes: tuple[Process, ...]
    flows: tuple[Flow, ...]
    parameters: tuple[Parameter, ...]
    database: DatabaseDraft
    comments: dict[str, tuple[str, ...]]
    """For each process id, lines for the top of its YAML file: the workbook's comment, and where
    the activity was read."""
    report: ImportReport


def convert(
    book: BrightwayWorkbook,
    index: ReleaseIndex,
    mapping: ImportMapping | None = None,
    *,
    study: str | None = None,
    method: str | tuple[str, ...] | None = None,
    studies: Mapping[str, Sequence[Supplier]] | None = None,
) -> Conversion:
    """Link every line of `book` to `index`'s release, and build the study's records.

    `studies` holds the processes of the study folders that the mapping file names and that
    exist, by folder name: the lines of those databases link to them, and the study uses them.

    Nothing is dropped for being unmapped. A line that does not link is written with no supplier,
    and the report says why. Raises `InvalidDataError` for a study name that is not one, and when
    no activity of the workbook can become a process.
    """
    name = study if study is not None else database_name(book.database)
    if not re.fullmatch(DATABASE_NAME, name):
        raise InvalidDataError(
            [
                Problem(
                    "bad-database-name",
                    f"database {name}",
                    "a database name starts with a letter or digit and holds letters, digits, '.', "
                    "'_' and '-' only",
                )
            ]
        )
    return _Converter(book, index, mapping or ImportMapping(), name, method, studies or {}).run()


class _Converter:
    def __init__(
        self,
        book: BrightwayWorkbook,
        index: ReleaseIndex,
        mapping: ImportMapping,
        study: str,
        method: str | tuple[str, ...] | None,
        studies: Mapping[str, Sequence[Supplier]],
    ) -> None:
        self.book = book
        self.index = index
        self.mapping = mapping
        self.study = study
        self.method = method
        self.studies = studies
        self.activities = ActivityReader(book, index.units, study)
        self.parameter_reader = ParameterReader(book, self.activities.heads, index.units)
        self.linker = Linker(
            book,
            index,
            mapping,
            studies,
            self.activities.heads,
            self.activities.skipped_names,
            self.activities.skipped_sources,
        )
        self.lines = LineReader(
            index.units,
            study,
            self.linker,
            self.parameter_reader,
            self.activities.flows,
        )

    def run(self) -> Conversion:
        self.activities.read()
        heads = self.activities.heads
        if not heads:
            problems = [
                Problem(o.code, f"activity {o.activity}", o.message, location=o.source)
                for o in self.activities.skipped
            ] or [
                Problem(
                    "no-activities",
                    f"workbook {self.book.path.name}",
                    "has no Activity block, so there is nothing to convert",
                )
            ]
            raise InvalidDataError(problems)
        self.parameter_reader.read()
        drafts = {head.id: self.lines.lines(head) for head in heads}
        processes = [
            Process(
                id=head.id,
                name=one_line(head.activity.name),
                location=head.location,
                kind=ProcessKind.ordinary_transforming_activity,
                exchanges=tuple(self._exchanges(head, drafts[head.id])),
            )
            for head in heads
        ]
        draft = self._study()
        return Conversion(
            processes=tuple(processes),
            flows=tuple(sorted(self.activities.flows.values(), key=lambda f: f.id)),
            parameters=tuple(self.parameter_reader.parameters),
            database=draft,
            comments={h.id: self._comment_lines(h) for h in heads},
            report=self._report(draft),
        )

    def _exchanges(self, head: Head, drafts: list[tuple[Draft, Outcome | None]]) -> list[Exchange]:
        """The lines of a process with their line ids, `e01` to `e99` and on, one width so that
        they sort as they read."""
        width = max(2, len(str(len(drafts))))
        exchanges: list[Exchange] = []
        for n, (draft, outcome) in enumerate(drafts, 1):
            line_id = f"e{n:0{width}d}"
            exchanges.append(
                Exchange(
                    process_id=head.id,
                    line_id=line_id,
                    role=draft.role,
                    flow_id=draft.flow_id,
                    flow_name=draft.flow_name,
                    amount=draft.amount,
                    formula=draft.formula,
                    unit=draft.unit,
                    supplier_id=draft.supplier_id,
                    comment=draft.comment,
                    uncertainty=draft.uncertainty,
                )
            )
            if outcome is not None:
                self.activities.by_activity[head.activity.source].append(
                    replace(outcome, process_id=head.id, line_id=line_id)
                )
        return exchanges

    def _comment_lines(self, head: Head) -> tuple[str, ...]:
        comment = head.activity.text("comment")
        lines = [" ".join(part.split()) for part in (comment or "").splitlines() if part.strip()]
        lines.append(f"Read from {head.activity.source} of {self.book.path.name}.")
        return tuple(lines)

    def _study(self) -> DatabaseDraft:
        release = self.index.release
        methods = (self.method,) if isinstance(self.method, str) else (self.method or ())
        return DatabaseDraft(
            name=self.study,
            background=f"{release.name}@{release.hash[:12]}",
            methods=methods,
            uses=tuple(self.linker.uses),
        )

    def _report(self, draft: DatabaseDraft) -> ImportReport:
        outcomes = tuple(
            o for a in self.book.activities for o in self.activities.by_activity[a.source]
        )
        notes = [*self.activities.notes, *self.parameter_reader.notes, *self.lines.notes]
        for sheet, why in self.book.skipped_sheets:
            notes.append(Note(f"sheet {sheet!r} was not read: {why}"))
        for column, rows in sorted(self.book.ignored_columns.items()):
            notes.append(
                Note(f"column {column!r} is not used by headwater: {rows} rows have a value in it")
            )
        databases = tuple(
            DatabaseUse(name or "(none)", rows, self._treated_as(name or None, notes))
            for name, rows in sorted(self.lines.database_rows.items())
        )
        unused = tuple(
            f"{row.source}: {row.type} {row.name}"
            for row in self.mapping.rows
            if row.source not in self.linker.used_mapping
        )
        release = self.index.release
        return ImportReport(
            workbook=self.book.path.name,
            source_database=self.book.database,
            name=self.study,
            release=f"{release.name}@{release.hash[:12]}",
            activities=len(self.book.activities),
            exchange_rows=self.book.exchange_rows,
            outcomes=outcomes,
            parameter_rows=self.book.parameter_rows,
            parameter_outcomes=tuple(self.parameter_reader.parameter_outcomes),
            skipped_activities=tuple(self.activities.skipped),
            notes=tuple(notes),
            databases=databases,
            uses=draft.uses,
            unused_mapping=unused,
            mapped_by=dict(self.lines.mapped_by),
        )

    def _treated_as(self, name: str | None, notes: list[Note]) -> str:
        kind, decision = self.linker.database_kind(name)
        info = self.index.release.info
        if kind == "own":
            return "this workbook's database"
        if kind == "release":
            found = re.search(r"\d+(?:\.\d+)+", name or "")
            if found and info.source == "ecoinvent" and found.group() != info.version:
                notes.append(
                    Note(
                        f"database {name!r} names version {found.group()}, but the release is "
                        f"{self.index.release.name} (version {info.version}). Names change "
                        "between versions: lines that do not link may be renames."
                    )
                )
            return f"the background, release {self.index.release.name}"
        if kind == "database" and decision is not None:
            if decision.study in self.studies:
                return f"the database {decision.study}, in the folder next to this one"
            return f"the database {decision.study}, which has no folder next to this one yet"
        if kind == "blank":
            return "not given: looked up in this workbook first, then in the release"
        return "not resolved: another foreground database. Name its folder in the mapping file"
