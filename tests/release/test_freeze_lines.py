"""Freezing refuses a bad line, so a release with one never exists.

Each test changes one line of the toy background and expects `build` to name it.
"""

from dataclasses import replace
from pathlib import Path

import pytest

from headwater.model import (
    CutOffCriterion,
    FlowProperty,
    FlowPropertyName,
    InvalidDataError,
    Issue,
    IssueStatus,
)

from .conftest import RESOLVED_AT, Records


def _problems(error: pytest.ExceptionInfo[InvalidDataError]) -> list[tuple[str, str]]:
    return [(p.code, p.item) for p in error.value.problems]


def test_an_emission_on_a_product_flow_is_refused(records, tmp_path: Path) -> None:
    bad = records.with_line("toy:fabric-production", "e03", flow_id="toy:cotton-fabric")

    with pytest.raises(InvalidDataError) as raised:
        bad.build(tmp_path)

    assert _problems(raised) == [("role-flow-kind", "toy:fabric-production line e03")]
    message = str(raised.value)
    assert "role 'emission' needs an elementary flow" in message
    assert "'toy:cotton-fabric' is a product flow" in message


def test_an_input_on_an_elementary_flow_is_refused(records, tmp_path: Path) -> None:
    # The line keeps its supplier, which makes electricity: so it also buys the wrong flow.
    bad = records.with_line("toy:fabric-production", "e02", flow_id="toy:co2", unit="kg")

    with pytest.raises(InvalidDataError) as raised:
        bad.build(tmp_path)

    assert _problems(raised) == [
        ("role-flow-kind", "toy:fabric-production line e02"),
        ("supplier-wrong-flow", "toy:fabric-production line e02"),
        ("issue-mismatch", "issue toy:fabric-production line e02"),
    ]


def test_a_supplier_that_makes_another_flow_is_refused(records, tmp_path: Path) -> None:
    # Fabric production buys electricity from the incineration process, which treats waste.
    bad = records.with_line(
        "toy:fabric-production", "e02", supplier_id="toy:incineration-textile-waste"
    )

    with pytest.raises(InvalidDataError) as raised:
        bad.build(tmp_path)

    assert _problems(raised) == [
        ("supplier-wrong-flow", "toy:fabric-production line e02"),
        ("issue-mismatch", "issue toy:fabric-production line e02"),
    ]
    assert "makes 'toy:textile-waste', not 'toy:electricity'" in str(raised.value)


def test_own_use_must_name_the_process_itself(records, tmp_path: Path) -> None:
    bad = records.with_new_line(
        "toy:fabric-production",
        "e06",
        role="own_use",
        flow_id="toy:cotton-fabric",
        amount=0.02,
        unit="kg",
        supplier_id="toy:electricity-production",
    )

    with pytest.raises(InvalidDataError) as raised:
        bad.build(tmp_path)

    assert _problems(raised) == [("own-use-supplier", "toy:fabric-production line e06")]
    assert "its supplier is 'toy:fabric-production'" in str(raised.value)


def test_own_use_of_the_process_itself_is_frozen(records, tmp_path: Path) -> None:
    good = records.with_new_line(
        "toy:fabric-production",
        "e06",
        role="own_use",
        flow_id="toy:cotton-fabric",
        amount=0.02,
        unit="kg",
        supplier_id="toy:fabric-production",
    )

    assert good.build(tmp_path).table("exchanges").num_rows == 11


def with_resolved_own_use(records: Records, process_id: str, status: IssueStatus) -> Records:
    """The records with one own use of fabric in a process, resolved as cut off or a data gap, so
    that it buys from nothing and no check of the supplier applies to it."""
    flow_id = "toy:cotton-fabric"
    resolution = (
        {"criterion": CutOffCriterion.mass, "share": 0.001, "cumulative_share": 0.001}
        if status is IssueStatus.cut_off
        else {}
    )
    issue = Issue(
        process_id=process_id,
        line_id="e09",
        flow_id=flow_id,
        status=status,
        reason="a test",
        resolved_by="test",
        resolved_at=RESOLVED_AT,
        **resolution,
    )
    with_line = records.with_new_line(
        process_id,
        "e09",
        role="own_use",
        flow_id=flow_id,
        amount=0.1,
        unit="kg",
        link_status=status.value,
    )
    return replace(with_line, issues=(*records.issues, issue))


