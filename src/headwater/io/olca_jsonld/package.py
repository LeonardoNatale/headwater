"""An openLCA JSON-LD package: data sets, one JSON file each, in a folder or a zip.

Example: `processes/<uuid>.json` holds one process with its exchanges, and an exchange points at
its flow by `{"@id": "<flow uuid>"}`, the file `flows/<flow uuid>.json`. The root holds
`olca-schema.json`, `{"version": 2}`, the version of the schema the files follow.

A `.zolca` file that openLCA writes when it backs up a database holds the database's Derby files
instead (`seg0/`, `service.properties`). Nothing but openLCA reads them, so it is refused with what
to do: export the database as JSON-LD from openLCA.
"""

import json
import os
import zipfile
from collections.abc import Iterator
from pathlib import Path, PurePosixPath
from types import TracebackType
from typing import Any, cast

from headwater.model import InvalidDataError, Problem

SCHEMA_FILE = "olca-schema.json"
SCHEMA_VERSION = 2

_DERBY = "service.properties"
"""A file at the root of every Derby database, so of every openLCA database backup."""

type DataSet = dict[str, Any]


def detect(path: Path) -> bool:
    """A folder or a .zip with `olca-schema.json` at its root, or any .zolca file, so a database
    backup gets the message that says what to do with it."""
    if path.is_dir():
        return (path / SCHEMA_FILE).is_file()
    suffix = path.suffix.lower()
    if not path.is_file() or suffix not in (".zip", ".zolca"):
        return False
    if suffix == ".zolca":
        return True
    try:
        with zipfile.ZipFile(path) as package:
            return SCHEMA_FILE in package.namelist()
    except (OSError, zipfile.BadZipFile):
        return False


class Package:
    """A JSON-LD package opened for reading. Use it in a `with` block, so a zip is closed."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._zip: zipfile.ZipFile | None = None
        try:
            if path.is_dir():
                names = _two_levels(path)
            else:
                self._zip = zipfile.ZipFile(path)
                names = self._zip.namelist()
        except (OSError, zipfile.BadZipFile) as error:
            raise _error("unreadable-file", f"cannot be read: {error}", path) from None
        self._files: dict[str, set[str]] = {}
        for name in names:
            parts = PurePosixPath(name).parts
            if len(parts) == 2 and parts[1].endswith(".json") and not parts[1].startswith("."):
                self._files.setdefault(parts[0], set()).add(parts[1].removesuffix(".json"))
        try:
            self._check_schema(set(names))
        except InvalidDataError:
            self.close()
            raise

    def __enter__(self) -> "Package":
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        if self._zip is not None:
            self._zip.close()

    def ids(self, folder: str) -> list[str]:
        """The ids of the data sets in a folder, such as `processes`, sorted."""
        return sorted(self._files.get(folder, ()))

    def has(self, folder: str, id: str) -> bool:
        return id in self._files.get(folder, ())

    def read(self, folder: str, id: str) -> DataSet:
        """The data set `<folder>/<id>.json`. Raises naming the file when it is not JSON."""
        name = f"{folder}/{id}.json"
        try:
            data = json.loads(self._bytes(name))
        except (OSError, KeyError, ValueError) as error:
            raise _error("unreadable-file", f"{name} cannot be read: {error}", self.path) from None
        if not isinstance(data, dict):
            raise _error("unreadable-file", f"{name} does not hold one JSON object", self.path)
        return cast(DataSet, data)

    def all(self, folder: str) -> Iterator[tuple[str, DataSet]]:
        """Every data set of a folder, with its id, in id order."""
        for id in self.ids(folder):
            yield id, self.read(folder, id)

    def _bytes(self, name: str) -> bytes:
        if self._zip is not None:
            return self._zip.read(name)
        return (self.path / name).read_bytes()

    def _check_schema(self, names: set[str]) -> None:
        if _DERBY in names and SCHEMA_FILE not in names:
            raise _error(
                "database-backup",
                "is an openLCA database backup (Derby files), not a JSON-LD package. Open it "
                "in openLCA 2 (Restore database), then export it as JSON-LD (File > Export > "
                "JSON-LD), and import the .zip that writes.",
                self.path,
            )
        version = None
        if SCHEMA_FILE in names:
            try:
                version = field(json.loads(self._bytes(SCHEMA_FILE)), "version")
            except (OSError, ValueError):
                pass
        if version != SCHEMA_VERSION:
            found = f"version {version}" if version is not None else f"no {SCHEMA_FILE}"
            raise _error(
                "unsupported-schema",
                f"has {found}. headwater reads openLCA JSON-LD of schema version "
                f"{SCHEMA_VERSION}, which openLCA 2 exports.",
                self.path,
            )


def field(data: Any, key: str) -> Any:
    """A field of a JSON object, or None when it is missing or `data` is not an object."""
    return cast(DataSet, data).get(key) if isinstance(data, dict) else None


def obj(data: Any, key: str) -> DataSet:
    """An object field, such as an exchange's `flow`; empty when it is missing."""
    found = field(data, key)
    return cast(DataSet, found) if isinstance(found, dict) else {}


def objs(data: Any, key: str) -> list[DataSet]:
    """The objects of a list field, such as a process's `exchanges`; none when it is missing."""
    found = field(data, key)
    items = cast(list[Any], found) if isinstance(found, list) else []
    return [cast(DataSet, x) for x in items if isinstance(x, dict)]


def text(data: Any, key: str) -> str | None:
    """A text field, stripped, or None when it is missing or blank."""
    found = field(data, key)
    return (found.strip() or None) if isinstance(found, str) else None


def ref_id(data: Any, key: str) -> str | None:
    """The `@id` of a reference field, such as an exchange's `{"flow": {"@id": "<uuid>"}}`."""
    return text(obj(data, key), "@id")


def _two_levels(folder: Path) -> list[str]:
    """The files of a folder and of its folders, as `<folder>/<file>`: data sets are never
    deeper."""
    names: list[str] = []
    with os.scandir(folder) as top:
        for entry in top:
            if entry.is_file():
                names.append(entry.name)
            elif entry.is_dir():
                with os.scandir(entry.path) as inside:
                    names += [f"{entry.name}/{e.name}" for e in inside if e.is_file()]
    return names


def _error(code: str, message: str, path: Path) -> InvalidDataError:
    return InvalidDataError([Problem(code, f"package {path.name}", message)])
