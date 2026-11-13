"""`headwater contributions`: the tree as text and the lines that move the score most."""

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
        TOY / "background", method=TOY / "methods" / "toy-climate", namespace="toy"
    )
    return open_database(study(toy)).calc(background, "tshirt:tshirt")


def test_the_tree_is_the_one_the_python_api_prints(
    toy: Path, toy_args: list[str], invoke: Invoke
) -> None:
    code, output = invoke("contributions", study(toy), *toy_args)

    assert code == 0, output
    assert str(result(toy).contributions()) in output
    assert "1 unit of T-shirt: 1.19205 (100.0%), direct 0.00000" in output
    assert "  0.25 kg of cotton fabric production: 0.92715 (77.8%), direct 0.52980" in output


def test_the_depth_and_the_minimum_share_are_passed_on(
    toy: Path, toy_args: list[str], invoke: Invoke
) -> None:
    _, shallow = invoke("contributions", study(toy), *toy_args, "--depth", "1")
    _, pruned = invoke("contributions", study(toy), *toy_args, "--min-share", "0.5")

    assert str(result(toy).contributions(max_depth=1)) in shallow
    assert "0.75 kWh of electricity production" not in shallow
    assert str(result(toy).contributions(min_share=0.5)) in pruned
    assert "rest: 0.39735 (33.3%)" in pruned


def test_the_top_levers_follow_the_tree(toy: Path, toy_args: list[str], invoke: Invoke) -> None:
    code, output = invoke("contributions", study(toy), *toy_args, "--sensitivity", "3")

    assert code == 0, output
    levers = output.split("Sensitivity, largest")[1].strip().splitlines()[1:]
    assert len(levers) == 3
    # docs/calculation.md: elasticity 0.7778 and sensitivity 3.7086 for the fabric line
    assert "tshirt:tshirt line e02" in levers[0]
    assert "input" in levers[0]
    assert "elasticity 0.7778" in levers[0]
    assert "sensitivity 3.7086" in levers[0]
    assert "toy:electricity-production line e02" in levers[1]
    assert "elasticity 0.5243" in levers[1]


def test_five_levers_are_shown_by_default_and_none_when_asked(
    toy: Path, toy_args: list[str], invoke: Invoke
) -> None:
    _, default = invoke("contributions", study(toy), *toy_args)
    _, none = invoke("contributions", study(toy), *toy_args, "--sensitivity", "0")

    assert len(default.split("Sensitivity, largest")[1].strip().splitlines()) == 1 + 5
    assert "Sensitivity, largest" not in none


def test_a_score_of_zero_has_a_tree_and_says_why_there_are_no_levers(
    toy: Path, toy_args: list[str], invoke: Invoke
) -> None:
    path = study(toy) / "exchanges.csv"
    path.write_text(TABLE_HEADER + "tshirt,e01,reference,tshirt:tshirt,1,unit,\n")

    code, output = invoke("contributions", study(toy), *toy_args)

    assert code == 0, output
    assert "1 unit of T-shirt: 0.00000" in output
    assert "no sensitivity" in output.lower()
    assert "zero" in output
    assert "Traceback" not in output


def test_a_study_that_does_not_validate_is_not_explained(
    toy: Path, toy_args: list[str], invoke: Invoke
) -> None:
    edit(study(toy) / "exchanges.csv", "toy:fabric-production", "toy:nowhere")

    code, output = invoke("contributions", study(toy), *toy_args)

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

    code, output = invoke("contributions", study(toy), *args)
    code_named, named = invoke("contributions", study(toy), *args, "--indicator", "methane")

    assert code == 2
    assert "climate-change" in output and "methane" in output
    assert "--indicator" in output
    assert "Traceback" not in output
    assert code_named == 0, named
    assert "1 unit of T-shirt: 0.00225" in named


def test_the_indicator_help_says_what_each_command_does_without_it(invoke: Invoke) -> None:
    _, calc = invoke("calc", "--help")
    _, diff = invoke("diff", "--help")
    _, explain = invoke("contributions", "--help")
    _, where = invoke("where", "--help")
    _, levers = invoke("sensitivity", "--help")

    assert "Default: all of them" in " ".join(calc.split())
    assert "Default: all of them" in " ".join(diff.split())
    assert "Needed when the method has several" in " ".join(explain.split())
    assert "Default: all of them" not in " ".join(explain.split())
    for text in (where, levers):
        assert "Needed when the method has several" in " ".join(text.split())


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

    code, output = invoke("contributions", database, *toy_args)
    code_bag, bag = invoke("contributions", database, *toy_args, "--product", "tshirt:bag")
    code_tshirt, tshirt = invoke("contributions", database, *toy_args, "--product", "tshirt:tshirt")

    assert code == 2, output
    assert "--product" in output and "tshirt:bag, tshirt:tshirt" in output
    assert code_bag == 0, bag
    assert "bag" in bag and "T-shirt" not in bag
    assert code_tshirt == 0, tshirt
    assert "T-shirt" in tshirt
