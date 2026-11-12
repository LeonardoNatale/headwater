"""Read a Brightway project from its files, as bw2data 4.5.4 and 4.7 write them, without bw2data.

A Brightway folder (`BRIGHTWAY2_DIR`) holds `projects.db`, the list of projects, and a folder
for each project, named from the project's name: the project `toy` is in `toy.10016b6e`. A
project folder holds:

- `databases.json`: each database, and how it is stored (`backend`).
- `lci/databases.db`: the activities (`activitydataset`) and exchanges (`exchangedataset`) of the
  databases stored in SQLite. Columns hold the keys, names and types; the rest of each record is a
  pickled dict, in `data`.
- `parameters.db`: project, database and activity parameters, and which exchanges have a formula
  that Brightway uses (`parameterizedexchange`).

Every file is opened read-only. A pickle can run code when it is read, so a pickle is read with an
unpickler that refuses every class: Brightway's records hold dicts, lists, tuples, text and
numbers only, and a record that holds anything else is an error that names it.
"""

import hashlib
import io
import json
import os
import pickle
import re
import sqlite3
import sys
import unicodedata
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Never, Self, cast

from headwater.model import InvalidDataError, Problem

PROJECTS = "projects.db"
LCI = "lci/databases.db"
PARAMETERS = "parameters.db"

LAYOUT: dict[str, dict[str, tuple[str, ...]]] = {
    PROJECTS: {"projectdataset": ("name", "full_hash")},
    LCI: {
        "activitydataset": ("data", "code", "database", "location", "name", "product", "type"),
        "exchangedataset": (
            "id",
            "data",
            "input_code",
            "input_database",
            "output_code",
            "output_database",
            "type",
        ),
    },
    PARAMETERS: {
        "projectparameter": ("name", "formula", "amount", "data"),
        "databaseparameter": ("database", "name", "formula", "amount", "data"),
        "activityparameter": ("group", "database", "code", "name", "formula", "amount", "data"),
        "parameterizedexchange": ("group", "exchange", "formula"),
    },
}
"""The tables headwater reads and the columns it needs, by file."""

PROCESS_TYPES = frozenset({"process", "processwithreferenceproduct"})
"""Node types of an activity that makes its own product. A node of another type than these, a
product node or a multifunctional one is a flow of nature."""
PRODUCT_TYPES = frozenset({"product", "multifunctional"})
"""Node types of bw2data 4's separate products and of activities with several products."""

_BATCH = 500
_DUMMY = re.compile(r"__dummy_[0-9a-f]+__")
"""The name bw2data gives the placeholder parameter of an activity that has formulas and no
parameters of its own."""

type Key = tuple[str, str]
type Record = Mapping[str, object]


