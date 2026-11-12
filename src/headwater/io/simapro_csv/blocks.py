"""The grammar of a SimaPro CSV export: blocks of rows, and sections inside a block.

After the header, an export is a list of blocks. A block starts with its keyword alone on a line
and ends with `End`:

```
Process                       <- the keyword

Category type                 <- a section: its name alone on a line after a blank line,
material                         then its rows until the next blank line

Products
Electricity, medium voltage {FR}| market for | Cut-off, U;kWh;1;100;not defined;Energy;

End
```

Blocks such as `Units` hold rows only, no sections. SimaPro writes a line break inside a text as
the character 0x7F; it is read back as a line break. Nothing here knows what a row means.
"""

import csv
from collections.abc import Collection, Iterable, Iterator
from dataclasses import dataclass

from headwater.model import Problem, did_you_mean

END = "End"


@dataclass(frozen=True, slots=True)
class Row:
    line: int
    """Its line in the file, from 1."""
    cells: tuple[str, ...]
    """Stripped, without the empty cells at its end. A blank line has none."""

    def __getitem__(self, index: int) -> str:
        """The cell at `index`, or an empty string past the last one."""
        return self.cells[index] if index < len(self.cells) else ""

    @property
    def word(self) -> str | None:
        """The text of a row that holds one cell only, such as a keyword or a section name."""
        return self.cells[0] if len(self.cells) == 1 else None


@dataclass(frozen=True, slots=True)
class Block:
    keyword: str
    line: int
    rows: tuple[Row, ...]
    """Every row between the keyword and `End`, blank lines included."""


@dataclass(frozen=True, slots=True)
class Section:
    name: str
    rows: tuple[Row, ...]


def read_blocks(
    lines: Iterable[str],
    *,
    first_line: int,
    separator: str,
    keywords: Collection[str],
    file: str,
    problems: list[Problem],
) -> Iterator[Block]:
    """The blocks of `lines`, the first of which is line `first_line` of the file, one at a
    time. Adds a problem for each line outside a block that is not a known keyword, and for a
    block with no `End`."""
    current: tuple[str, int] | None = None
    rows: list[Row] = []
    for row in _rows(lines, first_line, separator):
        if current is not None:
            if row.word == END:
                yield Block(current[0], current[1], tuple(rows))
                current, rows = None, []
            else:
                rows.append(row)
        elif row.word in keywords:
            current = (row.cells[0], row.line)
        elif row.cells:
            problems.append(
                Problem(
                    "unknown-block",
                    f"line {row.line}",
                    f"{row.cells[0]!r} is not a block of a SimaPro export headwater reads."
                    f"{did_you_mean(row.cells[0], keywords)} Blocks: {', '.join(sorted(keywords))}",
                    location=f"{file}:{row.line}",
                )
            )
    if current is not None:
        problems.append(
            Problem(
                "missing-end",
                f"block {current[0]}",
                f"starts at line {current[1]} and has no {END} line",
                location=f"{file}:{current[1]}",
            )
        )


def sections(
    block: Block, names: Collection[str], file: str, problems: list[Problem]
) -> list[Section]:
    """The sections of `block`. A row after a blank line must be one of `names`; one that is not
    adds a problem, and its rows are skipped."""
    found: list[tuple[str, list[Row]]] = []
    current: list[Row] | None = None
    after_blank = True
    for row in block.rows:
        if not row.cells:
            after_blank = True
        elif not after_blank:
            if current is not None:
                current.append(row)
        elif row.word in names:
            after_blank, current = False, []
            found.append((row.cells[0], current))
        else:
            after_blank, current = False, None
            problems.append(
                Problem(
                    "unknown-section",
                    f"block {block.keyword} line {row.line}",
                    f"{row.cells[0]!r} is not a section of a {block.keyword} block headwater "
                    f"reads.{did_you_mean(row.cells[0], names)} Sections: "
                    f"{', '.join(sorted(names))}",
                    location=f"{file}:{row.line}",
                )
            )
    return [Section(name, tuple(rows)) for name, rows in found]


def _rows(lines: Iterable[str], first_line: int, separator: str) -> Iterator[Row]:
    reader = csv.reader((line.rstrip("\r") for line in lines), delimiter=separator)
    start = first_line
    for cells in reader:
        found = [cell.strip().replace("\x7f", "\n") for cell in cells]
        while found and not found[-1]:
            found.pop()
        yield Row(start, tuple(found))
        start = first_line + reader.line_num
