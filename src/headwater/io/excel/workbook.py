"""Read a Brightway-format Excel workbook into records, with no linking.

The format is the one `bw2io.ExcelImporter` reads. A sheet is a list of blocks:

```
Database          <name>                          once in the whole workbook
Project parameters                                a table: name, amount, formula, ...
Database parameters                               a table, as above
Activity          <name>                          one block per activity
production amount 1                               metadata: the first column is the key
reference product ...
location          GLO
unit              kilogram
Parameters        <group>                         optional, a table of the activity's parameters
Exchanges                                         a table: name, amount, unit, location, type, ...
```

The rules of bw2io that real workbooks rely on are kept:

- A sheet whose first cell is `skip`, or is not text, is not read. A first row `cutoff <n>` keeps
  the first n columns of the sheet.
- Labels are not case sensitive. A table or metadata section ends at a blank row, but an activity
  ends at the next `Activity`, `Database` or `Project parameters` row, and its blank rows are
  ignored.
- A `categories` cell is a tuple written `air::urban air close to ground`.
- Values are read as Excel stored them (`data_only`), so a formula cell gives its last result.
- The uncertainty columns of a row (`uncertainty type`, `loc`, `scale`, `shape`, `minimum`,
  `maximum`, `pedigree`) are kept as written, but for text that is a number, which is read as the
  number, as bw2io's `csv_numerize` reads it. `undefined` and `no uncertainty` mean none.

Every record keeps where it was read, as `sheet!A12`, so a report can point at the row.
"""

import math
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

import openpyxl
from openpyxl.utils.exceptions import InvalidFileException

from headwater.io.excel.uncertainty import FIELDS
from headwater.model import InvalidDataError, Problem

_ACTIVITY_ENDS = frozenset({"activity", "database", "project parameters"})
_SECTIONS = _ACTIVITY_ENDS | {"database parameters"}
_NO_UNCERTAINTY = frozenset({"undefined", "no uncertainty"})
_EXCHANGE_COLUMNS = frozenset(
    {
        "name",
        "amount",
        "unit",
        "location",
        "type",
        "database",
        "reference product",
        "categories",
        "formula",
        "comment",
        "negative",
        *FIELDS,
    }
)
_PARAMETER_COLUMNS = frozenset(
    {"name", "amount", "formula", "unit", "comment", "group", "negative", *FIELDS}
)


@dataclass(frozen=True)
class ParameterRow:
    """One row of a table of parameters. `amount` is the cell as Excel stored it."""

    name: str
    amount: object
    formula: str | None
    unit: str | None
    comment: str | None
    source: str
    scope: str | None = None
    """None for a project or database parameter, or the activity the parameter belongs to."""
    uncertainty: Mapping[str, object] = field(default_factory=dict[str, object])
    """Brightway's uncertainty fields of the row, as `ExchangeRow.uncertainty`."""


@dataclass(frozen=True)
class ExchangeRow:
    """One row of an activity's `Exchanges` table. `amount` is the cell as Excel stored it."""

    name: str
    amount: object
    unit: str | None
    location: str | None
    type: str | None
    database: str | None
    reference_product: str | None
    categories: tuple[str, ...]
    formula: str | None
    comment: str | None
    uncertainty: Mapping[str, object]
    """Brightway's uncertainty fields of the row (`uncertainty type`, `loc`, `scale`...), those
    it has, as written: `{"uncertainty type": 2, "loc": -0.693147, "scale": 0.0953102}`. Empty
    when it has none."""
    source: str
    key: str | None = None
    """For a line of a Brightway project that buys from an activity of its own database: that
    activity's `source`, `twins/cut-b`. The line links to it, never to another activity with the
    same name. A workbook row has none."""

    @property
    def description(self) -> str:
        """`name | reference product | location`, as a person finds the line in the sheet."""
        parts = [self.name, self.reference_product or "", self.location or ""]
        if self.categories:
            parts = [self.name, "::".join(self.categories)]
        return " | ".join(" ".join(p.split()) for p in parts if p)


