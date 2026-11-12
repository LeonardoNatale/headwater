"""Where a score is emitted: `result.where()` and `bg.where(id)`, by process and by location.

Hand-checked on the toy (docs/calculation.md). For one T-shirt, electricity runs 1.25 kWh
(0.5 for the T-shirt, 0.75 for its 0.25 kg of fabric) and emits 0.5298 per kWh: 0.66225. Fabric
runs 0.25 kg and emits 2.1192 per kg: 0.52980. The T-shirt itself emits nothing. Together
1.19205, the score.
"""

from pathlib import Path

import pytest

import headwater as hw

from .conftest import CalcToy, Edit, LoadToy

REL = 1e-9
ELECTRICITY = "toy:electricity-production"
FABRIC = "toy:fabric-production"
INCINERATION = "toy:incineration-textile-waste"


def rows(where: hw.Where) -> list[tuple[str, float, str, str]]:
    return [(e.process_id, e.amount, e.unit, e.location) for e in where.processes]


def test_the_t_shirt_by_process(calc: CalcToy) -> None:
    result = calc("tshirt")

    where = result.where()

    assert rows(where) == [
        (ELECTRICITY, pytest.approx(1.25, rel=REL), "kWh", "GLO"),
        (FABRIC, pytest.approx(0.25, rel=REL), "kg", "GLO"),
    ]
    assert [e.name for e in where.processes] == [
        "electricity production",
        "cotton fabric production",
    ]
    assert [e.score for e in where.processes] == [
        pytest.approx(0.66225, rel=REL),
        pytest.approx(0.52980, rel=REL),
    ]
    assert (where.rest, where.rest_count) == (0.0, 0)
    assert where.score == pytest.approx(1.19205, rel=REL)
    assert where.score == pytest.approx(result.forward_score, rel=REL)
    assert (where.process_id, where.name, where.amount, where.unit) == (
        "tshirt:tshirt",
        "T-shirt",
        1.0,
        "unit",
    )


def test_the_t_shirt_by_location(calc: CalcToy, toy: Path, load_toy: LoadToy, edit: Edit) -> None:
    edit(toy / "background" / "processes" / "electricity-production.yaml", "GLO", "RER")
    result = calc("tshirt", load_toy(toy), root=toy)

    where = result.where()

    assert [(p.location, p.processes) for p in where.locations] == [("RER", 1), ("GLO", 1)]
    assert [p.score for p in where.locations] == [
        pytest.approx(0.66225, rel=REL),
        pytest.approx(0.52980, rel=REL),
    ]
    assert (where.locations_rest, where.locations_rest_count) == (0.0, 0)
    assert sum(p.score for p in where.locations) == pytest.approx(1.19205, rel=REL)


def test_one_location_sums_every_process_there(calc: CalcToy, either: hw.Background) -> None:
    """On the toy's folder and on its release, whose locations come from the processes table."""
    where = calc("tshirt", either).where()
    (glo,) = where.locations
    assert rows(where) == [
        (ELECTRICITY, pytest.approx(1.25, rel=REL), "kWh", "GLO"),
        (FABRIC, pytest.approx(0.25, rel=REL), "kg", "GLO"),
    ]

    assert (glo.location, glo.processes) == ("GLO", 2)
    assert glo.score == pytest.approx(1.19205, rel=REL)


def test_top_sums_the_rest(calc: CalcToy, toy: Path, load_toy: LoadToy, edit: Edit) -> None:
    edit(toy / "background" / "processes" / "electricity-production.yaml", "GLO", "RER")
    where = calc("tshirt", load_toy(toy), root=toy).where(top=1)

    assert rows(where) == [(ELECTRICITY, pytest.approx(1.25, rel=REL), "kWh", "RER")]
    assert where.rest == pytest.approx(0.52980, rel=REL)
    assert where.rest_count == 1
    assert [p.location for p in where.locations] == ["RER"]
    assert where.locations_rest == pytest.approx(0.52980, rel=REL)
    assert where.locations_rest_count == 1
    assert where.score == pytest.approx(1.19205, rel=REL)


def test_top_zero_and_none(calc: CalcToy) -> None:
    result = calc("tshirt")

    nothing = result.where(top=0)
    everything = result.where(top=None)

    assert (nothing.processes, nothing.locations) == ((), ())
    assert nothing.rest == pytest.approx(1.19205, rel=REL)
    assert (nothing.rest_count, nothing.locations_rest_count) == (2, 1)
    assert len(everything.processes) == 2
    with pytest.raises(ValueError, match="top"):
        result.where(top=-1)


