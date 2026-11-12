"""SimaPro names suppliers by their product text: linked, no supplier, or ambiguous."""

from pathlib import Path
from typing import Any

from headwater.io.formats import inventory_from
from headwater.link import read_supplier_mapping
from headwater.model import IssueStatus

ELECTRICITY = "Electricity, medium voltage {FR}| market for | Cut-off, U"


def _export(sp: Any, *bought: str) -> Path:
    """A shirt that buys each text in `bought`, and the processes that make electricity, wheat
    as a unit process and wheat as a system process."""
    return sp.write(
        sp.process(
            "SHIRT",
            ["shirt {FR}| sewing;p;1;100;not defined;Shirts;"],
            lines={"Materials/fuels": [f"{text};kg;1;Undefined;0;0;0;" for text in bought]},
        ),
        sp.process("ELEC", [f"{ELECTRICITY};kg;1;100;not defined;Energy;"]),
        sp.process("WHEAT-U", ["Wheat grain {FR} U;kg;1;100;not defined;Cereals;"]),
        sp.process("WHEAT-S", ["Wheat grain {FR} S;kg;1;100;not defined;Cereals;"]),
    )


def _bought(inventory: Any) -> dict[str, str | None]:
    shirt = next(p for p in inventory.processes if p.id == "test:1.0:SHIRT")
    return {e.line_id: e.supplier_id for e in shirt.exchanges if e.role.value == "input"}


def test_a_text_that_one_process_makes_is_linked(sp: Any) -> None:
    inventory = inventory_from(_export(sp, ELECTRICITY), sp.OPTIONS, format="simapro-csv")

    assert inventory.problems == ()
    assert _bought(inventory) == {"m1": "test:1.0:ELEC"}


def test_a_text_no_process_makes_is_an_open_issue_with_close_names(sp: Any) -> None:
    other = "Electricity, medium voltage {DE}| market for | Cut-off, U"

    inventory = inventory_from(_export(sp, other), sp.OPTIONS, format="simapro-csv")

    [problem] = inventory.problems
    assert problem.code == "no-supplier"
    assert "location 'DE'" in problem.message and "test:1.0:ELEC" in problem.message
    assert [i.status for i in inventory.issues] == [IssueStatus.open]


def test_a_text_two_processes_fit_is_ambiguous_until_a_row_decides(sp: Any, tmp_path: Path) -> None:
    path = _export(sp, "Wheat grain {FR} U")

    inventory = inventory_from(path, sp.OPTIONS, format="simapro-csv")

    [problem] = inventory.problems
    assert problem.code == "ambiguous-supplier"
    assert "test:1.0:WHEAT-U" in problem.message and "test:1.0:WHEAT-S" in problem.message
    mapping = tmp_path / "suppliers.csv"
    mapping.write_text(
        "supplier_id,name,product,location,unit,supplier,reason,who,when,comment\n"
        ",,Wheat grain,FR,,test:1.0:WHEAT-U,,leo,2026-10-25,the text ends in U\n",
        encoding="utf-8",
    )

    decided = inventory_from(
        path, sp.OPTIONS, format="simapro-csv", suppliers=read_supplier_mapping(mapping)
    )

    assert decided.problems == ()
    assert _bought(decided) == {"m1": "test:1.0:WHEAT-U"}


def test_a_product_bought_in_another_quantity_than_made_is_an_open_issue(sp: Any) -> None:
    gas = "natural gas {CH}| market for"
    path = sp.write(
        sp.process("GAS", [f"{gas};m3;1;100;not defined;Energy;"]),
        sp.process(
            "HEAT",
            ["heat {CH}| boiler;MJ;1;100;not defined;Energy;"],
            lines={"Materials/fuels": [f"{gas};kg;0.02;Undefined;0;0;0;"]},
        ),
        sp.table("Units", "kg;Mass;1;kg", "m3;Volume;1;m3", "MJ;Energy;1;MJ"),
    )

    inventory = inventory_from(path, sp.OPTIONS, format="simapro-csv")

    [problem] = inventory.problems
    assert (problem.code, problem.item) == ("supplier-other-quantity", "test:1.0:HEAT line m1")
    assert "in mass (kg)" in problem.message and "make it in volume (m3)" in problem.message
    assert [i.status for i in inventory.issues] == [IssueStatus.open]
