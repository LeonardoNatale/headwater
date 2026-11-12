"""A small hand-written ecoSpold2 source, `tests/fixtures/ecospold2/cutoff`, with made-up ids and
values. Activity N has the UUID `a0000000-0000-4000-8000-0000000000NN`:

1. widget production, GLO: every kind of line (inputs, waste, recyclable, emissions, a resource)
2. market for electricity, GLO: loses 2% of its own product
3. electricity production, natural gas, GLO
4. treatment of waste widget, incineration, GLO: a treatment, reference -1 kg
5. scrap metal, Recycled Content cut-off, GLO: where recyclables go, no burden
6. market for natural gas, ES: buys from 7 and from 8
7. natural gas production, RoW
8. natural gas, import from TT, ES: an empty file, listed in `exclude.csv`
9. market for scrap metal, GLO: buys a recyclable product

Products are `b0000000-...-00000000000M`: 1 widget, 2 electricity, 3 natural gas, 4 waste widget,
5 scrap metal, 6 spare part (used by no dataset). Elementary flows are `e0000000-...-00000000000M`:
1 carbon dioxide, fossil; 2 cadmium to soil; 3 carbon dioxide, in air; 4 methane (used by none).

`tests/fixtures/ecospold2/broken` holds files that tests copy in to break the source.

`tests/fixtures/ecospold2/apos` holds activities 1, 2, 3, 4, 6 and 7 under the APOS system model,
with the lines that mean the same in both models: widget production without its recyclable line,
and the natural gas market buying all its gas from 7.
"""

import shutil
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pyarrow as pa
import pytest

from headwater.io.ecospold2 import read_ecospold2
from headwater.io.exclude import read_exclude
from headwater.io.inventory import Inventory
from headwater.io.source import ALLOCATION, LINES, PROCESSES_READ, InventorySource
from headwater.model import DEFAULT_UNITS, Exchange, Flow, FlowKind, Process
from headwater.release import ReleaseInfo

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "ecospold2"


class Fixture:
    """Paths and ids of the fixture source."""

    source = FIXTURES / "cutoff"
    apos = FIXTURES / "apos"
    broken = FIXTURES / "broken"
    exclude = FIXTURES / "cutoff" / "exclude.csv"
    empty_file = "a0000000-0000-4000-8000-000000000008_b0000000-0000-4000-8000-000000000003.spold"
    missing_link_file = (
        "a0000000-0000-4000-8000-000000000010_b0000000-0000-4000-8000-000000000002.spold"
    )
    malformed_file = (
        "a0000000-0000-4000-8000-000000000011_b0000000-0000-4000-8000-000000000005.spold"
    )
    two_references_file = (
        "a0000000-0000-4000-8000-000000000012_b0000000-0000-4000-8000-000000000005.spold"
    )
    backwards_period_file = (
        "a0000000-0000-4000-8000-000000000013_b0000000-0000-4000-8000-000000000005.spold"
    )

    @staticmethod
    def activity(n: int) -> str:
        return f"a0000000-0000-4000-8000-{n:012d}"

    @staticmethod
    def product(n: int) -> str:
        return f"ecoinvent:product:b0000000-0000-4000-8000-{n:012d}"

    @staticmethod
    def elementary(n: int) -> str:
        return f"ecoinvent:flow:e0000000-0000-4000-8000-{n:012d}"

    @staticmethod
    def pid(activity_n: int, product_n: int, system_model: str = "cutoff") -> str:
        """The process id of activity N making product M."""
        return (
            f"ecoinvent:3.10:{system_model}:a0000000-0000-4000-8000-{activity_n:012d}"
            f"_b0000000-0000-4000-8000-{product_n:012d}"
        )

    @staticmethod
    def file_name(activity_n: int, product_n: int) -> str:
        return (
            f"a0000000-0000-4000-8000-{activity_n:012d}_b0000000-0000-4000-8000-{product_n:012d}"
            ".spold"
        )

    @staticmethod
    def line(activity_n: int, n: int) -> str:
        """The id of line N of activity N: the exchange's `id` attribute in the file."""
        return f"c00000{activity_n:02d}-0000-4000-8000-{n:012d}"

    @staticmethod
    def process(source: Inventory, process_id: str) -> Process:
        return next(p for p in source.processes if p.id == process_id)

    @classmethod
    def exchange(cls, source: Inventory, process_id: str, line_id: str) -> Exchange:
        return next(e for e in cls.process(source, process_id).exchanges if e.line_id == line_id)


@pytest.fixture(scope="session")
def fx() -> type[Fixture]:
    return Fixture


@pytest.fixture(scope="session")
def source() -> Inventory:
    """The fixture source, read in this process, with the empty file excluded."""
    return read_ecospold2(
        Fixture.source, version="3.10", exclude=read_exclude(Fixture.exclude), workers=1
    )


@pytest.fixture
def source_copy(tmp_path: Path) -> Callable[..., Path]:
    """A copy of the fixture source to break. Returns the copy's folder.

    `add` copies files from `broken/` into `datasets/`, `remove` deletes files from `datasets/`,
    and `edit` maps a path inside the copy to `(old, new)` text replacements.
    """

    def make(
        *,
        add: tuple[str, ...] = (),
        remove: tuple[str, ...] = (),
        edit: dict[str, list[tuple[str, str]]] | None = None,
    ) -> Path:
        target = tmp_path / "source"
        shutil.copytree(Fixture.source, target)
        for name in add:
            shutil.copy(Fixture.broken / name, target / "datasets" / name)
        for name in remove:
            (target / "datasets" / name).unlink()
        for relative, replacements in (edit or {}).items():
            path = target / relative
            text = path.read_text(encoding="utf-8")
            for old, new in replacements:
                assert old in text, f"{old!r} is not in {relative}"
                text = text.replace(old, new)
            path.write_text(text, encoding="utf-8")
        return target

    return make


