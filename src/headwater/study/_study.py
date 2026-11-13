"""Reading a study: `study.yaml`, its scenarios and its `no_factor.csv`, and the databases its
products are in, found next to it by the prefix of their ids.

Example: `tshirt-dyed/tshirt-footprint/study.yaml` names the product `tshirt:tshirt`, so the
study reads the database in `tshirt-dyed/tshirt`, with `tshirt-dyed/dyes`, which it uses. A
product whose prefix names no database next to the study is a product of the background, such
as `toy:fabric-production`.
"""

import difflib
from collections.abc import Collection, Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from headwater.model import (
    CutOff,
    InvalidDataError,
    Pin,
    Problem,
    Study,
    did_you_mean,
)
from headwater.study._files import problems_from_validation, read_yaml
from headwater.study._no_factor import read_no_factor
from headwater.study._scenarios import read_scenarios, scenarios_of
from headwater.study.folders import DatabaseFolder, StudyFolder, in_used
from headwater.study.reader import DATABASE_YAML, read_database

STUDY_YAML = "study.yaml"
_STUDY_ENTRIES = (STUDY_YAML, "no_factor.csv", "scenarios")
_PROCESS_ENTRIES = ("processes", "processes.csv", "exchanges.csv")
_DATABASE_ENTRIES = (
    "flows.csv",
    "flow_properties.csv",
    "units.csv",
    "parameters.yaml",
    "overrides",
    "cut_offs.csv",
    DATABASE_YAML,
)
_NOTES = (".md", ".txt", ".rst")


def read_study(path: Path | str, *, databases: Path | None = None) -> StudyFolder:
    """Read a study folder, and the databases its products are in, from the folder that holds
    it, or from `databases` when given, for a study read from a copy. Raises InvalidDataError
    naming every problem found."""
    root = Path(path)
    if not root.is_dir():
        raise InvalidDataError(
            [Problem("missing-file", f"folder {root}", f"{root} does not exist or is not a folder")]
        )
    problems: list[Problem] = []
    _check_entries(root, problems)
    record = _read_record(root, problems)
    if record is None:
        raise InvalidDataError(problems)
    parent = (databases or root).resolve() if databases is not None else root.resolve().parent
    products = [p.process for p in record.products]
    found, siblings = find_databases(parent, products, problems)
    reached = _reached(found)
    scenarios = read_scenarios(root, reached, siblings, problems)
    no_factor = read_no_factor(root, problems)
    _check_pins(record.background, found, problems, file=True, products=products)
    if problems:
        raise InvalidDataError(problems)
    return StudyFolder(
        path=root,
        record=record,
        name=record.name,
        products=record.products,
        methods=record.methods,
        cut_off=record.cut_off,
        background=record.background,
        databases=found,
        scenarios=scenarios,
        no_factor=no_factor,
    )


def study_in_memory(
    products: Mapping[str, float],
    methods: Sequence[str],
    *,
    databases: Path | str | None,
    scenarios: Mapping[str, Mapping[str, Any]] | None,
    cut_off: Mapping[str, Any] | None,
    background: str | None,
    name: str | None,
) -> StudyFolder:
    """A study that no file holds yet. Its products are found in the database folders in
    `databases`, the folder its databases sit in, and in the background when that is None.
    Raises InvalidDataError naming every problem, as reading a study does."""
    problems: list[Problem] = []
    try:
        record = Study.model_validate(
            {
                "name": name or "study",
                "goal": "-",
                "functional_unit": "-",
                "products": [{"process": p, "amount": a} for p, a in products.items()],
                "boundary": "cradle-to-gate",
                "methods": list(methods),
                "cut_off": cut_off or {"criteria": ["mass"], "max_cumulative": 0},
                "background": background,
            }
        )
    except ValidationError as error:
        raise InvalidDataError(problems_from_validation(error, model=Study)) from None
    found: tuple[DatabaseFolder, ...] = ()
    siblings: frozenset[str] = frozenset()
    if databases is not None:
        parent = Path(databases).resolve()
        if not parent.is_dir():
            raise InvalidDataError(
                [
                    Problem(
                        "missing-file",
                        f"folder {databases}",
                        f"{databases} does not exist or is not a folder: `databases` is the "
                        "folder that holds the database folders of the study's products",
                    )
                ]
            )
        found, siblings = find_databases(parent, list(products), problems)
    _check_pins(record.background, found, problems, file=False)
    if problems:
        raise InvalidDataError(problems)
    return StudyFolder(
        path=None,
        record=None,
        name=name,
        products=record.products,
        methods=record.methods,
        cut_off=CutOff.model_validate(cut_off) if cut_off is not None else None,
        background=record.background,
        databases=found,
        scenarios=scenarios_of(scenarios or {}, _reached(found), siblings),
    )


