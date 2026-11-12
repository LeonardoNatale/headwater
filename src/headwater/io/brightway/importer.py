"""Import a database of a Brightway project: read it, link it to a release, write the study, read
it back and validate it."""

from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import headwater as hw
from headwater.io.brightway.project import BrightwayProject
from headwater.io.brightway.reader import ProjectDatabase, read_database
from headwater.io.excel.convert import Conversion, convert
from headwater.io.excel.importer import (
    ExcelImport,
    _method,  # pyright: ignore[reportPrivateUsage]
    _studies,  # pyright: ignore[reportPrivateUsage]
)
from headwater.io.excel.index import ReleaseIndex
from headwater.io.excel.linking import Linker
from headwater.io.excel.mapping import DatabaseDecision, ImportMapping, MappingRow, read_mapping
from headwater.io.excel.records import database_name
from headwater.io.excel.report import Note
from headwater.io.excel.write import check_target, write_database
from headwater.model import InvalidDataError, Report
from headwater.query._internals import internals
from headwater.release import Release
from headwater.study import read_database as read_headwater_database


def import_brightway(
    project: str,
    database: str,
    release: Release,
    out: Path | str,
    *,
    brightway_dir: Path | str | None = None,
    mapping: Path | str | None = None,
    name: str | None = None,
    method: Path | str | Sequence[Path | str] | None = None,
) -> ExcelImport:
    """Convert the database `database` of the Brightway project `project` into the study folder
    `out`, linked to `release`.

    `brightway_dir` is the folder that holds Brightway's projects: by default `BRIGHTWAY2_DIR`,
    or where bw2data keeps them. The project's files are only read. Each line is linked by the
    node its exchange comes from: an activity of the database, a process of the release with that
    node's name, reference product and location, a flow of nature with its name and categories,
    or a process of the study of another database of the project, in the folder named after it
    next to `out`, which the study then uses. `mapping`, `name` and `method` are those of
    `import_excel`, and the result is an `ExcelImport`, as there.

    Raises `InvalidDataError`, and writes nothing, for a project or database that is not there or
    not stored as bw2data 4.5.4 to 4.7 stores it, a record that holds an object of a class, an
    exchange whose key leads to no node of the project, and what `import_excel` refuses.
    """
    earlier = check_target(out)
    with BrightwayProject(project, brightway_dir) as opened:
        read = read_database(opened, database)
    decisions = read_mapping(mapping) if mapping is not None else ImportMapping()
    index = ReleaseIndex(release)
    decisions, waiting = _siblings(decisions, read, index, Path(out), project)
    method_id, load_as = _method(release, method)
    background = hw.load(release.path, method=load_as) if load_as is not None else None
    studies = _studies(Path(out), decisions, f"{release.name}@{release.hash[:12]}")
    conversion = convert(read.book, index, decisions, study=name, method=method_id, studies=studies)
    conversion = _with_notes(conversion, read, waiting)
    source = f"Brightway project {project}, database {database}"
    conversion = earlier.merged(conversion.database, source).apply(conversion)
    folder = write_database(conversion, out, workbook=source)
    opened_study = hw.Database(read_headwater_database(folder))  # what was written must read
    if background is None:
        skipped = (
            f"release {release.name} holds no method, or several. Pass --method (a method folder, "
            "or the id of a method the release holds) to validate the database."
        )
        return ExcelImport(folder, conversion, None, skipped, earlier.replaced)
    problems = list(opened_study.validate(background).problems)
    try:
        internals(background).require_pin(
            opened_study.folder.database.background, conversion.database.name
        )
    except InvalidDataError as error:
        problems.extend(error.problems)
    return ExcelImport(folder, conversion, Report(tuple(problems)), None, earlier.replaced)


def _siblings(
    decisions: ImportMapping, read: ProjectDatabase, index: ReleaseIndex, out: Path, project: str
) -> tuple[ImportMapping, list[str]]:
    """The mapping, with each other database of the project that lines buy from sent to the
    study of its name next to `out`, unless the mapping file decides it, or it is the release.
    Also the databases sent to a study whose folder is not there yet."""
    linker = Linker(read.book, index, decisions, {}, [], {}, {})
    added: dict[str, DatabaseDecision] = {}
    waiting: list[str] = []
    for database in read.suppliers:
        kind, _ = linker.database_kind(database)
        if kind != "foreign":
            continue
        study = database_name(database)
        row = MappingRow(
            type="database",
            name=database,
            reference_product=None,
            location=None,
            categories=(),
            target=study,
            who="headwater",
            when=datetime(1970, 1, 1, tzinfo=UTC),
            comment=None,
            source=f"Brightway project {project}",
        )
        added[database] = DatabaseDecision("database", study, row)
        if not (out.resolve().parent / study / "database.yaml").is_file():
            waiting.append(database)
    if not added:
        return decisions, waiting
    return replace(decisions, databases={**added, **decisions.databases}), waiting


def _with_notes(conversion: Conversion, read: ProjectDatabase, waiting: list[str]) -> Conversion:
    notes: list[Note] = list(read.notes)
    for database in waiting:
        notes.append(
            Note(
                f"database {database!r} of the project is read as the database "
                f"{database_name(database)}, in the folder of that name next to this one, "
                "which is not there yet: import it there first. If it is the background under "
                f"another name, add `database,{database},,,,release,<who>,<when>` to the mapping "
                "file."
            )
        )
    report = replace(conversion.report, notes=(*conversion.report.notes, *notes))
    return replace(conversion, report=report)
