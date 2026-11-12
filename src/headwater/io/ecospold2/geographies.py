"""ecoinvent's geographies, from `MasterData/Geographies.xml`, as the `geographies` table of a
release: one row per geography, with its short code and its name.

Example: `GLO` is "Global", `RoW` is "Rest-of-World", `CN-SX` is "China, Shanxi (山西)". Each
code is unique. A process's location is one of these codes.

The file does not say which region contains which: it has no parent regions. They are not
invented here.
"""

from pathlib import Path

import pyarrow as pa
from lxml import etree

from headwater.io.ecospold2.masterdata import NS, parse_xml, text
from headwater.model import InvalidDataError, Problem
from headwater.release.schema import GEOGRAPHIES

FILE = "Geographies.xml"


def read_geographies(path: Path | str) -> pa.Table:
    """The geographies of an ecoSpold2 `Geographies.xml`, sorted by code. Raises naming every
    geography with no code or no name, and every code given twice."""
    file = Path(path)
    item = f"MasterData/{file.name}"
    if not file.is_file():
        raise InvalidDataError(
            [Problem("missing-file", item, f"is missing in {file.parent.parent}")]
        )
    try:
        root = parse_xml(file)
    except etree.XMLSyntaxError as error:
        raise InvalidDataError(
            [Problem("unreadable-file", item, f"is not valid XML: {error}")]
        ) from None
    problems: list[Problem] = []
    found: dict[str, str] = {}
    for element in root.iterfind(f"{NS}geography"):
        code, name = text(element, "shortname"), text(element, "name")
        where = f"{item} geography {element.get('id')}"
        if code is None or name is None:
            problems.append(Problem("invalid-masterdata", where, "needs a shortname and a name"))
        elif code in found:
            problems.append(
                Problem(
                    "duplicate-geography",
                    where,
                    f"uses the code {code!r} of {found[code]!r} again for {name!r}",
                )
            )
        else:
            found[code] = name
    if problems:
        raise InvalidDataError(problems)
    codes = sorted(found)
    columns: dict[str, list[str]] = {"code": codes, "name": [found[c] for c in codes]}
    return pa.Table.from_pydict(columns, schema=GEOGRAPHIES.schema)