def find_databases(
    parent: Path, process_ids: Sequence[str], problems: list[Problem]
) -> tuple[tuple[DatabaseFolder, ...], frozenset[str]]:
    """The database of each product that is in one, each once, in the order of the products,
    and the names of the database folders in `parent`. A product's database is the folder of
    `parent` named like the prefix of its id, which holds `database.yaml`."""
    siblings: frozenset[str] = frozenset()
    if parent.is_dir():
        siblings = frozenset(e.name for e in parent.iterdir() if (e / DATABASE_YAML).is_file())
    found: dict[str, DatabaseFolder] = {}
    refused: set[str] = set()
    for process_id in process_ids:
        prefix = process_id.split(":", 1)[0]
        if prefix not in siblings or prefix in refused:
            continue  # a product of the background, or of a database already reported
        if prefix not in found:
            try:
                database = read_database(parent / prefix)
            except InvalidDataError as error:
                problems.extend(
                    replace(p, location=in_used(prefix, p.location)) for p in error.problems
                )
                refused.add(prefix)
                continue
            if database.database.name != prefix:
                problems.append(
                    Problem(
                        "study-database-name",
                        f"database {prefix}",
                        f"the folder {prefix} holds the database {database.database.name!r}. A "
                        "study finds a database by the prefix of its products' ids, so a "
                        f"database has the name of its folder: write name: {prefix} in its "
                        "database.yaml, or rename the folder.",
                        location=in_used(prefix, DATABASE_YAML),
                    )
                )
                refused.add(prefix)
                continue
            found[prefix] = database
        own = [p.id for p in found[prefix].processes]
        if process_id not in own:
            problems.append(
                Problem(
                    "study-unknown-product",
                    f"product {process_id}",
                    f"the database {prefix} has no process {process_id!r}."
                    f"{did_you_mean(process_id, own)} Its processes: {', '.join(own)}",
                    location=STUDY_YAML,
                )
            )
    return tuple(found.values()), siblings


def _reached(databases: Collection[DatabaseFolder]) -> dict[str, DatabaseFolder]:
    found: dict[str, DatabaseFolder] = {}
    for database in databases:
        for folder in database.chain:
            found.setdefault(folder.database.name, folder)
    return found


def _check_pins(
    background: Pin | None,
    databases: Sequence[DatabaseFolder],
    problems: list[Problem],
    *,
    file: bool,
    products: Sequence[str] = (),
) -> None:
    """Every database a study reaches is calculated on one background, so they pin one release.
    A study pins one only when all its products are processes of a release."""
    pins = {str(d.database.background): d.database.name for d in databases}
    if len(pins) > 1:
        named = ", ".join(f"{name} pins {pin}" for pin, name in pins.items())
        problems.append(
            Problem(
                "study-backgrounds",
                "study",
                f"its products are in databases that pin different releases: {named}. A study is "
                "calculated on one background.",
                location=STUDY_YAML if file else None,
            )
        )
    if background is not None and databases:
        problems.append(
            Problem(
                "study-background",
                "study.yaml: background",
                f"pins {background}, and the products in databases are scored on the release "
                f"their databases pin ({', '.join(pins)}). A study pins a release only when "
                "every product is a process of it: take background out.",
                location=STUDY_YAML if file else None,
            )
        )
    if background is None and not databases and file:
        missing = ", ".join(f"{p} is in no database {p.split(':', 1)[0]}" for p in products)
        problems.append(
            Problem(
                "study-background",
                "study.yaml: background",
                f"is missing: no product is in a database next to the study ({missing}), so "
                "every product is a process of a release, and the study pins it: background: "
                "name@hash.",
                location=STUDY_YAML,
            )
        )


def _read_record(root: Path, problems: list[Problem]) -> Study | None:
    path = root / STUDY_YAML
    if not path.is_file():
        problems.append(Problem("missing-file", STUDY_YAML, f"{STUDY_YAML} is missing in {root}"))
        return None
    data = read_yaml(path, root, problems)
    if data is None:
        return None
    try:
        return Study.model_validate(data)
    except ValidationError as error:
        problems.extend(problems_from_validation(error, prefix=STUDY_YAML, model=Study))
        return None


def _check_entries(root: Path, problems: list[Problem]) -> None:
    """A study holds study.yaml, scenarios/ and no_factor.csv. A file of a database in it is an
    error, processes first: a study holds no processes (D46)."""
    for name in _PROCESS_ENTRIES:
        if (root / name).exists():
            problems.append(
                Problem(
                    "study-processes",
                    name,
                    "a study holds no processes: a process belongs in a database, a folder next "
                    "to the study with database.yaml, and the study names it in products (D46)",
                    location=name,
                )
            )
    for entry in sorted(root.iterdir()):
        name = entry.name
        if name.startswith(".") or name in _STUDY_ENTRIES or name in _PROCESS_ENTRIES:
            continue
        if name in _DATABASE_ENTRIES:
            problems.append(
                Problem(
                    "study-database-file",
                    name,
                    "is a file of a database. A study holds study.yaml, scenarios/ and "
                    "no_factor.csv; put the database in a folder of its own, next to the study.",
                    location=name,
                )
            )
            continue
        if entry.suffix in _NOTES:
            continue
        close = difflib.get_close_matches(name, _STUDY_ENTRIES, n=1, cutoff=0.8)
        if close:
            problems.append(
                Problem(
                    "unknown-file",
                    name,
                    "is not a file or folder of a study, so it would be ignored. "
                    f"Did you mean {close[0]!r}?",
                    location=name,
                )
            )
