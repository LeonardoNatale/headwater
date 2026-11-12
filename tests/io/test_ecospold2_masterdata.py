"""Flows, flow properties and units from MasterData."""

from collections.abc import Callable
from pathlib import Path

import pytest

from headwater.io.ecospold2 import read_ecospold2
from headwater.io.exclude import read_exclude
from headwater.io.inventory import Inventory
from headwater.model import (
    DEFAULT_UNITS,
    Catalog,
    Exchange,
    Flow,
    FlowKind,
    FlowProperty,
    FlowPropertyName,
    InvalidDataError,
    Role,
)


def test_every_masterdata_flow_is_a_flow_even_when_no_dataset_uses_it(
    source: Inventory, fx
) -> None:
    product, waste, elementary = FlowKind.product, FlowKind.waste, FlowKind.elementary
    expected = [
        Flow(id=fx.product(1), name="widget", kind=product, unit="kg"),
        Flow(id=fx.product(2), name="electricity", kind=product, unit="kWh"),
        Flow(id=fx.product(3), name="natural gas", kind=product, unit="m3", cas="008006-14-2"),
        Flow(id=fx.product(4), name="waste widget", kind=waste, unit="kg"),
        Flow(id=fx.product(5), name="scrap metal", kind=product, unit="kg"),
        Flow(id=fx.product(6), name="spare part", kind=product, unit="unit"),
        Flow(
            id=fx.elementary(1),
            name="Carbon dioxide, fossil",
            kind=elementary,
            unit="kg",
            compartment="air/urban air close to ground",
            cas="000124-38-9",
        ),
        Flow(
            id=fx.elementary(2),
            name="Cadmium",
            kind=elementary,
            unit="kg",
            compartment="soil/agricultural",
            cas="007440-43-9",
        ),
        Flow(
            id=fx.elementary(3),
            name="Carbon dioxide, in air",
            kind=elementary,
            unit="kg",
            compartment="natural resource/in air",
            cas="000124-38-9",
        ),
        Flow(
            id=fx.elementary(4),
            name="Methane, fossil",
            kind=elementary,
            unit="kg",
            compartment="air/urban air close to ground",
            cas="000074-82-8",
        ),
    ]
    assert sorted(source.flows, key=lambda f: f.id) == sorted(expected, key=lambda f: f.id)


def test_flow_properties_hold_one_reference_unit_of_the_flow(source: Inventory, fx) -> None:
    """Zero values are not flow properties (they stay on the lines that carry them). Wet mass
    becomes density for a flow counted in volume and mass per item for one counted in items; for
    a flow counted in kg it says nothing."""
    p = FlowPropertyName
    expected = {
        (fx.product(1), p.price, 2.5, "EUR2005"),
        (fx.product(1), p.dry_mass, 0.9, "kg"),
        (fx.product(1), p.carbon_content_fossil, 0.4, "dimensionless"),
        (fx.product(1), p.water_content, 0.11, "dimensionless"),
        (fx.product(2), p.price, 0.1, "EUR2005"),
        (fx.product(3), p.heating_value_net, 36.0, "MJ"),
        (fx.product(3), p.heating_value_gross, 40.0, "MJ"),
        (fx.product(3), p.density, 0.8, "kg"),
        (fx.product(3), p.price, 0.3, "EUR2005"),
        (fx.product(6), p.mass_per_item, 2.0, "kg"),
    }
    found = {(q.flow_id, q.property, q.unit): q.value for q in source.flow_properties}
    assert found == pytest.approx({(f, q, u): v for f, q, v, u in expected}, rel=1e-9)


def test_flow_properties_convert_across_quantities(source: Inventory, fx) -> None:
    catalog = Catalog(source.flows, source.processes, source.flow_properties, source.units)

    def amount(flow: str, value: float, unit: str) -> float:
        line = Exchange(
            process_id="study:p",
            line_id="e01",
            role=Role.input,
            flow_id=flow,
            amount=value,
            unit=unit,
        )
        return catalog.convert(line)

    assert amount(fx.product(3), 72.0, "MJ") == pytest.approx(2.0, rel=1e-12)
    assert amount(fx.product(3), 0.4, "kg") == pytest.approx(0.5, rel=1e-12)
    assert amount(fx.product(6), 6.0, "kg") == pytest.approx(3.0, rel=1e-12)


def test_units_agree_with_the_built_in_table(source: Inventory) -> None:
    for unit in source.units:
        if unit.name in DEFAULT_UNITS:
            assert unit == DEFAULT_UNITS.get(unit.name)
    merged = DEFAULT_UNITS.merge(source.units)

    assert merged.convert(1.0, "kWh", "MJ") == pytest.approx(3.6, rel=1e-12)


