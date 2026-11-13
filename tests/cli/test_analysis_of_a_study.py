"""`contributions`, `where` and `sensitivity` take a study as well as a database (D49), with the
`--product` and `--scenario` of `calc`. On the demo, 1 T-shirt of `tshirt-dyed` scores 1.242646,
and 1.237646 in the scenario `cleaner-dye`, the dye at 3.5 kg CO2 per kg instead of 4."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from headwater.cli import app
from tests.cli.conftest import ANSI

REL = 1e-9  # AGENTS.md: toy fixtures
STUDY = "studies/tshirt-footprint"
DATABASE = "studies/tshirt-dyed"


def run(*args: str) -> tuple[int, str, str]:
    result = CliRunner().invoke(app, list(args), catch_exceptions=False)
    return result.exit_code, ANSI.sub("", result.stdout), ANSI.sub("", result.stderr)


@pytest.fixture
def demo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HEADWATER_HOME", str(tmp_path / "home"))
    folder = tmp_path / "demo"
    assert run("demo", str(folder))[0] == 0
    monkeypatch.chdir(folder)
    assert run("release", "pull", "releases/toy-background@6864b7dbb65d")[0] == 0
    return folder


def document(*args: str) -> dict[str, object]:
    code, out, err = run(*args, "--json")
    assert code == 0, err
    return json.loads(out)


def test_contributions_of_the_study_are_those_of_its_database(demo: Path) -> None:
    study = document("contributions", STUDY)
    database = document("contributions", DATABASE)

    assert (study["format"], study["kind"], database["kind"]) == (
        "headwater-contributions/1",
        "study",
        "database",
    )
    [mine] = study["results"]  # type: ignore[misc]
    [theirs] = database["results"]  # type: ignore[misc]
    assert mine["score"] == pytest.approx(1.242646, rel=REL)
    assert mine["tree"] == theirs["tree"]
    assert mine["sensitivity"] == theirs["sensitivity"]
    assert mine["tree"]["children"][0]["process_id"] == "toy:fabric-production"
    assert mine["sensitivity"][0]["line_id"] == "e02"


def test_a_scenario_of_the_study_is_analysed_in_that_scenario(demo: Path) -> None:
    for command in ("contributions", "where", "sensitivity"):
        found = document(command, STUDY, "--scenario", "cleaner-dye")

        assert found["scenario"] == "cleaner-dye"
        [result] = found["results"]  # type: ignore[misc]
        assert result["score"] == pytest.approx(1.237646, rel=REL), command


def test_where_and_sensitivity_of_the_study_are_those_of_its_database(demo: Path) -> None:
    for command, key in (("where", "where"), ("sensitivity", "sensitivity")):
        [mine] = document(command, STUDY)["results"]  # type: ignore[misc]
        [theirs] = document(command, DATABASE)["results"]  # type: ignore[misc]

        assert mine[key] == theirs[key], command


def test_the_text_names_the_study_and_its_scenario(demo: Path) -> None:
    code, out, _ = run("contributions", STUDY, "--scenario", "cleaner-dye")

    assert code == 0
    assert out.splitlines()[:4] == [
        f"Study tshirt-footprint ({STUDY})",
        "Background: toy-background@6864b7dbb65d, method toy-climate 1",
        "Scenario: cleaner-dye",
        "Indicator: Climate change (kg CO2-eq)",
    ]
    assert "1 unit of T-shirt: 1.23765 (100.0%)" in out


def test_a_product_the_study_does_not_have_is_a_usage_error(demo: Path) -> None:
    code, _, err = run("where", STUDY, "--product", "tshirt-dyed:shirt")

    assert code == 2
    assert "has no product 'tshirt-dyed:shirt'" in err and "tshirt-dyed:tshirt" in err


def test_a_scenario_given_with_a_database_is_a_usage_error(demo: Path) -> None:
    code, _, err = run("sensitivity", DATABASE, "--scenario", "cleaner-dye")

    assert code == 2
    assert "holds no scenarios" in err


def test_a_scenario_the_study_does_not_have_is_a_usage_error(demo: Path) -> None:
    code, _, err = run("contributions", STUDY, "--scenario", "cleaner")

    assert code == 2
    assert "cleaner-dye" in err
