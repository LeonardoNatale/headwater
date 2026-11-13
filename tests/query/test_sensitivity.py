"""Sensitivity analysis: what a person can change in a study, ranked by elasticity.

Hand-checked on the T-shirt (score 1.19205). With x the stored scores and s how much each
process runs:
- a bought amount moves the score by x[supplier] × s[buyer]: 0.5298 × 0.25 = 0.13245 for the
  3 kWh of electricity in a kg of fabric;
- an emission moves it by c × s[process]: 1 × 1.25 per kg of CO2 of 1.25 kWh of electricity.
"""

from collections.abc import Callable
from pathlib import Path

import pytest

import headwater as hw
import headwater.query.result as result_module
from headwater.model import Role
from headwater.release import Release
from tests.conftest import add_lines

REL = 1e-9  # AGENTS.md: relative 1e-9 for toy fixtures
SCORE = 1.19205

TOY = Path(__file__).resolve().parent.parent / "fixtures" / "toy"

Calc = Callable[..., hw.Result]
LoadToy = Callable[[Path], hw.Background]
Edit = Callable[[Path, str, str], None]

TSHIRT_LEVERS_BY_ELASTICITY = [
    # (process, line, role, elasticity)
    ("tshirt:tshirt", "e02", Role.input, 7 / 9),  # 0.25 kg fabric
    ("toy:electricity-production", "e02", Role.emission, 0.625 / SCORE),  # 0.5 kg CO2 × 1.25 kWh
    ("toy:fabric-production", "e03", Role.emission, 0.5 / SCORE),  # 2.0 kg CO2 × 0.25 kg
    ("toy:fabric-production", "e02", Role.input, 1 / 3),  # 3 kWh per kg of fabric: 0.13245
    ("tshirt:tshirt", "e03", Role.input, 2 / 9),  # 0.5 kWh
    ("toy:electricity-production", "e03", Role.emission, 0.03725 / SCORE),  # CH4
    ("toy:fabric-production", "e04", Role.emission, 0.0298 / SCORE),  # CH4
    ("toy:incineration-textile-waste", "e02", Role.emission, 0.0),  # does not run
]


def keys(levers: tuple[hw.Sensitivity, ...]) -> list[tuple[str, str]]:
    return [(x.process_id, x.line_id) for x in levers]


def lever(levers: tuple[hw.Sensitivity, ...], process_id: str, line_id: str) -> hw.Sensitivity:
    (found,) = [x for x in levers if (x.process_id, x.line_id) == (process_id, line_id)]
    return found


def test_the_levers_of_the_tshirt_in_order(calc: Calc) -> None:
    levers = calc("tshirt").sensitivity()

    assert [(x.process_id, x.line_id, x.role) for x in levers] == [
        (process, line, role) for process, line, role, _ in TSHIRT_LEVERS_BY_ELASTICITY
    ]
    for found, (_, _, _, elasticity) in zip(levers, TSHIRT_LEVERS_BY_ELASTICITY, strict=True):
        assert found.elasticity == pytest.approx(elasticity, rel=REL, abs=1e-15)


def test_the_reference_line_of_a_process_is_not_a_lever_by_default(calc: Calc) -> None:
    levers = calc("tshirt").sensitivity()

    assert Role.reference not in {x.role for x in levers}
    assert ("tshirt:tshirt", "e01") not in keys(levers)


def test_include_reference_keeps_the_reference_lines_and_they_rank_first(calc: Calc) -> None:
    result = calc("tshirt")

    levers = result.sensitivity(include_reference=True)

    references = [x for x in levers if x.role is Role.reference]
    assert sorted(x.process_id for x in references) == [
        "toy:electricity-production",
        "toy:fabric-production",
        "toy:incineration-textile-waste",
        "tshirt:tshirt",
    ]
    # Doubling what a process makes per run halves what each unit costs.
    assert lever(levers, "tshirt:tshirt", "e01").elasticity == pytest.approx(-1.0, rel=REL)
    assert lever(levers, "toy:fabric-production", "e01").elasticity == pytest.approx(
        -7 / 9, rel=REL
    )
    assert lever(levers, "toy:electricity-production", "e01").elasticity == pytest.approx(
        -5 / 9, rel=REL
    )
    # Without them the order is the one above: the reference lines are only taken out.
    others = [x for x in levers if x.role is not Role.reference]
    assert others == list(result.sensitivity())
    sizes = [abs(x.elasticity) for x in levers]
    assert sizes == sorted(sizes, reverse=True)


