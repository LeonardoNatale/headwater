"""`headwater calc --product` and `--all-products`: the scores of any product of a database, or of
all of them, each named by its process and its product. With no option, every product is scored.

The toy T-shirt database is given a bag, made by the process `bag sewing` from 2 kWh of grid
electricity, so the process name differs from the product's. Nothing in the database buys the bag.
"""

import csv
import io
import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

import headwater as hw
from tests.conftest import add_process, open_database

from .conftest import copy_process

Invoke = Callable[..., tuple[int, str]]

TOY = Path(__file__).resolve().parent.parent / "fixtures" / "toy"


def add_product(database: Path, stem: str, process: str, product: str, kwh: float) -> None:
    """A product the database makes from `kwh` of grid electricity, by the process `process`."""
    add_process(
        database,
        stem,
        f"e01,reference,tshirt:{stem},1,kg,\n"
        f"e02,input,toy:electricity,{kwh},kWh,toy:electricity-production",
        details=f"{process},GLO,ordinary_transforming_activity",
    )
    with (database / "flows.csv").open("a") as handle:
        handle.write(f"tshirt:{stem},{product},product,kg,,\n")


@pytest.fixture
def two(toy: Path) -> Path:
    """The T-shirt database with a bag of 2 kWh, made by `bag sewing`."""
    database = toy / "tshirt" / "tshirt"
    add_product(database, "bag", "bag sewing", "bag", 2)
    return database


def background() -> hw.Background:
    return hw.load(TOY / "background", method=TOY / "methods" / "toy-climate", namespace="toy")


def test_two_products_print_each_with_the_score_database_scores_gives(
    two: Path, toy_args: list[str], invoke: Invoke
) -> None:
    code, output = invoke(
        "calc", two, *toy_args, "--product", "tshirt:tshirt", "--product", "tshirt:bag"
    )

    assert code == 0, output
    database, bg = open_database(two), background()
    tshirt = database.scores(bg, "tshirt:tshirt")["climate-change"]
    bag = database.scores(bg, "tshirt:bag")["climate-change"]
    # scores are aligned across products
    tshirt_line = f"T-shirt (tshirt:tshirt), per unit\n  Climate change: {tshirt:.6g} kg CO2-eq"
    assert tshirt_line in output
    bag_line = f"bag sewing | bag (tshirt:bag), per kg\n  Climate change:  {bag:.6g} kg CO2-eq"
    assert bag_line in output
    assert f"{tshirt:.6g}" == "1.19205"
    assert f"{bag:.6g}" == "1.0596"
    assert output.index("T-shirt (tshirt:tshirt)") > output.index("bag sewing | bag")
    # the provenance is the database's, with the largest residual of the products scored
    provenance = output.split("Provenance\n")[1]
    assert "input hash" not in provenance.split("each product")[0]
    assert re.search(r"  residual: \S+, the largest of the 2 products\n", provenance)
    assert "scores that cancel" not in provenance
    assert provenance.endswith("  each product's input hash and residual: in --json\n")


def test_all_products_prints_every_product_the_database_makes(
    two: Path, toy_args: list[str], invoke: Invoke
) -> None:
    add_product(two, "tote", "tote sewing", "tote", 3)

    code, output = invoke("calc", two, *toy_args, "--all-products")

    assert code == 0, output
    heads = [line for line in output.splitlines() if "(tshirt:" in line]
    assert heads == [
        "bag sewing | bag (tshirt:bag), per kg",
        "tote sewing | tote (tshirt:tote), per kg",
        "T-shirt (tshirt:tshirt), per unit",
    ]


def test_no_option_prints_every_product(two: Path, toy_args: list[str], invoke: Invoke) -> None:
    code, output = invoke("calc", two, *toy_args)
    _, every = invoke("calc", two, *toy_args, "--all-products")

    assert code == 0, output
    assert output == every
    assert "bag sewing | bag (tshirt:bag), per kg" in output
    assert "T-shirt (tshirt:tshirt), per unit\n  Climate change: 1.19205 kg CO2-eq" in output


