"""Flows the method has no factor for: counted as zero, and said so on the result.

A flow the method covers for some indicators counts 0 for the others. A flow of the background
that the method covers for no indicator is listed on the result. A flow the study itself emits
and the method does not cover is an error, because the author of the study chose it, until the
study's no_factor.csv acknowledges it (tests/query/test_no_factor_calc.py).
"""

from collections.abc import Callable
from pathlib import Path

import pytest

import headwater as hw
from headwater.model import InvalidDataError
from tests.conftest import open_database

REL = 1e-9  # AGENTS.md: relative 1e-9 for toy fixtures

Calc = Callable[..., hw.Result]
LoadToy = Callable[[Path], hw.Background]


def add_n2o(toy: Path, to_process: str, folder: str = "background") -> None:
    """Nitrous oxide has no factor in toy-climate."""
    with (toy / folder / "flows.csv").open("a") as f:
        f.write("toy:n2o,Dinitrogen monoxide,elementary,kg,air,10024-97-2\n")
    with (toy / folder / "processes" / f"{to_process}.csv").open("a") as f:
        f.write("e09,emission,toy:n2o,0.0001,kg,\n")


def test_a_result_with_every_flow_covered_lists_nothing(calc: Calc) -> None:
    assert calc("tshirt").not_characterised == ()


def test_a_background_flow_with_no_factor_is_listed_on_the_result(
    toy: Path, load_toy: LoadToy, calc: Calc
) -> None:
    add_n2o(toy, "electricity-production")

    result = calc("tshirt", root=toy, background=load_toy(toy))  # loading does not raise

    # It counts 0, so the score is the one without it.
    assert result.score == pytest.approx(1.19205, rel=REL)
    (flow,) = result.not_characterised
    assert flow.flow_id == "toy:n2o"
    assert flow.name == "Dinitrogen monoxide"
    assert flow.processes == ("toy:electricity-production",)


def test_a_flow_used_by_two_processes_is_listed_once_with_both(
    toy: Path, load_toy: LoadToy, calc: Calc
) -> None:
    add_n2o(toy, "fabric-production")
    with (toy / "background" / "processes" / "electricity-production.csv").open("a") as f:
        f.write("e09,emission,toy:n2o,0.0001,kg,\n")

    (flow,) = calc("tshirt", root=toy, background=load_toy(toy)).not_characterised

    assert flow.processes == ("toy:electricity-production", "toy:fabric-production")


def test_the_background_lists_its_flows_with_no_factor(toy: Path, load_toy: LoadToy) -> None:
    add_n2o(toy, "electricity-production")

    background = load_toy(toy)

    assert [f.flow_id for f in background.not_characterised] == ["toy:n2o"]


def add_ozone_indicator_covering_only_methane(toy: Path) -> None:
    method = toy / "methods" / "toy-climate"
    (method / "method.yaml").write_text(
        (method / "method.yaml").read_text()
        + "  - id: ozone\n    name: Ozone formation\n    unit: kg NMVOC-eq\n"
    )
    with (method / "factors.csv").open("a") as f:
        f.write("ozone,toy:ch4,,0.5\n")


def test_a_flow_covered_for_another_indicator_counts_zero_and_is_not_listed(
    toy: Path, load_toy: LoadToy
) -> None:
    add_ozone_indicator_covering_only_methane(toy)
    background = load_toy(toy)
    study = open_database(toy / "tshirt" / "tshirt")

    ozone = study.calc(background, "tshirt:tshirt", indicator="ozone")
    climate = study.calc(background, "tshirt:tshirt", indicator="climate-change")

    # Methane: 0.25 kg fabric × 0.004 + 1.25 kWh × 0.001 = 0.00225 kg, at 0.5. Carbon dioxide has
    # no ozone factor, so it counts 0 there. The method covers it for climate change, so it is
    # not "not characterised".
    assert ozone.score == pytest.approx(0.001125, rel=REL)
    assert ozone.not_characterised == ()
    assert climate.score == pytest.approx(1.19205, rel=REL)


def test_a_flow_the_study_emits_with_no_factor_is_an_error_even_if_the_background_has_it(
    toy: Path, load_toy: LoadToy
) -> None:
    add_n2o(toy, "electricity-production")
    add_n2o(toy, "tshirt", folder="tshirt/tshirt")
    background = load_toy(toy)
    study = open_database(toy / "tshirt" / "tshirt")

    with pytest.raises(InvalidDataError, match="flow toy:n2o") as info:
        study.calc(background, "tshirt:tshirt")

    assert [(p.code, p.item) for p in info.value.problems] == [("no-factor", "flow toy:n2o")]
    assert "tshirt:tshirt" in str(info.value)
    assert "toy:electricity-production" not in str(info.value)
