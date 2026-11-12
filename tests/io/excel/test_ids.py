"""The id each activity gets: `<study>:<name>-<location>`, from the activity alone, so the same
activity keeps its id whatever else is in the workbook and however often it is imported."""

import hashlib
from pathlib import Path

from headwater.io.excel import ExcelImport, import_excel
from headwater.io.excel.records import process_stem, slug
from headwater.release import Release

from .conftest import Sheet, Workbooks, block, database, line

LONG = (
    "transport, freight, lorry with refrigeration machine, 16-32 metric ton, EURO6, "
    "R134a refrigerant, cooling, {variant}"
)


def run(workbooks: Workbooks, release: Release, out: Path, *activities: Sheet) -> ExcelImport:
    rows = [*database(), *(row for activity in activities for row in activity)]
    return import_excel(workbooks.write({"a": rows}), release, out)


def ids(done: ExcelImport) -> list[str]:
    return [p.id for p in done.conversion.processes]


def test_an_activity_keeps_its_id_when_a_same_name_activity_is_added_and_imported_again(
    workbooks: Workbooks, mini_release: Release, out: Path
) -> None:
    autoclave = block("Autoclave no EVA input GLO", location="GLO", unit="unit")
    widget = block("widget", line("market for steel", 1, product="steel"))
    first = run(workbooks, mini_release, out, autoclave, widget)
    assert ids(first) == ["mine:autoclave-no-eva-input-glo", "mine:widget-glo"]

    twin = block("Autoclave no EVA input GLO", location="VN", unit="unit")
    again = run(workbooks, mini_release, out, autoclave, widget, twin)

    assert set(ids(first)) <= set(ids(again))
    assert "mine:autoclave-no-eva-input-glo-vn" in ids(again)
    assert again.folder == first.folder


def test_no_id_ends_in_its_location_twice(
    workbooks: Workbooks, mini_release: Release, out: Path
) -> None:
    done = run(
        workbooks,
        mini_release,
        out,
        block("gear RER", location="RER", unit="unit"),
        block("gear", location="RER", unit="unit"),
        block("Heat, Europe without Switzerland", location="Europe without Switzerland"),
    )
    assert ids(done) == [
        "mine:gear-rer",
        "mine:gear-rer-2",  # the same id from its name and location
        "mine:heat-europe-without-switzerland",
    ]
    assert any("mine:gear-rer-2" in n.message for n in done.report.notes)


def test_a_long_name_is_cut_at_a_word_boundary_and_kept_apart_by_a_hash(
    workbooks: Workbooks, mini_release: Release, out: Path
) -> None:
    diesel, petrol = LONG.format(variant="diesel"), LONG.format(variant="petrol")
    done = run(
        workbooks,
        mini_release,
        out,
        block(diesel, location="RER", unit="unit"),
        block(petrol, location="RER", unit="unit"),
    )
    found = ids(done)
    assert len(set(found)) == 2
    for name, pid in zip((diesel, petrol), found, strict=True):
        stem = pid.split(":", 1)[1]
        assert len(stem) <= 100
        full = slug(name)
        digest = hashlib.sha256(full.encode()).hexdigest()[:6]
        cut, _, tail = stem.rpartition(f"-{digest}-")
        assert tail == "rer"
        assert full.startswith(cut + "-")  # whole words only: the next character is a hyphen


def test_the_ids_do_not_depend_on_the_order_of_the_activities(
    workbooks: Workbooks, mini_release: Release, tmp_path: Path
) -> None:
    # Two activities with the same name and location are left out: the second is numbered, -2,
    # in the order of the workbook. Every other id comes from its activity alone.
    activities = [
        block("gear", location="GLO", unit="unit"),
        block("gear", location="RER", unit="unit"),
        block("Autoclave no EVA input GLO", location="GLO", unit="unit"),
        block("Autoclave no EVA input GLO", location="VN", unit="unit"),
        block(LONG.format(variant="diesel"), location="RER", unit="unit"),
        block(LONG.format(variant="petrol"), location="RER", unit="unit"),
        block("widget", line("gear", 1, "unit", "GLO", database="mine", product="gear")),
    ]
    forward = run(workbooks, mini_release, tmp_path / "forward", *activities)
    backward = run(workbooks, mini_release, tmp_path / "backward", *reversed(activities))
    assert sorted(ids(forward)) == sorted(ids(backward))
    assert len(set(ids(forward))) == len(activities)


