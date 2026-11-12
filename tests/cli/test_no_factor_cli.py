"""`headwater validate` and `headwater calc` on a study flow with no factor: both refuse it until
no_factor.csv acknowledges it, and agree on every case."""

from collections.abc import Callable
from pathlib import Path

import pytest

Invoke = Callable[..., tuple[int, str]]

HEADER = "flow,flow_name,method,reason,who,when\n"
COD = (
    'tshirt:cod,"COD, Chemical Oxygen Demand",toy-climate,'
    "not characterised in toy-climate,Sam,2026-10-12\n"
)
CO2 = "toy:co2,,toy-climate,thought so,Sam,2026-10-12\n"


def study(toy: Path) -> Path:
    return toy / "tshirt-no-factor" / "tshirt"


def rows(toy: Path, *lines: str) -> None:
    (study(toy) / "no_factor.csv").write_text(HEADER + "".join(lines))


def test_without_the_row_validate_and_calc_refuse_with_the_same_message(
    toy: Path, toy_args: list[str], invoke: Invoke
) -> None:
    rows(toy)

    validate_code, validated = invoke("validate", study(toy), *toy_args)
    calc_code, calculated = invoke("calc", study(toy), *toy_args)

    assert validate_code == 1, validated
    assert calc_code == 1, calculated
    message = next(line for line in validated.splitlines() if line.startswith("error: "))
    assert message.startswith("error: flow tshirt:cod: has no factor in method toy-climate")
    assert "tshirt:tshirt" in message
    assert f"- {message.removeprefix('error: ')}" in calculated
    assert "Traceback" not in validated + calculated


def test_with_the_row_both_pass_and_calc_lists_the_flow_with_its_reason(
    toy: Path, toy_args: list[str], invoke: Invoke
) -> None:
    validate_code, validated = invoke("validate", study(toy), *toy_args)
    calc_code, calculated = invoke("calc", study(toy), *toy_args)

    assert validate_code == 0, validated
    assert "Database tshirt: valid." in validated
    assert calc_code == 0, calculated
    assert "Climate change: 1.19205 kg CO2-eq" in calculated
    assert "not characterised flows: 1" in calculated
    assert (
        "COD, Chemical Oxygen Demand (tshirt:cod), used by tshirt:tshirt; "
        "the database's own: not characterised in toy-climate"
    ) in calculated


@pytest.mark.parametrize(
    "row",
    [
        pytest.param(CO2, id="flow with a factor"),
        pytest.param("tshirt:bod,,toy-climate,no factor,Sam,2026-10-12\n", id="flow not used"),
    ],
)
def test_a_row_not_needed_is_a_warning_and_calc_still_passes(
    toy: Path, toy_args: list[str], invoke: Invoke, row: str
) -> None:
    rows(toy, COD, row)

    validate_code, validated = invoke("validate", study(toy), *toy_args)
    calc_code, calculated = invoke("calc", study(toy), *toy_args)

    assert validate_code == 0, validated
    assert "row not needed" in validated
    assert "Database tshirt: valid, 1 warning." in validated
    assert calc_code == 0, calculated
    assert "Climate change: 1.19205 kg CO2-eq" in calculated
