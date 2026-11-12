"""Read database folders, background folders and methods into records.

Every reader collects all the problems it finds in the folder and raises one
`InvalidDataError` with the full list. None returns a partly read folder.
"""

import difflib
from dataclasses import dataclass, replace
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from headwater.model import (
    DEFAULT_UNITS,
    Database,
    Factor,
    Flow,
    FlowProperty,
    InvalidDataError,
    Method,
    NwRow,
    NwSet,
    Problem,
    Process,
    ProcessDetails,
    Unit,
    Units,
)
from headwater.model.database import Uses
from headwater.study._cut_offs import read_cut_offs
from headwater.study._exchanges import (
    EXCHANGE_COLUMNS,
    EXCHANGE_OPTIONAL,
    Formulas,
    read_exchange,
)
from headwater.study._files import (
    Row,
    blank_to_none,
    problems_from_validation,
    read_csv,
    read_number,
    read_yaml,
    relative,
)
from headwater.study._no_factor import read_no_factor
from headwater.study._overrides import build_overrides, read_override_rows
from headwater.study._parameters import read_parameters
from headwater.study._uses import Sibling, on_disk, read_used
from headwater.study.folders import DatabaseFolder, ProcessFolder

FLOW_COLUMNS = ("id", "name", "kind", "unit")
FLOW_OPTIONAL = ("compartment", "cas")
PROPERTY_COLUMNS = ("flow", "property", "value", "unit")
UNIT_COLUMNS = ("unit", "quantity", "factor_to_reference")
FACTOR_COLUMNS = ("indicator", "flow", "factor")
FACTOR_OPTIONAL = ("location",)
SET_COLUMNS = ("indicator",)
SET_OPTIONAL = ("normalisation", "weight")

# Record field names that differ from the column a person sees in the file.
_PROPERTY_COLUMN = {"flow_id": "flow"}
_FACTOR_COLUMN = {"flow_id": "flow"}


def read_database(path: Path | str, *, sibling: Sibling | None = None) -> DatabaseFolder:
    """Read a database folder, and the databases it uses.

    Reads `database.yaml` and `processes/*.csv`, and the optional `flows.csv`,
    `flow_properties.csv`, `units.csv`, `parameters.yaml`, `overrides/*.csv`, `cut_offs.csv`,
    `no_factor.csv`. A database holds no scenarios: they belong in a study (D46).

    Process ids are `<database name>:<file name without .csv>`. A process amount may be a formula
    of the database's parameters. Raises `InvalidDataError` naming every problem found.

    Each database named in `uses` is the folder of that name next to this one, read the same way,
    with the databases it uses in turn: they are `DatabaseFolder.used`. `sibling` finds such a
    folder by name, for a database read from somewhere other than this disk, such as a git
    commit.
    """
    root = Path(path)
    folder = _read_folder(root)
    if not folder.database.uses:
        return folder
    found = sibling if sibling is not None else on_disk(root)
    return replace(folder, used=read_used(folder, root.resolve().name, found, _read_folder))


def _read_folder(root: Path) -> DatabaseFolder:
    """One database folder, without the databases it uses."""
    _require_folder(root)
    problems: list[Problem] = []
    _check_entries(root, problems)
    database = _read_database_yaml(root, problems)
    namespace = database.name if database else root.name
    override_rows = read_override_rows(root, problems)
    process_ids = {f"{namespace}:{stem}" for stem in _process_stems(root)}
    parameters = read_parameters(
        root, process_ids | {r.get("process") for r in override_rows}, problems
    )
    formulas = Formulas(parameters)
    parts = _read_parts(root, namespace, formulas, problems)
    overrides, override_sources = build_overrides(override_rows, process_ids, formulas, problems)
    processes = parts.processes
    cut_offs = ()
    if database is not None:
        cut_offs, processes = read_cut_offs(root, database, processes, problems)
    no_factor = read_no_factor(root, problems)
    if problems or database is None or parameters is None:
        raise InvalidDataError(problems)
    return DatabaseFolder(
        path=root,
        namespace=database.name,
        processes=processes,
        flows=parts.flows,
        flow_properties=parts.properties,
        units=parts.units,
        database=database,
        parameters=parameters,
        overrides=overrides,
        cut_offs=cut_offs,
        override_sources=tuple(sorted(override_sources.items())),
        no_factor=no_factor,
    )


