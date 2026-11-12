"""The supplier and flow mapping files: reviewed decisions with who and when."""

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from headwater.link import FlowKey, SupplierKey, read_flow_mapping, read_supplier_mapping
from headwater.model import InvalidDataError

SUPPLIERS = "supplier_id,name,product,location,unit,supplier,reason,who,when,comment\n"
FLOWS = "source_id,source_name,source_compartment,target,who,when,comment\n"


def test_supplier_rows_are_keyed_by_id_or_by_name(csv_file: Callable[[str], Path]) -> None:
    mapping = read_supplier_mapping(
        csv_file(
            SUPPLIERS
            + "p:gas,,,,,p:grid,,leo,2026-10-24,\n"
            + ',,"electricity, medium voltage",CH,kWh,,Not in the package,leo,'
            + "2026-10-24T09:30:00+02:00,out of scope\n"
        )
    )

    [by_id] = mapping.by_id.values()
    assert (by_id.key, by_id.supplier, by_id.decision.source) == (
        "p:gas",
        "p:grid",
        "mapping.csv:2",
    )
    [by_name] = mapping.by_name.values()
    assert by_name.key == SupplierKey(
        product="electricity, medium voltage", location="CH", unit="kWh"
    )
    assert (by_name.supplier, by_name.reason) == (None, "Not in the package")
    assert by_name.decision.when == datetime(2026, 10, 24, 7, 30, tzinfo=UTC)
    assert by_name.decision.comment == "out of scope"


def test_a_date_with_no_time_is_midnight_utc(csv_file: Callable[[str], Path]) -> None:
    mapping = read_supplier_mapping(csv_file(SUPPLIERS + "p:gas,,,,,p:grid,,leo,2026-10-24,\n"))

    assert mapping.by_id["p:gas"].decision.when == datetime(2026, 10, 24, tzinfo=UTC)


@pytest.mark.parametrize(
    ("row", "code"),
    [
        ("p:gas,,electricity,,,p:grid,,leo,2026-10-24,", "bad-key"),
        (",,,,,p:grid,,leo,2026-10-24,", "bad-key"),
        ("p:gas,,,,,p:grid,why,leo,2026-10-24,", "bad-decision"),
        ("p:gas,,,,,,,leo,2026-10-24,", "bad-decision"),
        ("p:gas,,,,,p:grid,,,2026-10-24,", "missing-field"),
        ("p:gas,,,,,p:grid,,leo,yesterday,", "bad-date"),
    ],
    ids=["both keys", "no key", "supplier and reason", "neither", "no who", "bad when"],
)
def test_a_bad_supplier_row_is_named(csv_file: Callable[[str], Path], row: str, code: str) -> None:
    with pytest.raises(InvalidDataError) as raised:
        read_supplier_mapping(csv_file(SUPPLIERS + row + "\n"))

    assert [(p.code, p.item) for p in raised.value.problems] == [
        (code, "mapping mapping.csv line 2")
    ]


def test_a_key_decided_twice_is_an_error(csv_file: Callable[[str], Path]) -> None:
    text = SUPPLIERS + "p:gas,,,,,p:grid,,leo,2026-10-24,\np:gas,,,,,p:pv,,ana,2026-10-24,\n"

    with pytest.raises(InvalidDataError) as raised:
        read_supplier_mapping(csv_file(text))

    [problem] = raised.value.problems
    assert problem.code == "duplicate-row"
    assert "mapping.csv:2" in problem.message


def test_an_unknown_or_missing_column_is_named(csv_file: Callable[[str], Path]) -> None:
    with pytest.raises(InvalidDataError) as raised:
        read_supplier_mapping(csv_file("supplier_id,supplier,reason,who,date\n"))

    [problem] = raised.value.problems
    assert problem.code == "bad-columns"
    assert "unknown column 'date'" in problem.message
    assert "missing column(s): when" in problem.message


def test_flow_rows_are_keyed_by_id_or_by_name_and_compartment(
    csv_file: Callable[[str], Path],
) -> None:
    mapping = read_flow_mapping(
        csv_file(
            FLOWS
            + "toy:co2,,,ecoinvent:flow:e1,leo,2026-10-24,\n"
            + '"","Carbon dioxide, fossil",air/high population density,ecoinvent:flow:e1,leo,'
            + "2026-10-24,SimaPro name\n"
        )
    )

    assert set(mapping.rows) == {
        FlowKey(id="toy:co2"),
        FlowKey(name="Carbon dioxide, fossil", compartment="air/high population density"),
    }
    assert {r.target for r in mapping.rows.values()} == {"ecoinvent:flow:e1"}
    assert mapping.file == "mapping.csv"


@pytest.mark.parametrize(
    ("row", "code"),
    [
        ("toy:co2,CO2,,f1,leo,2026-10-24,", "bad-key"),
        ("toy:co2,,air,f1,leo,2026-10-24,", "bad-key"),
        ("toy:co2,,,,leo,2026-10-24,", "missing-field"),
    ],
    ids=["id and name", "id with compartment", "no target"],
)
def test_a_bad_flow_row_is_named(csv_file: Callable[[str], Path], row: str, code: str) -> None:
    with pytest.raises(InvalidDataError) as raised:
        read_flow_mapping(csv_file(FLOWS + row + "\n"))

    assert [p.code for p in raised.value.problems] == [code]


def test_a_row_with_more_cells_than_columns_is_refused(csv_file: Callable[[str], Path]) -> None:
    text = (
        "source_name,source_compartment,target,who,when\n"
        "Carbon dioxide, fossil,air,e1,leo,2026-10-24\n"
    )

    with pytest.raises(InvalidDataError) as raised:
        read_flow_mapping(csv_file(text))

    [problem] = raised.value.problems
    assert (problem.code, problem.location) == ("bad-row", "mapping.csv:2")
    assert "A comma inside a value needs quotes" in problem.message


def test_a_row_with_fewer_cells_than_columns_is_refused(csv_file: Callable[[str], Path]) -> None:
    with pytest.raises(InvalidDataError) as raised:
        read_flow_mapping(csv_file(FLOWS + "toy:co2,,,e1,leo,2026-10-24\n"))

    assert [(p.code, p.location) for p in raised.value.problems] == [("bad-row", "mapping.csv:2")]


def test_a_column_given_twice_is_refused(csv_file: Callable[[str], Path]) -> None:
    with pytest.raises(InvalidDataError) as raised:
        read_flow_mapping(csv_file("source_id,target,target,who,when\n"))

    [problem] = raised.value.problems
    assert problem.code == "bad-columns"
    assert "column 'target' appears twice" in problem.message
