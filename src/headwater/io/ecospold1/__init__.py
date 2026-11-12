"""Import ecoSpold1 XML files, the format of ecoinvent 2 and of the Swiss BAFU and UVEK databases.

Example: `import_ecospold1(folder, version="2026", source="bafu", licence="BAFU, cite")` reads every
`.xml` file under `folder`, links each line to the process that makes its product at its location
in its unit, and writes the release into the cache. The files say neither which database they are
nor its version, so those are given.
"""

import re
from itertools import islice
from pathlib import Path

from headwater.io.ecospold1.dataset import NAMESPACE, ROOT
from headwater.io.ecospold1.reader import read, xml_files
from headwater.io.inventory import Inventory, link_inventory
from headwater.io.source import Aspect, Format, InventoryOptions
from headwater.release import Release

_HEAD = 4096
"""Bytes of a file that detection reads."""

_CHECKED = 20
"""Files that detection looks at, at most: the first of a folder."""

_PROLOG = re.compile(rb"(?:\s|<\?.*?\?>|<!--.*?-->|<!DOCTYPE[^>]*>)*", re.DOTALL)
_ROOT = re.compile(rb"<" + ROOT.encode() + rb"(?=[\s/>])([^>]*)>")
_XMLNS = re.compile(rb"\sxmlns\s*=\s*[\"']([^\"']*)[\"']")


def _is_ecospold1(file: Path) -> bool:
    """The root of the file is `ecoSpold` in the ecoSpold1 namespace, or in none: openLCA writes
    none. Reads only the first bytes."""
    try:
        with file.open("rb") as stream:
            head = stream.read(_HEAD).removeprefix(b"\xef\xbb\xbf")
    except OSError:
        return False
    root = _ROOT.match(head, _PROLOG.match(head).end())  # pyright: ignore[reportOptionalMemberAccess]
    if root is None:
        return False
    declared = _XMLNS.search(root[1])
    return declared is None or declared[1] == NAMESPACE.encode()


def _detect(path: Path) -> bool:
    return any(_is_ecospold1(file) for file in islice(xml_files(path), _CHECKED))


FORMAT = Format(
    name="ecospold1",
    title="ecoSpold1 XML files, one or more datasets each (BAFU, UVEK, ecoinvent 2)",
    detect=_detect,
    read_inventory=read,
    inventory_options=frozenset({"version", "source", "licence", "workers"}),
    cannot_carry=frozenset({Aspect.process_kind}),
)


def read_ecospold1(
    folder: Path | str,
    *,
    version: str,
    source: str,
    licence: str,
    workers: int | None = None,
) -> Inventory:
    """Read ecoSpold1 files and link them. See `headwater.io.ecospold1.reader.read`."""
    options = InventoryOptions(version=version, source=source, licence=licence, workers=workers)
    return link_inventory(read(Path(folder), options))


def import_ecospold1(
    folder: Path | str,
    *,
    version: str,
    source: str,
    licence: str,
    name: str | None = None,
    into: Path | str | None = None,
    workers: int | None = None,
) -> Release:
    """Read ecoSpold1 files and freeze them. Raises `InvalidDataError` naming every problem."""
    found = read_ecospold1(folder, version=version, source=source, licence=licence, workers=workers)
    return found.freeze(name=name, into=into)


__all__ = ["FORMAT", "import_ecospold1", "read", "read_ecospold1"]
