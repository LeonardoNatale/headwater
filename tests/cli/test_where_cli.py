"""`headwater where`: where a study's score is emitted, by process and by location."""

import shutil
from collections.abc import Callable
from pathlib import Path

import headwater as hw
from tests.conftest import TABLE_HEADER, add_process, open_database, toy_database

Invoke = Callable[..., tuple[int, str]]

TOY = Path(__file__).resolve().parent.parent / "fixtures" / "toy"


def edit(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    assert old in text, f"{old!r} not found in {path}"
    path.write_text(text.replace(old, new, 1))


def study(toy: Path, name: str = "tshirt") -> Path:
    return toy_database(toy, name)


def result(toy: Path) -> hw.Result:
    background = hw.load(
        toy / "background", method=toy / "methods" / "toy-climate", namespace="toy"
    )
    return open_database(study(toy)).calc(background, "tshirt:tshirt")


def test_where_prints_what_the_python_api_prints(
    toy: Path, toy_args: list[str], invoke: Invoke
) -> None:
    code, output = invoke("where", study(toy), *toy_args)

    assert code == 0, output
    assert f"Database tshirt ({study(toy)})" in output
    assert "Indicator: Climate change (kg CO2-eq)" in output
    assert str(result(toy).where()) in output
    assert "  1.25 kWh of electricity production (GLO): 0.66225 (55.6%)" in output
    assert "  GLO: 1.19205 (100.0%), 2 processes" in output


def test_top_is_passed_on(toy: Path, invoke: Invoke) -> None:
    edit(
        toy / "background" / "processes.csv",
        "electricity production,GLO",
        "electricity production,RER",
    )
    args = ("--release", toy / "background", "--method", toy / "methods" / "toy-climate")

    code, output = invoke("where", study(toy), *args, "--namespace", "toy", "--top", "1")

    assert code == 0, output
    assert str(result(toy).where(top=1)) in output
    assert "  rest: 0.52980 (44.4%), 1 process" in output
    assert "  RER: 0.66225 (55.6%), 1 process" in output
    assert "  rest: 0.52980 (44.4%), 1 location" in output


def test_a_score_of_zero_has_nothing_to_show(
    toy: Path, toy_args: list[str], invoke: Invoke
) -> None:
    path = study(toy) / "exchanges.csv"
    path.write_text(TABLE_HEADER + "tshirt,e01,reference,tshirt:tshirt,1,unit,\n")

    code, output = invoke("where", study(toy), *toy_args)

    assert code == 0, output
    assert "1 unit of T-shirt: 0.00000\nBy process:\n  none" in output


def test_a_study_that_does_not_validate_is_not_split(
    toy: Path, toy_args: list[str], invoke: Invoke
) -> None:
    edit(study(toy) / "exchanges.csv", "toy:fabric-production", "toy:nowhere")

    code, output = invoke("where", study(toy), *toy_args)

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

    code, output = invoke("where", study(toy), *args)
    code_named, named = invoke("where", study(toy), *args, "--indicator", "methane")

    assert code == 2
    assert "--indicator" in output
    assert code_named == 0, named
    # 0.001 kg CH4 per kWh x 1.25 kWh, and 0.004 kg per kg of fabric x 0.25 kg
    assert "1 unit of T-shirt: 0.00225" in named
    assert "  1.25 kWh of electricity production (GLO): 0.00125 (55.6%)" in named


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

    code, output = invoke("where", database, *toy_args)
    code_bag, bag = invoke("where", database, *toy_args, "--product", "tshirt:bag")
    code_tshirt, tshirt = invoke("where", database, *toy_args, "--product", "tshirt:tshirt")

    assert code == 2, output
    assert "--product" in output and "tshirt:bag, tshirt:tshirt" in output
    assert code_bag == 0, bag
    assert "bag" in bag and "T-shirt" not in bag
    assert code_tshirt == 0, tshirt
    assert "T-shirt" in tshirt
