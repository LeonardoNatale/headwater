"""Read one method from ecoinvent's LCIA implementation workbook.

The workbook has three sheets:

- `CFs`: one factor per row, with the columns Method, Category, Indicator, Name, Compartment,
  Subcompartment and CF. A flow is named by its name, compartment and subcompartment.
- `Indicators`: Method, Category, Indicator and Indicator Unit, one row per factor.
- `Read me`: a `Database Version` row, for example `3.10, November 2023`.
"""

import math
import re
import zipfile
from collections import defaultdict
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

import openpyxl
from openpyxl.utils import get_column_letter
from openpyxl.utils.exceptions import InvalidFileException
from openpyxl.worksheet._read_only import ReadOnlyWorksheet

from headwater.model import InvalidDataError, Problem, did_you_mean

CFS = "CFs"
INDICATORS = "Indicators"
READ_ME = "Read me"
CF_COLUMNS = ("Method", "Category", "Indicator", "Name", "Compartment", "Subcompartment", "CF")
INDICATOR_COLUMNS = ("Method", "Category", "Indicator", "Indicator Unit")
VERSION_LABEL = "Database Version"

_VERSION = re.compile(r"\d+(?:\.\d+)+")

type Cell = object


@dataclass(frozen=True)
class WorkbookRow:
    """One factor of the workbook, for a flow named by name and compartment.

    `compartment` joins the compartment and subcompartment with `/`, the way a release writes the
    compartment of an ecoinvent flow: `air/urban air close to ground`. `location` is the row's
    first cell, for example `CFs!A12`.
    """

    category: str
    name: str
    compartment: str
    factor: float
    location: str


@dataclass(frozen=True)
class WorkbookCategory:
    """An impact category of the method, with the name of its indicator and the indicator's unit.

    Example: `climate change: fossil`, `global warming potential (GWP100)`, `kg CO2-Eq`.
    """

    name: str
    indicator: str
    unit: str


@dataclass(frozen=True)
class LciaWorkbook:
    """The factors of one method of the workbook, as written there."""

    path: Path
    method: str
    """The method's name in the workbook, for example `EF v3.1`."""
    version: str
    """The ecoinvent version the workbook is for, from its `Read me` sheet, for example `3.10`."""
    categories: tuple[WorkbookCategory, ...]
    """Sorted by name."""
    rows: tuple[WorkbookRow, ...]
    """In the order of the workbook."""
    left_out: tuple[str, ...] = ()
    """Categories of the method that were not chosen, sorted. Empty when every one was read."""


def read_workbook(
    path: Path | str, method: str, *, categories: Sequence[str] | None = None
) -> LciaWorkbook:
    """Read the factors of `method`, for example `EF v3.1`, and the units of its categories.

    `categories` chooses some categories of the method by name, for example `climate change:
    direct land use change` of `IPCC 2021`. The rows of the others are left out unchecked, so
    a category that cannot be read, such as one with five indicators, does not stop the rest.
    None reads every category.

    Rows of other methods are skipped. Raises `InvalidDataError` with every problem found: a
    missing sheet or column, an unknown method or category, a factor that is not a number, a
    flow written twice for one category, a category with two indicators or with no unit or two
    units.
    """
    chosen = None if categories is None else frozenset(c.strip() for c in categories)
    root = Path(path)
    try:
        book = openpyxl.load_workbook(root, read_only=True, data_only=True)
    except (OSError, InvalidFileException, zipfile.BadZipFile, KeyError) as exc:
        raise InvalidDataError(
            [
                Problem(
                    "unreadable-file",
                    f"workbook {root.name}",
                    f"cannot be read as an .xlsx workbook: {exc}",
                    location=str(root),
                )
            ]
        ) from None
    try:
        missing = [name for name in (READ_ME, CFS, INDICATORS) if name not in book.sheetnames]
        if missing:
            raise InvalidDataError(
                Problem(
                    "missing-sheet",
                    f"sheet {name}",
                    f"{root.name} has no sheet {name!r}. It has: {', '.join(book.sheetnames)}",
                )
                for name in missing
            )
        problems: list[Problem] = []
        version = _version(_sheet(book, READ_ME), problems)
        rows, indicators, found = _factors(_sheet(book, CFS), root, method, chosen, problems)
        units = _units(_sheet(book, INDICATORS), method, chosen, problems)
    finally:
        book.close()
    problems += _unknown_categories(method, chosen, found)
    read = _categories(indicators, units, problems)
    if problems or version is None:
        raise InvalidDataError(problems)
    left_out = tuple(sorted(found - chosen)) if chosen is not None else ()
    return LciaWorkbook(root, method, version, read, rows, left_out)


