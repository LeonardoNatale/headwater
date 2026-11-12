from collections.abc import Callable
from typing import cast

import pytest
from pydantic import BaseModel

from headwater.model import (
    DEFAULT_UNITS,
    Catalog,
    Exchange,
    Flow,
    FlowKind,
    FlowProperty,
    FlowPropertyName,
    InvalidDataError,
    Process,
    ProcessKind,
    Role,
)

KG = {"kind": "product", "unit": "kg"}


def make_catalog() -> Catalog:
    flows = [
        Flow(id="toy:fabric", name="cotton fabric", kind=FlowKind.product, unit="kg"),
        Flow(id="toy:gas", name="natural gas", kind=FlowKind.product, unit="m3"),
        Flow(id="toy:gross-only", name="gross only", kind=FlowKind.product, unit="kg"),
        Flow(id="toy:shirt", name="T-shirt", kind=FlowKind.product, unit="unit"),
    ]
    properties = [
        FlowProperty(
            flow_id="toy:gas", property=FlowPropertyName.heating_value_net, value=36, unit="MJ"
        ),
        FlowProperty(
            flow_id="toy:gross-only",
            property=FlowPropertyName.heating_value_gross,
            value=18,
            unit="MJ",
        ),
        FlowProperty(
            flow_id="toy:shirt", property=FlowPropertyName.mass_per_item, value=0.15, unit="kg"
        ),
    ]
    return Catalog(flows=flows, properties=properties)


def line(flow: str, amount: float, unit: str) -> Exchange:
    return Exchange(
        process_id="p:a",
        line_id="e02",
        role=Role.input,
        flow_id=flow,
        amount=amount,
        unit=unit,
        supplier_id="p:b",
    )


def test_amount_in_the_reference_unit_is_returned_unchanged() -> None:
    assert make_catalog().convert(line("toy:fabric", 0.25, "kg")) == pytest.approx(0.25, rel=1e-9)


def test_amount_in_another_unit_of_the_same_quantity() -> None:
    assert make_catalog().convert(line("toy:fabric", 250, "g")) == pytest.approx(0.25, rel=1e-12)


def test_property_converts_across_quantities() -> None:
    # The design's example: natural gas, 1 m3 = 36 MJ (net heating value).
    assert make_catalog().convert(line("toy:gas", 72, "MJ")) == pytest.approx(2.0, rel=1e-12)


def test_property_and_unit_conversion_combine() -> None:
    # 20 kWh = 72 MJ = 2 m3 of gas.
    assert make_catalog().convert(line("toy:gas", 20, "kWh")) == pytest.approx(2.0, rel=1e-12)


def test_mass_per_item() -> None:
    # A T-shirt weighs 0.15 kg, so 0.3 kg of T-shirts is 2 items.
    assert make_catalog().convert(line("toy:shirt", 0.3, "kg")) == pytest.approx(2.0, rel=1e-12)


def test_wrong_quantity_with_no_property() -> None:
    with pytest.raises(InvalidDataError) as raised:
        make_catalog().convert(line("toy:fabric", 1, "kWh"))

    (problem,) = raised.value.problems
    assert problem.code == "unit-quantity-mismatch"
    assert problem.item == "p:a line e02"
    for part in ("toy:fabric", "'kWh'", "energy", "'kg'", "mass"):
        assert part in problem.message


def test_gross_heating_value_is_never_used_instead_of_net() -> None:
    with pytest.raises(InvalidDataError) as raised:
        make_catalog().convert(line("toy:gross-only", 1, "MJ"))

    assert raised.value.problems[0].code == "unit-quantity-mismatch"


def test_unknown_unit() -> None:
    with pytest.raises(InvalidDataError) as raised:
        make_catalog().convert(line("toy:fabric", 1, "kgg"))

    (problem,) = raised.value.problems
    assert problem.code == "unknown-unit"
    assert "'kgg'" in problem.message
    assert "kg" in problem.message  # the closest known unit is suggested


def test_unknown_flow() -> None:
    with pytest.raises(InvalidDataError) as raised:
        make_catalog().convert(line("toy:fabrik", 1, "kg"))

    (problem,) = raised.value.problems
    assert problem.code == "unknown-flow"
    assert "toy:fabric" in problem.message


def test_flow_with_a_unit_that_does_not_exist() -> None:
    catalog = Catalog(flows=[Flow(id="toy:x", name="x", kind=FlowKind.product, unit="kgg")])

    with pytest.raises(InvalidDataError) as raised:
        catalog.convert(line("toy:x", 1, "kg"))

    assert raised.value.problems[0].item == "flow toy:x"
    assert "'kgg'" in raised.value.problems[0].message


def test_duplicate_flow_ids_with_different_definitions() -> None:
    flows = [
        Flow(id="toy:x", name="x", kind=FlowKind.product, unit="kg"),
        Flow(id="toy:x", name="x", kind=FlowKind.product, unit="g"),
    ]

    with pytest.raises(InvalidDataError) as raised:
        Catalog(flows=flows)

    assert raised.value.problems[0].code == "duplicate-flow"
    assert raised.value.problems[0].item == "flow toy:x"


def test_the_same_flow_twice_is_fine() -> None:
    flow = Flow(id="toy:x", name="x", kind=FlowKind.product, unit="kg")

    assert Catalog(flows=[flow, flow]).flow("toy:x") == flow


def test_an_equal_copy_of_a_flow_is_fine() -> None:
    flow = Flow(id="toy:x", name="x", kind=FlowKind.product, unit="kg")

    assert Catalog(flows=[flow, flow.model_copy()]).flow("toy:x") == flow