def test_the_t_shirt_as_text(calc: CalcToy, toy: Path, load_toy: LoadToy, edit: Edit) -> None:
    assert str(calc("tshirt").where()) == (
        "1 unit of T-shirt: 1.19205\n"
        "By process:\n"
        "  1.25 kWh of electricity production (GLO): 0.66225 (55.6%)\n"
        "  0.25 kg of cotton fabric production (GLO): 0.52980 (44.4%)\n"
        "By location:\n"
        "  GLO: 1.19205 (100.0%), 2 processes"
    )
    edit(toy / "background" / "processes" / "electricity-production.yaml", "GLO", "RER")
    assert str(calc("tshirt", load_toy(toy), root=toy).where(top=1)) == (
        "1 unit of T-shirt: 1.19205\n"
        "By process:\n"
        "  1.25 kWh of electricity production (RER): 0.66225 (55.6%)\n"
        "  rest: 0.52980 (44.4%), 1 process\n"
        "By location:\n"
        "  RER: 0.66225 (55.6%), 1 process\n"
        "  rest: 0.52980 (44.4%), 1 location"
    )


def test_a_process_that_makes_two_units_per_run(
    calc: CalcToy, toy: Path, load_toy: LoadToy
) -> None:
    """The amount is what the process makes, not how many times it runs: a run that makes 2
    T-shirts and emits 1 kg CO2 runs half a time for one T-shirt, which still makes 1 T-shirt."""
    (toy / "tshirt" / "tshirt" / "processes" / "tshirt.csv").write_text(
        "line,role,flow,amount,unit,supplier\n"
        "e01,reference,tshirt:tshirt,2,unit,\n"
        "e02,input,toy:cotton-fabric,0.5,kg,toy:fabric-production\n"
        "e03,input,toy:electricity,1,kWh,toy:electricity-production\n"
        "e04,emission,toy:co2,1,kg,\n"
    )

    where = calc("tshirt", load_toy(toy), root=toy).where()

    tshirt = next(e for e in where.processes if e.process_id == "tshirt:tshirt")
    assert (tshirt.amount, tshirt.unit) == (pytest.approx(1.0, rel=REL), "unit")
    assert tshirt.score == pytest.approx(0.5, rel=REL)
    assert where.score == pytest.approx(1.69205, rel=REL)
    assert "  1 unit of T-shirt (GLO): 0.50000 (29.5%)" in str(where)


def test_a_local_copy_has_its_own_row(calc: CalcToy) -> None:
    result = calc("tshirt-local-electricity")

    where = result.where()

    # The fabric buys the grid's 0.75 kWh at 0.5298, the T-shirt its own 0.5 kWh at 0.3298.
    assert [(e.copy_of, e.amount, e.score) for e in where.processes] == [
        (None, pytest.approx(0.25, rel=REL), pytest.approx(0.52980, rel=REL)),
        (None, pytest.approx(0.75, rel=REL), pytest.approx(0.39735, rel=REL)),
        (ELECTRICITY, pytest.approx(0.5, rel=REL), pytest.approx(0.16490, rel=REL)),
    ]
    assert where.processes[2].process_id == result.contributions().children[1].process_id
    assert where.score == pytest.approx(1.09205, rel=REL)
    assert "0.5 kWh of electricity production (local copy, GLO): 0.16490 (15.1%)" in str(where)


def test_a_waste_sent_to_treatment(calc: CalcToy) -> None:
    where = calc("tshirt-waste").where()

    incineration = next(e for e in where.processes if e.process_id == INCINERATION)
    # 0.05 kg of scraps treated, 1.2 kg CO2 per kg treated
    assert (incineration.amount, incineration.unit) == (pytest.approx(0.05, rel=REL), "kg")
    assert incineration.score == pytest.approx(0.06, rel=REL)
    assert where.score == pytest.approx(1.25205, rel=REL)


def test_a_scenario_changes_where(calc: CalcToy) -> None:
    where = calc("tshirt-grid-scenario", scenario="clean-grid").where()

    # The grid at 0.3 kg CO2 per kWh: 1.25 kWh x (0.3 + 0.001 x 29.8) = 0.41225
    assert [(e.process_id, e.amount, e.score) for e in where.processes] == [
        (FABRIC, pytest.approx(0.25, rel=REL), pytest.approx(0.52980, rel=REL)),
        (ELECTRICITY, pytest.approx(1.25, rel=REL), pytest.approx(0.41225, rel=REL)),
    ]
    assert where.score == pytest.approx(0.94205, rel=REL)
    assert "  1.25 kWh of electricity production (GLO): 0.41225 (43.8%)" in str(where)