def _sheet(book: openpyxl.Workbook, name: str) -> ReadOnlyWorksheet:
    """A sheet read to its last row. In read-only mode openpyxl stops at the size the file
    records, and a file can record a wrong one: rows past it would be dropped without a word."""
    sheet = book[name]
    assert isinstance(sheet, ReadOnlyWorksheet)
    sheet.reset_dimensions()
    return sheet


# --- Read me --------------------------------------------------------------------------------


def _version(sheet: ReadOnlyWorksheet, problems: list[Problem]) -> str | None:
    for row in sheet.iter_rows(values_only=True):
        cells = [c for c in row if c is not None]
        if not cells or _text(cells[0]) != VERSION_LABEL:
            continue
        value = cells[1] if len(cells) > 1 else None
        found = _VERSION.match(value.strip()) if isinstance(value, str) else None
        if found is None:
            problems.append(
                Problem(
                    "missing-version",
                    f"sheet {READ_ME}",
                    f"{VERSION_LABEL} is {value!r}, not text that starts with a version such as "
                    "'3.10, November 2023'",
                )
            )
            return None
        return found.group()
    problems.append(
        Problem(
            "missing-version",
            f"sheet {READ_ME}",
            f"has no {VERSION_LABEL!r} row, so the ecoinvent version of the workbook is unknown",
        )
    )
    return None


# --- CFs ------------------------------------------------------------------------------------


def _factors(
    sheet: ReadOnlyWorksheet,
    root: Path,
    method: str,
    chosen: frozenset[str] | None,
    problems: list[Problem],
) -> tuple[tuple[WorkbookRow, ...], dict[str, set[str]], set[str]]:
    """The rows of the chosen categories of `method`, the indicator names of each of those
    categories, and the names of every category of the method."""
    lines = _rows(sheet)
    columns = _columns(next(lines, None), CF_COLUMNS, CFS, problems)
    if columns is None:
        return (), {}, set()
    rows: list[WorkbookRow] = []
    indicators: dict[str, set[str]] = defaultdict(set)
    first_row: dict[tuple[str, str, str], str] = {}
    methods: set[str] = set()
    found: set[str] = set()
    for number, cells in lines:
        cell = _Cells(CFS, cells, columns, number)
        methods.add(cell.text("Method"))
        if cell.text("Method") != method:
            continue
        if cell.text("Category"):
            found.add(cell.text("Category"))
            if chosen is not None and cell.text("Category") not in chosen:
                continue
        missing = [c for c in ("Category", "Indicator", "Name", "Compartment") if not cell.text(c)]
        if missing:
            problems.append(
                Problem(
                    "missing-cell",
                    f"sheet {CFS} row {number}",
                    f"has no {', '.join(missing)}",
                    location=cell.where("Method"),
                )
            )
            continue
        category, name = cell.text("Category"), cell.text("Name")
        parts = (cell.text("Compartment"), cell.text("Subcompartment"))
        compartment = "/".join(p for p in parts if p)
        key = (category, name, compartment)
        label = f"{category}: {name} ({compartment})"
        location = cell.where("Method")
        indicators[category].add(cell.text("Indicator"))
        factor = _factor(cell, label, problems)
        if key in first_row:
            problems.append(
                Problem(
                    "duplicate-factor",
                    label,
                    "is written twice for this category",
                    location=f"{first_row[key]}, {location}",
                )
            )
            continue
        first_row[key] = location
        if factor is not None:
            rows.append(WorkbookRow(category, name, compartment, factor, location))
    if method not in methods:
        methods.discard("")
        problems.append(
            Problem(
                "unknown-method",
                f"method {method}",
                f"is not in {root.name}.{did_you_mean(method, methods)} Its methods: "
                f"{', '.join(sorted(methods))}",
            )
        )
    return tuple(rows), indicators, found


def _unknown_categories(
    method: str, chosen: frozenset[str] | None, found: set[str]
) -> list[Problem]:
    """A problem for each chosen category the method does not have. None when the method has no
    category at all: it is unknown, and that is already a problem."""
    if chosen is None or not found:
        return []
    return [
        Problem(
            "unknown-category",
            f"category {name}",
            f"is not a category of {method}.{did_you_mean(name, found)} Its categories: "
            f"{', '.join(sorted(found))}",
        )
        for name in sorted(chosen - found)
    ]