@dataclass(frozen=True)
class Activity:
    """An `Activity` block: its metadata, its own parameters and its exchanges."""

    name: str
    metadata: Mapping[str, object]
    """The metadata rows, by key in lower case: `production amount`, `reference product`,
    `location`, `unit`, `comment`."""
    parameters: tuple[ParameterRow, ...]
    exchanges: tuple[ExchangeRow, ...]
    source: str

    def text(self, key: str) -> str | None:
        """A metadata value as text, or None when it is missing or blank."""
        return _text(self.metadata.get(key))


@dataclass(frozen=True)
class BrightwayWorkbook:
    """What a workbook says, as written there."""

    path: Path
    database: str
    project_parameters: tuple[ParameterRow, ...]
    database_parameters: tuple[ParameterRow, ...]
    activities: tuple[Activity, ...]
    skipped_sheets: tuple[tuple[str, str], ...] = ()
    """Each sheet that was not read, with the reason."""
    ignored_columns: Mapping[str, int] = field(default_factory=dict[str, int])
    """Each column of an exchange table that headwater does not use, with the rows that have a
    value in it."""

    @property
    def exchange_rows(self) -> int:
        return sum(len(a.exchanges) for a in self.activities)

    @property
    def parameter_rows(self) -> int:
        return (
            len(self.project_parameters)
            + len(self.database_parameters)
            + sum(len(a.parameters) for a in self.activities)
        )


def read_workbook(path: Path | str) -> BrightwayWorkbook:
    """Read a Brightway-format workbook.

    Raises `InvalidDataError` naming every problem in the format: no `Database` row, two of them,
    a table with a column without a label, or a `cutoff` that is not a number. What a row says is
    not checked here.
    """
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
    reader = _Reader(root)
    try:
        for ws in book.worksheets:
            reader.sheet(
                ws.title,
                [
                    _Row(ws.title, number, tuple(_clean(c) for c in row))
                    for number, row in enumerate(ws.iter_rows(values_only=True), 1)
                ],
            )
    finally:
        book.close()
    return reader.finish()


# --- cells ----------------------------------------------------------------------------------


def _clean(cell: object) -> object:
    """A text cell without the spaces around it, and a blank one as None."""
    if isinstance(cell, str):
        cell = cell.strip()
        return cell or None
    return cell


def _text(cell: object) -> str | None:
    """A cell as text. A number is written the way Python writes it, so `1` and `1.0` differ."""
    return None if cell is None else str(cell).strip() or None


@dataclass(frozen=True)
class _Row:
    sheet: str
    number: int
    cells: tuple[object, ...]

    @property
    def source(self) -> str:
        return f"{self.sheet}!A{self.number}"

    @property
    def blank(self) -> bool:
        return all(c is None for c in self.cells)

    @property
    def label(self) -> str | None:
        first = self.cells[0] if self.cells else None
        return first.lower() if isinstance(first, str) else None

    def cell(self, index: int) -> object:
        return self.cells[index] if index < len(self.cells) else None


# --- reading --------------------------------------------------------------------------------


