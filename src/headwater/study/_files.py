"""Reading the files a person writes into problems or values: YAML and CSV for a database, and the
CSV decision files (`cut_offs.csv`, `no_factor.csv` and the mapping files), which share one reader
and one parser for `when`."""

import csv
import io
import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import yaml
from pydantic import BaseModel, ValidationError

from headwater.model import Problem, RecordError, did_you_mean

PLACEHOLDER = re.compile(r"TODO(?![\w-])")
"""The word TODO that the Excel import writes where a person must: `TODO`, `TODO: say what`. Not
`TODO-list` or `TODOs`."""


def relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


# --- YAML ------------------------------------------------------------------------------------


class _UniqueKeyLoader(yaml.SafeLoader):
    """A safe loader that refuses a key written twice. PyYAML keeps the last one silently."""


_MERGE_TAG = "tag:yaml.org,2002:merge"


def _construct_unique_mapping(
    loader: _UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[Any, Any]:
    seen: set[object] = set()
    for key_node, _ in node.value:
        if key_node.tag == _MERGE_TAG:
            continue  # `<<: *anchor`: the standard constructor merges it, and may be overridden
        key = cast(object, loader.construct_object(key_node, deep=deep))  # pyright: ignore[reportUnknownMemberType]
        try:
            if key in seen:
                raise yaml.constructor.ConstructorError(
                    None, None, f"duplicate key {key!r}", key_node.start_mark
                )
            seen.add(key)
        except TypeError:  # an unhashable key: the standard loader reports it
            break
    return yaml.SafeLoader.construct_mapping(loader, node, deep)  # pyright: ignore[reportUnknownMemberType]


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping
)


def read_yaml(path: Path, root: Path, problems: list[Problem]) -> dict[str, Any] | None:
    """The mapping in a YAML file, or None after adding a problem. An empty file is `{}`."""
    rel = relative(path, root)
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        problems.append(Problem("unreadable-file", rel, f"cannot be read: {exc}", location=rel))
        return None
    return parse_yaml(text, rel, problems)


def parse_yaml(text: str, rel: str, problems: list[Problem]) -> dict[str, Any] | None:
    """The mapping in YAML text, or None after adding a problem that names `rel`, the file it is
    from. Empty text is `{}`."""
    try:
        data = yaml.load(text, Loader=_UniqueKeyLoader)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f"{rel}:{mark.line + 1}" if mark is not None else rel
        reason = getattr(exc, "problem", None) or str(exc)
        problems.append(Problem("bad-yaml", rel, str(reason), location=where))
        return None
    if data is None:
        return {}
    if not isinstance(data, dict):
        problems.append(
            Problem("bad-yaml", rel, "must be a mapping of keys to values", location=rel)
        )
        return None
    return {str(k): v for k, v in data.items()}  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]


# --- CSV -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Row:
    cells: Mapping[str, str]
    location: str
    """The file and the line the row starts on, `cut_offs.csv:3`."""
    line: int

    def get(self, column: str) -> str:
        """The cell, stripped, or "" for a blank cell and a column the file does not have."""
        return self.cells.get(column, "")


def read_csv(
    path: Path,
    root: Path,
    *,
    required: tuple[str, ...],
    optional: tuple[str, ...] = (),
    renamed: Mapping[str, str] | None = None,
    problems: list[Problem],
) -> list[Row]:
    """The rows of a CSV file. A wrong header or a ragged row adds a problem.

    One row is one line: a row with fewer or more cells than the header, and a cell that spans
    lines, are problems. `renamed` maps a column that was called otherwise before to its name now,
    so the problem says so: `{"date": "when"}`.
    """
    rel = relative(path, root)
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        problems.append(Problem("unreadable-file", rel, f"cannot be read: {exc}", location=rel))
        return []
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    records: list[tuple[list[str], int]] = []  # each row with the line where it starts
    try:
        start = 1
        for cells in reader:
            records.append((cells, start))
            start = reader.line_num + 1
    except csv.Error as exc:
        problems.append(
            Problem(
                "bad-csv",
                rel,
                f"is not valid CSV: {exc}. Check for a missing or extra quote.",
                location=f"{rel}:{reader.line_num}",
            )
        )
        return []
    expected = f"expected columns: {', '.join(required)}"
    if optional:
        expected += f" (optional: {', '.join(optional)})"
    if not records or not any(cell.strip() for cell in records[0][0]):
        problems.append(Problem("bad-columns", rel, f"file is empty; {expected}", location=rel))
        return []
    columns = [cell.strip() for cell in records[0][0]]
    parts: list[str] = []
    for column in dict.fromkeys(c for c in columns if columns.count(c) > 1):
        parts.append(f"column {column!r} appears twice")
    for column in columns:
        if column not in required and column not in optional:
            now = (renamed or {}).get(column)
            hint = (
                f" It is now called {now!r}."
                if now is not None
                else did_you_mean(column, (*required, *optional))
            )
            parts.append(f"unknown column {column!r}.{hint}")
    missing = [c for c in required if c not in columns]
    if missing:
        parts.append(f"missing column(s): {', '.join(missing)}")
    if parts:
        problems.append(
            Problem("bad-columns", rel, f"{'; '.join(parts)}; {expected}", location=f"{rel}:1")
        )
        return []
    rows: list[Row] = []
    for cells, line in records[1:]:
        where = f"{rel}:{line}"
        if not any(cell.strip() for cell in cells):
            continue
        if any("\n" in cell or "\r" in cell for cell in cells):
            problems.append(
                Problem(
                    "bad-row",
                    f"file {rel}",
                    "a value spans several lines. One row is one line, so a quote is probably "
                    "missing or extra.",
                    location=where,
                )
            )
            continue
        if len(cells) != len(columns):
            problems.append(
                Problem(
                    "bad-row",
                    f"file {rel}",
                    f"row has {len(cells)} cells, but the header has {len(columns)}. "
                    "A comma inside a value needs quotes around the value.",
                    location=where,
                )
            )
            continue
        cells_by_column = {c: v.strip() for c, v in zip(columns, cells, strict=True)}
        rows.append(Row(cells_by_column, where, line))
    return rows


