"""Reading `overrides/*.csv`: changed lines of background processes."""

from collections.abc import Collection, Mapping
from dataclasses import replace
from pathlib import Path

from pydantic import ValidationError

from headwater.model import Exchange, Override, Problem
from headwater.study._exchanges import (
    EXCHANGE_COLUMNS,
    EXCHANGE_OPTIONAL,
    Formulas,
    read_exchange,
)
from headwater.study._files import Row, problems_from_validation, read_csv, relative

FOLDER = "overrides"
COLUMNS = ("process", *EXCHANGE_COLUMNS)


def read_override_rows(root: Path, problems: list[Problem]) -> list[Row]:
    """The rows of every file in `overrides/`, in the order of the file names."""
    folder = root / FOLDER
    if not folder.is_dir():
        return []
    rows: list[Row] = []
    for path in sorted(folder.iterdir()):
        if path.name.startswith("."):
            continue
        if path.suffix == ".csv" and path.is_file():
            rows.extend(
                read_csv(
                    path, root, required=COLUMNS, optional=EXCHANGE_OPTIONAL, problems=problems
                )
            )
            continue
        rel = relative(path, root)
        problems.append(
            Problem(
                "unknown-file",
                rel,
                "is not an override file. An override file is a .csv file with the changed "
                f"lines of background processes: {', '.join(COLUMNS)}.",
                location=rel,
            )
        )
    return rows


def build_overrides(
    rows: list[Row],
    study_process_ids: Collection[str],
    formulas: Formulas,
    problems: list[Problem],
) -> tuple[tuple[Override, ...], Mapping[str, str]]:
    """The overrides, one per background process, and where each line was written.

    The second value maps a line's label, such as `toy:fabric-production line e02`, to its file
    and row.
    """
    lines: dict[str, list[Exchange]] = {}
    sources: dict[str, str] = {}
    for row in rows:
        process_id = row.get("process")
        if not process_id:
            problems.append(
                Problem(
                    "bad-row",
                    "an override row",
                    "has no process. Name the background process whose line it changes.",
                    location=row.location,
                )
            )
            continue
        item = f"{process_id} line {row.get('line')}"
        if process_id in study_process_ids:
            problems.append(
                Problem(
                    "override-database-process",
                    item,
                    f"{process_id} is a process of the database. Change its own lines in "
                    "exchanges.csv. An override changes a process of the background.",
                    location=row.location,
                )
            )
            continue
        exchange = read_exchange(row, process_id, formulas, problems)
        if exchange is None:
            continue
        if exchange.label in sources:
            problems.append(
                Problem(
                    "override-duplicate",
                    exchange.label,
                    f"is already changed at {sources[exchange.label]}",
                    location=row.location,
                )
            )
            continue
        sources[exchange.label] = row.location
        lines.setdefault(process_id, []).append(exchange)
    overrides: list[Override] = []
    for process_id in sorted(lines):
        try:
            overrides.append(
                Override(
                    process_id=process_id,
                    lines=tuple(sorted(lines[process_id], key=lambda e: e.line_id)),
                )
            )
        except ValidationError as error:
            problems.extend(
                replace(p, location=p.location or sources.get(p.item))
                for p in problems_from_validation(error)
            )
    return tuple(overrides), sources
