"""Fixtures for the Excel import: Brightway-format sheets written as CSV in
`tests/fixtures/excel`, and the toy release they link to.

The sheets can be read in a review:

- `tshirt.csv`: the toy T-shirt as Brightway rows (kilogram, kilowatt hour, a waste line with a
  negative amount, and `fabric_kg` as a project parameter used by a formula). Against the toy
  release it scores 1.25205, and 1.19205 without the waste line.
- `tshirt-uncertain.csv`: the T-shirt with plain amounts, its fabric line lognormal (GSD 1.1) and
  its waste line uniform between -0.06 and -0.04 kg.
- `cases.csv`: one activity with a line for each way a line can fail to map. Each line says which
  in its `comment` column, as `case: <what>`.
- `skipped.csv`: a sheet that a workbook marks `skip`.

The `workbooks` fixture writes them to an .xlsx, with whole numbers as int cells like Excel
writes them. The toy release is the toy background of `tests/fixtures/toy`, frozen with its
method, and its names are what the sheets say: `cotton fabric production` (cotton fabric, kg),
`electricity production` (electricity, kWh), `incineration of textile waste` (a treatment of
textile waste, kg), all in GLO.
"""

import csv
import shutil
from collections.abc import Callable, Mapping
from pathlib import Path

import openpyxl
import pyarrow as pa
import pytest
from typer.testing import CliRunner

from headwater.model import (
    Exchange,
    Factor,
    Flow,
    FlowKind,
    Indicator,
    Method,
    Process,
    ProcessKind,
    Role,
)
from headwater.release import Release, ReleaseInfo, build
from headwater.study import read_method, read_process_folder

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
SHEETS = FIXTURES / "excel"
TOY = FIXTURES / "toy"

type Cell = str | int | float | None
type Sheet = list[list[Cell]]
type Invoke = Callable[..., tuple[int, str]]

INFO = ReleaseInfo(
    name="toy-background",
    source="toy",
    version="1",
    system_model="cutoff",
    licence="CC0-1.0",
    flow_list="toy",
)


def sheet(name: str) -> Sheet:
    """A fixture sheet, with numbers as numbers and blank cells as None."""
    with (SHEETS / f"{name}.csv").open(encoding="utf-8", newline="") as file:
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


def row_of(rows: Sheet, comment: str) -> int:
    """The 1-based row number of the line whose comment cell is `comment`."""
    found = [i for i, row in enumerate(rows, 1) if comment in row]
    assert len(found) == 1, f"{comment!r} is in {len(found)} rows"
    return found[0]


def without(rows: Sheet, name: str) -> Sheet:
    """The sheet without the exchange line whose first cell is `name`."""
    return [row for row in rows if not row or row[0] != name]


def with_cell(rows: Sheet, name: str, column: int, value: Cell) -> Sheet:
    """The sheet with one cell of the line whose first cell is `name` replaced."""
    out = [list(row) for row in rows]
    (index,) = [i for i, row in enumerate(out) if row and row[0] == name]
    out[index] += [None] * (column + 1 - len(out[index]))
    out[index][column] = value
    return out