def default_dir() -> Path:
    """Where bw2data keeps its projects when `BRIGHTWAY2_DIR` is not set: the user data folder
    that `platformdirs` gives `Brightway3`."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Brightway3"
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(local) / "pylca" / "Brightway3"
    data = os.environ.get("XDG_DATA_HOME", "").strip() or str(Path.home() / ".local" / "share")
    return Path(data) / "Brightway3"


def folder_name(name: str, *, full_hash: bool) -> str:
    """The folder of the project `name`, as bw_processing's `safe_filename` makes it: `toy` is
    `toy.10016b6e`, and `toy.10016b6ed5a5b09be08133fa2d282636` when the project keeps the whole
    hash."""
    plain = re.sub(r"[^\w\s-]", "", unicodedata.normalize("NFKD", name)).strip()
    digest = hashlib.md5(name.encode("utf8")).hexdigest()
    return f"{re.sub(r'[-\s]+', '-', plain)}.{digest if full_hash else digest[:8]}"


@dataclass(frozen=True)
class Node:
    """A row of `activitydataset`: an activity, or a flow of nature."""

    database: str
    code: str
    name: str | None
    product: str | None
    location: str | None
    type: str | None
    data: Record

    @property
    def key(self) -> Key:
        return (self.database, self.code)

    @property
    def label(self) -> str:
        """`tshirt/tshirt`: the database and the code."""
        return f"{self.database}/{self.code}"


@dataclass(frozen=True)
class Edge:
    """A row of `exchangedataset`: an exchange, from `input` to `output`."""

    id: int
    input: Key
    output: Key
    type: str | None
    data: Record

    @property
    def label(self) -> str:
        """`exchange 14 of tshirt/tshirt`."""
        return f"exchange {self.id} of {self.output[0]}/{self.output[1]}"


@dataclass(frozen=True)
class ParameterRecord:
    """A row of one of the tables of parameters."""

    name: str
    formula: str | None
    amount: object
    data: Record
    group: str | None = None
    """For an activity parameter, its group."""
    code: str | None = None
    """For an activity parameter, the code of its activity."""


class BrightwayProject:
    """A project of a Brightway folder, opened read-only. Use it in a `with` block, which closes
    its files.

    Raises `InvalidDataError` for a folder that holds no projects, a project that is not there,
    and a file whose tables are not the ones bw2data 4.5.4 to 4.7 writes."""

    def __init__(self, name: str, brightway_dir: Path | str | None = None) -> None:
        given = brightway_dir if brightway_dir is not None else os.environ.get("BRIGHTWAY2_DIR")
        self.base = Path(given).expanduser() if given else default_dir()
        self.name = name
        self._connections: dict[str, sqlite3.Connection] = {}
        self.folder = self._find()
        self.databases = self._databases()
        """Each database of the project, with its entry of `databases.json`."""

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        trace: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        for connection in self._connections.values():
            connection.close()
        self._connections.clear()

    def _find(self) -> Path:
        path = self.base / PROJECTS
        if not path.is_file():
            raise InvalidDataError(
                [
                    Problem(
                        "no-brightway-folder",
                        f"folder {self.base}",
                        f"has no {PROJECTS}, so it is not where Brightway keeps its projects. "
                        "Give that folder: the BRIGHTWAY2_DIR of the project, or "
                        f"--brightway-dir. Without either, bw2data uses {default_dir()}.",
                    )
                ]
            )
        rows = self._rows(PROJECTS, "SELECT name, full_hash FROM projectdataset", ())
        projects = {str(name): bool(full) for name, full in rows}
        if self.name not in projects:
            raise InvalidDataError(
                [
                    Problem(
                        "no-project",
                        f"project {self.name}",
                        f"is not a project of {self.base}. Its projects: "
                        f"{', '.join(sorted(projects)) or 'none'}.",
                    )
                ]
            )
        folder = self.base / folder_name(self.name, full_hash=projects[self.name])
        if not folder.is_dir():
            raise InvalidDataError(
                [
                    Problem(
                        "no-project-folder",
                        f"project {self.name}",
                        f"is listed in {path}, but its folder {folder} is not there",
                    )
                ]
            )
        return folder

    def _databases(self) -> dict[str, Record]:
        path = self.folder / "databases.json"
        try:
            found = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise InvalidDataError(
                [Problem("bad-databases-json", f"file {path}", f"cannot be read: {error}")]
            ) from error
        if not isinstance(found, dict):
            raise InvalidDataError(
                [Problem("bad-databases-json", f"file {path}", "is not a mapping of databases")]
            )
        entries = cast(dict[str, object], found)
        return {
            str(name): cast(Record, entry) if isinstance(entry, dict) else {}
            for name, entry in entries.items()
        }

    def backend(self, database: str) -> str:
        """How `database` is stored: `sqlite` unless `databases.json` says otherwise."""
        return str(self.databases.get(database, {}).get("backend") or "sqlite")

    def nodes(self, database: str) -> list[Node]:
        """Every node of `database`, by code."""
        rows = self._rows(
            LCI,
            "SELECT data, code, database, location, name, product, type FROM activitydataset "
            "WHERE database = ? ORDER BY code",
            (database,),
        )
        return [self._node(row) for row in rows]

    def find(self, keys: Iterable[Key]) -> dict[Key, Node]:
        """The nodes that have these keys. A key that no node has is left out."""
        by_database: dict[str, set[str]] = {}
        for database, code in keys:
            by_database.setdefault(database, set()).add(code)
        found: dict[Key, Node] = {}
        for database, codes in sorted(by_database.items()):
            for batch in _batches(sorted(codes)):
                rows = self._rows(
                    LCI,
                    "SELECT data, code, database, location, name, product, type "
                    "FROM activitydataset WHERE database = ? AND code IN "
                    f"({', '.join('?' * len(batch))})",
                    (database, *batch),
                )
                for row in rows:
                    node = self._node(row)
                    found[node.key] = node
        return found

    def edges(self, database: str) -> list[Edge]:
        """Every exchange of the activities of `database`, in the order Brightway wrote them."""
        rows = self._rows(
            LCI,
            "SELECT id, data, input_code, input_database, output_code, output_database, type "
            "FROM exchangedataset WHERE output_database = ? ORDER BY id",
            (database,),
        )
        edges: list[Edge] = []
        for exchange_id, blob, in_code, in_database, out_code, out_database, kind in rows:
            label = f"exchange {exchange_id} of {out_database}/{out_code}"
            edges.append(
                Edge(
                    id=cast(int, exchange_id),
                    input=(str(in_database), str(in_code)),
                    output=(str(out_database), str(out_code)),
                    type=text_of(kind),
                    data=plain(blob, label),
                )
            )
        return edges

    def formulas(self, exchange_ids: Iterable[int]) -> dict[int, str]:
        """The formula Brightway uses for each of these exchanges that has one."""
        found: dict[int, str] = {}
        for batch in _batches(sorted(set(exchange_ids))):
            rows = self._rows(
                PARAMETERS,
                "SELECT exchange, formula FROM parameterizedexchange WHERE exchange IN "
                f"({', '.join('?' * len(batch))})",
                tuple(batch),
            )
            found.update({cast(int, exchange): str(formula) for exchange, formula in rows})
        return found

    def project_parameters(self) -> list[ParameterRecord]:
        rows = self._rows(
            PARAMETERS, "SELECT name, formula, amount, data FROM projectparameter ORDER BY id", ()
        )
        return [
            ParameterRecord(str(name), text_of(formula), amount, plain(blob, f"parameter {name}"))
            for name, formula, amount, blob in rows
        ]

    def database_parameters(self, database: str) -> list[ParameterRecord]:
        rows = self._rows(
            PARAMETERS,
            "SELECT name, formula, amount, data FROM databaseparameter WHERE database = ? "
            "ORDER BY id",
            (database,),
        )
        return [
            ParameterRecord(
                str(name), text_of(formula), amount, plain(blob, f"parameter {name} of {database}")
            )
            for name, formula, amount, blob in rows
        ]

    def activity_parameters(self, database: str) -> list[ParameterRecord]:
        """The parameters of the activities of `database`, without bw2data's placeholders."""
        rows = self._rows(
            PARAMETERS,
            'SELECT "group", code, name, formula, amount, data FROM activityparameter '
            "WHERE database = ? ORDER BY id",
            (database,),
        )
        return [
            ParameterRecord(
                str(name),
                text_of(formula),
                amount,
                plain(blob, f"parameter {name} of {database}/{code}"),
                group=str(group),
                code=str(code),
            )
            for group, code, name, formula, amount, blob in rows
            if not _DUMMY.fullmatch(str(name))
        ]

    def _node(self, row: tuple[object, ...]) -> Node:
        blob, code, database, location, name, product, kind = row
        node_label = f"activity {database}/{code}"
        return Node(
            database=str(database),
            code=str(code),
            name=text_of(name),
            product=text_of(product),
            location=text_of(location),
            type=text_of(kind),
            data=plain(blob, node_label),
        )

    def _rows(
        self, file: str, statement: str, values: tuple[object, ...]
    ) -> Iterator[tuple[object, ...]]:
        connection = self._connection(file)
        return iter(
            cast(list[tuple[object, ...]], connection.execute(statement, values).fetchall())
        )

    def _connection(self, file: str) -> sqlite3.Connection:
        known = self._connections.get(file)
        if known is not None:
            return known
        path = self.base / file if file == PROJECTS else self.folder / file
        if not path.is_file():
            raise InvalidDataError([Problem("brightway-file", f"file {path}", "is not there")])
        try:
            connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
            problems = _layout(connection, file, path)
        except sqlite3.Error as error:
            raise InvalidDataError(
                [Problem("brightway-file", f"file {path}", f"cannot be read: {error}")]
            ) from error
        if problems:
            connection.close()
            raise InvalidDataError(problems)
        self._connections[file] = connection
        return connection


