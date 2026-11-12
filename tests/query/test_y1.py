"""Exit test Y1 of M2: the T-shirt with an override, a scenario, a parameter and a cut-off, every
number hand-checked, through the public API, on the toy folder and on the toy release.

The toy background (docs/calculation.md): electricity 0.5298 per kWh, fabric 3.7086 per kg, of
which 2.1192 direct and 3 kWh of electricity. The T-shirt buys 0.25 kg of fabric and 0.5 kWh.
"""

import pytest

import headwater as hw
from headwater.model import CutOffCriterion

REL = 1e-9  # AGENTS.md: relative 1e-9 for toy fixtures


def test_local_override_of_the_electricity_co2(either: hw.Background, calc) -> None:
    # Only the T-shirt's own 0.5 kWh uses the copy at 0.3298: 0.25 x 3.7086 + 0.5 x 0.3298.
    result = calc("tshirt-local-electricity", either)

    assert result.score == pytest.approx(1.09205, rel=REL)
    assert result.forward_score == pytest.approx(result.score, rel=1e-12)


def test_the_same_change_as_a_global_scenario(either: hw.Background, calc) -> None:
    # Fabric becomes 2.1192 + 3 x 0.3298 = 3.1086: 0.25 x 3.1086 + 0.5 x 0.3298.
    result = calc("tshirt-grid-scenario", either, scenario="clean-grid")

    assert result.score == pytest.approx(0.94205, rel=REL)
    assert result.forward_score == pytest.approx(result.score, rel=1e-12)
    assert result.provenance.scenario == "clean-grid"
    assert calc("tshirt-grid-scenario", either).score == pytest.approx(1.19205, rel=REL)


def test_local_override_of_the_fabric_at_2_5_kwh_per_kg(either: hw.Background, calc) -> None:
    # Fabric copy 2.1192 + 2.5 x 0.5298 = 3.4437: 0.25 x 3.4437 + 0.2649.
    result = calc("tshirt-local-fabric", either)

    assert result.score == pytest.approx(1.125825, rel=REL)
    assert result.forward_score == pytest.approx(result.score, rel=1e-12)


def test_the_parameter_fabric_kg(either: hw.Background, calc) -> None:
    result = calc("tshirt-parameter", either)

    (fabric,) = result.parameters()
    assert result.score == pytest.approx(1.19205, rel=REL)
    assert (fabric.parameter_id, fabric.value, fabric.unit) == ("fabric_kg", 0.25, "kg")
    assert fabric.sensitivity == pytest.approx(3.7086, rel=REL)
    assert fabric.elasticity == pytest.approx(0.92715 / 1.19205, rel=REL)
    assert fabric.elasticity == pytest.approx(0.7778, abs=5e-5)


def test_setting_fabric_kg_to_0_30(either: hw.Background, calc) -> None:
    # 0.30 x 3.7086 + 0.5 x 0.5298
    result = calc("tshirt-parameter", either, scenario="more-fabric")

    assert result.score == pytest.approx(1.37748, rel=REL)
    (fabric,) = result.parameters()
    assert fabric.value == 0.30
    assert fabric.elasticity == pytest.approx(0.30 * 3.7086 / 1.37748, rel=REL)


def test_the_packaging_tape_is_cut_off(either: hw.Background, calc) -> None:
    result = calc("tshirt-cut-off", either)

    assert result.score == pytest.approx(1.19205, rel=REL)
    (tape,) = result.provenance.cut_offs
    assert (tape.process_id, tape.line_id, tape.flow_id) == (
        "tshirt:tshirt",
        "e04",
        "tshirt:packaging-tape",
    )
    assert tape.criterion is CutOffCriterion.mass
    assert tape.share == pytest.approx(0.01 / 0.26, rel=1e-12)
    assert tape.cumulative_share == pytest.approx(0.01 / 0.26, rel=1e-12)
    assert tape.reason
    assert result.provenance.cut_off_shares == pytest.approx(
        {CutOffCriterion.mass: 0.01 / 0.26}, rel=1e-12
    )


def test_a_study_with_none_of_these_reports_none(either: hw.Background, calc) -> None:
    result = calc("tshirt", either)

    assert result.parameters() == ()
    assert result.provenance.cut_offs == ()
    assert result.provenance.cut_off_shares == {}
    assert result.provenance.scenario is None