class _Reader:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.problems: list[Problem] = []
        self.databases: list[tuple[str, str]] = []
        self.project: list[ParameterRow] = []
        self.database_parameters: list[ParameterRow] = []
        self.activities: list[Activity] = []
        self.skipped: list[tuple[str, str]] = []
        self.ignored: dict[str, int] = {}

    def problem(self, code: str, item: str, message: str, location: str) -> None:
        self.problems.append(Problem(code, item, message, location=location))

    def sheet(self, title: str, rows: list[_Row]) -> None:
        first = rows[0].cells[0] if rows and rows[0].cells else None
        if not isinstance(first, str):
            self.skipped.append((title, "its first cell is not text"))
            return
        if first.lower() == "skip":
            self.skipped.append((title, "its first cell says skip"))
            return
        if first.lower() == "cutoff":
            rows = self._cut(title, rows)
        i = 0
        while i < len(rows):
            row = rows[i]
            label = row.label
            if label == "database":
                i = self._database(rows, i)
            elif label == "project parameters":
                i = self._parameters(rows, i, self.project, None)
            elif label == "database parameters" and all(c is None for c in row.cells[1:]):
                i = self._parameters(rows, i, self.database_parameters, None)
            elif label == "activity":
                i = self._activity(rows, i)
            else:
                i += 1

    def _cut(self, title: str, rows: list[_Row]) -> list[_Row]:
        first = rows[0]
        try:
            width = int(str(first.cell(1)))
        except ValueError:
            self.problem(
                "bad-cutoff",
                f"sheet {title}",
                f"its first row says cutoff, so the next cell must be the number of columns to "
                f"read, but it is {first.cell(1)!r}",
                first.source,
            )
            return rows
        return [_Row(r.sheet, r.number, r.cells[:width]) for r in rows]

    def _database(self, rows: list[_Row], start: int) -> int:
        name = _text(rows[start].cell(1))
        if name is None:
            self.problem(
                "bad-database",
                f"row {rows[start].source}",
                "a Database row needs the name of the database in its second cell",
                rows[start].source,
            )
        else:
            self.databases.append((name, rows[start].source))
        i = start + 1
        while i < len(rows) and not rows[i].blank and rows[i].label not in _SECTIONS:
            i += 1
        return i

    def _parameters(
        self, rows: list[_Row], start: int, into: list[ParameterRow], scope: str | None
    ) -> int:
        """A table of parameters whose label is at `start`; it ends at a blank row."""
        end = start + 1
        while end < len(rows) and not rows[end].blank:
            end += 1
        table = rows[start + 1 : end]
        into.extend(self._parameter_rows(table, scope))
        return end

    def _parameter_rows(self, table: list[_Row], scope: str | None) -> list[ParameterRow]:
        if not table:
            return []
        header = self._header(table[0])
        if header is None:
            return []
        found: list[ParameterRow] = []
        for row in table[1:]:
            values = dict(zip(header, row.cells, strict=False))
            self._count_ignored(values, _PARAMETER_COLUMNS, "parameters")
            name = _text(values.get("name"))
            if name is None:
                self.problem(
                    "bad-parameter",
                    f"row {row.source}",
                    "a row of a table of parameters has no name",
                    row.source,
                )
                continue
            found.append(
                ParameterRow(
                    name=name,
                    amount=values.get("amount"),
                    formula=_text(values.get("formula")),
                    unit=_text(values.get("unit")),
                    comment=_text(values.get("comment")),
                    source=row.source,
                    scope=scope,
                    uncertainty=_uncertainty(values),
                )
            )
        return found

    def _count_ignored(
        self, values: Mapping[str, object], used: frozenset[str], table: str
    ) -> None:
        for column, value in values.items():
            if column not in used and value is not None:
                key = column if table == "exchanges" else f"{table}: {column}"
                self.ignored[key] = self.ignored.get(key, 0) + 1

    def _header(self, row: _Row) -> list[str] | None:
        """The column labels of a table: lower case, with the empty cells at the end cut."""
        cells = list(row.cells)
        while cells and cells[-1] is None:
            cells.pop()
        labels = [_text(c) for c in cells]
        if any(label is None for label in labels):
            self.problem(
                "bad-header",
                f"row {row.source}",
                "a column of this table has no label. Every column up to the last one needs one.",
                row.source,
            )
            return None
        names = [(label or "").lower() for label in labels]
        twice = sorted({n for n in names if names.count(n) > 1})
        if twice:
            self.problem(
                "bad-header",
                f"row {row.source}",
                f"column {', '.join(map(repr, twice))} appears twice in this table",
                row.source,
            )
            return None
        return names

    def _activity(self, rows: list[_Row], start: int) -> int:
        end = start + 1
        while end < len(rows) and rows[end].label not in _ACTIVITY_ENDS:
            end += 1
        block = [r for r in rows[start:end] if not r.blank]
        head = block[0]
        name = _text(head.cell(1))
        if name is None:
            self.problem(
                "bad-activity",
                f"row {head.source}",
                "an Activity row needs the name of the activity in its second cell",
                head.source,
            )
            return end
        found: dict[str, list[int]] = {"parameters": [], "exchanges": []}
        for index, row in enumerate(block[1:], 1):
            if row.label in found:
                found[row.label].append(index)
        for label, at in found.items():
            if len(at) > 1:
                self.problem(
                    "bad-activity",
                    f"activity {name}",
                    f"has {len(at)} {label} sections, and an activity has one",
                    head.source,
                )
        parameters_at = found["parameters"][0] if found["parameters"] else None
        exchanges_at = found["exchanges"][0] if found["exchanges"] else None
        sections = [i for i in (parameters_at, exchanges_at) if i is not None]
        metadata_end = min(sections, default=len(block))
        metadata: dict[str, object] = {}
        for row in block[1:metadata_end]:
            key = _text(row.cell(0))
            if key is not None:
                metadata[key.lower()] = row.cell(1)
        parameters: list[ParameterRow] = []
        if parameters_at is not None:
            after = (
                exchanges_at if exchanges_at is not None and exchanges_at > parameters_at else None
            )
            parameters = self._parameter_rows(block[parameters_at + 1 : after], name)
        exchanges: list[ExchangeRow] = []
        if exchanges_at is not None:
            exchanges = self._exchange_rows(block[exchanges_at + 1 :])
        self.activities.append(
            Activity(
                name=name,
                metadata=metadata,
                parameters=tuple(parameters),
                exchanges=tuple(exchanges),
                source=head.source,
            )
        )
        return end

    def _exchange_rows(self, table: list[_Row]) -> list[ExchangeRow]:
        if not table:
            return []
        header = self._header(table[0])
        if header is None:
            return []
        found: list[ExchangeRow] = []
        for row in table[1:]:
            values = dict(zip(header, row.cells, strict=False))
            categories = _text(values.get("categories"))
            found.append(
                ExchangeRow(
                    name=_text(values.get("name")) or "",
                    amount=values.get("amount"),
                    unit=_text(values.get("unit")),
                    location=_text(values.get("location")),
                    type=(_text(values.get("type")) or "").lower() or None,
                    database=_text(values.get("database")),
                    reference_product=_text(values.get("reference product")),
                    categories=(
                        tuple(part.strip() for part in categories.split("::")) if categories else ()
                    ),
                    formula=_text(values.get("formula")),
                    comment=_text(values.get("comment")),
                    uncertainty=_uncertainty(values),
                    source=row.source,
                )
            )
            self._count_ignored(values, _EXCHANGE_COLUMNS, "exchanges")
        return found

    def finish(self) -> BrightwayWorkbook:
        if not self.databases:
            self.problem(
                "no-database",
                f"workbook {self.path.name}",
                "has no Database row. A Brightway workbook names its database in a row whose "
                "first cell is Database and whose second cell is the name.",
                str(self.path),
            )
        if len(self.databases) > 1:
            where = ", ".join(f"{name} ({source})" for name, source in self.databases)
            self.problem(
                "multiple-databases",
                f"workbook {self.path.name}",
                f"has {len(self.databases)} Database rows: {where}. A workbook holds one.",
                self.databases[1][1],
            )
        if self.problems:
            raise InvalidDataError(self.problems)
        return BrightwayWorkbook(
            path=self.path,
            database=self.databases[0][0],
            project_parameters=tuple(self.project),
            database_parameters=tuple(self.database_parameters),
            activities=tuple(self.activities),
            skipped_sheets=tuple(self.skipped),
            ignored_columns={k: v for k, v in self.ignored.items() if v},
        )


def _uncertainty(values: Mapping[str, object]) -> dict[str, object]:
    """The uncertainty fields of a row that has them. Text that is a number is that number, so the
    type `2` written as text is a lognormal, as bw2io reads it."""
    found: dict[str, object] = {}
    for column in FIELDS:
        cell = values.get(column)
        if isinstance(cell, str):
            if column == "uncertainty type" and cell.lower() in _NO_UNCERTAINTY:
                continue
            try:
                value = float(cell) if "_" not in cell else math.nan
            except ValueError:
                value = math.nan
            if math.isfinite(value):
                cell = value
        if cell is not None:
            found[column] = cell
    return found