class Workbooks:
    """Writes sheets into a test's folder as an .xlsx workbook."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.written = 0

    @staticmethod
    def sheet(name: str) -> Sheet:
        return sheet(name)

    def write(self, sheets: Mapping[str, Sheet] | None = None) -> Path:
        """The toy T-shirt as the sheet `inventory`, or the sheets given by name."""
        book = openpyxl.Workbook()
        book.remove(book.worksheets[0])
        for title, rows in (
            sheets if sheets is not None else {"inventory": sheet("tshirt")}
        ).items():
            ws = book.create_sheet(title)
            for row in rows:
                ws.append(row)
        self.written += 1
        path = self.folder / f"inventory {self.written}.xlsx"
        book.save(path)
        return path


@pytest.fixture
def workbooks(tmp_path: Path) -> Workbooks:
    return Workbooks(tmp_path)


@pytest.fixture
def workbook(workbooks: Workbooks) -> Path:
    """The toy T-shirt workbook as it is."""
    return workbooks.write()


@pytest.fixture(scope="session")
def toy_release(tmp_path_factory: pytest.TempPathFactory) -> Release:
    """The toy background frozen into a release, with the toy climate method."""
    folder = read_process_folder(TOY / "background", namespace="toy")
    return build(
        INFO,
        processes=folder.processes,
        flows=folder.flows,
        flow_properties=folder.flow_properties,
        methods=(read_method(TOY / "methods" / "toy-climate"),),
        into=tmp_path_factory.mktemp("releases"),
    )


@pytest.fixture(scope="session")
def toy_release_with_places(tmp_path_factory: pytest.TempPathFactory) -> Release:
    """The toy release again, with a table of places that holds `GLO` only, so any other location
    of a process is an error of validation."""
    folder = read_process_folder(TOY / "background", namespace="toy")
    return build(
        INFO,
        processes=folder.processes,
        flows=folder.flows,
        flow_properties=folder.flow_properties,
        methods=(read_method(TOY / "methods" / "toy-climate"),),
        geographies=pa.table(
            {"code": pa.array(["GLO"], pa.string()), "name": pa.array(["Global"], pa.string())}
        ),
        into=tmp_path_factory.mktemp("releases-with-places"),
    )


@pytest.fixture
def out(tmp_path: Path) -> Path:
    """Where a test's database is written. It does not exist yet."""
    return tmp_path / "database"