def test_one_product_prints_the_layout_of_one(
    two: Path, toy_args: list[str], invoke: Invoke
) -> None:
    code, output = invoke("calc", two, *toy_args, "--product", "tshirt:bag")
    _, every = invoke("calc", two, *toy_args)

    assert code == 0, output
    assert "bag sewing | bag (tshirt:bag), per kg\n  Climate change: 1.0596 kg CO2-eq" in output
    assert "T-shirt (tshirt:tshirt)" not in output
    # the same header lines as when every product is scored
    assert output.splitlines()[:3] == every.splitlines()[:3]
    bag = open_database(two).calc(background(), "tshirt:bag").provenance
    assert f"  input hash: {bag.input_hash}\n" in output
    assert "scores that cancel: none" in output


def test_a_product_the_database_does_not_make_lists_the_ones_it_makes(
    two: Path, toy_args: list[str], invoke: Invoke
) -> None:
    code, output = invoke("calc", two, *toy_args, "--product", "tshirt:bags")

    assert code == 2, output
    assert "There is no process 'tshirt:bags'" in output
    assert "tshirt:bag, tshirt:tshirt" in output


def test_two_processes_that_make_one_flow_are_two_products(
    two: Path, toy_args: list[str], invoke: Invoke
) -> None:
    copy_process(two, "bag", "bag-b")

    code, output = invoke("calc", two, *toy_args, "--all-products")
    assert code == 0, output
    assert "bag sewing | bag (tshirt:bag), per kg" in output
    assert "bag sewing | bag (tshirt:bag-b), per kg" in output
    assert "Not scored" not in output

    code, output = invoke("calc", two, *toy_args, "--product", "tshirt:bag-b")
    assert code == 0, output
    assert "bag sewing | bag (tshirt:bag-b), per kg\n  Climate change: 1.0596 kg CO2-eq" in output


def test_product_and_all_products_together_is_a_usage_error(
    two: Path, toy_args: list[str], invoke: Invoke
) -> None:
    code, output = invoke("calc", two, *toy_args, "--all-products", "--product", "tshirt:bag")

    assert code == 2, output
    assert "--all-products" in output and "--product" in output


@pytest.mark.parametrize(
    ("option", "message"),
    [
        (["--iterations", "2"], "one product"),
        (["--scenario", "anything"], "holds no scenarios"),
    ],
)
def test_several_products_with_a_monte_carlo_run_or_a_scenario_is_a_usage_error(
    two: Path, toy_args: list[str], invoke: Invoke, option: list[str], message: str
) -> None:
    code, output = invoke("calc", two, *toy_args, "--all-products", *option)

    assert code == 2, output
    assert message in output


def test_a_product_is_named_by_its_process_as_well(
    toy: Path, toy_args: list[str], invoke: Invoke
) -> None:
    code, output = invoke("calc", toy / "tshirt-dyed" / "dyes", *toy_args)

    assert code == 0, output
    assert "reactive dye production | reactive dye (dyes:reactive-dye), per kg\n" in output


# --- JSON ---------------------------------------------------------------------------------------


def calc_json(invoke_stdout: Invoke, *args: str | Path) -> dict[str, Any]:
    code, output = invoke_stdout("calc", *args, "--json")
    assert code == 0, output
    return json.loads(output)


def test_the_json_of_every_product_has_the_scores_database_scores_gives(
    two: Path, toy_args: list[str], invoke_stdout: Invoke
) -> None:
    data = calc_json(invoke_stdout, two, *toy_args, "--all-products")

    assert data["format"] == "headwater-calc/3"
    assert (data["path"], data["kind"]) == (str(two), "database")
    assert [p["id"] for p in data["products"]] == ["tshirt:bag", "tshirt:tshirt"]
    bag, tshirt = data["products"]
    assert bag["name"] == "bag" and bag["unit"] == "kg" and bag["per"] == "1 kg"
    assert bag["flow"] == "tshirt:bag"
    assert tshirt["per"] == "1 unit"
    assert 0 <= bag["residual"] <= 1e-12 and 0 <= tshirt["residual"] <= 1e-12
    assert bag["cancels"] == [] and tshirt["cancels"] is None  # only the first is in full
    assert data["provenance"]["residual"] == max(tshirt["residual"], bag["residual"])
    assert bag["process"] == "bag sewing"
    assert tshirt["input_hash"] != bag["input_hash"]
    database, bg = open_database(two), background()
    assert tshirt["input_hash"] == database.calc(bg, "tshirt:tshirt").provenance.input_hash
    assert bag["input_hash"] == database.calc(bg, "tshirt:bag").provenance.input_hash
    for result in data["results"]:
        expected = database.scores(bg, result["product"]["id"])
        assert result["score"] == pytest.approx(expected[result["indicator"]["id"]], rel=1e-12)
    assert [r["product"]["id"] for r in data["results"]] == ["tshirt:bag", "tshirt:tshirt"]
    assert data["results"][0]["product"] == {
        "id": "tshirt:bag",
        "name": "bag",
        "process": "bag sewing",
    }
    assert data["results"][0]["indicator"] == {
        "id": "climate-change",
        "name": "Climate change",
        "unit": "kg CO2-eq",
    }


