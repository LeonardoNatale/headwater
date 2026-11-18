"""`headwater inventory`: the total of each elementary flow of a database, a study or a
background process, on the demo. The dyed T-shirt of the study emits 1.175 kg of CO2 and
0.00227 kg of CH4 (1.175 + 29.8 x 0.00227 = 1.242646), and 1.170 kg of CO2 in the scenario
cleaner-dye."""

import csv
import io
import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from headwater.cli import app
from headwater.model import Method
from headwater.release import Release, build
from headwater.study import read_method, read_process_folder
from tests.cli.conftest import ANSI, INFO, TOY

REL = 1e-9  # AGENTS.md: toy fixtures
STUDY = "project/tshirt-footprint"
RELEASE = "toy-background@6864b7dbb65d"


def run(*args: str) -> tuple[int, str, str]:
    result = CliRunner().invoke(app, list(args), catch_exceptions=False)
    return result.exit_code, ANSI.sub("", result.stdout), ANSI.sub("", result.stderr)


def document(*args: str) -> dict[str, Any]:
    code, out, err = run("inventory", *args, "--json")
    assert code == 0, err
    return json.loads(out)


def amounts(result: dict[str, Any]) -> dict[str, float]:
    return {f["flow_id"]: f["amount"] for f in result["flows"]}


@pytest.fixture
def demo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HEADWATER_HOME", str(tmp_path / "home"))
    folder = tmp_path / "demo"
    assert run("demo", str(folder))[0] == 0
    monkeypatch.chdir(folder)
    assert run("release", "pull", f"releases/{RELEASE}")[0] == 0
    return folder


def test_the_study_by_hand(demo: Path) -> None:
    code, out, _ = run("inventory", STUDY)

    assert code == 0
    assert out.splitlines() == [
        f"Study tshirt-footprint ({STUDY})",
        f"Background: {RELEASE}, method toy-climate 1",
        "",
        "1 × tshirt-dyed:tshirt: 2 elementary flows",
        "  kg",
        "    1.175  Carbon dioxide, fossil (air)  toy:co2",
        "    0.00227  Methane, fossil (air)  toy:ch4",
    ]


def test_json_of_the_study(demo: Path) -> None:
    found = document(STUDY)

    assert (found["format"], found["kind"], found["name"]) == (
        "headwater-inventory/1",
        "study",
        "tshirt-footprint",
    )
    assert found["methods"] == ["toy-climate"]
    assert "indicator" not in found
    [result] = found["results"]
    assert result["product"] == {"id": "tshirt-dyed:tshirt", "amount": 1.0}
    assert result["flow_count"] == 2
    assert amounts(result) == pytest.approx({"toy:co2": 1.175, "toy:ch4": 0.00227}, rel=REL)
    assert result["flows"][0] == {
        "flow_id": "toy:co2",
        "name": "Carbon dioxide, fossil",
        "compartment": "air",
        "unit": "kg",
        "amount": pytest.approx(1.175, rel=REL),
    }
    assert result["rest"] == []
    assert "sums" not in result, "no sum without a filter: kg of CO2 and of CH4 do not add up"
    assert result["provenance"]["pin"] == RELEASE


def test_a_scenario_of_the_study(demo: Path) -> None:
    found = document(STUDY, "--scenario", "cleaner-dye")

    assert found["scenario"] == "cleaner-dye"
    [result] = found["results"]
    assert amounts(result) == pytest.approx({"toy:co2": 1.17, "toy:ch4": 0.00227}, rel=REL)


def test_the_database_is_the_study(demo: Path) -> None:
    [mine] = document(STUDY)["results"]
    [theirs] = document("project/tshirt-dyed")["results"]

    assert mine["flows"] == theirs["flows"]


def test_a_filter_adds_a_sum_per_unit(demo: Path) -> None:
    [result] = document(STUDY, "--flow", "FOSSIL", "--compartment", "air")["results"]

    assert result["sums"] == [
        {
            "unit": "kg",
            "amount": pytest.approx(1.17727, rel=REL),
            "count": 2,
            "label": "sum of 2 flows matching 'FOSSIL' in air",
        }
    ]
    code, out, _ = run("inventory", STUDY, "--flow", "carbon")
    assert code == 0
    assert out.splitlines()[-4:] == [
        "1 × tshirt-dyed:tshirt: 2 elementary flows, 1 kept by the filters",
        "  kg",
        "    1.175  Carbon dioxide, fossil (air)  toy:co2",
        "    1.175  sum of 1 flow matching 'carbon'",
    ]


def test_a_compartment_keeps_those_under_it(demo: Path) -> None:
    [under] = document(STUDY, "--compartment", "air/")["results"]
    [none] = document(STUDY, "--compartment", "ai")["results"]

    assert len(under["flows"]) == 2
    assert none["flows"] == []
    assert none["sums"] == []


