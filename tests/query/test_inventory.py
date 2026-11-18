"""The inventory: the total of each elementary flow over a product's whole life cycle, B x s,
with no method. Times a method's factors it is the product's score, for every toy product.

By hand, the T-shirt runs 1.25 kWh of electricity (0.5 its own, 0.75 for its 0.25 kg of fabric)
and 0.25 kg of fabric production: CO2 1.25 x 0.5 + 0.25 x 2 = 1.125 kg, CH4 1.25 x 0.001 +
0.25 x 0.004 = 0.00225 kg, and 1.125 + 29.8 x 0.00225 = 1.19205.
"""

import functools
import math
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

import headwater as hw
from headwater.study import read_method
from tests.conftest import open_database, open_study_folder, toy_cases, toy_database

from .conftest import TOY_DIR, pinned, pinned_study

REL = 1e-9  # AGENTS.md: toy fixtures
SAME = 1e-12  # the inventory times the factors against the score: both are exact on the toy

FABRIC = "toy:fabric-production"
ELECTRICITY = "toy:electricity-production"
INCINERATION = "toy:incineration-textile-waste"
METHODS = ("toy-climate", "toy-impacts")


def amounts(totals: Sequence[hw.FlowTotal]) -> dict[str, float]:
    return {t.flow_id: t.amount for t in totals}


def assert_amounts(totals: Sequence[hw.FlowTotal], expected: Mapping[str, float]) -> None:
    assert amounts(totals) == pytest.approx(expected, rel=REL)


def characterised(totals: Sequence[hw.FlowTotal], factors: Mapping[str, float]) -> float:
    """The inventory times the factors of one indicator: a flow with no factor counts 0."""
    return math.fsum(t.amount * factors.get(t.flow_id, 0.0) for t in totals)


def factors_of(method: str) -> Mapping[str, Mapping[str, float]]:
    """The factors of a toy method, by indicator, then by flow id."""
    return read_method(TOY_DIR / "methods" / method).factors_by_indicator


@functools.cache
def load(method: str) -> hw.Background:
    """The toy background with a toy method, loaded once per method. Read only."""
    return hw.load(TOY_DIR / "background", method=TOY_DIR / "methods" / method, namespace="toy")


def test_the_tshirt_by_hand(background: hw.Background) -> None:
    result = open_database(TOY_DIR / "tshirt" / "tshirt").calc(background, "tshirt:tshirt")

    co2, ch4 = result.inventory()

    assert (co2.flow_id, co2.name, co2.compartment, co2.unit) == (
        "toy:co2",
        "Carbon dioxide, fossil",
        "air",
        "kg",
    )
    assert (ch4.flow_id, ch4.name, ch4.compartment, ch4.unit) == (
        "toy:ch4",
        "Methane, fossil",
        "air",
        "kg",
    )
    assert co2.amount == pytest.approx(1.125, rel=REL)
    assert ch4.amount == pytest.approx(0.00225, rel=REL)
    assert co2.amount + 29.8 * ch4.amount == pytest.approx(1.19205, rel=REL)


def test_a_flow_total_prints_on_one_line(background: hw.Background) -> None:
    co2 = background.inventory(FABRIC)[0]

    assert repr(co2) == "FlowTotal(toy:co2 Carbon dioxide, fossil (air): 3.5 kg)"


def test_background_products_by_hand(either: hw.Background) -> None:
    """1 kg of fabric: 2 kg of CO2 of its own and 3 kWh x 0.5. A treatment is per kg treated,
    a positive burden, and lists only what runs: incineration emits no methane."""
    assert_amounts(either.inventory(FABRIC), {"toy:co2": 3.5, "toy:ch4": 0.007})
    assert_amounts(either.inventory(ELECTRICITY), {"toy:co2": 0.5, "toy:ch4": 0.001})
    assert_amounts(either.inventory(INCINERATION), {"toy:co2": 1.2})


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        ("tshirt", {"toy:co2": 1.125, "toy:ch4": 0.00225}),
        # 0.05 kg of scraps incinerated, 1.2 kg of CO2 per kg: 1.25205
        ("tshirt-waste", {"toy:co2": 1.185, "toy:ch4": 0.00225}),
        # its own 0.5 kWh at 0.3 kg of CO2 instead of 0.5, the fabric's 0.75 kWh as it is: 1.09205
        ("tshirt-local-electricity", {"toy:co2": 1.025, "toy:ch4": 0.00225}),
        # a flow with no factor is listed too
        ("tshirt-no-factor", {"toy:co2": 1.125, "toy:ch4": 0.00225, "tshirt:cod": 0.002}),
    ],
)
def test_toy_databases_by_hand(
    background: hw.Background, case: str, expected: dict[str, float]
) -> None:
    result = open_database(toy_database(TOY_DIR, case)).calc(background, "tshirt:tshirt")

    assert_amounts(result.inventory(), expected)


def test_a_flow_of_the_database_takes_its_name_from_the_database(
    background: hw.Background,
) -> None:
    result = open_database(TOY_DIR / "tshirt-no-factor" / "tshirt").calc(
        background, "tshirt:tshirt"
    )

    [cod] = [t for t in result.inventory() if t.flow_id == "tshirt:cod"]

    assert (cod.name, cod.compartment, cod.unit) == ("COD, Chemical Oxygen Demand", "water", "kg")
    assert [n.flow_id for n in result.not_characterised] == ["tshirt:cod"]