def test_top_counts_after_the_reference_lines_are_taken_out(calc: Calc) -> None:
    result = calc("tshirt")

    assert keys(result.sensitivity(top=2)) == keys(result.sensitivity())[:2]
    (first,) = result.sensitivity(top=1, include_reference=True)
    assert (first.process_id, first.line_id) == ("tshirt:tshirt", "e01")


def test_an_emission_lever_is_the_factor_times_how_much_the_process_runs(calc: Calc) -> None:
    levers = calc("tshirt").sensitivity()

    co2 = lever(levers, "toy:electricity-production", "e02")

    assert (co2.role, co2.flow_id, co2.supplier_id, co2.unit) == (
        Role.emission,
        "toy:co2",
        None,
        "kg",
    )
    assert co2.amount == pytest.approx(0.5, rel=REL)
    assert co2.sensitivity == pytest.approx(1.25, rel=REL)  # c = 1, runs 1.25 kWh
    ch4 = lever(levers, "toy:fabric-production", "e04")
    assert ch4.sensitivity == pytest.approx(29.8 * 0.25, rel=REL)  # c = 29.8, runs 0.25 kg


@pytest.mark.parametrize("study", ["tshirt", "tshirt-waste"])
def test_the_elasticities_of_the_emissions_add_up_to_one(calc: Calc, study: str) -> None:
    # The score is the sum of every emission times its factor and how much its process runs, so
    # it moves 1% when all of them move 1%.
    levers = calc(study).sensitivity()

    emissions = [x for x in levers if x.role in (Role.emission, Role.resource)]
    assert sum(x.elasticity for x in emissions) == pytest.approx(1.0, rel=REL)


def test_an_emission_lever_matches_a_finite_difference(
    toy: Path, load_toy: LoadToy, calc: Calc, edit: Edit
) -> None:
    sensitivity = lever(
        calc("tshirt", root=toy, background=load_toy(toy)).sensitivity(),
        "toy:electricity-production",
        "e02",
    ).sensitivity
    edit(
        toy / "background" / "exchanges.csv",
        "electricity-production,e02,emission,toy:co2,0.5,kg",
        "electricity-production,e02,emission,toy:co2,0.6,kg",
    )

    after = calc("tshirt", root=toy, background=load_toy(toy))

    assert after.score - SCORE == pytest.approx(sensitivity * 0.1, rel=1e-6)
    assert after.score == pytest.approx(1.31705, rel=REL)


def test_a_study_emission_is_a_lever_too(toy: Path, load_toy: LoadToy, calc: Calc) -> None:
    # A crop takes CO2 from the air: a negative emission, in the study itself.
    add_lines(toy / "tshirt" / "tshirt", "tshirt", "e04,emission,toy:co2,-0.2,kg,")

    result = calc("tshirt", root=toy, background=load_toy(toy))

    found = lever(result.sensitivity(), "tshirt:tshirt", "e04")
    assert result.score == pytest.approx(SCORE - 0.2, rel=REL)
    assert found.amount == pytest.approx(-0.2, rel=REL)
    assert found.sensitivity == pytest.approx(1.0, rel=REL)  # c = 1, the T-shirt runs once
    assert found.elasticity == pytest.approx(-0.2 / (SCORE - 0.2), rel=REL)


def test_a_resource_line_is_a_lever(toy: Path, load_toy: LoadToy, calc: Calc, edit: Edit) -> None:
    edit(
        toy / "background" / "exchanges.csv",
        "electricity-production,e02,emission,toy:co2",
        "electricity-production,e02,resource,toy:co2",
    )

    result = calc("tshirt", root=toy, background=load_toy(toy))

    assert lever(result.sensitivity(), "toy:electricity-production", "e02").role is Role.resource


def test_own_use_is_a_lever_and_the_reference_on_its_matrix_cell_is_not(
    toy: Path, load_toy: LoadToy, calc: Calc
) -> None:
    # Own use is on the diagonal, the same matrix cell as the reference. The role tells them apart.
    add_lines(
        toy / "background",
        "electricity-production",
        "e04,own_use,toy:electricity,0.02,kWh,toy:electricity-production",
    )

    levers = calc("tshirt", root=toy, background=load_toy(toy)).sensitivity()

    assert lever(levers, "toy:electricity-production", "e04").role is Role.own_use
    assert ("toy:electricity-production", "e01") not in keys(levers)


