"""A study diffed across a release change: what the release changed, line by line, apart from what
the study changed.

The old study is scored on toy-1, the new one on toy-2. Between them sits the bridge: the old
study with only its suppliers moved to toy-2. The study's own processes and amounts are the same
in both, so the release's effect splits exactly into one term per line: the amount times the
change of what one unit of the supplier scores, or of the factor of an emission. The study's own
edits are then diffed from the bridge to the new study on toy-2, as `headwater diff` does.

Hand-checked: the T-shirt scores 1.2048 on toy-1 and 0.8775 once upgraded, see conftest.py.
"""

import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

import headwater as hw
from headwater.diff import DiffError, Source, diff_databases, render_text
from headwater.study import DatabaseFolder, read_database
from headwater.upgrade import plan
from tests.conftest import add_lines, add_process

Edit = Callable[[Path, str, str], None]
REL = 1e-9


@pytest.fixture
def old_background(studies: Path) -> hw.Background:
    pin = read_database(studies / "tshirt").database.background
    return hw.load(str(pin))


@pytest.fixture
def new_background(to: str) -> hw.Background:
    return hw.load(to)


@pytest.fixture
def old(studies: Path) -> DatabaseFolder:
    """The T-shirt on toy-1, read before the upgrade writes over it."""
    return read_database(studies / "tshirt")


def add_print(folder: Path) -> None:
    """A second process of the T-shirt database, printing, that buys 0.2 kWh from toy-1's
    market. Its lines leave the flow_name and comment columns of the fixture empty."""
    add_process(
        folder,
        "print",
        "e01,reference,tshirt:print,1,unit,,,\n"
        "e02,input,toy:electricity,0.2,kWh,toy:1:market-elec,,",
        details="printing,GLO,ordinary_transforming_activity",
    )


def upgraded(studies: Path, to: str, mapping: Path) -> DatabaseFolder:
    plan(studies / "tshirt", to=to, mapping=mapping).write()
    return read_database(studies / "tshirt")


def test_the_release_change_and_the_study_edit_are_told_apart(
    studies: Path,
    to: str,
    mapping: Path,
    old: DatabaseFolder,
    old_background: hw.Background,
    new_background: hw.Background,
    edit: Edit,
) -> None:
    upgraded(studies, to, mapping)
    edit(
        studies / "tshirt" / "exchanges.csv",
        "e02,input,toy:cotton-fabric,0.25",
        "e02,input,toy:cotton-fabric,0.22",
    )

    [row] = diff_databases(
        old, read_database(studies / "tshirt"), new_background, old_background=old_background
    ).rows

    assert row.old == pytest.approx(1.2048, rel=REL)
    assert row.new == pytest.approx(0.795, rel=REL)  # 0.8775 - 0.03 × 2.75
    assert row.background == pytest.approx(0.8775 - 1.2048, rel=REL)
    assert [(m.process_id, m.line_id, m.how) for m in row.moved] == [
        ("tshirt:tshirt", "e02", "same-dataset"),
        ("tshirt:tshirt", "e03", "same-id"),
        ("tshirt:tshirt", "e04", "new-study"),
        ("tshirt:tshirt", "e05", "factor"),
        ("tshirt:tshirt", "e06", "new-study"),
    ]
    assert [m.effect for m in row.moved] == pytest.approx(
        [
            0.25 * (2.75 - 3.5),
            0.5 * (0.25 - 0.5),
            0.01 * (3 - 4),
            0.001 * (27 - 29.8),
            0.01 * (0.8 - 1),
        ],
        rel=REL,
    )
    fabric = row.moved[0]
    assert (fabric.old_supplier, fabric.new_supplier) == ("toy:1:fabric-a", "toy:2:fabric-b")
    assert (fabric.old_value, fabric.new_value) == pytest.approx((3.5, 2.75), rel=REL)
    assert sum(m.effect for m in row.moved) == pytest.approx(row.background, rel=REL)
    [driver] = row.drivers
    assert driver.difference.item == "tshirt:tshirt line e02"
    assert driver.contribution == pytest.approx(-0.03 * 2.75, rel=REL)
    assert row.interaction == pytest.approx(0.0, abs=1e-12)
    assert row.background is not None and driver.contribution is not None
    assert row.change == pytest.approx(
        row.background + driver.contribution + (row.interaction or 0.0), rel=REL
    )


def test_an_upgrade_alone_is_all_release_and_no_study(
    studies: Path,
    to: str,
    mapping: Path,
    old: DatabaseFolder,
    old_background: hw.Background,
    new_background: hw.Background,
) -> None:
    new = upgraded(studies, to, mapping)

    result = diff_databases(old, new, new_background, old_background=old_background)

    [row] = result.rows
    assert row.background == pytest.approx(row.change, rel=REL)
    assert row.drivers == ()
    assert row.interaction == pytest.approx(0.0, abs=1e-12)
    assert result.differences == ()
    assert result.old_release is not None and result.old_release.startswith("toy-1@")
    assert result.release is not None and result.release.startswith("toy-2@")


