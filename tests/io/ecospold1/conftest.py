"""A small builder of ecoSpold1 files, so each test writes only the lines it is about.

The `es1` fixture has `exchange(...)` for one line, `dataset(...)` for one process and `write(...)`
for a file; `make(*datasets)` puts datasets in a new folder, `read(folder)` gives the source as the
reader writes it, `load(folder)` the inventory after the shared stages, and `lines_of(*datasets)`
the lines by process name and line id. Group codes are written `in1` to `in5` and `out0` to
`out4`, as the file's `inputGroup` and `outputGroup`.
"""

from pathlib import Path
from typing import Any
from xml.sax.saxutils import quoteattr

import pytest

from headwater.io.ecospold1 import read
from headwater.io.formats import inventory_from
from headwater.io.inventory import Inventory
from headwater.io.source import InventoryOptions, InventorySource

NAMESPACE = "http://www.EcoInvent.org/EcoSpold01"

OPTIONS = InventoryOptions(version="1", source="test", licence="CC0-1.0", workers=1)


class Lines:
    """The lines of a source by process name and line id, as plain rows."""

    def __init__(self, source: InventorySource) -> None:
        names = {r["id"]: r["name"] for r in source.processes.to_pylist()}
        self.rows = {(names[r["process_id"]], r["line_id"]): r for r in source.lines.to_pylist()}
        self.flows = {f.id: f for f in source.flows}

    def __getitem__(self, key: tuple[str, int]) -> dict[str, Any]:
        return self.rows[key[0], str(key[1])]

    def flow(self, name: str, number: int) -> Any:
        return self.flows[self[name, number]["flow_id"]]


class ES1:
    options = OPTIONS

    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.count = 0

    @staticmethod
    def exchange(
        number: int,
        name: str,
        unit: str,
        amount: float,
        group: str,
        *,
        location: str | None = None,
        category: str | None = None,
        sub_category: str | None = None,
        **attributes: Any,
    ) -> str:
        """One `<exchange>`. Extra keyword arguments are written as attributes, for example
        `uncertaintyType=1, standardDeviation95=1.21, generalComment="(1,2,3,4,5,na)"`."""
        side = "inputGroup" if group.startswith("in") else "outputGroup"
        found: dict[str, Any] = {
            "number": number,
            "name": name,
            "location": location,
            "category": category,
            "subCategory": sub_category,
            "unit": unit,
            "meanValue": amount,
            **attributes,
        }
        text = " ".join(f"{k}={quoteattr(str(v))}" for k, v in found.items() if v is not None)
        return f"<exchange {text}><{side}>{group[-1]}</{side}></exchange>"

    @staticmethod
    def allocation(product: int, fraction: float, *lines: int) -> str:
        """One `<allocation>`: `fraction` percent for the co-product `product`, on these lines."""
        refs = "".join(f"<referenceToInputOutput>{n}</referenceToInputOutput>" for n in lines)
        return (
            f'<allocation referenceToCoProduct="{product}" fraction="{fraction}">{refs}'
            "</allocation>"
        )

    @staticmethod
    def dataset(
        name: str,
        *lines: str,
        number: int = 1,
        location: str | None = "CH",
        unit: str = "kg",
        category: str = "materials",
        sub_category: str = "unspecified",
        kind: int = 1,
        time: str = "<startYear>2000</startYear><endYear>2005</endYear>",
        allocations: tuple[str, ...] = (),
        infrastructure: bool = False,
    ) -> str:
        geography = f'<geography location="{location}"/>' if location else ""
        return f"""<dataset number="{number}">
<metaInformation><processInformation>
<referenceFunction name={quoteattr(name)} unit="{unit}" amount="1" category={quoteattr(category)}
 subCategory={quoteattr(sub_category)} infrastructureProcess="{str(infrastructure).lower()}"
 datasetRelatesToProduct="true"/>
{geography}
<timePeriod dataValidForEntirePeriod="true">{time}</timePeriod>
<dataSetInformation type="{kind}" impactAssessmentResult="false" version="1" internalVersion="1"
 energyValues="0" timestamp="2026-10-25T00:00:00"/>
</processInformation></metaInformation>
<flowData>{"".join(lines)}{"".join(allocations)}</flowData>
</dataset>"""

    @staticmethod
    def write(folder: Path, file: str, *datasets: str, namespace: str | None = NAMESPACE) -> Path:
        """A file holding these datasets, with the ecoSpold1 namespace unless `namespace` is
        None (openLCA writes none)."""
        declared = f' xmlns="{namespace}"' if namespace else ""
        path = folder / file
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f'<?xml version="1.0" encoding="UTF-8"?>\n<ecoSpold{declared}>\n'
            + "\n".join(datasets)
            + "\n</ecoSpold>\n",
            encoding="utf-8",
        )
        return path

    @classmethod
    def maker(cls, name: str, unit: str = "kg", *, location: str = "CH", number: int = 1) -> str:
        """A process that only makes 1 `unit` of the product `name`."""
        line = cls.exchange(1, name, unit, 1, "out0", location=location)
        return cls.dataset(name, line, number=number, unit=unit, location=location)

    def make(self, *datasets: str, namespace: str | None = NAMESPACE) -> Path:
        """A new folder with these datasets, one file each."""
        self.count += 1
        folder = self.tmp_path / f"source{self.count}"
        for n, text in enumerate(datasets):
            self.write(folder, f"{n:03d}.xml", text, namespace=namespace)
        return folder

    @staticmethod
    def read(folder: Path, options: InventoryOptions = OPTIONS) -> InventorySource:
        return read(folder, options)

    @staticmethod
    def load(folder: Path, options: InventoryOptions = OPTIONS) -> Inventory:
        return inventory_from(folder, options, format="ecospold1")

    def lines_of(self, *datasets: str, namespace: str | None = NAMESPACE) -> Lines:
        return Lines(self.read(self.make(*datasets, namespace=namespace)))


@pytest.fixture
def es1(tmp_path: Path) -> ES1:
    return ES1(tmp_path)