def read_uses(path: Path | str) -> tuple[str, ...]:
    """The databases that `database.yaml` in the folder `path` names in `uses`, without reading
    anything else of the folder: `("dyes",)`. Raises `InvalidDataError` when `database.yaml` or
    its `uses` does not read."""
    root = Path(path)
    problems: list[Problem] = []
    data = _read_required_yaml(root, DATABASE_YAML, problems)
    if data is None:
        raise InvalidDataError(problems)
    try:
        return _USES.validate_python(data.get("uses") or ())
    except ValidationError as error:
        raise InvalidDataError(
            problems_from_validation(error, prefix=f"{DATABASE_YAML}: uses")
        ) from None


_USES: TypeAdapter[tuple[str, ...]] = TypeAdapter(Uses)


def read_process_folder(path: Path | str, *, namespace: str | None = None) -> ProcessFolder:
    """Read a folder of process files that has no `database.yaml`, such as a background.

    Process ids are `<namespace>:<file name without .csv>`. The namespace defaults to the
    folder name. Raises `InvalidDataError`.
    """
    root = Path(path)
    _require_folder(root)
    problems: list[Problem] = []
    name = namespace or root.name
    parts = _read_parts(root, name, None, problems)
    if problems:
        raise InvalidDataError(problems)
    return ProcessFolder(
        path=root,
        namespace=name,
        processes=parts.processes,
        flows=parts.flows,
        flow_properties=parts.properties,
        units=parts.units,
    )


def read_method(path: Path | str) -> Method:
    """Read `method.yaml` (id, version, variant, flow list, indicators) and `factors.csv`."""
    root = Path(path)
    _require_folder(root)
    problems: list[Problem] = []
    data = _read_required_yaml(root, "method.yaml", problems)
    factors = _read_factors(root, problems)
    method: Method | None = None
    if data is not None:
        if "factors" in data:
            problems.append(
                Problem("invalid-field", "method.yaml: factors", "factors belong in factors.csv")
            )
            data.pop("factors")
        try:
            method = Method.model_validate({**data, "factors": factors})
        except ValidationError as error:
            problems.extend(problems_from_validation(error, prefix="method.yaml", model=Method))
    if problems or method is None:
        raise InvalidDataError(problems)
    return method


def read_set(path: Path | str) -> NwSet:
    """Read a normalisation and weighting set: `set.yaml` (id, version, the method it pins, unit
    and source) and `factors.csv` (indicator, normalisation, weight)."""
    root = Path(path)
    _require_folder(root)
    problems: list[Problem] = []
    data = _read_required_yaml(root, "set.yaml", problems)
    rows = _read_set_rows(root / "factors.csv", root, problems)
    found: NwSet | None = None
    if data is not None:
        if "rows" in data:
            problems.append(
                Problem("invalid-field", "set.yaml: rows", "rows belong in factors.csv")
            )
            data.pop("rows")
        if rows is not None:
            try:
                found = NwSet.model_validate({**data, "rows": rows})
            except ValidationError as error:
                problems.extend(problems_from_validation(error, prefix="set.yaml", model=NwSet))
    if problems or found is None:
        raise InvalidDataError(problems)
    return found


def read_set_rows(path: Path | str) -> tuple[NwRow, ...]:
    """The rows of a set's CSV, `indicator,normalisation,weight`, as a person writes them: a
    blank cell, or a column left out, gives that row no normalisation or no weight."""
    file = Path(path)
    problems: list[Problem] = []
    rows = _read_set_rows(file, file.parent, problems)
    if problems or rows is None:
        raise InvalidDataError(problems)
    return rows


def _read_set_rows(path: Path, root: Path, problems: list[Problem]) -> tuple[NwRow, ...] | None:
    if not path.is_file():
        problems.append(Problem("missing-file", path.name, f"{path.name} is missing in {root}"))
        return None
    before = len(problems)
    rows: list[NwRow] = []
    for row in read_csv(path, root, required=SET_COLUMNS, optional=SET_OPTIONAL, problems=problems):
        item = f"set row {row.get('indicator')}"
        numbers = {
            column: read_number(row, column, item, problems) if row.get(column) else None
            for column in SET_OPTIONAL
        }
        try:
            rows.append(NwRow(indicator=row.get("indicator"), **numbers))
        except ValidationError as error:
            problems.extend(problems_from_validation(error, item=item, location=row.location))
    return tuple(rows) if len(problems) == before else None


