"""Convert a folder from the layout before D48, `processes/<name>.yaml` with `processes/<name>.csv`,
to the two tables `processes.csv` and `exchanges.csv`, and check that nothing changed.

Example: `processes/tshirt.yaml` and `processes/tshirt.csv` of the toy T-shirt become one row of
processes.csv and three rows of exchanges.csv. Each cell of a line keeps its text, so an amount
written `0.30` stays `0.30`. The comment lines of a YAML file go into the `comment` cell of its
row, joined by a space.

Every folder is written into a copy first, and the copy is read as `hw.open` reads a database.
Its records must equal those of the old layout: process ids, details, lines with their formulas
and distributions, and everything else of the folder. Only when every folder given passes are the
tables moved into place and `processes/` removed.
"""

import os
import shutil
import tempfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, cast

import yaml

from headwater.model import InvalidDataError, Problem
from headwater.study._files import relative
from headwater.study._old_layout import FileLayout
from headwater.study._tables import EXCHANGES, OLD_FOLDER, PROCESSES
from headwater.study._write import (
    Cells,
    comment_cell,
    csv_rows,
    exchanges_csv,
    process_cells,
    processes_csv,
)
from headwater.study.folders import ProcessFolder
from headwater.study.reader import DATABASE_YAML, read_whole

_STUDY_YAML = "study.yaml"


@dataclass(frozen=True)
class Converted:
    """One folder converted: how many processes and lines its tables hold, and how many files
    `processes/` held."""

    path: Path
    processes: int
    lines: int
    files: int


def convert(path: Path | str) -> tuple[Converted, ...]:
    """Convert a database, a background folder, or each folder of a parent folder that holds
    `processes/` (one level down), and return what was converted.

    Raises `InvalidDataError`, and changes nothing, for a folder already converted, a parent with
    nothing to convert, a study of the format before D46, a folder that does not read in the old
    layout, a comment after a value in a process YAML, and a record that would not read the same.
    """
    folders = _folders(Path(path))
    planned: list[tuple[Path, Path, Converted]] = []
    problems: list[Problem] = []
    try:
        for folder in folders:
            try:
                planned.append(_plan(folder))
            except InvalidDataError as error:
                where = folder.name if len(folders) > 1 else None
                problems.extend(_at(where, p) for p in error.problems)
        if problems:
            raise InvalidDataError(problems)
        for folder, copy, _ in planned:
            _put_in_place(folder, copy)
    finally:
        for _, copy, _ in planned:
            shutil.rmtree(copy.parent, ignore_errors=True)
    return tuple(done for _, _, done in planned)


def _folders(root: Path) -> list[Path]:
    if not root.is_dir():
        raise InvalidDataError(
            [Problem("missing-file", f"folder {root}", f"{root} does not exist or is not a folder")]
        )
    if (root / OLD_FOLDER).is_dir():
        return [root]
    if (root / PROCESSES).exists() or (root / EXCHANGES).exists():
        raise InvalidDataError([_converted(root)])
    found = [
        child
        for child in sorted(root.iterdir())
        if child.is_dir() and not child.name.startswith(".") and _old(child)
    ]
    if not found:
        raise InvalidDataError(
            [
                Problem(
                    "nothing-to-convert",
                    f"folder {root}",
                    f"neither {root} nor a folder in it holds processes/ with process files, so "
                    "there is nothing to convert",
                )
            ]
        )
    return found


def _old(folder: Path) -> bool:
    """A folder of the old layout: `processes/` holds .yaml or .csv files. openLCA's own
    `processes/*.json` is not one."""
    old = folder / OLD_FOLDER
    return old.is_dir() and any(f.suffix in (".yaml", ".csv") for f in old.iterdir())


def _converted(root: Path) -> Problem:
    return Problem(
        "already-converted",
        f"folder {root}",
        f"already holds {PROCESSES} or {EXCHANGES} and no processes/: it is in the layout of "
        "headwater 0.2.0, so there is nothing to convert",
    )


