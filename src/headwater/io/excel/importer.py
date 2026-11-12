"""Import a workbook: read it, link it to a release, write the study, read it back and validate."""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import headwater as hw
from headwater.io.excel.convert import Conversion, convert
from headwater.io.excel.index import ReleaseIndex, Supplier
from headwater.io.excel.mapping import ImportMapping, read_mapping
from headwater.io.excel.report import ImportReport, Status
from headwater.io.excel.workbook import read_workbook
from headwater.io.excel.write import check_target, write_database
from headwater.model import Flow, FlowKind, InvalidDataError, Problem, Process, Report, Severity
from headwater.query._internals import internals
from headwater.release import Release
from headwater.study import read_database, read_method


@dataclass(frozen=True)
class ExcelImport:
    """The result of an import: the study folder, and what became of the workbook's rows."""

    folder: Path
    conversion: Conversion
    validation: Report | None
    """`Study.validate` on the written study, or None when it could not run."""
    validation_skipped: str | None = None
    """Why validation did not run."""
    replaced: bool = False
    """The folder held the unedited files of an earlier import, and they were replaced."""

    @property
    def report(self) -> ImportReport:
        return self.conversion.report

    def unexplained(self) -> tuple[Problem, ...]:
        """The validation problems that are not just something the report already lists: a line
        this import could not map (a line with no supplier, and the flow made up for it)."""
        if self.validation is None:
            return ()
        listed = {
            f"{o.process_id} line {o.line_id}"
            for o in self.report.outcomes
            if o.status is not Status.mapped and o.process_id is not None
        }
        placeholders = {
            f"flow {flow.id}" for flow in self.conversion.flows if "unmapped-" in flow.id
        }
        listed |= placeholders
        return tuple(p for p in self.validation.problems if p.item not in listed)

    def blocking(self) -> tuple[Problem, ...]:
        """What stops `calc`: the errors of validation, and the flows of the study that the method
        has no factor for, whether validation gives them as an error or a warning. The author
        chose the flow, so the method may be the wrong one, the line may not be needed, or 0 is
        right and a row of `no_factor.csv` says so."""
        if self.validation is None:
            return ()
        return tuple(
            p
            for p in self.validation.problems
            if p.severity is Severity.error or p.code == "no-factor"
        )

    @property
    def clean(self) -> bool:
        """Every line mapped, every formula copied, and a study that validates and that `calc`
        will not refuse."""
        return self.report.clean and self.validation is not None and not self.blocking()


def import_excel(
    workbook: Path | str,
    release: Release,
    out: Path | str,
    *,
    mapping: Path | str | None = None,
    name: str | None = None,
    method: Path | str | Sequence[Path | str] | None = None,
) -> ExcelImport:
    """Convert a Brightway-format workbook into the database folder `out`, linked to `release`.

    `mapping` is a reviewed mapping file for the lines that do not match by themselves. `name`
    is the database's name, by default the workbook's database. `method` is a method folder, or
    the id of a method the release holds, to validate against and to name in `database.yaml`, or
    a list of them used together; it may be left out when the release holds one method. Lines that
    cannot be mapped are written with no supplier and reported, never dropped.

    `out` may hold the unedited files of an earlier import, which are replaced: import, decide in
    the mapping file, import again. Raises `InvalidDataError`, and writes nothing, for a workbook or
    mapping file that is wrong, an `out` with files that no import wrote or that were edited since,
    and a `method` that is not found or does not fit the release.
    """
    earlier = check_target(out)  # before the slow steps below
    book = read_workbook(workbook)
    decisions: ImportMapping = read_mapping(mapping) if mapping is not None else ImportMapping()
    index = ReleaseIndex(release)
    method_id, load_as = _method(release, method)
    # Load the background first: a method that does not fit the release is refused before
    # anything is written.
    background = hw.load(release.path, method=load_as) if load_as is not None else None
    studies = _studies(Path(out), decisions, f"{release.name}@{release.hash[:12]}")
    conversion = convert(book, index, decisions, study=name, method=method_id, studies=studies)
    source = f"{Path(workbook).name} (Brightway Excel format)"
    conversion = earlier.merged(conversion.database, source).apply(conversion)
    folder = write_database(conversion, out, workbook=source)
    opened = hw.Database(read_database(folder))  # what was written must read
    if background is None:
        skipped = (
            f"release {release.name} holds no method, or several. Pass --method (a method folder, "
            "or the id of a method the release holds) to validate the database."
        )
        return ExcelImport(folder, conversion, None, skipped, earlier.replaced)
    problems = list(opened.validate(background).problems)
    try:
        internals(background).require_pin(
            opened.folder.database.background, conversion.database.name
        )
    except InvalidDataError as error:
        problems.extend(error.problems)
    return ExcelImport(folder, conversion, Report(tuple(problems)), None, earlier.replaced)


