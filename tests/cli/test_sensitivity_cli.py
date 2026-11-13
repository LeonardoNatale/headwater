"""`headwater sensitivity`: the lines that move a study's score most, as `contributions` lists
them."""

import shutil
from collections.abc import Callable
from pathlib import Path

from tests.conftest import TABLE_HEADER, add_process, toy_database

Invoke = Callable[..., tuple[int, str]]

TOY = Path(__file__).resolve().parent.parent / "fixtures" / "toy"


def edit(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    assert old in text, f"{old!r} not found in {path}"
    path.write_text(text.replace(old, new, 1))


def study(toy: Path, name: str = "tshirt") -> Path:
    return toy_database(toy, name)


def lever_lines(output: str) -> list[str]:
    return output.split("Sensitivity, largest elasticity first:")[1].strip("\n").splitlines()


def test_the_levers_are_listed_with_the_header_of_explain(
    toy: Path, toy_args: list[str], invoke: Invoke
) -> None:
    code, output = invoke("sensitivity", study(toy), *toy_args)

    assert code == 0, output
    assert f"Database tshirt ({study(toy)})" in output
    assert "Indicator: Climate change (kg CO2-eq)" in output
    # docs/calculation.md: the T-shirt's levers, largest elasticity first
    assert lever_lines(output) == [
        "  tshirt:tshirt line e02: input 0.25 kg of toy:cotton-fabric from "
        "toy:fabric-production, elasticity 0.7778, sensitivity 3.7086",
        "  toy:electricity-production line e02: emission 0.5 kg of toy:co2, "
        "elasticity 0.5243, sensitivity 1.25",
        "  toy:fabric-production line e03: emission 2 kg of toy:co2, "
        "elasticity 0.4194, sensitivity 0.25",
        "  toy:fabric-production line e02: input 3 kWh of toy:electricity from "
        "toy:electricity-production, elasticity 0.3333, sensitivity 0.13245",
        "  tshirt:tshirt line e03: input 0.5 kWh of toy:electricity from "
        "toy:electricity-production, elasticity 0.2222, sensitivity 0.5298",
        "  toy:electricity-production line e03: emission 0.001 kg of toy:ch4, "
        "elasticity 0.0312, sensitivity 37.25",
        "  toy:fabric-production line e04: emission 0.004 kg of toy:ch4, "
        "elasticity 0.0250, sensitivity 7.45",
        # The T-shirt sends nothing to incineration, which therefore runs 0 times.
        "  toy:incineration-textile-waste line e02: emission 1.2 kg of toy:co2, "
        "elasticity 0.0000, sensitivity 0",
    ]


def test_top_keeps_the_largest(toy: Path, toy_args: list[str], invoke: Invoke) -> None:
    code, output = invoke("sensitivity", study(toy), *toy_args, "--top", "2")

    assert code == 0, output
    found = lever_lines(output)
    assert len(found) == 2
    assert "tshirt:tshirt line e02" in found[0]
    assert "toy:electricity-production line e02" in found[1]


def test_the_reference_lines_are_listed_when_asked(
    toy: Path, toy_args: list[str], invoke: Invoke
) -> None:
    _, without = invoke("sensitivity", study(toy), *toy_args)
    code, output = invoke("sensitivity", study(toy), *toy_args, "--include-reference")

    assert code == 0, output
    assert "reference" not in "\n".join(lever_lines(without))
    # 1% more T-shirt per run of its process makes the score of one T-shirt 1% smaller.
    assert lever_lines(output)[0] == (
        "  tshirt:tshirt line e01: reference 1 unit of tshirt:tshirt, "
        "elasticity -1.0000, sensitivity -1.1921"
    )


def test_a_score_of_zero_says_why_there_are_no_levers(
    toy: Path, toy_args: list[str], invoke: Invoke
) -> None:
    path = study(toy) / "exchanges.csv"
    path.write_text(TABLE_HEADER + "tshirt,e01,reference,tshirt:tshirt,1,unit,\n")

    code, output = invoke("sensitivity", study(toy), *toy_args)

    assert code == 0, output
    assert "No sensitivity:" in output
    assert "zero" in output
    assert "Traceback" not in output


def test_a_study_that_does_not_validate_has_no_levers(
    toy: Path, toy_args: list[str], invoke: Invoke
) -> None:
    edit(study(toy) / "exchanges.csv", "toy:fabric-production", "toy:nowhere")

    code, output = invoke("sensitivity", study(toy), *toy_args)

    assert code == 1
    assert "toy:nowhere" in output
    assert "Traceback" not in output


def test_a_method_with_several_indicators_needs_one_named(
    toy: Path, tmp_path: Path, invoke: Invoke
) -> None:
    method = tmp_path / "two-indicators"
    shutil.copytree(TOY / "methods" / "toy-climate", method)
    edit(
        method / "method.yaml",
        "    unit: kg CO2-eq\n",
        "    unit: kg CO2-eq\n  - id: methane\n    name: Methane\n    unit: kg CH4\n",
    )
    with (method / "factors.csv").open("a") as handle:
        handle.write("methane,toy:ch4,,1\n")
    args = ("--release", TOY / "background", "--method", method, "--namespace", "toy")

    code, output = invoke("sensitivity", study(toy), *args)
    code_named, named = invoke("sensitivity", study(toy), *args, "--indicator", "methane")

    assert code == 2
    assert "--indicator" in output
    assert "Traceback" not in output
    assert code_named == 0, named
    assert "Indicator: Methane (kg CH4)" in named
    assert "elasticity" in lever_lines(named)[0]


def two_products(toy: Path) -> Path:
    """The T-shirt database with a second product, a bag made from 2 kWh of grid electricity."""
    database = study(toy)
    add_process(
        database,
        "bag",
        "e01,reference,tshirt:bag,1,kg,\ne02,input,toy:electricity,2,kWh,toy:electricity-production",
        details="bag sewing,GLO,ordinary_transforming_activity",
    )
    with (database / "flows.csv").open("a") as handle:
        handle.write("tshirt:bag,bag,product,kg,,\n")
    return database


def test_a_database_with_two_products_needs_product(
    toy: Path, toy_args: list[str], invoke: Invoke
) -> None:
    database = two_products(toy)

    code, output = invoke("sensitivity", database, *toy_args)
    code_bag, bag = invoke("sensitivity", database, *toy_args, "--product", "tshirt:bag")
    code_tshirt, tshirt = invoke("sensitivity", database, *toy_args, "--product", "tshirt:tshirt")

    assert code == 2, output
    assert "--product" in output and "tshirt:bag, tshirt:tshirt" in output
    assert code_bag == 0, bag
    assert lever_lines(bag)[0].startswith("  tshirt:bag line e02")
    assert code_tshirt == 0, tshirt
    assert lever_lines(tshirt)[0].startswith("  tshirt:tshirt line e02")