def test_levers_build_only_the_levers_asked_for(
    calc: Calc, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Ranking works on columns. A Sensitivity is made for the lines returned, not for every line.
    result = calc("tshirt")
    made: list[int] = []
    real = result_module.Sensitivity
    monkeypatch.setattr(
        result_module, "Sensitivity", lambda **fields: (made.append(1), real(**fields))[1]
    )

    top = result.sensitivity(top=3)

    assert len(top) == 3
    assert len(made) == 3
    assert result.sensitivity(top=0) == ()
    assert len(made) == 3


def test_levers_of_a_release_read_nothing_more_from_it(
    release: Release, monkeypatch: pytest.MonkeyPatch, calc: Calc
) -> None:
    background = hw.load(release.path, method="toy-climate")

    def no_query(self: Release) -> None:
        raise AssertionError("levers went back to the release")

    monkeypatch.setattr(Release, "connect", no_query)
    result = calc("tshirt", background)

    assert [x.line_id for x in result.sensitivity(top=2)] == ["e02", "e02"]


CANCEL = {
    "role": "avoided_product",
    "flow_id": "toy:electricity",
    "amount": 3.0,
    "unit": "kWh",
    "supplier_id": "toy:electricity-production",
}


def cancelled(result: hw.Result) -> list[tuple[str, str]]:
    levers = result.sensitivity(include_reference=True)
    return [
        key
        for key in [("toy:fabric-production", "e02"), ("toy:fabric-production", "e05")]
        if key not in keys(levers)
    ]


def test_lines_that_cancel_in_one_matrix_cell_have_no_lever_in_a_folder(
    toy: Path, load_toy: LoadToy, calc: Calc
) -> None:
    # Fabric production buys 3 kWh and is credited 3 kWh from the same supplier: A holds 0 there.
    add_lines(
        toy / "background",
        "fabric-production",
        "e05,avoided_product,toy:electricity,3,kWh,toy:electricity-production",
    )

    result = calc("tshirt", root=toy, background=load_toy(toy))

    assert cancelled(result) == [("toy:fabric-production", "e02"), ("toy:fabric-production", "e05")]
    assert result.score == pytest.approx(0.25 * 2.1192 + 0.5 * 0.5298, rel=REL)
    assert ("toy:fabric-production", "e03") in keys(result.sensitivity())  # the rest are untouched


def test_lines_that_cancel_in_one_matrix_cell_have_no_lever_in_a_release(
    make_release, records, calc: Calc
) -> None:
    release = make_release(records.with_new_line("toy:fabric-production", "e05", **CANCEL))
    background = hw.load(release.path, method="toy-climate")

    result = calc("tshirt", background)

    assert cancelled(result) == [("toy:fabric-production", "e02"), ("toy:fabric-production", "e05")]
    assert result.score == pytest.approx(0.25 * 2.1192 + 0.5 * 0.5298, rel=REL)


def test_levers_with_the_same_elasticity_keep_the_order_of_their_lines(
    make_release, records, calc: Calc
) -> None:
    # The incineration does not run for this T-shirt: its three emission lines all have an
    # elasticity of 0, and come last in the order of their lines, every time.
    extra = records.with_new_line(
        "toy:incineration-textile-waste",
        "e03",
        role="emission",
        flow_id="toy:ch4",
        amount=0.001,
        unit="kg",
    ).with_new_line(
        "toy:incineration-textile-waste",
        "e04",
        role="emission",
        flow_id="toy:co2",
        amount=0.1,
        unit="kg",
    )

    for _ in range(2):
        background = hw.load(make_release(extra).path, method="toy-climate")
        levers = calc("tshirt", background).sensitivity()

        assert [(x.process_id, x.line_id) for x in levers[-3:]] == [
            ("toy:incineration-textile-waste", "e02"),
            ("toy:incineration-textile-waste", "e03"),
            ("toy:incineration-textile-waste", "e04"),
        ]
        assert [x.elasticity for x in levers[-3:]] == [0.0, 0.0, 0.0]
