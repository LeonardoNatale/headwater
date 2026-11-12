"""Brightway's unit names, and the units of headwater's table."""

import pytest

from headwater.io.excel.units import resolve_unit
from headwater.model import DEFAULT_UNITS, Unit, Units


@pytest.mark.parametrize(
    ("written", "unit"),
    [
        ("kilogram", "kg"),
        ("kg", "kg"),
        ("Kilogram", "kg"),
        ("  kilogram ", "kg"),
        ("gram", "g"),
        ("ton", "t"),
        ("kilowatt hour", "kWh"),
        ("kWh", "kWh"),
        ("kwh", "kWh"),
        ("megajoule", "MJ"),
        ("cubic meter", "m3"),
        ("m3", "m3"),
        ("square meter", "m2"),
        ("meter", "m"),
        ("kilometer", "km"),
        ("hectare", "ha"),
        ("unit", "unit"),
        ("ton kilometer", "tkm"),
        ("litre", "L"),
    ],
)
def test_a_brightway_unit_is_the_unit_of_the_table_that_it_means(written: str, unit: str) -> None:
    assert resolve_unit(written, DEFAULT_UNITS) == unit


@pytest.mark.parametrize(
    "written", ["furlong", "", "kilo", "metric ton*kilometre", "person kilometer"]
)
def test_a_unit_the_table_does_not_have_is_none(written: str) -> None:
    assert resolve_unit(written, DEFAULT_UNITS) is None


def test_a_name_the_table_already_has_is_kept_even_when_brightway_would_rename_it() -> None:
    units = Units(
        [
            *DEFAULT_UNITS,
            Unit(name="metric ton*km", quantity="freight-transport-work", factor_to_reference=1),
        ]
    )
    assert resolve_unit("metric ton*km", units) == "metric ton*km"
    assert resolve_unit("ton kilometer", units) == "tkm"  # the first the table has


def test_the_unit_a_release_uses_is_chosen_when_the_default_table_lacks_it() -> None:
    ecoinvent = Units(
        [
            Unit(name="metric ton*km", quantity="freight-transport-work", factor_to_reference=1),
            Unit(name="l", quantity="volume", factor_to_reference=0.001),
            Unit(name="kg", quantity="mass", factor_to_reference=1),
        ]
    )
    assert resolve_unit("ton kilometer", ecoinvent) == "metric ton*km"
    assert resolve_unit("litre", ecoinvent) == "l"
