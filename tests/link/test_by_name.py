"""Linking by name: exactly one process must fit every field the line gives."""

from collections.abc import Callable
from pathlib import Path

import pyarrow as pa
import pytest

from headwater.link import Supplier, SupplierKey, Suppliers, link_by_name, read_supplier_mapping
from headwater.model import DEFAULT_UNITS, Flow, FlowKind, IssueStatus
from headwater.release import Release

GRID = Supplier("p:grid", "market for electricity", "electricity", "GLO", "kWh")
GRID_CH = Supplier("p:grid-ch", "market for electricity", "electricity", "CH", "kWh")
PV = Supplier("p:pv", "electricity production, photovoltaic", "electricity", "CH", "kWh")
FABRIC = Supplier("p:fabric", "cotton fabric production", "cotton fabric", "GLO", "kg")
SUPPLIERS = Suppliers([GRID, GRID_CH, PV, FABRIC])


def _buy(lx, **key: str) -> pa.Table:
    named = {f"supplier_{field}": value for field, value in key.items()}
    return lx.table(
        lx.line("p:shirt", "e01", "reference", "f:shirt"),
        lx.line("p:shirt", "e02", "input", "f:electricity", **named),
        lx.line("p:shirt", "e03", "emission", "f:co2"),
    )


@pytest.mark.parametrize(
    ("key", "supplier"),
    [
        ({"product": "electricity", "location": "GLO", "unit": "kWh"}, "p:grid"),
        ({"name": "market for electricity", "location": "CH"}, "p:grid-ch"),
        ({"name": "electricity production, photovoltaic"}, "p:pv"),
        ({"product": "cotton fabric"}, "p:fabric"),
    ],
    ids=["product, location and unit", "name and location", "name only", "product only"],
)
def test_the_one_process_that_fits_every_given_field_is_linked(
    lx, key: dict[str, str], supplier: str
) -> None:
    linked = link_by_name(_buy(lx, **key), SUPPLIERS)

    assert lx.columns(linked.lines, "line_id", "supplier_id", "link_status") == [
        ("e01", None, None),
        ("e02", supplier, "linked"),
        ("e03", None, None),
    ]
    assert (linked.issues, linked.problems) == ((), ())


def test_several_processes_that_fit_are_an_open_issue_naming_them(lx) -> None:
    linked = link_by_name(_buy(lx, product="electricity", location="CH"), SUPPLIERS)

    assert lx.columns(linked.lines, "supplier_id", "link_status")[1] == (None, None)
    [issue] = linked.issues
    assert (issue.line_id, issue.status) == ("e02", IssueStatus.open)
    [problem] = linked.problems
    assert problem.code == "ambiguous-supplier"
    assert "p:grid-ch" in problem.message and "p:pv" in problem.message


def test_with_units_a_supplier_must_make_its_product_in_the_lines_quantity(lx) -> None:
    suppliers = Suppliers([GRID, FABRIC], DEFAULT_UNITS)
    table = lx.table(
        lx.line(
            "p:shirt", "e02", "input", "f:electricity", unit="MJ", supplier_product="electricity"
        ),
        lx.line(
            "p:shirt", "e03", "input", "f:electricity", unit="kg", supplier_product="electricity"
        ),
    )

    linked = link_by_name(table, suppliers)

    assert lx.columns(linked.lines, "supplier_id") == [("p:grid",), (None,)]
    [problem] = linked.problems
    assert (problem.code, problem.item) == ("supplier-other-quantity", "p:shirt line e03")
    assert "in mass (kg)" in problem.message and "energy (kWh)" in problem.message


def test_names_match_exactly_and_close_ones_are_only_suggested(lx) -> None:
    linked = link_by_name(_buy(lx, product="Electricity", location="GLO"), SUPPLIERS)

    [problem] = linked.problems
    assert problem.code == "no-supplier"
    assert "p:grid" in problem.message  # suggested, not linked
    assert lx.columns(linked.lines, "supplier_id")[1] == (None,)


def test_a_mapping_row_by_name_decides_first(lx, csv_file: Callable[[str], Path]) -> None:
    mapping = read_supplier_mapping(
        csv_file(
            "product,location,unit,supplier,reason,who,when,comment\n"
            "electricity,CH,,p:pv,,leo,2026-10-24,the mill has a PV contract\n"
            "steam,,,,No steam in the package,leo,2026-10-24,\n"
        )
    )
    table = lx.table(
        lx.line(
            "p:shirt",
            "e02",
            "input",
            "f:electricity",
            supplier_product="electricity",
            supplier_location="CH",
        ),
        lx.line("p:shirt", "e04", "input", "f:steam", supplier_product="steam"),
    )

    linked = link_by_name(table, SUPPLIERS, decided=mapping)

    assert lx.columns(linked.lines, "supplier_id", "link_status") == [
        ("p:pv", "linked"),
        (None, "data_gap"),
    ]
    decided, gap = linked.issues
    assert (decided.status, decided.supplier_id, decided.reason) == (
        IssueStatus.linked,
        "p:pv",
        "the mill has a PV contract",
    )
    assert (gap.status, gap.reason, gap.resolved_by) == (
        IssueStatus.data_gap,
        "No steam in the package",
        "leo",
    )
    assert linked.unused_rows == ()


def test_suppliers_of_tables_offer_each_reference_line(lx) -> None:
    processes = pa.Table.from_pylist(
        [
            {"id": "p:gin", "name": "cotton ginning", "location": "IN"},
            {"id": "p:grid", "name": "market for electricity", "location": "GLO"},
        ]
    )
    table = lx.table(
        lx.line("p:gin", "p1", "reference", "f:fibre"),
        lx.line("p:gin", "p2", "reference", "f:seed"),
        lx.line("p:grid", "e01", "reference", "f:electricity", unit="kWh"),
    )
    flows = [
        Flow(id="f:fibre", name="cotton fibre", kind=FlowKind.product, unit="kg"),
        Flow(id="f:seed", name="cotton seed", kind=FlowKind.product, unit="kg"),
        Flow(id="f:electricity", name="electricity", kind=FlowKind.product, unit="kWh"),
    ]

    offered = Suppliers.of_tables(processes, table, flows)

    assert offered.find(SupplierKey(product="cotton seed")) == (
        Supplier("p:gin", "cotton ginning", "cotton seed", "IN", "kg"),
    )
    assert [s.id for s in offered.find(SupplierKey(unit="kWh"))] == ["p:grid"]


def test_suppliers_of_a_release_read_its_reference_lines(toy_release: Release) -> None:
    offered = Suppliers.of_release(toy_release)

    [electricity] = offered.find(SupplierKey(product="electricity"))
    assert electricity.id == "toy:electricity-production"
    assert electricity.unit == "kWh"
