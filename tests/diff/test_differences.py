"""The records that differ between two readings of a study, before anything is calculated."""

import shutil
from pathlib import Path

from headwater.diff import Change, Difference, differences
from headwater.diff._changes import Names
from headwater.study import DatabaseFolder, read_database
from tests.conftest import add_lines, add_process, toy_database

TOY = Path(__file__).resolve().parent.parent / "fixtures" / "toy"


def edit(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    assert old in text, f"{old!r} not found in {path}"
    path.write_text(text.replace(old, new, 1))


def study(root: Path, name: str = "tshirt") -> DatabaseFolder:
    return read_database(toy_database(root, name))


def csv(root: Path) -> Path:
    return root / "tshirt" / "tshirt" / "exchanges.csv"


def only(found: tuple[Difference, ...]) -> Difference:
    assert len(found) == 1, [d.description for d in found]
    return found[0]


def test_the_same_study_has_no_differences(toy: Path) -> None:
    assert differences(study(toy), study(toy)) == ()


def test_a_changed_amount_names_the_line_and_both_amounts(toy: Path) -> None:
    old = study(toy)
    edit(csv(toy), "e02,input,toy:cotton-fabric,0.25", "e02,input,toy:cotton-fabric,0.22")

    found = only(differences(old, study(toy)))

    assert found.kind == "line"
    assert found.change is Change.changed
    assert found.item == "tshirt:tshirt line e02"
    assert found.id == "line tshirt:tshirt line e02"
    assert [(f.name, f.old, f.new) for f in found.fields] == [("amount", 0.25, 0.22)]
    assert found.flow_id == "toy:cotton-fabric"
    assert "0.25" in found.description and "0.22" in found.description
    assert found.scored


def test_a_changed_supplier_and_unit_are_two_fields_of_one_difference(toy: Path) -> None:
    old = study(toy)
    edit(csv(toy), "0.5,kWh,toy:electricity-production", "500,Wh,toy:fabric-production")

    found = only(differences(old, study(toy)))

    assert found.item == "tshirt:tshirt line e03"
    assert {f.name for f in found.fields} == {"amount", "unit", "supplier"}
    assert dict((f.name, f.new) for f in found.fields)["supplier"] == "toy:fabric-production"


def test_an_added_line_and_a_removed_line(toy: Path) -> None:
    old = study(toy)
    edit(csv(toy), "tshirt,e03,input,toy:electricity,0.5,kWh,toy:electricity-production\n", "")
    add_lines(
        toy / "tshirt" / "tshirt",
        "tshirt",
        "e04,waste_to_treatment,toy:textile-waste,0.05,kg,toy:incineration-textile-waste",
    )

    found = differences(old, study(toy))

    by_item = {d.item: d for d in found}
    assert set(by_item) == {"tshirt:tshirt line e03", "tshirt:tshirt line e04"}
    assert by_item["tshirt:tshirt line e03"].change is Change.removed
    assert by_item["tshirt:tshirt line e04"].change is Change.added
    assert "0.05" in by_item["tshirt:tshirt line e04"].description


def test_a_new_process_is_one_difference_not_one_per_line(toy: Path) -> None:
    old = study(toy)
    add_process(
        toy / "tshirt" / "tshirt",
        "packaging",
        "e01,reference,tshirt:packaging,1,unit,\ne02,emission,toy:co2,0.5,kg,",
    )
    with (toy / "tshirt" / "tshirt" / "flows.csv").open("a") as handle:
        handle.write("tshirt:packaging,packaging,product,unit,,\n")

    found = differences(old, study(toy))

    by_kind = {(d.kind, d.item): d for d in found}
    assert set(by_kind) == {("process", "tshirt:packaging"), ("flow", "tshirt:packaging")}
    assert by_kind[("process", "tshirt:packaging")].change is Change.added


def test_a_removed_process(toy: Path) -> None:
    folder = toy / "tshirt" / "tshirt"
    tables = {name: (folder / name).read_text() for name in ("processes.csv", "exchanges.csv")}
    add_process(
        folder,
        "packaging",
        "e01,reference,tshirt:tshirt-box,1,unit,\ne02,emission,toy:co2,0.5,kg,",
    )
    with (folder / "flows.csv").open("a") as handle:
        handle.write("tshirt:tshirt-box,box,product,unit,,\n")
    old = study(toy)
    for name, text in tables.items():  # the tables without the packaging rows
        (folder / name).write_text(text)

    found = [d for d in differences(old, study(toy)) if d.kind == "process"]

    assert [(d.item, d.change) for d in found] == [("tshirt:packaging", Change.removed)]


def test_a_changed_process_name_is_a_difference_of_the_process(toy: Path) -> None:
    old = study(toy)
    edit(toy / "tshirt/tshirt/processes.csv", "tshirt,T-shirt,", "tshirt,Polo shirt,")

    found = only(differences(old, study(toy)))

    assert (found.kind, found.change, found.item) == ("process", Change.changed, "tshirt:tshirt")
    assert [(f.name, f.old, f.new) for f in found.fields] == [("name", "T-shirt", "Polo shirt")]


def test_a_parameter_is_one_difference_and_names_the_lines_it_moves(toy: Path) -> None:
    shutil.rmtree(toy / "tshirt" / "tshirt")
    shutil.copytree(TOY / "tshirt-parameter" / "tshirt", toy / "tshirt" / "tshirt")
    old = study(toy)
    edit(toy / "tshirt/tshirt/parameters.yaml", "value: 0.25", "value: 0.30")

    found = only(differences(old, study(toy)))

    assert (found.kind, found.change, found.item) == ("parameter", Change.changed, "fabric_kg")
    assert [(f.name, f.old, f.new) for f in found.fields] == [("value", 0.25, 0.30)]
    assert found.affects == ("tshirt:tshirt line e02",)
    assert found.scored


def test_a_parameter_added_and_one_removed(toy: Path) -> None:
    shutil.rmtree(toy / "tshirt" / "tshirt")
    shutil.copytree(TOY / "tshirt-parameter" / "tshirt", toy / "tshirt" / "tshirt")
    old = study(toy)
    edit(toy / "tshirt/tshirt/exchanges.csv", "fabric_kg", "0.25")
    (toy / "tshirt/tshirt/parameters.yaml").write_text(
        "database:\n  electricity_kwh:\n    value: 0.5\n    unit: kWh\n"
    )

    found = differences(old, study(toy))

    changes = {(d.kind, d.item): d.change for d in found}
    assert changes[("parameter", "fabric_kg")] is Change.removed
    assert changes[("parameter", "electricity_kwh")] is Change.added
    assert ("line", "tshirt:tshirt line e02") in changes  # the formula became a number


def test_an_override_line_is_a_difference_of_its_own(toy: Path) -> None:
    old = study(toy)
    shutil.copytree(
        TOY / "tshirt-local-electricity" / "tshirt" / "overrides",
        toy / "tshirt" / "tshirt" / "overrides",
    )

    found = only(differences(old, study(toy)))

    assert (found.kind, found.change) == ("override", Change.added)
    assert found.item == "toy:electricity-production line e02"
    assert "0.3" in found.description
    assert found.scored


def test_an_override_changed_and_removed(toy: Path) -> None:
    shutil.copytree(
        TOY / "tshirt-local-electricity" / "tshirt" / "overrides",
        toy / "tshirt" / "tshirt" / "overrides",
    )
    old = study(toy)
    edit(
        toy / "tshirt/tshirt/overrides/own-wind-power.csv",
        "emission,toy:co2,0.3",
        "emission,toy:co2,0.2",
    )
    changed = only(differences(old, study(toy)))
    shutil.rmtree(toy / "tshirt" / "tshirt" / "overrides")
    removed = only(differences(old, study(toy)))

    assert (changed.kind, changed.change) == ("override", Change.changed)
    assert [(f.name, f.old, f.new) for f in changed.fields] == [("amount", 0.3, 0.2)]
    assert (removed.kind, removed.change) == ("override", Change.removed)


def test_a_cut_off_is_listed_once_and_changes_no_score(toy: Path) -> None:
    old = study(toy)
    shutil.rmtree(toy / "tshirt" / "tshirt")
    shutil.copytree(TOY / "tshirt-cut-off" / "tshirt", toy / "tshirt" / "tshirt")

    found = differences(old, study(toy))

    kinds = {(d.kind, d.item) for d in found}
    assert ("cut_off", "tshirt:tshirt line e04") in kinds
    assert ("line", "tshirt:tshirt line e04") in kinds  # the new line itself
    assert ("database", "cut_off") in kinds  # max_cumulative 0.03 to 0.05
    cut_off = next(d for d in found if d.kind == "cut_off")
    assert cut_off.change is Change.added
    assert not cut_off.scored


def test_a_line_that_became_a_cut_off_is_not_also_a_changed_line(toy: Path) -> None:
    shutil.rmtree(toy / "tshirt" / "tshirt")
    shutil.copytree(TOY / "tshirt-cut-off" / "tshirt", toy / "tshirt" / "tshirt")
    old_csv = (toy / "tshirt/tshirt/cut_offs.csv").read_text()
    (toy / "tshirt/tshirt/cut_offs.csv").write_text(old_csv.splitlines()[0] + "\n")
    # without the cut-off row the line has no supplier, which validation refuses, but reading
    # the study only needs the records to be valid
    old = study(toy)
    (toy / "tshirt/tshirt/cut_offs.csv").write_text(old_csv)

    found = differences(old, study(toy))

    assert {(d.kind, d.item) for d in found} == {("cut_off", "tshirt:tshirt line e04")}


def test_a_description_that_is_reworded_is_a_database_difference(toy: Path) -> None:
    with (toy / "tshirt/tshirt/database.yaml").open("a") as handle:
        handle.write("description: Footprint of one T-shirt, internal use\n")
    old = study(toy)
    edit(
        toy / "tshirt/tshirt/database.yaml",
        "description: Footprint of one T-shirt, internal use",
        "description: Footprint of one T-shirt for the sales team",
    )

    found = only(differences(old, study(toy)))

    assert (found.kind, found.change, found.item) == ("database", Change.changed, "description")
    assert not found.scored


def test_a_flow_that_changes_unit_and_a_flow_property_are_differences(toy: Path) -> None:
    old = study(toy)
    edit(
        toy / "tshirt/tshirt/flows.csv",
        "tshirt:tshirt,T-shirt,product,unit,,",
        "tshirt:tshirt,T-shirt,product,kg,,",
    )

    found = only(differences(old, study(toy)))

    assert (found.kind, found.change, found.item) == ("flow", Change.changed, "tshirt:tshirt")
    assert found.scored


def test_a_distribution_alone_is_a_changed_field_that_moves_no_score(toy: Path) -> None:
    old = study(toy)
    rows = csv(toy).read_text().splitlines()
    csv(toy).write_text(
        "\n".join(
            [rows[0] + ",distribution,scale,minimum,maximum"]
            + [r + (",lognormal,0.1,," if r.startswith("tshirt,e02") else ",,,,") for r in rows[1:]]
        )
        + "\n"
    )

    found = only(differences(old, study(toy)))

    assert found.item == "tshirt:tshirt line e02"
    assert [f.name for f in found.fields] == ["uncertainty"]
    assert found.description == "uncertainty none → lognormal scale 0.1"


def test_a_parameters_distribution_alone_is_a_changed_field(toy: Path) -> None:
    root = toy / "tshirt-parameter" / "tshirt"
    old = read_database(root)
    (root / "parameters.yaml").write_text(
        "database:\n  fabric_kg: {value: 0.25, unit: kg, distribution: normal, scale: 0.01,"
        " comment: 'Fabric in one T-shirt, from the bill of materials'}\n"
    )

    found = only(differences(old, read_database(root)))

    assert found.item == "fabric_kg"
    assert [f.name for f in found.fields] == ["uncertainty"]


# --- comments are not causes -------------------------------------------------------------------


def with_comments(path: Path, comments: dict[str, str]) -> None:
    """exchanges.csv with a `comment` column, holding `comments` by line id."""
    rows = [",".join(r.split(",")[:7]) for r in path.read_text().splitlines()]
    out = [rows[0] + ",comment"]
    out += [r + "," + comments.get(r.split(",")[1], "") for r in rows[1:]]
    path.write_text("\n".join(out) + "\n")


def test_a_comment_edit_alone_is_a_comment_change_that_moves_no_score(toy: Path) -> None:
    with_comments(csv(toy), {"e02": "Fabric from the bill of materials"})
    old = study(toy)
    with_comments(csv(toy), {"e02": "Fabric weighed at the factory"})

    found = only(differences(old, study(toy)))

    assert found.kind == "comment"
    assert found.item == "tshirt:tshirt line e02"
    assert found.id == "comment tshirt:tshirt line e02"
    assert found.flow_id == "toy:cotton-fabric"
    assert found.description == (
        'comment "Fabric from the bill of materials" → "Fabric weighed at the factory"'
    )
    assert not found.scored


def test_a_comment_edited_with_the_amount_is_listed_apart_from_the_cause(toy: Path) -> None:
    with_comments(csv(toy), {"e02": "Fabric from the bill of materials"})
    old = study(toy)
    with_comments(csv(toy), {"e02": "Lighter fabric"})
    edit(csv(toy), "toy:cotton-fabric,0.25", "toy:cotton-fabric,0.22")

    line, comment = differences(old, study(toy))

    assert (line.kind, line.description) == ("line", "amount 0.25 → 0.22 kg")
    assert [f.name for f in line.fields] == ["amount"]
    assert line.scored
    assert (comment.kind, comment.item) == ("comment", "tshirt:tshirt line e02")
    assert comment.description == 'comment "Fabric from the bill of materials" → "Lighter fabric"'
    assert not comment.scored


def test_a_comment_added_where_there_was_none(toy: Path) -> None:
    old = study(toy)
    with_comments(csv(toy), {"e03": "Grid mix"})

    found = only(differences(old, study(toy)))

    assert (found.kind, found.description) == ("comment", 'comment none → "Grid mix"')


def test_the_comment_of_an_override_line_and_of_a_parameter(toy: Path) -> None:
    shutil.copytree(
        TOY / "tshirt-local-electricity" / "tshirt" / "overrides",
        toy / "tshirt" / "tshirt" / "overrides",
    )
    old = study(toy)
    edit(toy / "tshirt/tshirt/overrides/own-wind-power.csv", "Wind power:", "Wind:")
    root = toy / "tshirt-parameter" / "tshirt"
    old_parameter = read_database(root)
    edit(root / "parameters.yaml", "from the bill of materials", "weighed")

    override = only(differences(old, study(toy)))
    parameter = only(differences(old_parameter, read_database(root)))

    assert (override.kind, override.item) == (
        "comment",
        "override of toy:electricity-production line e02",
    )
    assert not override.scored
    assert (parameter.kind, parameter.item) == ("comment", "parameter fabric_kg")
    assert parameter.description == (
        'comment "Fabric in one T-shirt, from the bill of materials" → '
        '"Fabric in one T-shirt, weighed"'
    )
    assert not parameter.scored


# --- in words ----------------------------------------------------------------------------------


NAMES = Names(
    flows={"toy:electricity": "electricity", "toy:cotton-fabric": "cotton fabric"},
    processes={
        "toy:electricity-production": "electricity production (GLO)",
        "toy:fabric-production": "cotton fabric production (GLO)",
    },
)


def test_a_changed_supplier_is_described_by_the_names_given(toy: Path) -> None:
    old = study(toy)
    edit(csv(toy), "0.5,kWh,toy:electricity-production", "0.5,kWh,toy:fabric-production")

    found = only(differences(old, study(toy)))

    assert found.description == "supplier toy:electricity-production → toy:fabric-production"
    assert found.described(NAMES) == (
        "supplier electricity production (GLO) → cotton fabric production (GLO)"
    )
    assert found.described(Names()) == found.description


def test_two_suppliers_with_the_same_name_are_told_apart_by_their_ids(toy: Path) -> None:
    old = study(toy)
    edit(csv(toy), "0.5,kWh,toy:electricity-production", "0.5,kWh,toy:fabric-production")
    same = Names(
        processes={
            "toy:electricity-production": "a market (GLO)",
            "toy:fabric-production": "a market (GLO)",
        }
    )

    found = only(differences(old, study(toy)))

    assert found.described(same) == (
        "supplier a market (GLO) toy:electricity-production → a market (GLO) toy:fabric-production"
    )


def test_an_added_line_is_described_with_its_flow_and_supplier_in_words(toy: Path) -> None:
    old = study(toy)
    add_lines(
        toy / "tshirt" / "tshirt",
        "tshirt",
        "e04,input,toy:electricity,0.1,kWh,toy:electricity-production",
    )

    found = only(differences(old, study(toy)))

    assert found.description == (
        "added: input 0.1 kWh of toy:electricity, supplier toy:electricity-production"
    )
    assert found.described(NAMES) == (
        "added: input 0.1 kWh of toy:electricity, supplier electricity production (GLO)"
    )