def test_a_record_seen_again_is_not_compared_with_itself(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # At ecoinvent size a catalog is built from 100,000 records, and comparing each with itself
    # through pydantic's __eq__ cost most of a calculation.
    calls: list[str] = []

    def counting(kind: type[BaseModel]) -> None:
        original = cast(Callable[[object, object], bool], kind.__eq__)

        def eq(self: object, other: object) -> bool:
            calls.append(kind.__name__)
            return original(self, other)

        monkeypatch.setattr(kind, "__eq__", eq)

    for kind in (Flow, Process, FlowProperty):
        counting(kind)
    flow = Flow(id="toy:x", name="x", kind=FlowKind.product, unit="kg")
    ref = Exchange(
        process_id="p:a", line_id="e01", role=Role.reference, flow_id="toy:x", amount=1, unit="kg"
    )
    process = Process(
        id="p:a",
        name="a",
        location="GLO",
        kind=ProcessKind.ordinary_transforming_activity,
        exchanges=(ref,),
    )
    prop = FlowProperty(
        flow_id="toy:x", property=FlowPropertyName.mass_per_item, value=0.15, unit="kg"
    )

    Catalog(flows=[flow, flow], processes=[process, process], properties=[prop, prop])

    assert calls == []


def test_duplicate_properties_that_differ_are_still_refused() -> None:
    gas = Flow(id="toy:gas", name="gas", kind=FlowKind.product, unit="m3")
    a = FlowProperty(
        flow_id="toy:gas", property=FlowPropertyName.heating_value_net, value=36, unit="MJ"
    )
    b = FlowProperty(
        flow_id="toy:gas", property=FlowPropertyName.heating_value_net, value=35, unit="MJ"
    )

    with pytest.raises(InvalidDataError) as raised:
        Catalog(flows=[gas], properties=[a, b])

    assert raised.value.problems[0].code == "duplicate-property"


def test_duplicate_process_ids() -> None:
    ref = Exchange(
        process_id="p:a", line_id="e01", role=Role.reference, flow_id="toy:x", amount=1, unit="kg"
    )
    kind = ProcessKind.ordinary_transforming_activity
    a = Process(id="p:a", name="a", location="GLO", kind=kind, exchanges=(ref,))
    b = Process(id="p:a", name="b", location="GLO", kind=kind, exchanges=(ref,))

    with pytest.raises(InvalidDataError) as raised:
        Catalog(flows=[], processes=[a, b])

    assert raised.value.problems[0].code == "duplicate-process"
    assert raised.value.problems[0].item == "process p:a"


def test_two_properties_with_the_same_name_on_one_flow() -> None:
    props = [
        FlowProperty(flow_id="toy:gas", property=FlowPropertyName.density, value=0.8, unit="kg"),
        FlowProperty(flow_id="toy:gas", property=FlowPropertyName.density, value=0.9, unit="kg"),
    ]

    with pytest.raises(InvalidDataError) as raised:
        Catalog(flows=[], properties=props)

    assert raised.value.problems[0].code == "duplicate-property"
    assert "toy:gas" in raised.value.problems[0].item


def test_lookups() -> None:
    catalog = make_catalog()

    assert catalog.flow("toy:gas") is not None
    assert catalog.flow("nope") is None
    assert catalog.process("nope") is None
    assert catalog.units is DEFAULT_UNITS


def test_two_properties_that_could_both_convert_are_ambiguous() -> None:
    # Both are mass per item, so a line in kg of T-shirts does not say which one to use.
    flows = [Flow(id="toy:shirt", name="T-shirt", kind=FlowKind.product, unit="unit")]
    properties = [
        FlowProperty(
            flow_id="toy:shirt", property=FlowPropertyName.mass_per_item, value=0.15, unit="kg"
        ),
        FlowProperty(flow_id="toy:shirt", property=FlowPropertyName.density, value=0.2, unit="kg"),
    ]
    catalog = Catalog(flows=flows, properties=properties)

    with pytest.raises(InvalidDataError) as raised:
        catalog.convert(line("toy:shirt", 0.3, "kg"))

    (problem,) = raised.value.problems
    assert problem.code == "ambiguous-conversion"
    assert problem.item == "p:a line e02"
    assert "mass_per_item" in problem.message
    assert "density" in problem.message


def _process(flow_id: str) -> Process:
    reference = Exchange(
        process_id="p:a", line_id="e01", role=Role.reference, flow_id=flow_id, amount=1, unit="kg"
    )
    return Process(
        id="p:a",
        name="a",
        location="GLO",
        kind=ProcessKind.market_activity,
        exchanges=(reference,),
    )


def test_a_process_is_a_treatment_when_its_reference_flow_is_a_waste() -> None:
    catalog = Catalog(
        flows=[
            Flow(id="toy:fabric", name="fabric", kind=FlowKind.product, unit="kg"),
            Flow(id="toy:scraps", name="scraps", kind=FlowKind.waste, unit="kg"),
        ]
    )

    # The kind of the process does not matter: a market of a waste is still a treatment.
    assert catalog.is_treatment(_process("toy:scraps")) is True
    assert catalog.is_treatment(_process("toy:fabric")) is False


def test_is_treatment_needs_the_reference_flow_to_exist() -> None:
    catalog = Catalog(flows=[Flow(id="toy:scraps", name="s", kind=FlowKind.waste, unit="kg")])

    with pytest.raises(InvalidDataError) as raised:
        catalog.is_treatment(_process("toy:scrapz"))

    (problem,) = raised.value.problems
    assert problem.code == "unknown-flow"
    assert problem.item == "p:a line e01"
    assert "toy:scraps" in problem.message
