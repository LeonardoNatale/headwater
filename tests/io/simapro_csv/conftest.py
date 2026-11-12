"""SimaPro CSV exports written for one test each, in the layout SimaPro writes. Values are made up.

The `sp` fixture gives `Export`: `sp.process(...)` is one `Process` block with every section
SimaPro writes, empty unless given, and `sp.write(*blocks)` writes an export after a header.
"""

from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from headwater.io.source import InventoryOptions, MethodOptions

TESTS = Path(__file__).resolve().parents[2]

_METADATA = (
    "Category type",
    "Process identifier",
    "Type",
    "Process name",
    "Status",
    "Time period",
    "Geography",
    "Technology",
    "Representativeness",
    "Multiple output allocation",
    "Substitution allocation",
    "Cut off rules",
    "Capital goods",
    "Boundary with nature",
    "Infrastructure",
    "Date",
    "Record",
    "Generator",
    "External documents",
    "Literature references",
    "Collection method",
    "Data treatment",
    "Verification",
    "Comment",
    "Allocation rules",
    "System description",
)

_LINES = (
    "Avoided products",
    "Resources",
    "Materials/fuels",
    "Electricity/heat",
    "Emissions to air",
    "Emissions to water",
    "Emissions to soil",
    "Final waste flows",
    "Non material emissions",
    "Social issues",
    "Economic issues",
    "Waste to treatment",
    "Input parameters",
    "Calculated parameters",
)


class Export:
    """Builds SimaPro exports. `write` is set by the fixture to write into the test's folder."""

    HEADER = (
        "{SimaPro 9.5.0.0}\n"
        "{processes}\n"
        "{Date: 25/10/2026}\n"
        "{CSV separator: Semicolon}\n"
        "{Decimal separator: .}\n"
        "{Date separator: /}\n"
        "{Short date format: dd/MM/yyyy}\n"
        "{Convert expressions to constants: Yes}\n"
    )
    METHOD_HEADER = HEADER.replace("{processes}", "{methods}")
    OPTIONS = InventoryOptions(version="1.0", source="test", licence="CC0-1.0")
    METHOD = MethodOptions()
    TOY = TESTS / "fixtures" / "formats" / "simapro-csv" / "toy" / "toy.csv"
    TOY_METHOD = TESTS / "fixtures" / "formats" / "simapro-csv" / "toy-climate" / "toy-climate.csv"

    def __init__(self, folder: Path) -> None:
        self.folder = folder

    @staticmethod
    def process(
        identifier: str,
        products: Sequence[str] = (),
        *,
        waste_treatment: Sequence[str] = (),
        lines: Mapping[str, Sequence[str]] | None = None,
        category_type: str = "material",
        process_name: str = "",
        comment: str = "",
    ) -> str:
        """A `Process` block. `products` and `waste_treatment` are rows of those sections;
        `lines` gives the rows of the other sections by name."""
        values = {
            "Category type": category_type,
            "Process identifier": identifier,
            "Type": "Unit process",
            "Process name": process_name,
            "Comment": comment,
        }
        parts = ["Process", ""]
        for name in _METADATA:
            parts += [name, values.get(name, ""), ""]
        if waste_treatment:
            parts += ["Waste treatment", *waste_treatment, ""]
        else:
            parts += ["Products", *products, ""]
        for name in _LINES:
            parts += [name, *(lines or {}).get(name, ()), ""]
        parts += ["End", ""]
        return "\n".join(parts)

    @staticmethod
    def table(keyword: str, *rows: str) -> str:
        """A block of rows, such as `Units` or `Airborne emissions`."""
        return "\n".join([keyword, *rows, "", "End", ""])

    @staticmethod
    def method(name: str, *categories: tuple[str, Sequence[str]], version: str = "1;0") -> str:
        """A `Method` block: each category is a `name;unit` row and its substance rows."""
        parts = ["Method", "", "Name", name, "", "Version", version, "", "Comment", "", ""]
        parts += ["Category", "Others", "", "Use Damage Assessment", "No", ""]
        parts += ["Use Normalization", "No", "", "Use Weighting", "No", ""]
        parts += ["Use Addition", "No", "", "Weighting unit", "Pt", ""]
        for heading, rows in categories:
            parts += ["Impact category", heading, "", "Substances", *rows, ""]
        parts += ["End", ""]
        return "\n".join(parts)

    def write(self, *blocks: str, header: str = HEADER, encoding: str = "utf-8") -> Path:
        """Writes `export.csv` from blocks, after `header`."""
        path = self.folder / "export.csv"
        path.write_bytes("\n".join([header, *blocks]).encode(encoding))
        return path


@pytest.fixture
def sp(tmp_path: Path) -> Export:
    return Export(tmp_path)