def test_units_without_a_built_in_match_come_from_the_conversions(source: Inventory) -> None:
    expected = {
        "EUR2005": ("currency", 1.0),
        "USD2005": ("currency", 0.8),
        "l": ("volume", 0.001),
        "metric ton": ("mass", 1000.0),
        "dimensionless": ("dimensionless", 1.0),
    }
    for name, (quantity, factor) in expected.items():
        unit = source.units.get(name)
        assert unit is not None, name
        assert unit.quantity == quantity, name
        assert unit.factor_to_reference == pytest.approx(factor, rel=1e-9), name


UNIT_CONVERSIONS = "MasterData/UnitConversions.xml"
INTERMEDIATE = "MasterData/IntermediateExchanges.xml"


def _read(folder: Path) -> InvalidDataError:
    with pytest.raises(InvalidDataError) as raised:
        read_ecospold2(
            folder, version="3.10", exclude=read_exclude(folder / "exclude.csv"), workers=1
        )
    return raised.value


def test_a_unit_defined_two_ways_is_an_error(source_copy: Callable[..., Path]) -> None:
    folder = source_copy(edit={UNIT_CONVERSIONS: [('factor="3.6"', 'factor="3.5"')]})

    error = _read(folder)

    assert [(p.code, p.item) for p in error.problems] == [("unit-conflict", "unit 'kWh'")]
    assert "3.5" in str(error) and "3.6" in str(error)


def test_two_paths_with_different_factors_are_an_error(source_copy: Callable[..., Path]) -> None:
    conflict = """  <unitConversion id="a3000000-0000-4000-8000-000000000099" factor="0.7">
    <unitFromName xml:lang="en">USD2005</unitFromName>
    <unitToName xml:lang="en">CHF2005</unitToName>
    <unitType xml:lang="en">currency</unitType>
  </unitConversion>
</validUnitConversions>"""
    folder = source_copy(edit={UNIT_CONVERSIONS: [("</validUnitConversions>", conflict)]})

    error = _read(folder)

    assert [p.code for p in error.problems] == ["unit-conflict"]
    assert "USD2005" in str(error) and "CHF2005" in str(error)


def test_a_flow_missing_from_masterdata_fails_naming_the_line(
    source_copy: Callable[..., Path], fx
) -> None:
    folder = source_copy(
        edit={
            INTERMEDIATE: [
                (
                    '<intermediateExchange id="b0000000-0000-4000-8000-000000000004"',
                    '<intermediateExchange id="b0000000-0000-4000-8000-000000000044"',
                ),
            ]
        }
    )

    error = _read(folder)

    assert [(p.code, p.item) for p in error.problems] == [
        ("unknown-flow", f"{fx.pid(1, 1)} line {fx.line(1, 4)}"),
        ("unknown-flow", f"{fx.pid(4, 4)} line {fx.line(4, 1)}"),
    ]


def test_a_line_whose_class_disagrees_with_masterdata_fails(
    source_copy: Callable[..., Path], fx
) -> None:
    """MasterData says scrap metal is a waste; the lines say it is recyclable. A process is a
    treatment when its reference flow is a waste, so a disagreement would flip a sign."""
    folder = source_copy(
        edit={
            INTERMEDIATE: [
                (
                    '<classificationValue xml:lang="en">Recyclable</classificationValue>',
                    '<classificationValue xml:lang="en">Waste</classificationValue>',
                )
            ]
        }
    )

    error = _read(folder)

    assert sorted((p.code, p.item) for p in error.problems) == sorted(
        ("class-mismatch", f"{fx.pid(a, p)} line {fx.line(a, n)}")
        for a, p, n in [(1, 1, 5), (5, 5, 1), (9, 5, 1), (9, 5, 2)]
    )
    assert "Recyclable" in str(error) and "Waste" in str(error)


def test_a_missing_masterdata_file_is_named(source_copy: Callable[..., Path]) -> None:
    folder = source_copy()
    (folder / UNIT_CONVERSIONS).unlink()

    error = _read(folder)

    assert [(p.code, p.item) for p in error.problems] == [("missing-file", UNIT_CONVERSIONS)]


def test_a_folder_without_datasets_is_named(tmp_path: Path) -> None:
    with pytest.raises(InvalidDataError) as raised:
        read_ecospold2(tmp_path, version="3.10", workers=1)

    assert {p.code for p in raised.value.problems} == {"missing-folder"}
    assert "datasets" in str(raised.value)


def test_flow_property_records_are_valid(source: Inventory) -> None:
    for prop in source.flow_properties:
        assert FlowProperty.model_validate(prop.model_dump()) == prop