def test_a_supplier_with_no_successor_that_the_new_study_drops_counts_as_the_release(
    studies: Path,
    to: str,
    mapping: Path,
    old: DatabaseFolder,
    old_background: hw.Background,
    new_background: hw.Background,
    edit: Edit,
) -> None:
    upgraded(studies, to, mapping)
    edit(
        studies / "tshirt" / "exchanges.csv",
        "tshirt,e06,input,toy:tape,0.01,kg,toy:2:tape-rer,tape,\n",
        "",
    )

    [row] = diff_databases(
        old, read_database(studies / "tshirt"), new_background, old_background=old_background
    ).rows

    tape = next(m for m in row.moved if m.line_id == "e06")
    assert tape.how == "no-successor"
    assert tape.new_supplier is None
    assert tape.effect == pytest.approx(-0.01, rel=REL)
    assert sum(m.effect for m in row.moved) == pytest.approx(row.background, rel=REL)
    assert row.drivers == ()


def test_every_product_of_the_study_can_be_compared(
    studies: Path,
    to: str,
    mapping: Path,
    old_background: hw.Background,
    new_background: hw.Background,
    edit: Edit,
) -> None:
    folder = studies / "tshirt"
    add_print(folder)
    flows = folder / "flows.csv"
    flows.write_text(flows.read_text() + "tshirt:print,print,product,unit,,\n")
    add_lines(folder, "tshirt", "e07,input,tshirt:print,1,unit,tshirt:print,print,")
    old = read_database(folder)

    new = upgraded(studies, to, mapping)
    rows = diff_databases(
        old, new, new_background, old_background=old_background, products="all"
    ).rows

    by_product = {r.product_id: r for r in rows}
    assert set(by_product) == {"tshirt:tshirt", "tshirt:print"}
    printing = by_product["tshirt:print"]
    assert (printing.old, printing.new) == pytest.approx((0.1, 0.05), rel=REL)
    [moved] = printing.moved
    assert (moved.process_id, moved.line_id) == ("tshirt:print", "e02")
    shirt = by_product["tshirt:tshirt"]
    assert shirt.old == pytest.approx(1.2048 + 0.1, rel=REL)
    # line e07 buys from the study itself: the release moves it through print's line e02
    assert ("tshirt:print", "e02") in [(m.process_id, m.line_id) for m in shirt.moved]
    assert sum(m.effect for m in shirt.moved) == pytest.approx(shirt.background, rel=REL)


def test_a_diff_on_one_release_has_no_release_part(
    studies: Path, old: DatabaseFolder, old_background: hw.Background, edit: Edit
) -> None:
    edit(
        studies / "tshirt" / "exchanges.csv",
        "e02,input,toy:cotton-fabric,0.25",
        "e02,input,toy:cotton-fabric,0.22",
    )

    [row] = diff_databases(old, read_database(studies / "tshirt"), old_background).rows

    assert row.background is None
    assert row.moved == ()


def test_a_line_whose_dataset_and_product_are_gone_is_the_release_s(
    studies: Path,
    to: str,
    mapping: Path,
    old_background: hw.Background,
    new_background: hw.Background,
    tmp_path: Path,
) -> None:
    before = tmp_path / "before" / "tshirt"
    shutil.copytree(studies / "tshirt", before)
    add_lines(before, "tshirt", "e07,input,toy:glue,0.01,kg,toy:1:glue,glue,")
    upgraded(studies, to, mapping)

    [row] = diff_databases(
        read_database(before),
        read_database(studies / "tshirt"),
        new_background,
        old_background=old_background,
    ).rows

    glue = next(m for m in row.moved if m.line_id == "e07")
    assert (glue.how, glue.new_supplier) == ("no-successor", None)
    assert glue.effect == pytest.approx(-0.02, rel=REL)
    assert sum(m.effect for m in row.moved) == pytest.approx(row.background, rel=REL)


def test_two_pins_on_one_background_say_how_to_diff_across_releases(
    studies: Path, to: str, mapping: Path, old: DatabaseFolder, new_background: hw.Background
) -> None:
    new = upgraded(studies, to, mapping)

    with pytest.raises(DiffError) as caught:
        diff_databases(old, new, new_background)

    assert "old_background" in str(caught.value)
    assert "not built yet" not in str(caught.value)


def test_the_text_of_two_products_across_a_release_change_names_the_product(
    studies: Path,
    to: str,
    mapping: Path,
    old_background: hw.Background,
    new_background: hw.Background,
) -> None:
    folder = studies / "tshirt"
    add_print(folder)
    flows = folder / "flows.csv"
    flows.write_text(flows.read_text() + "tshirt:print,print,product,unit,,\n")
    old = read_database(folder)
    new = upgraded(studies, to, mapping)

    diff = diff_databases(old, new, new_background, old_background=old_background, products="all")
    text = render_text(
        diff, path="studies/tshirt", old=Source("a", "1" * 40), new=Source("b", "2" * 40)
    )

    assert "T-shirt (tshirt:tshirt)" in text and "print (tshirt:print)" in text
    # no product is the reference: the line-by-line part is of the first product, print
    assert "Release change, line by line, on print, " in text
