"""docs/appendix/glossary.md uses today's terms: a database is two tables, not a folder of
`processes/`, and every term a newcomer meets in the README and notebooks 01 to 03 has a row."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GLOSSARY = (ROOT / "docs" / "appendix" / "glossary.md").read_text()
TERMS = re.findall(r"^\| ([^|]+?) \|", GLOSSARY, flags=re.M)
"""The first cell of each table row, header rows included."""

NEEDED = [
    "Database",
    "Study",
    "Scenario",
    "Override",
    "Pin",
    "Release",
    "Line",
    "Role",
    "Supplier",
    "Cut-off",
    "no_factor",
    "Single score",
    "Input hash",
    "Provenance",
    "Uses",
]


def test_the_glossary_names_no_folder_of_processes() -> None:
    assert "processes/" not in GLOSSARY


@pytest.mark.parametrize("term", NEEDED)
def test_the_glossary_has_a_row_for(term: str) -> None:
    assert term in TERMS, f"docs/appendix/glossary.md has no row for {term!r}"