type Report = Callable[[str, str], None]
"""Adds a problem: its code, then its message."""


def read_when(text: str, report: Report, *, naive: bool = False) -> datetime | None:
    """An ISO date or time in UTC, or None after reporting `bad-date`.

    A date is midnight, and a time with no zone is UTC: `2026-10-11` and `2026-10-11T09:30`.
    The database's files keep it without a zone (`naive=True`). The mapping files keep it with one,
    since a decision's time ends up in a release's issues.
    """
    try:
        when = datetime.fromisoformat(text)
    except ValueError:
        report("bad-date", f"when {text!r} is not an ISO date or time, for example 2026-10-11")
        return None
    when = when.replace(tzinfo=UTC) if when.tzinfo is None else when.astimezone(UTC)
    return when.replace(tzinfo=None) if naive else when


def parse_number(text: str, what: str) -> float:
    """A finite number from a cell. Raises ValueError with a message for a person."""
    if not text:
        raise ValueError(f"{what} is empty")
    try:
        value = float(text)
    except ValueError:
        hint = " Use a dot as the decimal separator." if "," in text else ""
        raise ValueError(f"{what} {text!r} is not a number.{hint}") from None
    if not math.isfinite(value):
        raise ValueError(f"{what} {text!r} must be a finite number")
    return value


def read_number(row: Row, column: str, item: str, problems: list[Problem]) -> float | None:
    """The number in a column of a row, or None after adding a problem that names `item`."""
    try:
        return parse_number(row.get(column), column)
    except ValueError as exc:
        problems.append(Problem("bad-number", item, str(exc), location=row.location))
        return None


def number_or_formula(raw: object, what: str) -> tuple[float | None, str | None]:
    """A YAML value that is a number, or a formula written as text, as `(number, None)` or
    `(None, formula text)`. Raises ValueError, with a message for a person, for anything else.

    A text that is a number is a number: `"0.30"` is 0.3.
    """
    if isinstance(raw, bool):
        raise ValueError(
            f"{what} is a yes/no value. YAML reads yes, no, on and off that way. "
            "Write a number, or put quotes around a formula"
        )
    if isinstance(raw, int | float):
        number = float(raw)
    elif isinstance(raw, str):
        text = raw.strip()
        try:
            number = float(text)
        except ValueError:
            return None, text
    else:
        raise ValueError(f"{what} must be a number or a formula, got {raw!r}")
    if not math.isfinite(number):
        raise ValueError(f"{what} {raw!r} must be a finite number")
    return number, None


def blank_to_none(text: str) -> str | None:
    return text or None


# --- pydantic errors -------------------------------------------------------------------------

_SCALARS = (str, int, float, bool)
_WITHOUT_INPUT = {"missing", "extra_forbidden"}


def problems_from_validation(
    error: ValidationError,
    *,
    item: str | None = None,
    prefix: str | None = None,
    location: str | None = None,
    model: type[BaseModel] | None = None,
    rename: Mapping[str, str] | None = None,
) -> list[Problem]:
    """Turn a pydantic error into problems that name the item.

    `item` names a row or record. Without it, each problem is named `<prefix>: <field>`.
    `rename` maps record field names to the column names a person sees in the file.
    """
    found: list[Problem] = []
    for detail in error.errors(include_url=False):
        ctx = detail.get("ctx") or {}
        cause = ctx.get("error")
        if isinstance(cause, RecordError):
            found.extend(p if p.location else replace(p, location=location) for p in cause.problems)
            continue
        names = [str(part) for part in detail["loc"]]
        names = [(rename or {}).get(n, n) for n in names]
        field = ".".join(names)
        message = detail["msg"].removeprefix("Value error, ")
        if detail["type"] not in _WITHOUT_INPUT and isinstance(detail.get("input"), _SCALARS):
            message += f" (got {detail['input']!r})"
        if detail["type"] == "extra_forbidden" and model is not None and names:
            message += f".{did_you_mean(names[-1], model.model_fields)}"
        if item is not None:
            text = f"{field}: {message}" if field else message
            found.append(Problem("invalid-field", item, text, location=location))
        else:
            where = f"{prefix}: {field}" if field else str(prefix)
            found.append(Problem("invalid-field", where, message, location=location))
    return found