def test_a_scenario_that_changes_a_purchase(calc: CalcToy, toy: Path, load_toy: LoadToy) -> None:
    """A change to a line of A: fabric takes 2.5 kWh per kg, so electricity runs 0.5 + 0.625."""
    (
        toy / "tshirt-grid-scenario" / "tshirt-footprint" / "scenarios" / "clean-grid.yaml"
    ).write_text(
        "description: Fabric takes 2.5 kWh per kg.\n"
        "changes:\n"
        "  - process: toy:fabric-production\n"
        "    line: e02\n"
        "    amount: 2.5\n"
    )

    where = calc("tshirt-grid-scenario", load_toy(toy), root=toy, scenario="clean-grid").where()

    assert [(e.process_id, e.amount, e.score) for e in where.processes] == [
        (ELECTRICITY, pytest.approx(1.125, rel=REL), pytest.approx(1.125 * 0.5298, rel=REL)),
        (FABRIC, pytest.approx(0.25, rel=REL), pytest.approx(0.52980, rel=REL)),
    ]
    assert where.score == pytest.approx(1.125 * 0.5298 + 0.5298, rel=REL)


def test_a_score_of_zero(calc: CalcToy, toy: Path, load_toy: LoadToy) -> None:
    path = toy / "tshirt" / "tshirt" / "processes" / "tshirt.csv"
    path.write_text("line,role,flow,amount,unit,supplier\ne01,reference,tshirt:tshirt,1,unit,\n")

    where = calc("tshirt", load_toy(toy), root=toy).where()

    assert (where.score, where.processes, where.locations) == (0.0, (), ())
    assert str(where) == ("1 unit of T-shirt: 0.00000\nBy process:\n  none\nBy location:\n  none")


@pytest.fixture(params=["folder", "release"])
def bg(
    request: pytest.FixtureRequest, background: hw.Background, release_background: hw.Background
) -> hw.Background:
    return background if request.param == "folder" else release_background


def test_a_background_product(bg: hw.Background) -> None:
    where = bg.where(FABRIC)

    # 1 kg of fabric: its own 2.1192, and 3 kWh of electricity x 0.5298 = 1.5894
    assert rows(where) == [
        (FABRIC, pytest.approx(1.0, rel=REL), "kg", "GLO"),
        (ELECTRICITY, pytest.approx(3.0, rel=REL), "kWh", "GLO"),
    ]
    assert [e.score for e in where.processes] == [
        pytest.approx(2.1192, rel=REL),
        pytest.approx(1.5894, rel=REL),
    ]
    assert where.score == pytest.approx(3.7086, rel=REL)
    assert str(where).startswith("1 kg of cotton fabric production: 3.70860\n")


def test_a_background_treatment_is_per_kg_treated(bg: hw.Background) -> None:
    where = bg.where(INCINERATION)

    assert rows(where) == [(INCINERATION, pytest.approx(1.0, rel=REL), "kg", "GLO")]
    assert where.score == pytest.approx(1.2, rel=REL)
    assert (where.amount, where.unit) == (1.0, "kg")


def test_a_background_product_top(bg: hw.Background) -> None:
    assert len(bg.where(FABRIC, top=None).processes) == 2
    (fabric,) = bg.where(FABRIC, top=1).processes
    assert fabric.process_id == FABRIC
    with pytest.raises(ValueError, match="top"):
        bg.where(FABRIC, top=-1)


def test_a_background_product_by_indicator(bg: hw.Background) -> None:
    assert bg.where(FABRIC, indicator="climate-change").score == pytest.approx(3.7086, rel=REL)
    with pytest.raises(ValueError, match="no indicator"):
        bg.where(FABRIC, indicator="water")


def test_an_unknown_product_names_the_closest(bg: hw.Background) -> None:
    with pytest.raises(ValueError, match="toy:fabric-production"):
        bg.where("toy:fabric-productio")


def test_a_background_product_with_nothing_upstream(toy: Path, load_toy: LoadToy) -> None:
    path = toy / "background" / "processes" / "incineration-textile-waste.csv"
    path.write_text("line,role,flow,amount,unit,supplier\ne01,reference,toy:textile-waste,1,kg,\n")

    where = load_toy(toy).where(INCINERATION)

    assert (where.score, where.processes, where.rest_count) == (0.0, (), 0)
