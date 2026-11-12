"""Import Brightway-format Excel inventories into database folders.

Example: `import_excel("inventory.xlsx", release, "studies/tshirt")` reads the workbook, links
every line to the release by exact name, reference product and location, writes the database folder
and validates it. A line that cannot be linked is written with no supplier and reported in
`done.report`, so nothing is retyped and nothing is dropped.
"""

from headwater.io.excel.convert import Conversion, DatabaseDraft, convert
from headwater.io.excel.importer import ExcelImport, import_excel
from headwater.io.excel.index import ReleaseIndex
from headwater.io.excel.mapping import ImportMapping, MappingRow, read_mapping
from headwater.io.excel.report import (
    DatabaseUse,
    ImportReport,
    Note,
    Outcome,
    ParameterOutcome,
    Status,
)
from headwater.io.excel.workbook import (
    Activity,
    BrightwayWorkbook,
    ExchangeRow,
    ParameterRow,
    read_workbook,
)
from headwater.io.excel.write import write_database

__all__ = [
    "Activity",
    "BrightwayWorkbook",
    "Conversion",
    "DatabaseUse",
    "ExcelImport",
    "ExchangeRow",
    "ImportMapping",
    "ImportReport",
    "MappingRow",
    "Note",
    "Outcome",
    "ParameterOutcome",
    "ParameterRow",
    "ReleaseIndex",
    "Status",
    "DatabaseDraft",
    "convert",
    "import_excel",
    "read_mapping",
    "read_workbook",
    "write_database",
]