def test_a_line_that_does_not_link_gets_a_flow_id_by_the_same_rule(
    workbooks: Workbooks, mini_release: Release, out: Path
) -> None:
    done = run(
        workbooks,
        mini_release,
        out,
        block(
            "widget",
            line("market for unobtainium GLO", 1, location="GLO", product="unobtainium"),
            line(LONG.format(variant="diesel"), 1, "unit", "RER", product="transport"),
        ),
    )
    unmapped = sorted(f.id for f in done.conversion.flows if ":unmapped-" in f.id)
    assert unmapped[0] == "mine:unmapped-market-for-unobtainium-glo"
    stem = unmapped[1].removeprefix("mine:unmapped-")
    assert len(stem) <= 100 and stem.endswith("-rer")


def test_the_stem_of_an_id() -> None:
    assert process_stem("T-shirt", "GLO") == "t-shirt-glo"
    assert process_stem("Autoclave no EVA input GLO", "GLO") == "autoclave-no-eva-input-glo"
    assert process_stem("market for steel", None) == "market-for-steel"
    assert process_stem("GLO", "GLO") == "glo"
    assert process_stem("", None).startswith("item-")
    # A name with no letter a-z or digit keeps apart from another by a hash of the name.
    assert process_stem("!!!", "GLO").startswith("item-")
    assert process_stem("!!!", "GLO") != process_stem("???", "GLO")
    # A location that fills the limit alone is not cut, and the name is not lost.
    assert process_stem("A", "x" * 99).endswith("-" + "x" * 99)


def test_activities_with_one_name_get_ids_from_their_reference_products(
    workbooks: Workbooks, mini_release: Release, out: Path
) -> None:
    """Steps of one process named alike, `assembly` three times in GLO, are told apart by what
    each makes (D40): the ids say which step is which, and no longer depend on their order."""
    steps = [
        block("assembly", location="GLO", unit="unit", product=product)
        for product in ("upper", "sole", "shoe")
    ]
    widget = block("widget", line("market for steel", 1, product="steel"))

    done = run(workbooks, mini_release, out, *steps, widget)
    again = run(workbooks, mini_release, out, widget, *reversed(steps))

    assert ids(done) == ["mine:upper-glo", "mine:sole-glo", "mine:shoe-glo", "mine:widget-glo"]
    assert sorted(ids(again)) == sorted(ids(done))
    assert any(
        "mine:sole-glo" in n.message and "reference product" in n.message for n in done.report.notes
    )


def test_twins_alike_in_product_too_are_numbered(
    workbooks: Workbooks, mini_release: Release, out: Path
) -> None:
    twins = [block("assembly", location="GLO", unit="unit", product="upper") for _ in range(2)]

    done = run(workbooks, mini_release, out, *twins)

    assert ids(done) == ["mine:upper-glo", "mine:upper-glo-2"]


def test_an_activity_of_its_own_name_keeps_its_id_next_to_steps_that_make_it(
    workbooks: Workbooks, mini_release: Release, out: Path
) -> None:
    steps = [block("assembly", unit="unit", product="widget") for _ in range(2)]
    widget = block("widget", line("market for steel", 1, product="steel"))

    done = run(workbooks, mini_release, out, *steps, widget)

    assert ids(done) == ["mine:widget-glo-2", "mine:widget-glo-3", "mine:widget-glo"]