# --- database.yaml --------------------------------------------------------------------------


def _require_folder(root: Path) -> None:
    if not root.is_dir():
        raise InvalidDataError(
            [Problem("missing-file", f"folder {root}", f"{root} does not exist or is not a folder")]
        )


def _read_required_yaml(root: Path, name: str, problems: list[Problem]) -> dict[str, object] | None:
    path = root / name
    if not path.is_file():
        problems.append(Problem("missing-file", name, f"{name} is missing in {root}"))
        return None
    return read_yaml(path, root, problems)


def _read_database_yaml(root: Path, problems: list[Problem]) -> Database | None:
    data = _read_required_yaml(root, DATABASE_YAML, problems)
    if data is None:
        if not (root / DATABASE_YAML).exists() and (root / "study.yaml").is_file():
            old = (root / "processes").is_dir()
            problems[-1] = Problem(
                "missing-file",
                DATABASE_YAML,
                f"{DATABASE_YAML} is missing in {root}, which holds study.yaml: a folder of "
                "processes is a database, read from database.yaml (D46)"
                if old
                else f"{root} holds a study (study.yaml), not a database",
            )
        return None
    try:
        return Database.model_validate(data)
    except ValidationError as error:
        problems.extend(problems_from_validation(error, prefix=DATABASE_YAML, model=Database))
        return None


DATABASE_YAML = "database.yaml"

_DATABASE_FILES = (
    DATABASE_YAML,
    "flows.csv",
    "flow_properties.csv",
    "units.csv",
    "parameters.yaml",
    "cut_offs.csv",
    "no_factor.csv",
)
_DATABASE_FOLDERS = ("processes", "overrides")
_NOTES = (".md", ".txt", ".rst")


def _check_entries(root: Path, problems: list[Problem]) -> None:
    """A known name of the wrong kind, or a name close to a known one, is a mistake: the file
    would be ignored without a word. Hidden files and notes (.md, .txt) are left alone. A study's
    files are refused: one folder is a database or a study, and a study holds the scenarios."""
    if (root / "scenarios").exists():
        problems.append(
            Problem(
                "database-scenarios",
                "scenarios",
                "a database holds no scenarios: scenarios belong in a study, a folder next to "
                "the database with study.yaml, whose products are processes of the database "
                "(D46)",
                location="scenarios",
            )
        )
    if (root / "study.yaml").is_file() and (root / DATABASE_YAML).is_file():
        problems.append(
            Problem(
                "database-and-study",
                "study.yaml",
                "the folder holds both database.yaml and study.yaml. A folder is a database "
                "or a study: move the study to a folder of its own, next to the database.",
                location="study.yaml",
            )
        )
    known = (*_DATABASE_FILES, *_DATABASE_FOLDERS)
    for name in _DATABASE_FILES:
        if (root / name).is_dir():
            problems.append(_wrong_kind(name, "a folder", "a file"))
    for name in _DATABASE_FOLDERS:
        if (root / name).is_file():
            problems.append(_wrong_kind(name, "a file", "a folder"))
    for entry in sorted(root.iterdir()):
        name = entry.name
        if name.startswith(".") or name in (*known, "scenarios", "study.yaml"):
            continue
        if entry.suffix in _NOTES:
            continue
        close = difflib.get_close_matches(name, known, n=1, cutoff=0.8)
        if close:
            problems.append(
                Problem(
                    "unknown-file",
                    name,
                    f"is not a file or folder of a database, so it would be ignored. "
                    f"Did you mean {close[0]!r}?",
                    location=name,
                )
            )


def _wrong_kind(name: str, found: str, wanted: str) -> Problem:
    return Problem(
        "wrong-kind", name, f"is {found}, but a database has {wanted} of that name", location=name
    )


