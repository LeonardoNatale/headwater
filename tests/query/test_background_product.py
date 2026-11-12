"""A background product on its own: `bg.contributions(id)` and `bg.sensitivity(id)`.

Hand-checked on the toy background (docs/calculation.md). For 1 kg of cotton fabric:
direct 2.1192 + 3 kWh x 0.5298 = 3.7086. Electricity runs 3 kWh for it, so its CO2 line
(0.5 kg per kWh, factor 1) moves the score by 3 per kg, and 1% more of it by 1.5 / 3.7086 %.
"""

from pathlib import Path

import pytest

import headwater as hw
from headwater.calc import CalculationError
from headwater.model import Role

REL = 1e-9
FABRIC = "toy:fabric-production"
INCINERATION = "toy:incineration-textile-waste"

# (process, line, role, elasticity) for 1 kg of fabric, largest first. The incineration does not
# run for it, so its emission has an elasticity of 0.
FABRIC_LEVERS = [
    (FABRIC, "e03", Role.emission, 2.0 / 3.7086),  # 2.0 kg CO2, factor 1, runs once
    (FABRIC, "e02", Role.input, 3 / 7),  # 3 kWh per kg: 0.5298 x 3 / 3.7086
    ("toy:electricity-production", "e02", Role.emission, 1.5 / 3.7086),  # 0.5 kg x 3 kWh
    (FABRIC, "e04", Role.emission, 0.1192 / 3.7086),  # 0.004 kg CH4 x 29.8
    ("toy:electricity-production", "e03", Role.emission, 0.0894 / 3.7086),  # 0.001 x 29.8 x 3
    (INCINERATION, "e02", Role.emission, 0.0),
]


@pytest.fixture(params=["folder", "release"])
def bg(request, background: hw.Background, release_background: hw.Background) -> hw.Background:
    return background if request.param == "folder" else release_background


def test_explain_a_background_product(bg: hw.Background) -> None:
    root = bg.contributions(FABRIC)

    assert (root.process_id, root.name, root.amount, root.unit) == (
        FABRIC,
        "cotton fabric production",
        1.0,
        "kg",
    )
    assert root.score == pytest.approx(3.7086, rel=REL)
    assert root.per_unit == pytest.approx(3.7086, rel=REL)
    assert root.direct == pytest.approx(2.1192, rel=REL)
    (electricity,) = root.children
    assert (electricity.process_id, electricity.amount, electricity.unit) == (
        "toy:electricity-production",
        3.0,
        "kWh",
    )
    assert electricity.score == pytest.approx(1.5894, rel=REL)
    assert root.rest == 0.0
    assert str(root) == (
        "1 kg of cotton fabric production: 3.70860 (100.0%), direct 2.11920\n"
        "  3 kWh of electricity production: 1.58940 (42.9%), direct 1.58940"
    )


def test_a_background_explanation_prunes_like_a_result_does(bg: hw.Background) -> None:
    short = bg.contributions(FABRIC, min_share=0.5)
    shallow = bg.contributions(FABRIC, max_depth=0)

    assert short.children == () and short.rest == pytest.approx(1.5894, rel=REL)
    assert shallow.children == () and shallow.rest == pytest.approx(1.5894, rel=REL)
    with pytest.raises(ValueError, match="min_share"):
        bg.contributions(FABRIC, min_share=-1)


def test_a_waste_treatment_scores_a_positive_burden_per_kg_treated(bg: hw.Background) -> None:
    root = bg.contributions(INCINERATION)

    assert (root.amount, root.unit, root.per_unit, root.score, root.direct) == (
        pytest.approx(1.0, rel=REL),
        "kg",
        pytest.approx(1.2, rel=REL),
        pytest.approx(1.2, rel=REL),
        pytest.approx(1.2, rel=REL),
    )