def test_top_keeps_the_largest_per_unit_and_sums_the_rest(demo: Path) -> None:
    [result] = document(STUDY, "--top", "1")["results"]

    assert [f["flow_id"] for f in result["flows"]] == ["toy:co2"]
    assert result["rest"] == [{"unit": "kg", "amount": pytest.approx(0.00227, rel=REL), "count": 1}]
    code, out, _ = run("inventory", STUDY, "--top", "1")
    assert code == 0
    assert out.splitlines()[-1] == "    0.00227  rest, 1 flow"


def test_csv(demo: Path) -> None:
    code, out, _ = run("inventory", STUDY, "--csv")

    assert code == 0
    rows = list(csv.DictReader(io.StringIO(out)))
    assert [r["flow_id"] for r in rows] == ["toy:co2", "toy:ch4"]
    assert rows[0] == {
        "product": "tshirt-dyed:tshirt",
        "product_amount": "1.0",
        "flow_id": "toy:co2",
        "flow_name": "Carbon dioxide, fossil",
        "compartment": "air",
        "unit": "kg",
        "amount": rows[0]["amount"],
    }
    assert float(rows[0]["amount"]) == pytest.approx(1.175, rel=REL)
    [result] = document(STUDY)["results"]
    assert [float(r["amount"]) for r in rows] == [f["amount"] for f in result["flows"]]


def test_csv_and_json_together_is_a_usage_error(demo: Path) -> None:
    code, _, err = run("inventory", STUDY, "--csv", "--json")

    assert code == 2
    assert "give --csv or --json, not both" in err


def test_a_background_process(demo: Path) -> None:
    found = document("toy:fabric-production", "--release", RELEASE)

    assert (found["kind"], found["name"], found["path"]) == (
        "process",
        "toy:fabric-production",
        None,
    )
    [result] = found["results"]
    assert result["product"] == {"id": "toy:fabric-production", "amount": 1.0}
    assert amounts(result) == pytest.approx({"toy:co2": 3.5, "toy:ch4": 0.007}, rel=REL)
    code, out, _ = run("inventory", "toy:incineration-textile-waste", "--release", RELEASE)
    assert code == 0
    assert out.splitlines()[-3:] == [
        "1 × toy:incineration-textile-waste: 1 elementary flow",
        "  kg",
        "    1.2  Carbon dioxide, fossil (air)  toy:co2",
    ]


def test_a_background_process_needs_a_release(demo: Path) -> None:
    code, _, err = run("inventory", "toy:fabric-production")

    assert code == 2
    assert "--release" in err


def test_an_unknown_process_names_the_closest(demo: Path) -> None:
    code, _, err = run("inventory", "toy:fabric-prodution", "--release", RELEASE)

    assert code == 2
    assert "toy:fabric-production" in err


def test_a_background_process_has_no_product_or_scenario(demo: Path) -> None:
    code, _, err = run(
        "inventory", "toy:fabric-production", "--release", RELEASE, "--scenario", "x"
    )

    assert code == 2
    assert "not a background process" in err


def release_with(methods: tuple[Method, ...]) -> Release:
    folder = read_process_folder(TOY / "background", namespace="toy")
    return build(
        INFO,
        processes=folder.processes,
        flows=folder.flows,
        flow_properties=folder.flow_properties,
        methods=methods,
    )


def name(release: Release) -> str:
    return f"{release.name}@{release.hash[:12]}"


def test_a_release_with_no_method_asks_for_one(home: Path) -> None:
    release = name(release_with(()))

    code, _, err = run("inventory", "toy:fabric-production", "--release", release)
    assert code == 1
    assert "holds no method" in err
    assert "does not change the totals" in err

    method = str(TOY / "methods" / "toy-climate")
    for option in ("--method", "--method-folder"):
        code, out, err = run(
            "inventory", "toy:fabric-production", "--release", release, option, method
        )
        assert code == 0, err
        assert "3.5  Carbon dioxide, fossil (air)  toy:co2" in out


def test_a_release_with_several_methods_loads_the_first(home: Path) -> None:
    methods = tuple(read_method(TOY / "methods" / m) for m in ("toy-impacts", "toy-climate"))
    release = name(release_with(methods))

    code, out, err = run("inventory", "toy:fabric-production", "--release", release)

    assert code == 0, err
    assert "holds 2 methods: loading the first, toy-climate" in err
    assert "does not change the totals" in err
    assert "3.5  Carbon dioxide, fossil (air)  toy:co2" in out


def test_a_folder_of_process_files_needs_a_method(home: Path) -> None:
    code, _, err = run("inventory", "toy:fabric-production", "--release", str(TOY / "background"))

    assert code == 1
    assert "--method must be a method folder" in err
