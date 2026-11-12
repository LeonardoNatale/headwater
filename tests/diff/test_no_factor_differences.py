"""Rows of `no_factor.csv` that differ between two readings of a study.

Example: `tshirt-no-factor` acknowledges that COD counts 0 in `toy-climate`. Changing the reason
moves the study's input hash but no score, so the diff lists the row under the changes that are
not part of the score.
"""

import shutil
from pathlib import Path

import pytest

import headwater as hw
from headwater.diff import Change, diff_databases, differences
from headwater.study import DatabaseFolder, read_database

TOY = Path(__file__).resolve().parent.parent / "fixtures" / "toy"
ROW = "no_factor.csv"
REASON = "not characterised in toy-climate"


def edit(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    assert old in text, f"{old!r} not found in {path}"
    path.write_text(text.replace(old, new, 1))


@pytest.fixture
def folder(toy: Path) -> Path:
    """The T-shirt study that emits COD and acknowledges it."""
    shutil.rmtree(toy / "tshirt" / "tshirt")
    shutil.copytree(TOY / "tshirt-no-factor" / "tshirt", toy / "tshirt" / "tshirt")
    return toy / "tshirt" / "tshirt"


def read(path: Path) -> DatabaseFolder:
    return read_database(path)


def test_a_changed_reason_is_a_difference_that_names_the_row(folder: Path) -> None:
    old = read(folder)
    edit(folder / ROW, REASON, "below 1% of the footprint")

    [found] = differences(old, read(folder))

    assert found.kind == "no_factor"
    assert found.id == "no_factor tshirt:cod toy-climate"
    assert found.change is Change.changed
    assert found.item == "tshirt:cod"
    assert found.flow_id == "tshirt:cod"
    assert [(f.name, f.old, f.new) for f in found.fields] == [
        ("reason", REASON, "below 1% of the footprint")
    ]
    assert (
        found.description == f"for method toy-climate: reason {REASON} → below 1% of the footprint"
    )
    assert not found.scored


def test_who_and_when_are_part_of_the_row(folder: Path) -> None:
    old = read(folder)
    edit(folder / ROW, "Sam,2026-10-12", "Anna,2026-11-01")

    [found] = differences(old, read(folder))

    assert [f.name for f in found.fields] == ["who", "when"]
    assert "who Sam → Anna" in found.description
    assert "when 2026-10-12" in found.description and "2026-11-01" in found.description


def test_a_row_that_is_added_says_why_the_flow_counts_zero(folder: Path) -> None:
    new = read(folder)
    (folder / ROW).write_text((folder / ROW).read_text().splitlines()[0] + "\n")

    [found] = differences(read(folder), new)

    assert found.change is Change.added
    assert found.item == "tshirt:cod"
    assert found.description.startswith("added for method toy-climate: ")
    assert REASON in found.description and "Sam" in found.description
    assert not found.scored


def test_a_row_that_is_removed_keeps_what_it_said(folder: Path) -> None:
    old = read(folder)
    (folder / ROW).write_text((folder / ROW).read_text().splitlines()[0] + "\n")

    [found] = differences(old, read(folder))

    assert found.change is Change.removed
    assert found.description.startswith("removed for method toy-climate")
    assert REASON in found.description


def test_a_row_for_the_same_flow_and_another_method_is_another_row(folder: Path) -> None:
    old = read(folder)
    with (folder / ROW).open("a") as handle:
        handle.write(f"tshirt:cod,,EF-3.1,{REASON},Sam,2026-10-12\n")

    [found] = differences(old, read(folder))

    assert found.change is Change.added
    assert found.id == "no_factor tshirt:cod EF-3.1"


def test_the_same_rows_in_another_order_are_not_a_difference(folder: Path) -> None:
    with (folder / ROW).open("a") as handle:
        handle.write(f"tshirt:other,,toy-climate,{REASON},Sam,2026-10-12\n")
    old = read(folder)
    header, first, second = (folder / ROW).read_text().splitlines()
    (folder / ROW).write_text("\n".join([header, second, first]) + "\n")

    assert differences(old, read(folder)) == ()


def test_the_flow_name_written_in_the_row_is_not_a_scored_difference(folder: Path) -> None:
    old = read(folder)
    edit(folder / ROW, '"COD, Chemical Oxygen Demand"', '"COD"')

    [found] = differences(old, read(folder))

    assert [f.name for f in found.fields] == ["flow_name"]
    assert not found.scored


def test_a_changed_reason_moves_no_score_and_is_listed(
    folder: Path, background: hw.Background
) -> None:
    old = read(folder)
    edit(folder / ROW, REASON, "below 1% of the footprint")

    result = diff_databases(old, read(folder), background)

    [row] = result.rows
    assert not row.changed
    assert row.drivers == ()
    assert result.old_input_hash != result.new_input_hash
    assert [d.kind for d in result.differences] == ["no_factor"]