def test_the_levers_of_a_background_product_are_hand_checked(bg: hw.Background) -> None:
    levers = bg.sensitivity(FABRIC)

    assert [(x.process_id, x.line_id, x.role) for x in levers] == [
        (process, line, role) for process, line, role, _ in FABRIC_LEVERS
    ]
    for found, (_, _, _, elasticity) in zip(levers, FABRIC_LEVERS, strict=True):
        assert found.elasticity == pytest.approx(elasticity, rel=REL, abs=1e-15)
    by_line = {(x.process_id, x.line_id): x for x in levers}
    assert by_line[FABRIC, "e02"].sensitivity == pytest.approx(0.5298, rel=REL)  # x[electricity]
    assert by_line[FABRIC, "e02"].supplier_id == "toy:electricity-production"
    assert by_line["toy:electricity-production", "e02"].sensitivity == pytest.approx(3.0, rel=REL)


def test_the_elasticities_of_the_emissions_of_a_product_add_up_to_one(bg: hw.Background) -> None:
    levers = bg.sensitivity(FABRIC)

    emissions = [x for x in levers if x.role is Role.emission]
    assert sum(x.elasticity for x in emissions) == pytest.approx(1.0, rel=REL)


def test_reference_lines_and_top_work_as_on_a_result(bg: hw.Background) -> None:
    with_reference = bg.sensitivity(FABRIC, include_reference=True)

    assert (with_reference[0].process_id, with_reference[0].line_id) == (FABRIC, "e01")
    assert with_reference[0].elasticity == pytest.approx(-1.0, rel=REL)  # twice the output per run
    assert [x.line_id for x in bg.sensitivity(FABRIC, top=2)] == ["e03", "e02"]
    with pytest.raises(ValueError, match="top"):
        bg.sensitivity(FABRIC, top=-1)


def test_a_treatment_has_levers_per_kg_treated(bg: hw.Background) -> None:
    (emission, *rest) = bg.sensitivity(INCINERATION)

    # 1.2 kg CO2 per kg treated, factor 1, the treatment runs once per kg: the whole score.
    assert (emission.line_id, emission.role) == ("e02", Role.emission)
    assert emission.sensitivity == pytest.approx(1.0, rel=REL)
    assert emission.elasticity == pytest.approx(1.0, rel=REL)
    assert all(x.elasticity == 0.0 for x in rest)


def test_a_study_and_a_background_agree_on_the_fabric_it_buys(bg: hw.Background, calc) -> None:
    result = calc("tshirt", background=bg)
    bought = next(c for c in result.contributions().children if c.process_id == FABRIC)

    direct = bg.contributions(FABRIC)

    assert bought.per_unit == pytest.approx(direct.score, rel=REL)
    assert bought.direct / bought.amount == pytest.approx(direct.direct, rel=REL)


def test_an_unknown_product_is_an_error_with_a_hint(bg: hw.Background) -> None:
    for call in (bg.contributions, bg.sensitivity):
        with pytest.raises(ValueError, match="toy:fabric-productoin") as error:
            call("toy:fabric-productoin")
        assert "Did you mean 'toy:fabric-production'?" in str(error.value)


def test_the_indicator_is_chosen_as_for_a_study(bg: hw.Background) -> None:
    assert bg.contributions(FABRIC, indicator="climate-change").score == pytest.approx(
        3.7086, rel=REL
    )
    for call in (bg.contributions, bg.sensitivity):
        with pytest.raises(ValueError, match="no indicator 'nope'"):
            call(FABRIC, indicator="nope")


def test_levers_of_a_product_that_scores_zero_name_the_product(toy: Path, load_toy, edit) -> None:
    edit(
        toy / "background" / "processes" / "incineration-textile-waste.csv",
        "e02,emission,toy:co2,1.2,kg",
        "e02,emission,toy:co2,0,kg",
    )
    background = load_toy(toy)

    assert background.contributions(INCINERATION).score == 0.0
    with pytest.raises(CalculationError, match=INCINERATION):
        background.sensitivity(INCINERATION)
