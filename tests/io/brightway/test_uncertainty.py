"""A workbook and a project that hold the same T-shirt give the same distributions, through the
one mapping both importers share."""

import csv
import math
from pathlib import Path

import openpyxl

from headwater.io.brightway import import_brightway
from headwater.io.excel import import_excel
from headwater.release import Release

from .conftest import FIXTURES, Project


def workbook(sheet: str, into: Path) -> Path:
    """A fixture sheet of `tests/fixtures/excel` as an .xlsx, with numbers as numbers."""
    book = openpyxl.Workbook()
    ws = book.worksheets[0]
    ws.title = "inventory"
    with (FIXTURES / "excel" / f"{sheet}.csv").open(encoding="utf-8", newline="") as file:
        for row in csv.reader(file):
            ws.append([_cell(text) for text in row])
    path = into / f"{sheet}.xlsx"
    book.save(path)
    return path


def _cell(text: str) -> str | int | float | None:
    if not text:
        return None
    for kind in (int, float):
        try:
            return kind(text)
        except ValueError:
            pass
    return text


def process_files(folder: Path) -> dict[str, list[str]]:
    """The lines of each process file, without the comment that says where it was read."""
    return {
        p.name: [line for line in p.read_text().splitlines() if not line.startswith("# Read from")]
        for p in sorted((folder / "processes").iterdir())
    }


def test_a_workbook_and_a_project_of_the_same_tshirt_give_the_same_process_files(
    project: Project, studies: Path, tmp_path: Path, toy_release: Release
) -> None:
    """The project's T-shirt with plain amounts, its fabric lognormal (GSD 1.1) and its waste
    uniform, as `tshirt-uncertain.csv` writes it. parameters.yaml differs: the project has its
    own parameters."""
    for exchange_id, fields in (
        (14, {"uncertainty type": 2, "loc": math.log(0.25), "scale": math.log(1.1)}),
        (15, {}),
        (16, {"uncertainty type": 4, "minimum": -0.06, "maximum": -0.04}),
    ):
        project.sql(
            "DELETE FROM parameterizedexchange WHERE exchange = ?",
            exchange_id,
            file="parameters.db",
        )
        data = {
            k: v
            for k, v in project.exchange(exchange_id).items()
            if k not in ("formula", "uncertainty type", "loc", "scale")
        }
        project.set_exchange(exchange_id, {**data, **fields})
    from_project = import_brightway(
        "toy", "tshirt", toy_release, studies / "tshirt", brightway_dir=project.root
    )
    from_workbook = import_excel(
        workbook("tshirt-uncertain", tmp_path), toy_release, tmp_path / "tshirt", name="tshirt"
    )
    assert from_project.clean and from_workbook.clean
    found = process_files(from_workbook.folder)
    assert found == process_files(from_project.folder)
    assert found["t-shirt-glo.csv"][2:] == [
        "e02,input,toy:cotton-fabric,0.25,kg,toy:fabric-production,cotton fabric,"
        "Fabric in the bill of materials,lognormal,0.09531017980432493,,",
        "e03,input,toy:electricity,0.5,kWh,toy:electricity-production,electricity,,,,,",
        "e04,waste_to_treatment,toy:textile-waste,0.05,kg,toy:incineration-textile-waste,"
        "textile waste,Scraps sent to incineration,uniform,,0.04,0.06",
    ]