def _process_stems(root: Path) -> set[str]:
    """The names of the processes that have both a .csv and a .yaml file."""
    folder = root / "processes"
    if not folder.is_dir():
        return set()
    files = [f for f in folder.iterdir() if f.is_file() and not f.name.startswith(".")]
    return {f.stem for f in files if f.suffix == ".csv"} & {
        f.stem for f in files if f.suffix == ".yaml"
    }


# --- the parts of a folder ------------------------------------------------------------------


@dataclass(frozen=True)
class _Parts:
    processes: tuple[Process, ...]
    flows: tuple[Flow, ...]
    properties: tuple[FlowProperty, ...]
    units: tuple[Unit, ...]


def _read_parts(
    root: Path, namespace: str, formulas: Formulas | None, problems: list[Problem]
) -> _Parts:
    processes = _read_processes(root, namespace, formulas, problems)
    flows = _read_flows(root, problems)
    properties = _read_properties(root, problems)
    units = _read_units(root, problems)
    return _Parts(tuple(processes), tuple(flows), tuple(properties), tuple(units))


def _optional_csv(
    root: Path,
    name: str,
    problems: list[Problem],
    *,
    required: tuple[str, ...],
    optional: tuple[str, ...] = (),
) -> list[Row]:
    path = root / name
    if not path.is_file():
        return []
    return read_csv(path, root, required=required, optional=optional, problems=problems)


# --- processes ------------------------------------------------------------------------------


def _read_processes(
    root: Path, namespace: str, formulas: Formulas | None, problems: list[Problem]
) -> list[Process]:
    """Each process is `processes/<stem>.csv` (its exchanges) with `processes/<stem>.yaml`."""
    folder = root / "processes"
    files = (
        sorted(f for f in folder.iterdir() if not f.name.startswith(".")) if folder.is_dir() else []
    )
    stems: dict[str, set[str]] = {".csv": set(), ".yaml": set()}
    for f in files:
        if f.suffix in stems and f.is_file():
            stems[f.suffix].add(f.stem)
            continue
        rel = relative(f, root)
        problems.append(
            Problem(
                "unknown-file",
                rel,
                "is not a process file. A process is a .csv file (its exchanges) with a .yaml "
                "file of the same name (its name, location and kind).",
                location=rel,
            )
        )
    if not stems[".csv"] and not stems[".yaml"]:
        problems.append(
            Problem(
                "no-processes",
                "processes/",
                f"no process files in {root}. A process is processes/<name>.csv "
                "with processes/<name>.yaml",
            )
        )
    for stem in sorted(stems[".csv"] - stems[".yaml"]):
        problems.append(_unpaired(f"processes/{stem}.csv", f"processes/{stem}.yaml"))
    for stem in sorted(stems[".yaml"] - stems[".csv"]):
        problems.append(_unpaired(f"processes/{stem}.yaml", f"processes/{stem}.csv"))
    processes: list[Process] = []
    for stem in sorted(stems[".csv"] & stems[".yaml"]):
        process = _read_process(folder, root, stem, f"{namespace}:{stem}", formulas, problems)
        if process is not None:
            processes.append(process)
    return processes


def _unpaired(found: str, missing: str) -> Problem:
    return Problem(
        "missing-file",
        found,
        f"has no {missing} beside it. A process needs both: a .csv file for its exchanges "
        "and a .yaml file for its name, location and kind.",
        location=found,
    )


def _read_process(
    folder: Path,
    root: Path,
    stem: str,
    process_id: str,
    formulas: Formulas | None,
    problems: list[Problem],
) -> Process | None:
    before = len(problems)
    csv_path = folder / f"{stem}.csv"
    rows = read_csv(
        csv_path, root, required=EXCHANGE_COLUMNS, optional=EXCHANGE_OPTIONAL, problems=problems
    )
    exchanges = [
        e for row in rows if (e := read_exchange(row, process_id, formulas, problems)) is not None
    ]
    details = _read_details(folder / f"{stem}.yaml", root, problems)
    if len(problems) > before or details is None:
        # A line that failed would only cause confusing errors about the whole process.
        return None
    try:
        return Process(id=process_id, exchanges=tuple(exchanges), **dict(details))
    except ValidationError as error:
        problems.extend(problems_from_validation(error, location=relative(csv_path, root)))
        return None


