"""A line's distribution in a database file: the columns distribution, scale, minimum, maximum.

The centre of a distribution is always the line's amount: the median of a lognormal, the mean of
a normal, the mode of a triangular. Example: the T-shirt's 0.25 kg of fabric, lognormal with a
geometric standard deviation of 1.1, is `0.25,kg,...,lognormal,0.0953102,,`.
"""

import math
from collections.abc import Callable
from pathlib import Path

import pytest

from headwater.model import Distribution, InvalidDataError, Problem, Uncertainty
from headwater.study import read_database

Edit = Callable[[Path, str, str], None]
Write = Callable[[Path, str], None]

TSHIRT = "tshirt/tshirt"
HEADER = "line,role,flow,amount,unit,supplier,distribution,scale,minimum,maximum\n"
REFERENCE = "e01,reference,tshirt:tshirt,1,unit,,,,,\n"
ELECTRICITY = "e03,input,toy:electricity,0.5,kWh,toy:electricity-production,,,,\n"
LN11 = math.log(1.1)


def tshirt_with(fabric: str) -> str:
    """The T-shirt's process file with line e02 as given: the cells after the supplier."""
    return (
        HEADER
        + REFERENCE
        + f"e02,input,toy:cotton-fabric,0.25,kg,toy:fabric-production,{fabric}\n"
        + ELECTRICITY
    )


def fabric_line(toy: Path, write: Write, fabric: str) -> Uncertainty | None:
    write(toy / TSHIRT / "processes/tshirt.csv", tshirt_with(fabric))
    database = read_database(toy / TSHIRT)
    (process,) = database.processes
    return next(e for e in process.exchanges if e.line_id == "e02").uncertainty


def problems(toy: Path, write: Write, fabric: str) -> tuple[Problem, ...]:
    write(toy / TSHIRT / "processes/tshirt.csv", tshirt_with(fabric))
    with pytest.raises(InvalidDataError) as raised:
        read_database(toy / TSHIRT)
    return raised.value.problems


def test_a_lognormal_has_the_amount_as_its_median(toy: Path, write: Write) -> None:
    found = fabric_line(toy, write, f"lognormal,{LN11!r},,")

    assert found is not None
    assert found.distribution is Distribution.lognormal
    assert found.loc == pytest.approx(math.log(0.25), rel=1e-15)
    assert found.scale == pytest.approx(LN11, rel=1e-15)


def test_a_normal_has_the_amount_as_its_mean(toy: Path, write: Write) -> None:
    found = fabric_line(toy, write, "normal,0.02,,")

    assert found is not None
    assert (found.distribution, found.loc, found.scale) == (Distribution.normal, 0.25, 0.02)


def test_a_triangular_has_the_amount_as_its_mode(toy: Path, write: Write) -> None:
    found = fabric_line(toy, write, "triangular,,0.2,0.3")

    assert found is not None
    assert (found.loc, found.minimum, found.maximum) == (0.25, 0.2, 0.3)


def test_a_uniform_takes_its_minimum_and_maximum(toy: Path, write: Write) -> None:
    found = fabric_line(toy, write, "uniform,,0.2,0.3")

    assert found is not None
    assert (found.distribution, found.minimum, found.maximum) == (Distribution.uniform, 0.2, 0.3)


def test_blank_cells_mean_no_distribution(toy: Path, write: Write) -> None:
    assert fabric_line(toy, write, ",,,") is None


def test_a_file_without_the_columns_has_no_distributions(toy: Path) -> None:
    (process,) = read_database(toy / TSHIRT).processes

    assert all(e.uncertainty is None for e in process.exchanges)


@pytest.mark.parametrize(
    ("fabric", "words"),
    [
        ("lognormal,,,", "needs scale"),
        ("normal,,,", "needs scale"),
        ("uniform,,0.2,", "needs minimum and maximum"),
        ("triangular,,,0.3", "needs minimum and maximum"),
        ("lognormal,-0.1,,", "scale -0.1 must be above 0"),
        ("lognormal,0.1,0.2,0.3", "lognormal does not use minimum and maximum"),
        ("uniform,0.1,0.2,0.3", "uniform does not use scale"),
        ("uniform,,0.3,0.4", "amount 0.25 is outside minimum 0.3 and maximum 0.4"),
        ("triangular,,0.1,0.2", "amount 0.25 is outside minimum 0.1 and maximum 0.2"),
        (",0.1,,", "scale is given without a distribution"),
        ("lognormal,abc,,", "scale"),
    ],
)
def test_a_distribution_that_does_not_fit_is_an_error(
    toy: Path, write: Write, fabric: str, words: str
) -> None:
    (problem,) = problems(toy, write, fabric)

    assert problem.code in ("bad-uncertainty", "incomplete-uncertainty", "bad-number")
    assert problem.item == "tshirt:tshirt line e02"
    assert words in problem.message
    assert problem.location == "processes/tshirt.csv:3"