def _factor(cell: "_Cells", label: str, problems: list[Problem]) -> float | None:
    """The CF of a row as a float, or None after adding a problem. Excel stores whole numbers
    as int."""
    value = cell.value("CF")
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        reason = "is empty" if value is None else f"{value!r} is not a finite number"
        problems.append(Problem("bad-factor", label, f"CF {reason}", location=cell.where("CF")))
        return None
    return float(value)


# --- Indicators -----------------------------------------------------------------------------


def _units(
    sheet: ReadOnlyWorksheet, method: str, chosen: frozenset[str] | None, problems: list[Problem]
) -> dict[tuple[str, str], set[str]]:
    """The units of each chosen category and indicator of `method`."""
    lines = _rows(sheet)
    columns = _columns(next(lines, None), INDICATOR_COLUMNS, INDICATORS, problems)
    units: dict[tuple[str, str], set[str]] = defaultdict(set)
    if columns is None:
        return units
    for number, cells in lines:
        cell = _Cells(INDICATORS, cells, columns, number)
        if cell.text("Method") != method:
            continue
        if chosen is not None and cell.text("Category") not in chosen:
            continue
        unit = cell.text("Indicator Unit")
        if not unit:
            problems.append(
                Problem(
                    "missing-cell",
                    f"sheet {INDICATORS} row {number}",
                    "has no Indicator Unit",
                    location=cell.where("Indicator Unit"),
                )
            )
            continue
        units[cell.text("Category"), cell.text("Indicator")].add(unit)
    return units


def _categories(
    indicators: dict[str, set[str]],
    units: dict[tuple[str, str], set[str]],
    problems: list[Problem],
) -> tuple[WorkbookCategory, ...]:
    categories: list[WorkbookCategory] = []
    for category, names in sorted(indicators.items()):
        if len(names) > 1:
            problems.append(
                Problem(
                    "two-indicators",
                    f"category {category}",
                    f"has the indicators {' and '.join(sorted(names))}. A category must have "
                    "one indicator, because the indicator's id is made from the category.",
                )
            )
            continue
        [indicator] = names
        found = units.get((category, indicator), set())
        if len(found) != 1:
            problems.append(
                Problem(
                    "missing-unit" if not found else "conflicting-unit",
                    f"category {category}",
                    f"indicator {indicator!r} has "
                    + (f"the units {' and '.join(sorted(found))}" if found else "no unit")
                    + f" in sheet {INDICATORS}",
                )
            )
            continue
        categories.append(WorkbookCategory(category, indicator, next(iter(found))))
    return tuple(categories)


# --- cells ----------------------------------------------------------------------------------


def _rows(sheet: ReadOnlyWorksheet) -> Iterator[tuple[int, Sequence[Cell]]]:
    """Each row that has a value, with its number in the sheet."""
    for number, cells in enumerate(sheet.iter_rows(values_only=True), start=1):
        if any(c is not None and _text(c) != "" for c in cells):
            yield number, cells


def _columns(
    header: tuple[int, Sequence[Cell]] | None,
    expected: tuple[str, ...],
    sheet: str,
    problems: list[Problem],
) -> dict[str, int] | None:
    """The index of each expected column, or None after adding a problem."""
    names = [_text(c) for c in header[1]] if header else []
    columns = {name: i for i, name in enumerate(names) if name}
    parts: list[str] = []
    for name in dict.fromkeys(n for n in names if n and names.count(n) > 1):
        parts.append(f"column {name!r} appears twice")
    for name in columns:
        if name not in expected:
            parts.append(f"unknown column {name!r}.{did_you_mean(name, expected)}")
    missing = [c for c in expected if c not in columns]
    if missing:
        parts.append(f"missing column(s): {', '.join(missing)}")
    if parts:
        problems.append(
            Problem(
                "bad-columns",
                f"sheet {sheet}",
                f"{'; '.join(parts)}; expected columns: {', '.join(expected)}",
                location=f"{sheet}!{header[0] if header else 1}",
            )
        )
        return None
    return columns


def _text(cell: Cell) -> str:
    return cell.strip() if isinstance(cell, str) else "" if cell is None else str(cell)


class _Cells:
    """The cells of one row, by column name."""

    def __init__(
        self, sheet: str, cells: Sequence[Cell], columns: dict[str, int], number: int
    ) -> None:
        self.sheet = sheet
        self.cells = cells
        self.columns = columns
        self.number = number

    def value(self, column: str) -> Cell:
        index = self.columns[column]
        return self.cells[index] if index < len(self.cells) else None

    def text(self, column: str) -> str:
        return _text(self.value(column))

    def where(self, column: str) -> str:
        return f"{self.sheet}!{get_column_letter(self.columns[column] + 1)}{self.number}"