@pytest.mark.parametrize("status", [IssueStatus.cut_off, IssueStatus.data_gap])
def test_an_own_use_in_a_treatment_is_refused_as_it_has_no_sign(
    records, tmp_path: Path, status: IssueStatus
) -> None:
    # The incineration process treats textile waste. Its own use of fabric, with no supplier,
    # passes every check of the line, but a treatment has no sign for it: hw.load refuses it.
    bad = with_resolved_own_use(records, "toy:incineration-textile-waste", status)

    with pytest.raises(InvalidDataError) as raised:
        bad.build(tmp_path)

    assert _problems(raised) == [("role-no-sign", "toy:incineration-textile-waste line e09")]
    assert "role 'own_use' has no sign in a process that treats waste" in str(raised.value)
    assert not any(tmp_path.iterdir())


def test_an_own_use_with_no_supplier_in_another_process_is_frozen(records, tmp_path: Path) -> None:
    good = with_resolved_own_use(records, "toy:fabric-production", IssueStatus.cut_off)

    assert good.build(tmp_path).table("exchanges").num_rows == 11


def test_a_supplier_on_a_line_that_buys_nothing_is_refused(records, tmp_path: Path) -> None:
    bad = records.with_line(
        "toy:fabric-production", "e03", supplier_id="toy:electricity-production"
    )

    with pytest.raises(InvalidDataError) as raised:
        bad.build(tmp_path)

    assert _problems(raised) == [("supplier-not-allowed", "toy:fabric-production line e03")]


def test_a_unit_of_another_quantity_with_no_property_is_refused(records, tmp_path: Path) -> None:
    # Electricity counted in kg: mass against energy, and the flow has no property for it.
    bad = records.with_line("toy:fabric-production", "e02", unit="kg", amount=3.0)

    with pytest.raises(InvalidDataError) as raised:
        bad.build(tmp_path)

    assert _problems(raised) == [("unit-quantity-mismatch", "toy:fabric-production line e02")]


def test_a_unit_of_another_quantity_that_converts_through_a_property_is_frozen(
    records, tmp_path: Path
) -> None:
    density = FlowProperty(
        flow_id="toy:cotton-fabric", property=FlowPropertyName.density, value=0.005, unit="m3"
    )
    good = records.with_properties(density).with_new_line(
        "toy:fabric-production",
        "e06",
        role="own_use",
        flow_id="toy:cotton-fabric",
        amount=0.0005,
        unit="m3",
        supplier_id="toy:fabric-production",
    )

    release = good.build(tmp_path)

    assert sorted(u.name for u in release.units()) == ["kWh", "kg", "m3"]


def test_two_properties_that_convert_the_same_way_are_refused(records, tmp_path: Path) -> None:
    density = FlowProperty(
        flow_id="toy:electricity", property=FlowPropertyName.density, value=0.1, unit="m3"
    )
    bad = (
        records.with_properties(density)
        .with_properties(
            FlowProperty(
                flow_id="toy:electricity",
                property=FlowPropertyName.mass_per_item,
                value=1.0,
                unit="m3",
            )
        )
        .with_line("toy:fabric-production", "e02", unit="m3", amount=0.3)
    )

    with pytest.raises(InvalidDataError) as raised:
        bad.build(tmp_path)

    assert _problems(raised) == [("ambiguous-conversion", "toy:fabric-production line e02")]


def test_every_problem_is_listed_line_by_line(records, tmp_path: Path) -> None:
    bad = (
        records.with_line("toy:fabric-production", "e03", flow_id="toy:cotton-fabric")
        .with_line("toy:electricity-production", "e02", unit="kWh")
        .with_line("toy:fabric-production", "e02", supplier_id="toy:incineration-textile-waste")
    )

    with pytest.raises(InvalidDataError) as raised:
        bad.build(tmp_path)

    assert _problems(raised) == [
        ("unit-quantity-mismatch", "toy:electricity-production line e02"),
        ("supplier-wrong-flow", "toy:fabric-production line e02"),
        ("role-flow-kind", "toy:fabric-production line e03"),
        ("issue-mismatch", "issue toy:fabric-production line e02"),
    ]
    assert not tmp_path.joinpath("toy-background").exists()