def test_an_unknown_distribution_suggests_the_closest(toy: Path, write: Write) -> None:
    (problem,) = problems(toy, write, "lognorml,0.1,,")

    assert problem.code == "unknown-distribution"
    assert "'lognormal'" in problem.message


def test_a_lognormal_on_an_amount_of_0_is_an_error(toy: Path, write: Write) -> None:
    text = tshirt_with(f"lognormal,{LN11!r},,").replace("0.25,kg", "0,kg")
    write(toy / TSHIRT / "processes/tshirt.csv", text)
    with pytest.raises(InvalidDataError) as raised:
        read_database(toy / TSHIRT)

    (problem,) = raised.value.problems
    assert problem.code == "bad-uncertainty"
    assert "amount of 0" in problem.message


def test_a_formula_line_cannot_have_its_own_distribution(toy: Path, write: Write) -> None:
    write(toy / TSHIRT / "parameters.yaml", "database:\n  fabric_kg: 0.25\n")
    text = tshirt_with(f"lognormal,{LN11!r},,").replace("0.25,kg", "fabric_kg,kg")
    write(toy / TSHIRT / "processes/tshirt.csv", text)
    with pytest.raises(InvalidDataError) as raised:
        read_database(toy / TSHIRT)

    (problem,) = raised.value.problems
    assert problem.code == "formula-with-distribution"
    assert "fabric_kg" in problem.message  # give the distribution to the parameter


# --- overrides -------------------------------------------------------------------------------


def test_an_override_row_can_give_its_line_a_distribution(toy: Path, write: Write) -> None:
    write(
        toy / TSHIRT / "overrides/mill.csv",
        "process,line,role,flow,amount,unit,supplier,distribution,scale,minimum,maximum\n"
        "toy:fabric-production,e02,input,toy:electricity,2.5,kWh,toy:electricity-production,"
        f"lognormal,{LN11!r},,\n",
    )
    (override,) = read_database(toy / TSHIRT).overrides
    (line,) = override.lines

    assert line.uncertainty is not None
    assert line.uncertainty.loc == pytest.approx(math.log(2.5), rel=1e-15)


# --- parameters ------------------------------------------------------------------------------


def test_a_value_parameter_can_have_a_distribution(toy: Path, write: Write) -> None:
    write(
        toy / TSHIRT / "parameters.yaml",
        f"database:\n  fabric_kg: {{value: 0.25, distribution: lognormal, scale: {LN11!r}}}\n",
    )
    parameter = read_database(toy / TSHIRT).parameters.get("fabric_kg")

    assert parameter is not None
    assert parameter.uncertainty is not None
    assert parameter.uncertainty.loc == pytest.approx(math.log(0.25), rel=1e-15)


def test_a_formula_parameter_cannot_have_a_distribution(toy: Path, write: Write) -> None:
    write(
        toy / TSHIRT / "parameters.yaml",
        "database:\n  loss: 0.05\n"
        f"  fabric_kg: {{formula: 0.2 * (1 + loss), distribution: lognormal, scale: {LN11!r}}}\n",
    )
    with pytest.raises(InvalidDataError) as raised:
        read_database(toy / TSHIRT)

    (problem,) = raised.value.problems
    assert problem.code == "formula-with-distribution"
    assert problem.item == "parameter fabric_kg"


def test_a_parameter_distribution_that_does_not_fit_is_an_error(toy: Path, write: Write) -> None:
    write(
        toy / TSHIRT / "parameters.yaml",
        "database:\n"
        "  fabric_kg: {value: 0.25, distribution: uniform, minimum: 0.3, maximum: 0.4}\n",
    )
    with pytest.raises(InvalidDataError) as raised:
        read_database(toy / TSHIRT)

    (problem,) = raised.value.problems
    assert problem.code == "bad-uncertainty"
    assert "value 0.25 is outside minimum 0.3 and maximum 0.4" in problem.message