def _layout(connection: sqlite3.Connection, file: str, path: Path) -> list[Problem]:
    """What is missing from the tables of `file`, as problems."""
    problems: list[Problem] = []
    for table, needed in LAYOUT[file].items():
        columns = {str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')}
        missing = [c for c in needed if c not in columns]
        if missing:
            what = "is not there" if not columns else f"has no column {', '.join(missing)}"
            problems.append(
                Problem(
                    "brightway-layout",
                    f"table {table} of {path}",
                    f"{what}. headwater reads the files of bw2data 4.5.4 to 4.7.",
                )
            )
    return problems


class _ClassInPickle(Exception):
    pass


class _PlainUnpickler(pickle.Unpickler):
    def find_class(self, module_name: str, global_name: str, /) -> Never:
        raise _ClassInPickle(f"{module_name}.{global_name}")


def plain(blob: object, where: str) -> Record:
    """A pickled dict of plain data. Raises `InvalidDataError` naming `where` for a pickle that
    holds an object of a class, or that is not a dict."""
    if not isinstance(blob, bytes):
        raise InvalidDataError(
            [Problem("bad-pickle", where, f"has {type(blob).__name__} where a pickle should be")]
        )
    try:
        value = _PlainUnpickler(io.BytesIO(blob)).load()
    except _ClassInPickle as found:
        raise InvalidDataError(
            [
                Problem(
                    "pickled-class",
                    where,
                    f"holds an object of class {found}. headwater reads plain data only (dicts, "
                    "lists, tuples, text and numbers), because reading an object of a class can "
                    "run code. Write the field as plain data in Brightway, then import again.",
                )
            ]
        ) from None
    except Exception as error:  # a damaged pickle raises any of a dozen errors
        raise InvalidDataError(
            [Problem("bad-pickle", where, f"cannot be read: {error}")]
        ) from error
    if not isinstance(value, dict):
        raise InvalidDataError(
            [Problem("bad-pickle", where, f"holds a {type(value).__name__}, not a dict")]
        )
    return cast(Record, value)


def text_of(value: object) -> str | None:
    """A value as text with no surrounding blanks, or None for nothing or blank text."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _batches(items: list[str] | list[int]) -> Iterator[list[str] | list[int]]:
    for start in range(0, len(items), _BATCH):
        yield items[start : start + _BATCH]
