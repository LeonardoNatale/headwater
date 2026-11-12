"""Import SimaPro CSV exports: processes into a release, methods into method folders.

Example: `inventory_from(Path("agribalyse.csv"), InventoryOptions(source="agribalyse",
version="3.2", licence="..."), format="simapro-csv")` reads every process of the export and links
each line to its supplier by name. `method_from(Path("ef.csv"), release, MethodOptions(method="EF
3.1"), format="simapro-csv")` maps the method's factors onto that release by name and compartment.

One format reads both kinds of export. They share the header, the grammar and the flow list, and
the reader of one kind refuses an export of the other, saying which command reads it.
"""

from pathlib import Path

from headwater.io.simapro_csv.header import FIRST_LINE
from headwater.io.simapro_csv.method import read_method
from headwater.io.simapro_csv.reader import read
from headwater.io.source import Aspect, Format


def _detect(path: Path) -> bool:
    """A file whose first line is SimaPro's: `{SimaPro 9.5.0.0}`."""
    if not path.is_file():
        return False
    try:
        with path.open("rb") as file:
            start = file.read(len(FIRST_LINE) + 3)
    except OSError:
        return False
    return start.removeprefix(b"\xef\xbb\xbf").startswith(FIRST_LINE.encode())


FORMAT = Format(
    name="simapro-csv",
    title="SimaPro CSV export of processes or of methods (.csv)",
    detect=_detect,
    read_inventory=read,
    inventory_options=frozenset({"version", "source", "licence"}),
    read_method=read_method,
    method_options=frozenset({"method", "categories", "id", "version", "variant"}),
    cannot_carry=frozenset({Aspect.process_kind}),
)

__all__ = ["FORMAT", "read", "read_method"]