def _read_details(path: Path, root: Path, problems: list[Problem]) -> ProcessDetails | None:
    data = read_yaml(path, root, problems)
    if data is None:
        return None
    rel = relative(path, root)
    try:
        return ProcessDetails.model_validate(data)
    except ValidationError as error:
        problems.extend(
            problems_from_validation(error, prefix=rel, location=rel, model=ProcessDetails)
        )
        return None


# --- flows, properties, units ---------------------------------------------------------------


def _read_flows(root: Path, problems: list[Problem]) -> list[Flow]:
    rows = _optional_csv(root, "flows.csv", problems, required=FLOW_COLUMNS, optional=FLOW_OPTIONAL)
    flows: list[Flow] = []
    seen: set[str] = set()
    for row in rows:
        item = f"flow {row.get('id')}"
        try:
            flow = Flow.model_validate(
                {
                    "id": row.get("id"),
                    "name": row.get("name"),
                    "kind": row.get("kind"),
                    "unit": row.get("unit"),
                    "compartment": blank_to_none(row.get("compartment")),
                    "cas": blank_to_none(row.get("cas")),
                }
            )
        except ValidationError as error:
            problems.extend(problems_from_validation(error, item=item, location=row.location))
            continue
        if flow.id in seen:
            problems.append(
                Problem("duplicate-flow", item, "is listed twice", location=row.location)
            )
            continue
        seen.add(flow.id)
        flows.append(flow)
    return flows


def _read_properties(root: Path, problems: list[Problem]) -> list[FlowProperty]:
    rows = _optional_csv(root, "flow_properties.csv", problems, required=PROPERTY_COLUMNS)
    properties: list[FlowProperty] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        item = f"flow {row.get('flow')} property {row.get('property')}"
        value = read_number(row, "value", item, problems)
        if value is None:
            continue
        try:
            prop = FlowProperty.model_validate(
                {
                    "flow_id": row.get("flow"),
                    "property": row.get("property"),
                    "value": value,
                    "unit": row.get("unit"),
                }
            )
        except ValidationError as error:
            problems.extend(
                problems_from_validation(
                    error, item=item, location=row.location, rename=_PROPERTY_COLUMN
                )
            )
            continue
        key = (prop.flow_id, prop.property.value)
        if key in seen:
            problems.append(
                Problem("duplicate-property", item, "is listed twice", location=row.location)
            )
            continue
        seen.add(key)
        properties.append(prop)
    return properties


def _read_units(root: Path, problems: list[Problem]) -> list[Unit]:
    rows = _optional_csv(root, "units.csv", problems, required=UNIT_COLUMNS)
    units: list[Unit] = []
    for row in rows:
        item = f"unit {row.get('unit')!r}"
        factor = read_number(row, "factor_to_reference", item, problems)
        if factor is None:
            continue
        try:
            units.append(
                Unit(name=row.get("unit"), quantity=row.get("quantity"), factor_to_reference=factor)
            )
        except ValidationError as error:
            problems.extend(problems_from_validation(error, item=item, location=row.location))
    try:
        DEFAULT_UNITS.merge(Units(units))
    except InvalidDataError as error:
        problems.extend(error.problems)
    return units


# --- method factors -------------------------------------------------------------------------


def _read_factors(root: Path, problems: list[Problem]) -> list[Factor]:
    path = root / "factors.csv"
    if not path.is_file():
        problems.append(Problem("missing-file", "factors.csv", f"factors.csv is missing in {root}"))
        return []
    rows = read_csv(
        path, root, required=FACTOR_COLUMNS, optional=FACTOR_OPTIONAL, problems=problems
    )
    factors: list[Factor] = []
    for row in rows:
        item = f"factor {row.get('indicator')}/{row.get('flow')}"
        value = read_number(row, "factor", item, problems)
        if value is None:
            continue
        try:
            factors.append(
                Factor(
                    indicator=row.get("indicator"),
                    flow_id=row.get("flow"),
                    location=blank_to_none(row.get("location")),
                    factor=value,
                )
            )
        except ValidationError as error:
            problems.extend(
                problems_from_validation(
                    error, item=item, location=row.location, rename=_FACTOR_COLUMN
                )
            )
    return factors
