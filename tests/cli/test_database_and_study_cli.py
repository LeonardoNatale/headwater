"""The commands speak of databases and studies (D46): `diff` takes either, a product is named by
the id of its process, and `headwater database uses` lists the studies that reach a database.

The toy: `dyes` makes a reactive dye (5.0596 per kg), `tshirt` buys 0.01 kg of it per T-shirt
(1.242646), and the study `tshirt-and-dye` scores 2 T-shirts and 0.5 kg of dye. Lowering the
dye's CO2 from 4 to 3.5 moves one T-shirt by -0.005 and 1 kg of dye by -0.5.
"""

import json
import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

from tests.conftest import add_process

Invoke = Callable[..., tuple[int, str]]
Commit = Callable[[str], str]
Git = Callable[..., str]

TOY = Path(__file__).resolve().parent.parent / "fixtures" / "toy"
DYE_CSV = "tshirt-dyed/dyes/exchanges.csv"
BAG_SEWING = "bag sewing,GLO,ordinary_transforming_activity"


def edit(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    assert old in text, f"{old!r} not found in {path}"
    path.write_text(text.replace(old, new, 1))


def add_bag(database: Path) -> None:
    """The process `bag sewing`, id `tshirt:bag-sewing`, which makes the flow `tshirt:bag` from
    2 kWh of grid electricity: 1.0596."""
    add_process(
        database,
        "bag-sewing",
        "e01,reference,tshirt:bag,1,kg,\ne02,input,toy:electricity,2,kWh,toy:electricity-production",
        details=BAG_SEWING,
    )
    with (database / "flows.csv").open("a") as handle:
        handle.write("tshirt:bag,bag,product,kg,,\n")


@pytest.fixture
def dyed(repo: Path, commit: Commit) -> Path:
    shutil.copytree(TOY / "tshirt-dyed", repo / "tshirt-dyed")
    commit("the dyed T-shirt, its dyes and two studies")
    return repo


# --- a study -----------------------------------------------------------------------------------


def test_a_study_diff_lists_its_products_times_their_amounts(
    dyed: Path, commit: Commit, git: Git, toy_args: list[str], invoke: Invoke
) -> None:
    git("switch", "--quiet", "--create", "cleaner")
    edit(dyed / DYE_CSV, "toy:co2,4,", "toy:co2,3.5,")
    commit("a cleaner dye")

    code, output = invoke("diff", "main", "tshirt-dyed/tshirt-and-dye", *toy_args)

    assert code == 0, output
    assert output.splitlines()[0].startswith("Study tshirt-dyed/tshirt-and-dye: main (")
    blocks = output.split("\n\n")
    assert (
        "2 × T-shirt (tshirt:tshirt)\n"
        "  Climate change: 2.48529 → 2.47529 kg CO2-eq, change -0.01 (-0.4%)"
    ) in blocks
    assert (
        "0.5 × reactive dye production | reactive dye (dyes:reactive-dye)\n"
        "  Climate change: 2.5298 → 2.2798 kg CO2-eq, change -0.25 (-9.9%)"
    ) in blocks
    assert "e03  dyes:reactive-dye line e03 (Carbon dioxide, fossil): amount 4 → 3.5 kg" in output


def test_a_study_diff_lists_every_product_even_when_none_moved(
    dyed: Path, commit: Commit, git: Git, toy_args: list[str], invoke: Invoke
) -> None:
    git("switch", "--quiet", "--create", "goal")
    edit(dyed / "tshirt-dyed/tshirt-and-dye/study.yaml", "a test of", "a check of")
    commit("a new goal")

    code, output = invoke("diff", "main", "tshirt-dyed/tshirt-and-dye", *toy_args)

    assert code == 0, output
    assert "2 × T-shirt (tshirt:tshirt)\n  no change" in output
    assert "0.5 × reactive dye production | reactive dye (dyes:reactive-dye)\n  no change" in output
    assert "other products" not in output
    assert "study goal: " in output.split("Other changes, not part of the score:")[1]


def test_a_study_diff_as_json_says_its_kind_and_amounts(
    dyed: Path, commit: Commit, git: Git, toy_args: list[str], invoke_stdout: Invoke
) -> None:
    git("switch", "--quiet", "--create", "cleaner")
    edit(dyed / DYE_CSV, "toy:co2,4,", "toy:co2,3.5,")
    commit("a cleaner dye")

    code, output = invoke_stdout("diff", "main", "tshirt-dyed/tshirt-and-dye", "--json", *toy_args)

    assert code == 0, output
    data = json.loads(output)
    assert data["format"] == "headwater-diff/2"
    assert (data["path"], data["kind"], data["created"]) == (
        "tshirt-dyed/tshirt-and-dye",
        "study",
        False,
    )
    assert [(p["id"], p["amount"]) for p in data["products"]] == [
        ("tshirt:tshirt", {"old": 2.0, "new": 2.0}),
        ("dyes:reactive-dye", {"old": 0.5, "new": 0.5}),
    ]
    assert "study" not in data and "reference" not in data["products"][0]


CLEANER_DYE = "tshirt-dyed/tshirt-footprint/scenarios/cleaner-dye.yaml"


def test_a_changed_scenario_is_listed_as_lines_not_json(
    dyed: Path, toy_args: list[str], invoke: Invoke, invoke_stdout: Invoke
) -> None:
    scenario = dyed / CLEANER_DYE
    edit(scenario, "amount: 3.5", "amount: 3")
    edit(scenario, "emits 3.5 kg CO2 per kg of dye, not 4.", "emits 3 kg CO2 per kg of dye.")

    code, output = invoke("diff", "main", "tshirt-dyed/tshirt-footprint", *toy_args)

    assert code == 0, output
    assert (
        "Other changes, not part of the score:\n"
        "  scenario cleaner-dye: dyes:reactive-dye line e03 amount 3.5 → 3; description "
        '"The dye plant emits 3.5 kg CO2 per kg of dye, not 4." → '
        '"The dye plant emits 3 kg CO2 per kg of dye."'
    ) in output
    assert "{" not in output and "line_id" not in output

    code, output = invoke_stdout(
        "diff", "main", "tshirt-dyed/tshirt-footprint", *toy_args, "--json"
    )

    assert code == 0, output
    [difference] = [d for d in json.loads(output)["differences"] if d["kind"] == "scenario"]
    assert difference["item"] == "cleaner-dye" and difference["change"] == "changed"
    assert difference["text"].startswith("dyes:reactive-dye line e03 amount 3.5 → 3; ")


def test_a_scenario_change_on_one_side_only_says_added_or_removed(
    dyed: Path, toy_args: list[str], invoke: Invoke
) -> None:
    (dyed / CLEANER_DYE).write_text(
        "description: The dye plant emits 3.5 kg CO2 per kg of dye, not 4.\n"
        "changes:\n"
        "  - process: tshirt:tshirt\n"
        "    line: e02\n"
        "    amount: 0.3\n"
        "  - parameter: kwh_per_kg\n"
        "    value: 1.5\n"
    )

    code, output = invoke("diff", "main", "tshirt-dyed/tshirt-footprint", *toy_args)

    assert code == 0, output
    assert (
        "  scenario cleaner-dye: tshirt:tshirt line e02 added, amount 0.3; parameter kwh_per_kg "
        "of dyes added, value 1.5; dyes:reactive-dye line e03 removed, it set amount 3.5"
    ) in output
    assert "{" not in output


# --- a new database ----------------------------------------------------------------------------


def test_a_new_database_is_one_line_with_its_products_counted(
    repo: Path, commit: Commit, git: Git, toy_args: list[str], invoke: Invoke
) -> None:
    git("switch", "--quiet", "--create", "bags")
    shutil.copytree(TOY / "tshirt" / "tshirt", repo / "bags" / "tshirt")
    add_bag(repo / "bags" / "tshirt")
    commit("a T-shirt and a bag")

    code, output = invoke("diff", "main", "bags/tshirt", *toy_args)

    assert code == 0, output
    assert output.splitlines()[2:] == [
        "",
        "New database: 2 products, not scored. `headwater calc bags/tshirt` scores them.",
    ]


# --- --product takes a process id -------------------------------------------------------------


def test_diff_product_takes_the_id_of_a_process(
    repo: Path, commit: Commit, git: Git, toy_args: list[str], invoke_stdout: Invoke
) -> None:
    add_bag(repo / "tshirt" / "tshirt")
    commit("a bag")
    edit(repo / "tshirt/tshirt/exchanges.csv", "toy:electricity,2,", "toy:electricity,3,")

    code, output = invoke_stdout(
        "diff", "main", "tshirt/tshirt", "--product", "tshirt:bag-sewing", "--json", *toy_args
    )

    assert code == 0, output
    data = json.loads(output)
    assert [p["id"] for p in data["products"]] == ["tshirt:bag-sewing"]
    [row] = data["results"]
    assert row["new"] - row["old"] == pytest.approx(0.5298, rel=1e-6)


def test_calc_product_takes_the_id_of_a_process(
    toy: Path, toy_args: list[str], invoke: Invoke
) -> None:
    add_bag(toy / "tshirt" / "tshirt")

    code, output = invoke(
        "calc", toy / "tshirt" / "tshirt", "--product", "tshirt:bag-sewing", *toy_args
    )

    assert code == 0, output
    assert "bag sewing | bag (tshirt:bag-sewing), per kg" in output
    assert "1.0596" in output
    assert "T-shirt" not in output


@pytest.mark.parametrize("command", ["calc", "contributions"])
def test_a_flow_id_is_refused_with_the_process_that_makes_it(
    toy: Path, toy_args: list[str], invoke: Invoke, command: str
) -> None:
    add_bag(toy / "tshirt" / "tshirt")

    code, output = invoke(command, toy / "tshirt" / "tshirt", "--product", "tshirt:bag", *toy_args)

    assert code == 2, output
    assert "tshirt:bag is the flow of the process tshirt:bag-sewing: give its id" in output


def test_two_processes_that_make_one_flow_are_both_scored(
    toy: Path, toy_args: list[str], invoke_stdout: Invoke
) -> None:
    database = toy / "tshirt" / "tshirt"
    add_bag(database)
    add_process(
        database,
        "bag-b",
        "e01,reference,tshirt:bag,1,kg,\ne02,input,toy:electricity,3,kWh,toy:electricity-production",
        details=BAG_SEWING,
    )

    code, output = invoke_stdout("calc", database, "--json", *toy_args)

    assert code == 0, output
    data = json.loads(output)
    assert data["format"] == "headwater-calc/3"
    assert [p["id"] for p in data["products"]] == [
        "tshirt:bag-b",
        "tshirt:bag-sewing",
        "tshirt:tshirt",
    ]
    assert "not_scored" not in data


# --- headwater database uses -------------------------------------------------------------------


def test_database_uses_lists_the_databases_and_the_studies_that_reach_it(
    dyed: Path, invoke_stdout: Invoke
) -> None:
    code, output = invoke_stdout("database", "uses", "tshirt-dyed/dyes", "--json")

    assert code == 0, output
    data = json.loads(output)
    assert data["format"] == "headwater-uses/2"
    [dyes] = data["databases"]
    assert dyes["path"] == "tshirt-dyed/dyes"
    assert dyes["users"] == [
        {"path": "tshirt-dyed/tshirt", "kind": "database", "through": ["dyes"]},
        {"path": "tshirt-dyed/tshirt-and-dye", "kind": "study", "through": ["dyes"]},
        {"path": "tshirt-dyed/tshirt-footprint", "kind": "study", "through": ["tshirt", "dyes"]},
    ]


def test_the_study_group_is_gone(invoke: Invoke) -> None:
    code, output = invoke("study", "uses", "tshirt-dyed/dyes")

    assert code == 2
    assert "No such command 'study'" in output
