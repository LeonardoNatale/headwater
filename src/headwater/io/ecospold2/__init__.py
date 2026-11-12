"""Import ecoinvent's ecoSpold2 files and MasterData into a release.

Example: `import_ecospold2(folder, version="3.12", exclude=read_exclude("exclude.csv"))` reads
every dataset of `folder/datasets`, checks it against `folder/MasterData`, links suppliers by
UUID and writes the release into the cache.
"""

from collections.abc import Iterable
from pathlib import Path

from headwater.io.ecospold2.reader import read
from headwater.io.exclude import Exclusion
from headwater.io.inventory import Inventory, link_inventory
from headwater.io.source import Format, InventoryOptions
from headwater.release import Release


def _detect(path: Path) -> bool:
    return (path / "datasets").is_dir() and (path / "MasterData").is_dir()


FORMAT = Format(
    name="ecospold2",
    title="ecoSpold2 folder of ecoinvent, with datasets/ and MasterData/ (cut-off or APOS)",
    detect=_detect,
    read_inventory=read,
    inventory_options=frozenset({"version", "exclude", "workers"}),
)


def read_ecospold2(
    folder: Path | str,
    *,
    version: str,
    exclude: Iterable[Exclusion] = (),
    workers: int | None = None,
) -> Inventory:
    """Read an ecoSpold2 folder and link it. See `headwater.io.ecospold2.reader.read`."""
    options = InventoryOptions(version=version, exclude=tuple(exclude), workers=workers)
    return link_inventory(read(Path(folder), options))


def import_ecospold2(
    folder: Path | str,
    *,
    version: str,
    name: str | None = None,
    exclude: Iterable[Exclusion] = (),
    into: Path | str | None = None,
    workers: int | None = None,
) -> Release:
    """Read an ecoSpold2 folder and freeze it. Raises `InvalidDataError` naming every problem."""
    source = read_ecospold2(folder, version=version, exclude=exclude, workers=workers)
    return source.freeze(name=name, into=into)


__all__ = ["FORMAT", "import_ecospold2", "read", "read_ecospold2"]
