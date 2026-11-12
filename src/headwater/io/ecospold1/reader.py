"""Read a folder of ecoSpold1 files into an inventory source.

`read` finds the XML files, parses them (in parallel by default) into raw datasets, and assembles
the tables. Problems that make the records wrong raise `InvalidDataError` naming each one. Each
line that buys keeps the supplier its file names, by product, location and unit, unlinked: linking
is a shared stage.
"""

import math
import multiprocessing
import os
import re
from collections import Counter
from collections.abc import Iterator, Sequence
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from functools import partial
from pathlib import Path

from headwater.io.ecospold1.assemble import assemble
from headwater.io.ecospold1.dataset import ParsedFile, parse_files
from headwater.io.ecospold1.units import APPROXIMATE
from headwater.io.source import InventoryOptions, InventorySource
from headwater.model import InvalidDataError, Problem, Severity
from headwater.release import ReleaseInfo
from headwater.release.manifest import FOLDER_NAME

_WINDOWS_MAX_WORKERS = 61
"""ProcessPoolExecutor refuses more workers than this on Windows."""

_CHUNK = 128
"""Most files per task sent to a worker."""


def xml_files(path: Path) -> Iterator[Path]:
    """The XML files at `path`: the file itself, or every `.xml` under a folder."""
    if path.is_file():
        yield path
        return
    for found in path.rglob("*"):
        if found.suffix.lower() == ".xml" and not found.name.startswith(".") and found.is_file():
            yield found


def read(folder: Path, options: InventoryOptions) -> InventorySource:
    """Read every ecoSpold1 file under `folder`, a database called `options.source` of version
    `options.version` under `options.licence`: the files say none of them. Links nothing: that is
    `headwater.io.inventory.link_inventory`.

    `options.workers` is the number of processes that parse files, by default one per CPU; 1
    parses in this process. The result does not depend on it. With more than one, a script that
    calls this needs `if __name__ == "__main__":`, as any Python multiprocessing does.
    """
    source, version, licence = _identity(options)
    files = sorted(xml_files(folder))
    parsed = _parse(files, folder, options.workers)
    _raise_if_any([p for f in parsed for p in f.problems])
    found = [(f.file, d) for f in parsed for d in f.datasets]
    if not found:
        raise InvalidDataError(
            [Problem("missing-file", str(folder), "has no ecoSpold1 dataset to import")]
        )
    built = assemble(found, source, version)
    _raise_if_any(built.problems)
    return InventorySource(
        info=ReleaseInfo(
            name=f"{source}-{version}",
            source=source,
            version=version,
            licence=licence,
            flow_list=f"{source}-{version}",
        ),
        processes=built.processes,
        lines=built.lines,
        flows=built.flows,
        flow_properties=(),
        units=built.units,
        allocation=built.allocation,
        left_out=built.left_out,
        warnings=(*(w for f in parsed for w in f.warnings), *_renamed(parsed)),
    )


def _identity(options: InventoryOptions) -> tuple[str, str, str]:
    """The source, version and licence. Raises naming each one that is missing, or that cannot be
    part of an id and of a release name."""
    given = {"source": options.source, "version": options.version, "licence": options.licence}
    problems = [
        Problem(
            "missing-option",
            f"--{name}",
            "is needed: ecoSpold1 files do not say which database they come from",
        )
        for name, value in given.items()
        if not value
    ]
    problems += [
        Problem(
            "bad-option",
            f"--{name}",
            f"{given[name]!r} goes into ids and into the release's name: use letters, digits, "
            "'.', '_' and '-' only",
        )
        for name in ("source", "version")
        if given[name] and not re.fullmatch(FOLDER_NAME, given[name] or "")
    ]
    _raise_if_any(problems)
    assert options.source and options.version and options.licence
    return options.source, options.version, options.licence


def _parse(files: Sequence[Path], base: Path, workers: int | None) -> list[ParsedFile]:
    """Parse the files in batches of at most `_CHUNK`, in order, at least one per worker."""
    if not files:
        raise InvalidDataError(
            [Problem("missing-file", str(base), "has no XML file: ecoSpold1 files end in .xml")]
        )
    parse = partial(parse_files, base=base)
    count = min(workers or os.cpu_count() or 1, len(files))
    if os.name == "nt":
        count = min(count, _WINDOWS_MAX_WORKERS)
    size = max(1, min(_CHUNK, math.ceil(len(files) / max(count, 1))))
    batches = [files[i : i + size] for i in range(0, len(files), size)]
    if count <= 1:
        return parse(files)
    context = multiprocessing.get_context("spawn")
    try:
        with ProcessPoolExecutor(max_workers=count, mp_context=context) as pool:
            return [parsed for batch in pool.map(parse, batches) for parsed in batch]
    except BrokenProcessPool as error:
        raise InvalidDataError(
            [
                Problem(
                    "worker-stopped",
                    str(base),
                    f"a parsing process stopped without a message ({error}), for example when "
                    "out of memory. Run again with fewer workers, or with 1 to parse in place.",
                )
            ]
        ) from None


def _renamed(parsed: Sequence[ParsedFile]) -> list[Problem]:
    """A warning for each unit the reader renamed by a convention of the source, with the number
    of lines that use it."""
    used = Counter[str]()
    for f in parsed:
        used.update(f.renamed)
    return [
        Problem(
            "approximate-unit",
            f"unit {unit!r}",
            f"{APPROXIMATE[unit]}. {n:,} {'line uses' if n == 1 else 'lines use'} it",
            severity=Severity.warning,
        )
        for unit, n in sorted(used.items())
    ]


def _raise_if_any(problems: Sequence[Problem]) -> None:
    if problems:
        raise InvalidDataError(problems)
