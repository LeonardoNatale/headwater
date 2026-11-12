"""Names say database where they mean a database, and study where they mean a study (D46), with
no shims for the old ones."""

import dataclasses
import json
import shutil
from pathlib import Path

from typer.testing import CliRunner

import headwater as hw
from headwater.cli import app
from headwater.io.excel import ImportReport
from headwater.io.excel.report import Outcome

ROOT = Path(__file__).resolve().parents[1]
TOY = ROOT / "tests" / "fixtures" / "toy"
RESULTS = ROOT / "bench" / "results"


def fields(cls: type) -> set[str]:
    return {f.name for f in dataclasses.fields(cls)}


def test_a_monte_carlo_run_counts_the_database_lines_it_drew() -> None:
    assert "database_lines" in fields(hw.MonteCarloProvenance)
    assert "study_lines" not in fields(hw.MonteCarloProvenance)


def test_the_import_report_names_the_database_written_and_the_workbook_s_own() -> None:
    assert {"name", "source_database"} <= fields(ImportReport)
    assert not {"study", "database"} & fields(ImportReport)
    assert "database" in fields(Outcome) and "study" not in fields(Outcome)


def test_the_bench_results_name_database_lines() -> None:
    for name in (
        "diff_cost_results.json",
        "diff_cost_2000_results.json",
        "monte_carlo_study_ecoinvent-3.12-cutoff_results.json",
    ):
        data = json.loads((RESULTS / name).read_text())
        assert "database_lines" in data, name
        assert "study_lines" not in data and "study_processes" not in data, name


def test_a_study_s_pin_error_names_the_study(tmp_path: Path) -> None:
    """The databases of the toy study pin `toy-background@fixture`, which is no release."""
    shutil.copytree(TOY / "tshirt-dyed", tmp_path / "tshirt-dyed")

    result = CliRunner().invoke(app, ["calc", str(tmp_path / "tshirt-dyed" / "tshirt-footprint")])

    assert result.exit_code == 1, result.output
    assert "study tshirt-footprint background toy-background@fixture" in result.output
    assert "database tshirt-footprint" not in result.output
