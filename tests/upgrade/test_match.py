"""How each supplier of a database finds its successor in the new release, rule by rule.

The rules, in order: a reviewed mapping row; the same ids; a correspondence row; the same dataset
(name, product and location); else unmapped, with suggestions that are never applied.
"""

from collections.abc import Callable
from pathlib import Path

import pytest

from headwater.model import InvalidDataError
from headwater.upgrade import Match, Plan, Rule, plan

Edit = Callable[[Path, str, str], None]


def matched(found: Plan) -> dict[str, Match]:
    return {m.old.id: m for m in found.matches}


def test_each_supplier_maps_by_the_first_rule_that_decides_it(
    studies: Path, to: str, mapping: Path
) -> None:
    found = plan(studies / "tshirt", to=to, mapping=mapping)

    by_old = matched(found)
    assert {old: (m.rule, m.new and m.new.id) for old, m in by_old.items()} == {
        "toy:1:market-elec": (Rule.same_id, "toy:2:market-elec"),
        "toy:1:fabric-a": (Rule.same_dataset, "toy:2:fabric-b"),
        "toy:1:dye": (Rule.mapping, "toy:2:dye-r"),
        "toy:1:tape": (Rule.mapping, "toy:2:tape-rer"),
    }
    assert found.complete
    assert by_old["toy:1:fabric-a"].lines == ("tshirt:tshirt line e02",)


def test_a_mapping_row_says_who_decided_and_when(studies: Path, to: str, mapping: Path) -> None:
    dye = matched(plan(studies / "tshirt", to=to, mapping=mapping))["toy:1:dye"]

    assert dye.decision is not None
    assert dye.decision.who == "test"
    assert dye.decision.when.date().isoformat() == "2026-10-25"
    assert dye.decision.source == "upgrade-mapping.csv:2"
    assert dye.automatic is None


def test_counts_are_per_rule_in_suppliers_and_in_lines(
    studies: Path, to: str, mapping: Path
) -> None:
    found = plan(studies / "tshirt", to=to, mapping=mapping)

    assert found.counts() == {
        Rule.mapping: (2, 2),
        Rule.same_id: (1, 1),
        Rule.correspondence: (0, 0),
        Rule.same_dataset: (1, 1),
        Rule.unmapped: (0, 0),
    }


def test_a_supplier_no_rule_decides_is_unmapped_with_its_reason_and_suggestions(
    studies: Path, to: str
) -> None:
    found = plan(studies / "tshirt", to=to)

    tape = matched(found)["toy:1:tape"]
    assert tape.rule is Rule.unmapped
    assert tape.new is None
    assert tape.code == "not-in-release"
    assert "tape production | tape | GLO" in (tape.reason or "")
    assert [s.id for s in tape.suggestions][:1] == ["toy:2:tape-rer"]
    assert not found.complete
    assert {m.old.id for m in found.unmapped} == {"toy:1:dye", "toy:1:tape"}


def test_a_suggestion_is_never_applied(studies: Path, to: str) -> None:
    found = plan(studies / "tshirt", to=to)

    dye = matched(found)["toy:1:dye"]
    assert dye.rule is Rule.unmapped
    assert "toy:2:dye-r" in [s.id for s in dye.suggestions]  # the same product, renamed
    assert dye.new is None


def test_a_correspondence_row_maps_a_renamed_dataset(studies: Path, to: str) -> None:
    found = plan(
        studies / "tshirt",
        to=to,
        correspondence={"toy:1:dye": ("toy:2:dye-r",)},
    )

    dye = matched(found)["toy:1:dye"]
    assert (dye.rule, dye.new and dye.new.id) == (Rule.correspondence, "toy:2:dye-r")


def test_a_correspondence_row_with_several_successors_is_a_split_and_unmapped(
    studies: Path, to: str
) -> None:
    found = plan(
        studies / "tshirt",
        to=to,
        correspondence={"toy:1:fabric-a": ("toy:2:fabric-b", "toy:2:fabric-in")},
    )

    fabric = matched(found)["toy:1:fabric-a"]
    assert fabric.rule is Rule.unmapped
    assert fabric.code == "split"
    assert [s.id for s in fabric.suggestions] == ["toy:2:fabric-b", "toy:2:fabric-in"]


def test_a_mapping_row_decides_before_the_rules_and_says_what_they_would_give(
    studies: Path, to: str, tmp_path: Path
) -> None:
    rows = tmp_path / "rows.csv"
    rows.write_text(
        "supplier_id,name,product,location,unit,supplier,reason,who,when,comment\n"
        "toy:1:fabric-a,,,,,toy:2:fabric-in,,test,2026-10-25,the mill is in India\n"
    )

    fabric = matched(plan(studies / "tshirt", to=to, mapping=rows))["toy:1:fabric-a"]

    assert (fabric.rule, fabric.new and fabric.new.id) == (Rule.mapping, "toy:2:fabric-in")
    assert fabric.automatic is not None
    assert fabric.automatic.id == "toy:2:fabric-b"


def test_a_mapping_row_with_a_reason_is_refused_because_a_database_holds_no_data_gap(
    studies: Path, to: str, tmp_path: Path
) -> None:
    rows = tmp_path / "rows.csv"
    rows.write_text(
        "supplier_id,name,product,location,unit,supplier,reason,who,when,comment\n"
        "toy:1:tape,,,,,,no tape in toy-2,test,2026-10-25,\n"
    )

    with pytest.raises(InvalidDataError) as caught:
        plan(studies / "tshirt", to=to, mapping=rows)

    [problem] = caught.value.problems
    assert problem.code == "reason-row"
    assert "cut_offs.csv" in problem.message


def test_a_mapping_row_to_a_process_the_new_release_lacks_is_unmapped(
    studies: Path, to: str, tmp_path: Path
) -> None:
    rows = tmp_path / "rows.csv"
    rows.write_text(
        "supplier_id,name,product,location,unit,supplier,reason,who,when,comment\n"
        "toy:1:tape,,,,,toy:2:tape,,test,2026-10-25,\n"
    )

    tape = matched(plan(studies / "tshirt", to=to, mapping=rows))["toy:1:tape"]

    assert tape.rule is Rule.unmapped
    assert tape.code == "not-a-process"
    assert "toy:2:tape" in (tape.reason or "")


def test_a_mapping_row_nothing_needed_is_a_warning(
    studies: Path, to: str, tmp_path: Path, mapping: Path
) -> None:
    rows = tmp_path / "rows.csv"
    rows.write_text(
        mapping.read_text() + "toy:1:elec,,,,,toy:2:elec,,test,2026-10-25,not bought here\n"
    )

    found = plan(studies / "tshirt", to=to, mapping=rows)

    [warning] = found.warnings
    assert warning.code == "row-not-needed"
    assert "rows.csv:4" in warning.item


def test_a_database_already_on_the_release_it_moves_to_is_refused(
    studies: Path, to: str, edit: Edit
) -> None:
    yaml = studies / "tshirt" / "database.yaml"
    edit(yaml, yaml.read_text().split("background: ")[1].split()[0], to)

    with pytest.raises(InvalidDataError) as caught:
        plan(studies / "tshirt", to=to)

    assert caught.value.problems[0].code == "already-upgraded"