class Gin:
    """A cotton gin as a reader gives it: one process that makes two products.

    `gin` takes 1 kg of seed cotton from `farm` (by id) and 0.2 kWh of electricity (by name:
    product `electricity`), emits 0.1 kg CO2, and makes 0.35 kg of fibre (line p1) and 0.6 kg
    of seed (line p2). The source gives the economic factors 0.9 and 0.1. `farm` emits 1 kg CO2
    per kg of seed cotton, `grid` 0.5 kg per kWh, and `shirt` buys 0.2 kg of fibre from `gin`.
    """

    factors = (
        {"process_id": "gin", "product": "p1", "factor": 0.9},
        {"process_id": "gin", "product": "p2", "factor": 0.1},
    )

    @staticmethod
    def line(
        process_id: str,
        line_id: str,
        role: str,
        flow_id: str,
        amount: float,
        unit: str = "kg",
        **fields: Any,
    ) -> dict[str, Any]:
        return {
            "process_id": process_id,
            "line_id": line_id,
            "role": role,
            "flow_id": flow_id,
            "amount": amount,
            "unit": unit,
            "properties": [],
            **fields,
        }

    @classmethod
    def lines(cls) -> list[dict[str, Any]]:
        line = cls.line
        return [
            line("farm", "e01", "reference", "seed-cotton", 1.0),
            line("farm", "e02", "emission", "co2", 1.0),
            line("grid", "e01", "reference", "electricity", 1.0, "kWh"),
            line("grid", "e02", "emission", "co2", 0.5),
            line("gin", "p1", "reference", "fibre", 0.35),
            line("gin", "p2", "reference", "seed", 0.6),
            line("gin", "e03", "input", "seed-cotton", 1.0, supplier_id="farm"),
            line("gin", "e04", "input", "electricity", 0.2, "kWh", supplier_product="electricity"),
            line("gin", "e05", "emission", "co2", 0.1),
            line("shirt", "e01", "reference", "shirt", 1.0, "unit"),
            line("shirt", "e02", "input", "fibre", 0.2, supplier_id="gin"),
        ]

    @classmethod
    def source(
        cls,
        lines: list[dict[str, Any]] | None = None,
        factors: tuple[dict[str, Any], ...] | None = None,
    ) -> InventorySource:
        processes = [
            {"id": p, "name": name, "location": "GLO", "kind": "ordinary_transforming_activity"}
            for p, name in (
                ("farm", "seed cotton production"),
                ("grid", "electricity"),
                ("gin", "cotton ginning"),
                ("shirt", "T-shirt"),
            )
        ]
        flows = tuple(
            Flow(id=f, name=f.replace("-", " "), kind=kind, unit=unit)
            for f, kind, unit in (
                ("seed-cotton", FlowKind.product, "kg"),
                ("electricity", FlowKind.product, "kWh"),
                ("fibre", FlowKind.product, "kg"),
                ("seed", FlowKind.product, "kg"),
                ("shirt", FlowKind.product, "unit"),
                ("co2", FlowKind.elementary, "kg"),
            )
        )
        return InventorySource(
            info=ReleaseInfo(
                name="gin", source="gin", version="1", licence="CC0-1.0", flow_list="gin"
            ),
            processes=pa.Table.from_pylist(processes, schema=PROCESSES_READ.schema),
            lines=pa.Table.from_pylist(lines or cls.lines(), schema=LINES.schema),
            flows=flows,
            flow_properties=(),
            units=DEFAULT_UNITS,
            allocation=pa.Table.from_pylist(
                list(cls.factors if factors is None else factors), schema=ALLOCATION.schema
            ),
        )


@pytest.fixture(scope="session")
def gin() -> type[Gin]:
    return Gin


@pytest.fixture
def csv_file(tmp_path: Path) -> Callable[[str], Path]:
    """Writes a mapping file, `mapping.csv`, with this text."""

    def write(text: str) -> Path:
        path = tmp_path / "mapping.csv"
        path.write_text(text, encoding="utf-8")
        return path

    return write


@pytest.fixture(scope="session")
def zip_package() -> Callable[[Path, Path], Path]:
    """Zips the data sets of a JSON-LD package folder into `path`, as openLCA exports it."""

    def write(folder: Path, path: Path) -> Path:
        with zipfile.ZipFile(path, "w") as package:
            for file in sorted(folder.rglob("*.json")):
                package.write(file, file.relative_to(folder).as_posix())
        return path

    return write


@pytest.fixture(scope="session")
def database_backup() -> Callable[[Path], Path]:
    """Writes a .zolca that openLCA makes when it backs up a database: Derby files, not
    JSON-LD."""

    def write(path: Path) -> Path:
        with zipfile.ZipFile(path, "w") as backup:
            backup.writestr("service.properties", "derby")
            backup.writestr("seg0/c10.dat", b"\x00")
        return path

    return write