def _plan(root: Path) -> tuple[Path, Path, Converted]:
    """Read the old layout, write the tables into a copy, and check the copy reads the same.
    Returns the folder, the copy, and what the copy holds."""
    if (root / PROCESSES).exists() or (root / EXCHANGES).exists():
        raise InvalidDataError(
            [
                Problem(
                    "both-layouts",
                    f"folder {root}",
                    f"holds processes/ next to {PROCESSES} or {EXCHANGES}. The tables are the "
                    "processes (D48): remove processes/ if they hold every process, or remove "
                    "the tables and convert again.",
                )
            ]
        )
    database = (root / DATABASE_YAML).is_file()
    if not database and (root / _STUDY_YAML).is_file():
        raise InvalidDataError(
            [
                Problem(
                    "old-study",
                    f"folder {root}",
                    "holds study.yaml with processes/, the format before D46, which headwater "
                    "no longer reads: make it a database and a study (D46) before converting",
                )
            ]
        )
    old = read_whole(root, FileLayout)
    problems: list[Problem] = []
    comments = _comments(root, problems)
    if problems:
        raise InvalidDataError(problems)
    rows: list[Cells] = []
    lines: list[Cells] = []
    files = [f for f in (root / OLD_FOLDER).iterdir() if not f.name.startswith(".")]
    for process in old.processes:
        code = process.id.split(":", 1)[1]
        rows.append(process_cells(code, process, comments.get(code, "")))
        text = (root / OLD_FOLDER / f"{code}.csv").read_bytes().decode("utf-8")
        lines += [{"process": code, **cells} for _, cells in csv_rows(text)]
    holder = Path(tempfile.mkdtemp(prefix=f".tmp-convert-{root.name}-", dir=root.parent))
    copy = holder / root.name  # the same name: a background's namespace is its folder's name
    try:
        shutil.copytree(root, copy, symlinks=True)
        shutil.rmtree(copy / OLD_FOLDER)
        (copy / PROCESSES).write_bytes(processes_csv(rows).encode("utf-8"))
        (copy / EXCHANGES).write_bytes(exchanges_csv(lines).encode("utf-8"))
        _record(root, copy, files)
        new = read_whole(copy)
        _same(old, replace(new, path=old.path))
    except BaseException:
        shutil.rmtree(holder, ignore_errors=True)
        raise
    done = Converted(root, len(rows), len(lines), len(files))
    return root, copy, done


def _same(old: ProcessFolder, new: ProcessFolder) -> None:
    if old == new:
        return
    before = {p.id: p for p in old.processes}
    after = {p.id: p for p in new.processes}
    differ = sorted(i for i in before.keys() | after.keys() if before.get(i) != after.get(i))
    what = f"processes {', '.join(differ)}" if differ else "the folder's other records"
    raise InvalidDataError(
        [
            Problem(
                "conversion-differs",
                f"folder {old.path}",
                f"{what} would not read the same from the tables, so nothing was converted. "
                "This is a bug of the converter: please report it with the folder.",
            )
        ]
    )


def _comments(root: Path, problems: list[Problem]) -> dict[str, str]:
    """The comment of each process YAML, by the file's name: its comment lines joined by a space.
    A comment after a value has no place in the tables, so it is a problem naming its line."""
    found: dict[str, str] = {}
    for path in sorted((root / OLD_FOLDER).glob("*.yaml")):
        lines = _comment_lines(path, relative(path, root), problems)
        if lines:
            found[path.stem] = comment_cell(lines)
    return found


_ZERO_WIDTH = (
    yaml.StreamStartToken,
    yaml.StreamEndToken,
    yaml.BlockMappingStartToken,
    yaml.BlockSequenceStartToken,
    yaml.BlockEndToken,
)


def _comment_lines(path: Path, rel: str, problems: list[Problem]) -> list[str]:
    text = path.read_bytes().decode("utf-8").removeprefix("﻿")
    covered: dict[int, int] = {}  # each line a value reaches, and the column where it ends there
    for start, end in _spans(text):
        for line in range(start[0], end[0]):
            covered[line] = len(text)  # a value that goes on to the next line
        covered[end[0]] = max(covered.get(end[0], 0), end[1])
    found: list[str] = []
    for n, line in enumerate(text.splitlines()):
        rest = line[covered[n] :] if n in covered else line
        if not rest.strip().startswith("#"):
            continue
        if n in covered:
            problems.append(
                Problem(
                    "comment-after-value",
                    rel,
                    "has a comment after a value, which has no place in the tables. Move it to a "
                    "line of its own, which goes into the comment column, or remove it.",
                    location=f"{rel}:{n + 1}",
                )
            )
            continue
        found.append(rest.strip().removeprefix("#"))
    return found


def _spans(text: str) -> list[tuple[tuple[int, int], tuple[int, int]]]:
    """Where each token of a YAML text that holds text starts and ends: (line, column) pairs."""
    spans: list[tuple[tuple[int, int], tuple[int, int]]] = []
    tokens = cast(Iterable[Any], yaml.scan(text, Loader=yaml.SafeLoader))  # pyright: ignore[reportUnknownMemberType]
    for token in tokens:
        if isinstance(token, _ZERO_WIDTH):
            continue
        start, end = token.start_mark, token.end_mark
        spans.append(((int(start.line), int(start.column)), (int(end.line), int(end.column))))
    return spans


def _record(root: Path, copy: Path, files: Sequence[Path]) -> None:
    """Rewrite the record of an Excel or Brightway import, when there is one, for the tables."""
    from headwater.io.excel.write import MANIFEST, convert_record

    if (copy / MANIFEST).is_file():
        old = [relative(f, root) for f in files]
        convert_record(root, copy, old, [PROCESSES, EXCHANGES])


def _put_in_place(root: Path, copy: Path) -> None:
    from headwater.io.excel.write import MANIFEST

    for name in (PROCESSES, EXCHANGES, MANIFEST):
        if (copy / name).is_file():
            os.replace(copy / name, root / name)
    shutil.rmtree(root / OLD_FOLDER)


def _at(where: str | None, problem: Problem) -> Problem:
    if where is None:
        return problem
    return replace(problem, location=f"{where}/{problem.location}" if problem.location else where)