@pytest.fixture(autouse=True)
def plain_wide_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without colour and with a wide terminal, a message is not split or styled."""
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setenv("TERMINAL_WIDTH", "200")
    monkeypatch.delenv("FORCE_COLOR", raising=False)


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, toy_release: Release) -> Path:
    """A HEADWATER_HOME that holds the toy release, so `name@hash` finds it."""
    path = tmp_path / "home"
    (path / "releases").mkdir(parents=True)
    shutil.copytree(toy_release.path, path / "releases" / toy_release.path.name)
    monkeypatch.setenv("HEADWATER_HOME", str(path))
    return path


@pytest.fixture
def invoke() -> Invoke:
    """Run the command line and return the exit code and what it printed, errors included."""
    from headwater.cli import app

    runner = CliRunner()

    def run(*args: str | Path) -> tuple[int, str]:
        result = runner.invoke(app, [str(a) for a in args], catch_exceptions=False)
        return result.exit_code, result.output

    return run


# --- a small ecoinvent-like release for the linking tests ---------------------------------------

ECO = ReleaseInfo(
    name="mini-ecoinvent",
    source="ecoinvent",
    version="3.10",
    system_model="cutoff",
    licence="CC0-1.0",
    flow_list="mini",
)


def _process(pid: str, name: str, location: str, flow: str, unit: str) -> Process:
    return Process(
        id=pid,
        name=name,
        location=location,
        kind=ProcessKind.market_activity,
        exchanges=(
            Exchange(
                process_id=pid,
                line_id="e01",
                role=Role.reference,
                flow_id=flow,
                amount=1.0,
                unit=unit,
            ),
        ),
    )


@pytest.fixture(scope="session")
def mini_release(tmp_path_factory: pytest.TempPathFactory) -> Release:
    """A release with names like ecoinvent's, made to be linked in different ways:

    - `market for steel`: RER and RoW make `steel`, GLO makes `steel, low-alloyed`. So the name
      alone is ambiguous, and the name with a location may not be.
    - `market for heat`, GLO: `heat, district or industrial` in MJ.
    - `treatment of wastewater`, RoW: a treatment, its reference is the waste `wastewater`, m3.
    - `market for sand`, GLO: sand in kg, whose flow has no kg/m3 properties.
    - Elementary flows: `Water` in `water/unspecified` and in `air/urban air close to ground`,
      `Carbon dioxide, fossil` in `air/urban air close to ground`, `Carbon dioxide, in air` in
      `natural resource/in air`, `Occupation` in `economic/unspecified`, and `Cadmium` in
      `soil/agricultural`. The method counts the first CO2 as 1 and holds zero for the others,
      except cadmium, which has no factor.
    """

    def product(fid: str, name: str, unit: str, kind: FlowKind = FlowKind.product) -> Flow:
        return Flow(id=fid, name=name, kind=kind, unit=unit)

    def nature(fid: str, name: str, compartment: str, unit: str) -> Flow:
        return Flow(id=fid, name=name, kind=FlowKind.elementary, unit=unit, compartment=compartment)

    flows = [
        product("mini:steel", "steel", "kg"),
        product("mini:alloy", "steel, low-alloyed", "kg"),
        product("mini:heat", "heat, district or industrial", "MJ"),
        product("mini:wastewater", "wastewater", "m3", FlowKind.waste),
        product("mini:sand", "sand", "kg"),
        nature("mini:water-ground", "Water", "water/unspecified", "m3"),
        nature("mini:water-air", "Water", "air/urban air close to ground", "m3"),
        nature("mini:co2", "Carbon dioxide, fossil", "air/urban air close to ground", "kg"),
        nature("mini:co2-in-air", "Carbon dioxide, in air", "natural resource/in air", "kg"),
        nature("mini:occupation", "Occupation", "economic/unspecified", "m2"),
        nature("mini:cadmium", "Cadmium", "soil/agricultural", "kg"),
    ]
    processes = [
        _process("mini:steel-rer", "market for steel", "RER", "mini:steel", "kg"),
        _process("mini:steel-row", "market for steel", "RoW", "mini:steel", "kg"),
        _process("mini:alloy-glo", "market for steel", "GLO", "mini:alloy", "kg"),
        _process("mini:heat-glo", "market for heat", "GLO", "mini:heat", "MJ"),
        _process("mini:wastewater-row", "treatment of wastewater", "RoW", "mini:wastewater", "m3"),
        _process("mini:sand-glo", "market for sand", "GLO", "mini:sand", "kg"),
    ]
    method = Method(
        id="mini-climate",
        version="1",
        flow_list="mini",
        indicators=(Indicator(id="climate", name="Climate change", unit="kg CO2-eq"),),
        factors=tuple(
            Factor(indicator="climate", flow_id=f.id, factor=1.0 if f.id == "mini:co2" else 0.0)
            for f in flows
            if f.kind is FlowKind.elementary and f.id != "mini:cadmium"
        ),
    )
    return build(
        ECO,
        processes=processes,
        flows=flows,
        methods=(method,),
        geographies=pa.table(
            {
                "code": pa.array(["GLO", "RER", "RoW"], pa.string()),
                "name": pa.array(["Global", "Europe", "Rest of World"], pa.string()),
            }
        ),
        into=tmp_path_factory.mktemp("mini-releases"),
    )


# --- building sheets without the boilerplate -----------------------------------------------------

HEADER: list[Cell] = [
    "name",
    "amount",
    "unit",
    "location",
    "type",
    "database",
    "reference product",
    "formula",
    "categories",
    "comment",
]


def line(
    name: str,
    amount: Cell,
    unit: str | None = "kilogram",
    location: str | None = "GLO",
    type: str | None = "technosphere",
    database: str | None = "mini-ecoinvent",
    product: str | None = None,
    formula: str | None = None,
    categories: str | None = None,
    comment: str | None = None,
) -> list[Cell]:
    """One row of an exchanges table, in the columns of `HEADER`."""
    return [name, amount, unit, location, type, database, product, formula, categories, comment]


def block(
    name: str,
    *lines: list[Cell],
    product: str | None = None,
    location: str = "GLO",
    unit: str = "kilogram",
    amount: Cell = 1,
    own: str = "mine",
    parameters: Sheet | None = None,
    comment: str | None = None,
) -> Sheet:
    """An activity: its metadata, its production line and `lines`, then a blank row."""
    rows: Sheet = [
        ["Activity", name],
        ["production amount", amount],
        ["reference product", product or name],
        ["location", location],
        ["unit", unit],
    ]
    if comment is not None:
        rows.append(["comment", comment])
    rows += parameters or []
    rows += [
        ["Exchanges"],
        list(HEADER),
        line(name, amount, unit, location, "production", own, product or name),
        *lines,
        [],
    ]
    return rows


def database(name: str = "mine") -> Sheet:
    return [["Database", name], []]
