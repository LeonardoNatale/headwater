"""A hand-written workbook in the layout of ecoinvent's LCIA implementation file, and a release
whose flow list it maps to.

The workbook's three sheets are CSV files in `tests/fixtures/lcia`, so they can be read in a
review: `read_me.csv`, `cfs.csv` and `indicators.csv`. The `workbooks` fixture writes them to an
.xlsx, with whole numbers as int cells like Excel writes them. Values are made up, except the
climate factors of carbon dioxide (1), methane (29.8) and dinitrogen monoxide (273), which are
the public IPCC AR6 values.

The flow list is the ecoSpold2 fixture source (see `tests/io/conftest.py`), frozen as release
`ecoinvent-3.10-cutoff`. Its elementary flows: e1 carbon dioxide, fossil (air/urban air close to
ground), e2 cadmium (soil/agricultural), e3 carbon dioxide, in air (natural resource/in air),
e4 methane, fossil (air/urban air close to ground).
"""

import csv
from collections.abc import Sequence
from pathlib import Path

import openpyxl
import pytest

from headwater.io.inventory import Inventory
from headwater.release import Release

LCIA = Path(__file__).resolve().parents[2] / "fixtures" / "lcia"

type Cell = str | int | float | None
type Sheet = list[list[Cell]]


def sheet(name: str) -> Sheet:
    """A fixture sheet, with numbers as numbers and blank cells as None."""
    with (LCIA / f"{name}.csv").open(encoding="utf-8", newline="") as file:
        return [[_cell(text) for text in row] for row in csv.reader(file)]


def _cell(text: str) -> Cell:
    if not text:
        return None
    for kind in (int, float):
        try:
            return kind(text)
        except ValueError:
            pass
    return text


class Workbooks:
    """Writes the fixture workbook into a test's folder, with any sheet replaced or left out."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.written = 0

    @staticmethod
    def sheet(name: str) -> Sheet:
        return sheet(name)

    def write(
        self,
        *,
        read_me: Sheet | None = None,
        cfs: Sheet | None = None,
        indicators: Sheet | None = None,
        leave_out: Sequence[str] = (),
    ) -> Path:
        book = openpyxl.Workbook()
        book.remove(book.worksheets[0])
        sheets = {
            "Read me": read_me if read_me is not None else sheet("read_me"),
            "CFs": cfs if cfs is not None else sheet("cfs"),
            "Indicators": indicators if indicators is not None else sheet("indicators"),
        }
        for title, rows in sheets.items():
            if title in leave_out:
                continue
            ws = book.create_sheet(title)
            for row in rows:
                ws.append(row)
        self.written += 1
        path = self.folder / f"LCIA Implementation {self.written}.xlsx"
        book.save(path)
        return path


@pytest.fixture
def workbooks(tmp_path: Path) -> Workbooks:
    return Workbooks(tmp_path)


@pytest.fixture
def workbook(workbooks: Workbooks) -> Path:
    """The fixture workbook as it is."""
    return workbooks.write()


@pytest.fixture(scope="session")
def release(source: Inventory, tmp_path_factory: pytest.TempPathFactory) -> Release:
    return source.freeze(into=tmp_path_factory.mktemp("releases"))
