"""Import impact methods from ecoinvent's LCIA implementation workbook into method folders.

Example: `read_workbook("LCIA Implementation 3.12.xlsx", "EF v3.1")`, then
`import_method(workbook, release)` maps every factor to an elementary flow of the release and
reports what did not map, and `write_method(imported, "methods/EF-3.1")` writes `method.yaml`
and `factors.csv`.
"""

import zipfile
from pathlib import Path

from headwater.io.lcia.reader import MethodImport, import_method, read
from headwater.io.lcia.workbook import LciaWorkbook, WorkbookCategory, WorkbookRow, read_workbook
from headwater.io.method import IndicatorReport, UnmappedFactor, write_method
from headwater.io.source import Format


def _detect(path: Path) -> bool:
    """An .xlsx file with a sheet named `CFs`, read from the file's list of sheets only."""
    if path.suffix.lower() != ".xlsx" or not path.is_file():
        return False
    try:
        with zipfile.ZipFile(path) as book:
            return b'name="CFs"' in book.read("xl/workbook.xml")
    except (OSError, KeyError, zipfile.BadZipFile):
        return False


FORMAT = Format(
    name="ecoinvent-lcia",
    title="ecoinvent's LCIA implementation workbook (.xlsx)",
    detect=_detect,
    read_method=read,
    method_options=frozenset({"method", "categories", "id", "version", "variant"}),
)

__all__ = [
    "FORMAT",
    "IndicatorReport",
    "LciaWorkbook",
    "MethodImport",
    "UnmappedFactor",
    "WorkbookCategory",
    "WorkbookRow",
    "import_method",
    "read",
    "read_workbook",
    "write_method",
]