def test_a_study_by_hand_in_each_scenario(background: hw.Background) -> None:
    """The dyed T-shirt buys 0.01 kg of dye, 4 kg of CO2 and 2 kWh per kg: 1.242646. The
    scenario cleaner-dye sets the dye's CO2 to 3.5: 1.237646."""
    study = open_study_folder(TOY_DIR / "tshirt-dyed" / "tshirt-footprint")

    [plain] = study.calc(background)
    [cleaner] = study.calc(background, scenario="cleaner-dye")

    assert_amounts(plain.inventory(), {"toy:co2": 1.175, "toy:ch4": 0.00227})
    assert_amounts(cleaner.inventory(), {"toy:co2": 1.17, "toy:ch4": 0.00227})
    assert plain.score == pytest.approx(1.242646, rel=REL)
    assert cleaner.score == pytest.approx(1.237646, rel=REL)


def test_a_study_gives_each_product_times_its_amount(background: hw.Background) -> None:
    """2 dyed T-shirts and 0.5 kg of dye, which runs 1 kWh."""
    study = open_study_folder(TOY_DIR / "tshirt-dyed" / "tshirt-and-dye")

    tshirts, dye = study.calc(background)

    assert (tshirts.amount, dye.amount) == (2, 0.5)
    assert_amounts(tshirts.inventory(), {"toy:co2": 2.35, "toy:ch4": 0.00454})
    assert_amounts(dye.inventory(), {"toy:co2": 2.5, "toy:ch4": 0.001})


def test_a_background_product_of_a_study_in_memory(background: hw.Background) -> None:
    study = hw.Study(products={FABRIC: 2}, methods=["toy-climate"])

    [result] = study.calc(background)

    assert_amounts(result.inventory(), {"toy:co2": 7.0, "toy:ch4": 0.014})


def test_inventory_is_sorted_by_unit_then_size(background: hw.Background) -> None:
    result = open_database(TOY_DIR / "tshirt-no-factor" / "tshirt").calc(
        background, "tshirt:tshirt"
    )

    assert [t.flow_id for t in result.inventory()] == ["toy:co2", "toy:ch4", "tshirt:cod"]


def test_inventory_names_the_closest_id(background: hw.Background) -> None:
    with pytest.raises(ValueError, match="toy:fabric-production"):
        background.inventory("toy:fabric-prodution")


def test_inventory_does_not_depend_on_the_method() -> None:
    climate, impacts = (load(m) for m in METHODS)

    assert climate.inventory(FABRIC) == impacts.inventory(FABRIC)


@pytest.mark.parametrize("method", METHODS)
@pytest.mark.parametrize("process_id", [FABRIC, ELECTRICITY, INCINERATION])
def test_times_the_factors_it_is_the_score_of_a_background_product(
    method: str, process_id: str
) -> None:
    background = load(method)
    totals = background.inventory(process_id)

    for indicator, factors in factors_of(method).items():
        expected = background.score(process_id, indicator=indicator)
        assert math.isclose(characterised(totals, factors), expected, rel_tol=SAME), indicator


@pytest.mark.parametrize("case", toy_cases(TOY_DIR))
def test_times_the_factors_it_is_the_score_of_every_toy_database(case: str) -> None:
    """On toy-climate, the method every toy database names. The background products above also
    check toy-impacts, whose three indicators share flows."""
    method = "toy-climate"
    background = load(method)
    database = open_database(toy_database(TOY_DIR, case))
    checked = 0
    for process in database.folder.processes:
        for indicator, factors in factors_of(method).items():
            result = database.calc(background, process.id, indicator=indicator)
            found = characterised(result.inventory(), factors)
            assert math.isclose(found, result.score, rel_tol=SAME), (process.id, indicator)
            checked += 1
    assert checked > 0


def studies() -> list[Path]:
    return sorted(p.parent for p in TOY_DIR.glob("*/*/study.yaml"))


@pytest.mark.parametrize("path", studies(), ids=lambda p: f"{p.parent.name}/{p.name}")
def test_times_the_factors_it_is_the_score_of_every_toy_study(path: Path) -> None:
    """Each product, its amount of it, in each scenario of the study, on toy-climate."""
    method = "toy-climate"
    background = load(method)
    study = open_study_folder(path)
    for scenario in [None, *(s.name for s in study.folder.scenarios)]:
        for indicator, factors in factors_of(method).items():
            for result in study.calc(background, indicator=indicator, scenario=scenario):
                found = characterised(result.inventory(), factors)
                assert math.isclose(found, result.score, rel_tol=SAME), (
                    result.process_id,
                    scenario,
                    indicator,
                )


def test_the_same_from_a_release(release_background: hw.Background) -> None:
    database = pinned(open_database(TOY_DIR / "tshirt-dyed" / "tshirt"), release_background)
    study = pinned_study(
        open_study_folder(TOY_DIR / "tshirt-dyed" / "tshirt-footprint"), release_background
    )

    result = database.calc(release_background, "tshirt:tshirt")
    [studied] = study.calc(release_background)

    assert_amounts(result.inventory(), {"toy:co2": 1.175, "toy:ch4": 0.00227})
    assert studied.inventory() == result.inventory()
