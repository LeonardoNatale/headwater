"""Finding a factor's flow: a mapping row, the same id, or the same name and compartment."""

from collections.abc import Callable
from pathlib import Path

import pytest

from headwater.link import FlowKey, map_flows, read_flow_mapping
from headwater.model import Flow, FlowKind, InvalidDataError

URBAN = "air/urban air close to ground"


def _flow(id: str, name: str, compartment: str) -> Flow:
    return Flow(id=id, name=name, kind=FlowKind.elementary, unit="kg", compartment=compartment)


FLOWS = (
    _flow("e:co2", "Carbon dioxide, fossil", URBAN),
    _flow("e:ch4", "Methane, fossil", URBAN),
)


def test_a_key_maps_by_id_or_by_exact_name_and_compartment() -> None:
    by_id, by_name = FlowKey(id="e:co2"), FlowKey(name="Methane, fossil", compartment=URBAN)

    found = map_flows([by_id, by_name], FLOWS)

    assert {k: (m.flow_id, m.how) for k, m in found.mapped.items()} == {
        by_id: ("e:co2", "id"),
        by_name: ("e:ch4", "name and compartment"),
    }
    assert found.unmapped == {}


def test_a_key_that_matches_nothing_is_unmapped_with_the_reason() -> None:
    folded = FlowKey(name="methane, fossil", compartment=URBAN)
    missing = FlowKey(id="e:n2o")

    found = map_flows([folded, missing], FLOWS)

    assert found.mapped == {}
    assert "name and compartment" in found.unmapped[folded]
    assert "this id" in found.unmapped[missing]


def test_a_mapping_row_decides_first_and_unused_rows_are_returned(
    csv_file: Callable[[str], Path],
) -> None:
    mapping = read_flow_mapping(
        csv_file(
            "source_id,source_name,source_compartment,target,who,when\n"
            "toy:co2,,,e:co2,leo,2026-10-24\n"
            ",Methane,air,e:ch4,leo,2026-10-24\n"
            "toy:n2o,,,e:co2,leo,2026-10-24\n"
        )
    )
    keys = [FlowKey(id="toy:co2"), FlowKey(name="Methane", compartment="air")]

    found = map_flows(keys, FLOWS, mapping=mapping)

    assert [(m.flow_id, m.how) for m in found.mapped.values()] == [
        ("e:co2", "mapping.csv:2"),
        ("e:ch4", "mapping.csv:3"),
    ]
    assert [r.key for r in found.unused_rows] == [FlowKey(id="toy:n2o")]


def test_a_row_whose_target_is_not_a_flow_is_an_error(csv_file: Callable[[str], Path]) -> None:
    mapping = read_flow_mapping(
        csv_file("source_id,target,who,when\ntoy:co2,e:nowhere,leo,2026-10-24\n")
    )

    with pytest.raises(InvalidDataError) as raised:
        map_flows([FlowKey(id="toy:co2")], FLOWS, mapping=mapping)

    assert [p.code for p in raised.value.problems] == ["mapping-target-missing"]


TWICE = (*FLOWS, _flow("e:co2-again", "Carbon dioxide, fossil", URBAN))
"""Two elementary flows share a name and compartment."""


def test_two_flows_with_one_name_and_compartment_leave_the_other_keys_alone() -> None:
    keys = [FlowKey(id="e:co2"), FlowKey(name="Methane, fossil", compartment=URBAN)]

    found = map_flows(keys, TWICE)

    assert [m.flow_id for m in found.mapped.values()] == ["e:co2", "e:ch4"]


def test_a_key_by_name_that_fits_two_flows_is_refused_naming_them() -> None:
    co2 = FlowKey(name="Carbon dioxide, fossil", compartment=URBAN)

    with pytest.raises(InvalidDataError) as raised:
        map_flows([co2, FlowKey(name="Methane, fossil", compartment=URBAN)], TWICE)

    [problem] = raised.value.problems
    assert problem.code == "ambiguous-flow"
    assert "e:co2, e:co2-again" in problem.message
    assert "flow mapping" in problem.message


def test_a_mapping_row_decides_a_key_that_fits_two_flows(csv_file: Callable[[str], Path]) -> None:
    mapping = read_flow_mapping(
        csv_file(
            "source_id,source_name,source_compartment,target,who,when\n"
            f'"","Carbon dioxide, fossil",{URBAN},e:co2-again,leo,2026-10-25\n'
        )
    )
    co2 = FlowKey(name="Carbon dioxide, fossil", compartment=URBAN)

    found = map_flows([co2], TWICE, mapping=mapping)

    assert (found.mapped[co2].flow_id, found.mapped[co2].how) == ("e:co2-again", "mapping.csv:2")
