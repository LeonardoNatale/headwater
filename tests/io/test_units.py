"""The unit table of a source, shared by every format that gives conversions between units."""

import pytest

from headwater.io.units import Conversion, unit_table
from headwater.model import DEFAULT_UNITS, InvalidDataError


def test_a_group_holding_built_in_units_takes_their_quantity_and_factors() -> None:
    units = unit_table(
        [Conversion("Wh", "MJ", 0.0036, "Units of energy"), Conversion("TJ", "MJ", 1e6, "Units")],
        [],
        source="unit_groups/",
    )

    assert units.get("Wh") == DEFAULT_UNITS.get("Wh")
    tera = units.get("TJ")
    assert tera is not None
    assert (tera.quantity, tera.factor_to_reference) == ("energy", 1e6)


def test_a_group_with_no_built_in_unit_is_its_own_quantity() -> None:
    units = unit_table(
        [Conversion("kBq", "Bq", 1000.0, "Units of radioactivity")], ["dB"], source="unit_groups/"
    )

    kilo = units.get("kBq")
    assert kilo is not None
    assert (kilo.quantity, kilo.factor_to_reference) == ("Units of radioactivity", 1000.0)
    assert units.quantity_of("dB") == "dB"


def test_a_conflict_names_the_source_of_the_conversions() -> None:
    with pytest.raises(InvalidDataError) as raised:
        unit_table([Conversion("g", "kg", 0.002, "Units of mass")], [], source="unit_groups/")

    [problem] = raised.value.problems
    assert problem.code == "unit-conflict"
    assert problem.message.startswith("unit_groups/ makes 1 g")