def test_the_json_has_a_stable_shape(toy: Path, toy_args: list[str], invoke_stdout: Invoke) -> None:
    data = calc_json(invoke_stdout, toy / "tshirt" / "tshirt", *toy_args)

    assert set(data) == {
        "format", "path", "kind", "name", "background", "method", "methods", "engine_version",
        "solver", "scenario", "products", "results", "provenance",
    }  # fmt: skip
    assert data["name"] == "tshirt"
    assert data["method"]["id"] == "toy-climate" and data["methods"] == ["toy-climate"]
    assert data["background"] == {"release": None, "folder": str(TOY / "background")}
    [result] = data["results"]
    assert result["score"] == pytest.approx(1.19205, rel=1e-9)
    [product] = data["products"]
    assert set(product) == {
        "id", "name", "flow", "unit", "per", "process", "input_hash", "residual", "cancels",
    }  # fmt: skip
    database = open_database(toy / "tshirt" / "tshirt")
    assert (
        product["input_hash"] == database.calc(background(), "tshirt:tshirt").provenance.input_hash
    )
    provenance = data["provenance"]  # the database's
    assert provenance["residual"] == product["residual"] <= 1e-12
    assert set(provenance) == {
        "method_hash", "pin", "uses", "condition", "database_condition", "residual",
        "not_characterised", "database",
    }  # fmt: skip
    assert set(provenance["database"]) == {"name", "commit", "changed"}


def test_the_json_lists_two_processes_that_make_one_flow_as_two_products(
    two: Path, toy_args: list[str], invoke_stdout: Invoke
) -> None:
    copy_process(two, "bag", "bag-b")

    data = calc_json(invoke_stdout, two, *toy_args, "--all-products")

    assert [(p["id"], p["flow"]) for p in data["products"]] == [
        ("tshirt:bag", "tshirt:bag"),
        ("tshirt:bag-b", "tshirt:bag"),
        ("tshirt:tshirt", "tshirt:tshirt"),
    ]
    assert "not_scored" not in data


def test_json_with_a_monte_carlo_run_is_a_usage_error(
    two: Path, toy_args: list[str], invoke: Invoke
) -> None:
    code, output = invoke("calc", two, *toy_args, "--json", "--iterations", "2")

    assert code == 2, output
    assert "--json" in output


# --- CSV ----------------------------------------------------------------------------------------


def test_the_csv_has_one_row_per_product_and_indicator(
    two: Path, toy_args: list[str], invoke_stdout: Invoke
) -> None:
    code, output = invoke_stdout("calc", two, *toy_args, "--all-products", "--csv")

    assert code == 0, output
    rows = list(csv.DictReader(io.StringIO(output)))
    assert list(rows[0]) == [
        "process_id", "process", "product", "per", "indicator_id", "indicator", "score_unit",
        "score",
    ]  # fmt: skip
    assert [(r["process_id"], r["process"], r["product"]) for r in rows] == [
        ("tshirt:bag", "bag sewing", "bag"),
        ("tshirt:tshirt", "T-shirt", "T-shirt"),
    ]
    database, bg = open_database(two), background()
    for row in rows:
        expected = database.scores(bg, row["process_id"])[row["indicator_id"]]
        assert float(row["score"]) == pytest.approx(expected, rel=1e-12)
    assert [(r["per"], r["score_unit"]) for r in rows] == [
        ("1 kg", "kg CO2-eq"),
        ("1 unit", "kg CO2-eq"),
    ]


def test_csv_and_json_together_is_a_usage_error(
    two: Path, toy_args: list[str], invoke: Invoke
) -> None:
    code, output = invoke("calc", two, *toy_args, "--csv", "--json")

    assert code == 2, output
    assert "--csv" in output
