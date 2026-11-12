"""Suppliers are named by product, location and unit, and linked by the shared stage.

ecoSpold1 has no supplier ids: a line says `electricity, CH, kWh`, and the process that makes
exactly that is its supplier.
"""

import pytest

from headwater.model import InvalidDataError, IssueStatus, Role


def _buyer(es1, supplier: str = "electricity", unit: str = "kWh", location: str | None = "CH"):
    return es1.dataset(
        "widget",
        es1.exchange(1, "widget", "kg", 1, "out0", location="CH"),
        es1.exchange(2, supplier, unit, 3, "in2", location=location),
        number=9,
    )


def _supplier(inventory, name: str):
    return next(p for p in inventory.processes if p.name == name)


def test_a_line_links_to_the_process_with_that_product_location_and_unit(es1) -> None:
    folder = es1.make(es1.maker("electricity", "kWh"), _buyer(es1))

    inventory = es1.load(folder)

    widget = _supplier(inventory, "widget")
    line = widget.exchanges[1]
    assert line.role is Role.input
    assert line.supplier_id == _supplier(inventory, "electricity").id
    assert line.link_status is not None and line.link_status.value == "linked"
    assert inventory.issues == () and inventory.problems == ()


def test_a_line_with_no_location_links_when_one_process_makes_the_product(es1) -> None:
    folder = es1.make(es1.maker("electricity", "kWh"), _buyer(es1, location=None))

    inventory = es1.load(folder)

    assert _supplier(inventory, "widget").exchanges[1].supplier_id is not None
    assert inventory.problems == ()


def test_a_line_whose_supplier_is_not_in_the_source_is_an_open_issue(es1) -> None:
    folder = es1.make(es1.maker("electricity", "kWh", location="DE"), _buyer(es1))

    inventory = es1.load(folder)

    [problem] = inventory.problems
    assert problem.code == "no-supplier"
    assert "electricity" in problem.message
    [issue] = inventory.issues
    assert issue.status is IssueStatus.open and issue.line_id == "2"
    with pytest.raises(InvalidDataError):
        inventory.freeze(into=es1.tmp_path / "releases")


def test_a_line_in_another_unit_than_the_supplier_makes_does_not_link(es1) -> None:
    folder = es1.make(es1.maker("electricity", "kWh"), _buyer(es1, unit="MJ"))

    inventory = es1.load(folder)

    assert [p.code for p in inventory.problems] == ["no-supplier"]


def test_two_processes_that_make_the_same_product_are_ambiguous(es1) -> None:
    first = es1.dataset(
        "electricity, hydro",
        es1.exchange(1, "electricity", "kWh", 1, "out0", location="CH"),
        unit="kWh",
        number=1,
    )
    second = es1.dataset(
        "electricity, wind",
        es1.exchange(1, "electricity", "kWh", 1, "out0", location="CH"),
        unit="kWh",
        number=2,
    )
    folder = es1.make(first, second, _buyer(es1))

    inventory = es1.load(folder)

    [problem] = inventory.problems
    assert problem.code == "ambiguous-supplier"
    assert "electricity, hydro" in problem.message and "electricity, wind" in problem.message
    assert [i.status for i in inventory.issues] == [IssueStatus.open]


def test_a_co_product_is_found_by_its_own_name_not_the_process_name(es1) -> None:
    """The process is `chp`, its products are `heat` and `electricity`, and a buyer asks for
    electricity."""
    chp = es1.dataset(
        "chp",
        es1.exchange(1, "heat", "MJ", 5, "out0", location="CH"),
        es1.exchange(2, "electricity", "kWh", 1, "out2", location="CH"),
        es1.exchange(3, "natural gas", "m3", 0.5, "in5", location="CH"),
        es1.allocation(1, 60, 3),
        es1.allocation(2, 40, 3),
        number=1,
        unit="MJ",
    )
    gas = es1.maker("natural gas", "m3", number=2)
    folder = es1.make(chp, gas, _buyer(es1))

    inventory = es1.load(folder)

    split = {p.id.rsplit(":", 1)[-1]: p for p in inventory.processes if p.name == "chp"}
    assert set(split) == {"1", "2"}
    buyer = _supplier(inventory, "widget")
    assert buyer.exchanges[1].supplier_id == split["2"].id
    assert inventory.problems == ()
