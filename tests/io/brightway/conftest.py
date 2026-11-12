"""Fixtures for the Brightway-project import: the toy project of `tests/fixtures/brightway`, and
the toy release it links to.

`bench/bw_toy_project.py` wrote the project once with bw2data 4.5.4. Its databases:

- `toy-biosphere`: `co2` and `ch4`, fossil, to air.
- `toy-background`: `cotton-fabric`, `electricity` and `incineration` (-1 kg of textile waste),
  named as the toy release names them.
- `dyes`: `reactive-dye`, whose electricity line is the formula `kwh_per_kg` (a database
  parameter, 2).
- `tshirt`: `tshirt`, 1 unit. Exchange 14 buys 0.25 kg of fabric (formula `fabric_kg`, a database
  parameter), 15 buys 0.5 kWh (lognormal, GSD 1.1), 16 sends 0.05 kg to incineration (formula
  `-waste_kg`, an activity parameter `fabric_kg * scrap_share`, where `scrap_share` is a project
  parameter, normal with a standard deviation of 0.02). It scores 1.25205, and 1.19205 without
  exchange 16.
- `tshirt-dyed`: `dyed-tshirt`, which buys 0.01 kg of dye from `dyes`. It scores 1.242646.
- `twins`: `cut-a` and `cut-b`, both `fabric cutting`, cut fabric, GLO, which buy 1 and 1.2 kg of
  fabric per kg. `tshirt-a` buys 0.25 kg from `cut-a` (exchange 26) and `tshirt-b` from `cut-b`,
  each with 0.5 kWh. They score 1.19205 and 1.37748.

Tests that need another project change a copy with sqlite3 and pickle, as bw2data stores it.
"""

import pickle
import shutil
import sqlite3
from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner

from headwater.release import Release, ReleaseInfo, build
from headwater.study import read_method, read_process_folder

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
TOY = FIXTURES / "toy"
PROJECT = "toy"
FOLDER = "toy.10016b6e"

type Invoke = Callable[..., tuple[int, str]]

INFO = ReleaseInfo(
    name="toy-background",
    source="toy",
    version="1",
    system_model="cutoff",
    licence="CC0-1.0",
    flow_list="toy",
)


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


class Project:
    """A copy of the toy project in a test's folder, to read and to change."""

    def __init__(self, root: Path) -> None:
        self.root = root
        shutil.copytree(FIXTURES / "brightway", root)

    @property
    def folder(self) -> Path:
        return self.root / FOLDER

    def sql(self, statement: str, *values: object, file: str = "lci/databases.db") -> None:
        with sqlite3.connect(self.folder / file) as db:
            db.execute(statement, values)
        db.close()

    def exchange(self, exchange_id: int) -> dict[str, object]:
        with sqlite3.connect(self.folder / "lci/databases.db") as db:
            (blob,) = db.execute(
                "SELECT data FROM exchangedataset WHERE id = ?", (exchange_id,)
            ).fetchone()
        db.close()
        return pickle.loads(blob)

    def set_exchange(self, exchange_id: int, data: dict[str, object]) -> None:
        self.sql(
            "UPDATE exchangedataset SET data = ? WHERE id = ?", pickle.dumps(data), exchange_id
        )

    def delete_exchange(self, exchange_id: int) -> None:
        self.sql("DELETE FROM exchangedataset WHERE id = ?", exchange_id)
        self.sql(
            "DELETE FROM parameterizedexchange WHERE exchange = ?",
            exchange_id,
            file="parameters.db",
        )


@pytest.fixture
def project(tmp_path: Path) -> Project:
    """A copy of the toy project, as a Brightway folder (`BRIGHTWAY2_DIR`)."""
    return Project(tmp_path / "brightway")


@pytest.fixture
def studies(tmp_path: Path) -> Path:
    """The folder the studies of a test are written into, side by side."""
    path = tmp_path / "studies"
    path.mkdir()
    return path


@pytest.fixture(autouse=True)
def plain_wide_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without colour and with a wide terminal, a message is not split or styled."""
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setenv("TERMINAL_WIDTH", "200")
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    monkeypatch.delenv("BRIGHTWAY2_DIR", raising=False)


@pytest.fixture
def invoke() -> Invoke:
    """Run the command line and return the exit code and what it printed, errors included."""
    from headwater.cli import app

    runner = CliRunner()

    def run(*args: str | Path) -> tuple[int, str]:
        result = runner.invoke(app, [str(a) for a in args], catch_exceptions=False)
        return result.exit_code, result.output

    return run