def _studies(out: Path, decisions: ImportMapping, pin: str) -> dict[str, tuple[Supplier, ...]]:
    """The processes of each database folder that the mapping file names and that is next to
    `out`, by folder name. A folder that is not there yet is left out: its lines wait for it.

    Raises `InvalidDataError`, before anything is written, for one that does not read, whose
    database is not named like its folder, or that does not pin `pin`, the background the
    database written pins: the database written would not read."""
    found: dict[str, tuple[Supplier, ...]] = {}
    for decision in decisions.databases.values():
        name = decision.study
        folder = out.resolve().parent / name if name is not None else None
        if name is None or folder is None or not (folder / "database.yaml").is_file():
            continue
        used = read_database(folder)
        problems: list[Problem] = []
        if used.database.name != name:
            problems.append(
                Problem(
                    "used-database-name",
                    f"database {name}",
                    f"the folder {name} holds the database {used.database.name!r}. A database "
                    f"that another uses has the name of its folder: write name: {name} in its "
                    "database.yaml, or rename the folder.",
                    location=f"{folder}/database.yaml",
                )
            )
        if str(used.database.background) != pin:
            problems.append(
                Problem(
                    "uses-background",
                    f"database {name}",
                    f"pins background {used.database.background}, and the database written "
                    f"pins {pin}. A database and the databases it uses pin the same release, "
                    "written the same way.",
                    location=f"{folder}/database.yaml",
                )
            )
        if problems:
            raise InvalidDataError(problems)
        flows = {f.id: f for f in (flow for study in used.chain for flow in study.flows)}
        found[name] = tuple(
            _supplier(p, flows.get(p.reference_exchange.flow_id)) for p in used.processes
        )
    return found


def _supplier(process: Process, flow: Flow | None) -> Supplier:
    """A process of a study, as the workbook names it: its name, its product's name, where."""
    reference = process.reference_exchange
    return Supplier(
        id=process.id,
        name=process.name,
        product=flow.name if flow is not None else reference.flow_id,
        location=process.location,
        flow_id=reference.flow_id,
        kind=flow.kind if flow is not None else FlowKind.product,
        unit=flow.unit if flow is not None else reference.unit,
    )


def _method(
    release: Release, method: Path | str | Sequence[Path | str] | None
) -> tuple[str | tuple[str, ...] | None, list[Path | str] | None]:
    """The id to write in `database.yaml`, or the ids of several methods, and what to pass to
    `hw.load`, or None for both."""
    held = {m.id: m for m in release.methods()}
    wanted = [method] if isinstance(method, Path | str) else list(method or ())
    if not wanted:
        if len(held) == 1:
            (only,) = held
            return only, [only]
        return None, None
    ids: list[str] = []
    load_as: list[Path | str] = []
    for asked in wanted:
        if isinstance(asked, str) and asked in held:
            ids.append(asked)
            load_as.append(asked)
            continue
        folder = Path(asked)
        if not (folder / "method.yaml").is_file():
            raise InvalidDataError(
                [
                    Problem(
                        "method-not-found",
                        f"method {asked}",
                        "is neither the id of a method the release holds nor a method folder "
                        f"(one with method.yaml). The release holds: "
                        f"{', '.join(held) or 'no methods'}.",
                    )
                ]
            )
        ids.append(read_method(folder).id)
        load_as.append(folder)
    return (ids[0] if len(ids) == 1 else tuple(ids)), load_as
