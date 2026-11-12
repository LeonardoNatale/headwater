"""Importing again keeps what a person wrote in database.yaml.

The import owns the process files and sets `name`, `background` and `uses` in database.yaml every
time. `methods` follows the import only while the person has not changed it. Anything else a
person adds, a `description` or a comment, stays as it is, so the loop (import, edit
database.yaml, decide in the mapping file, import again) never loses an edit.
"""

import json
from pathlib import Path

import pytest
import yaml

import headwater as hw
from headwater.io.excel import ExcelImport, import_excel
from headwater.io.excel.write import MANIFEST
from headwater.model import InvalidDataError
from headwater.release import Release
from tests.conftest import only_process, open_database

from .conftest import Invoke, Workbooks, block, database, line, sheet, with_cell

REL = 1e-9

MINE = "description: One T-shirt, for internal use  # what it is\n# a note of mine\n"


def edit(folder: Path, extra: str = MINE) -> str:
    """What a person does after the first import: add a field and a comment of their own.
    Returns the file as they leave it."""
    path = folder / "database.yaml"
    text = path.read_text() + extra
    path.write_text(text)
    return text


def values(folder: Path) -> dict[str, object]:
    return yaml.safe_load((folder / "database.yaml").read_text())


def decide(folder: Path) -> Path:
    path = folder.parent / "mapping.csv"
    path.write_text(
        "type,name,reference_product,location,target,who,when,comment\n"
        "technosphere,electricity production,electricity,RER,toy:electricity-production,leo,"
        "2026-10-12,the GLO mix stands for RER\n"
    )
    return path


def notes(done: ExcelImport) -> list[str]:
    return [n.message for n in done.report.notes]


def test_what_a_person_wrote_survives_the_loop_and_the_file_is_not_rewritten(
    workbooks: Workbooks, toy_release: Release, out: Path
) -> None:
    book = workbooks.write(
        {"inventory": with_cell(sheet("tshirt"), "electricity production", 3, "RER")}
    )
    first = import_excel(book, toy_release, out)
    assert not first.clean
    mine = edit(out)

    second = import_excel(book, toy_release, out, mapping=decide(out))

    assert second.replaced and second.clean
    assert (out / "database.yaml").read_text() == mine  # nothing it owns changed: not one byte
    assert second.validation is not None and second.validation.problems == ()
    opened = open_database(out)
    score = opened.calc(hw.load(toy_release.path), only_process(opened)).score
    assert score == pytest.approx(1.25205, rel=REL)


def test_the_database_after_the_loop_is_the_one_a_single_import_gives_for_the_fields_it_owns(
    workbooks: Workbooks, toy_release: Release, out: Path
) -> None:
    book = workbooks.write(
        {"inventory": with_cell(sheet("tshirt"), "electricity production", 3, "RER")}
    )
    import_excel(book, toy_release, out)
    edit(out)
    import_excel(book, toy_release, out, mapping=decide(out))
    import_excel(book, toy_release, out.parent / "fresh", mapping=decide(out))

    def processes(folder: Path) -> dict[str, bytes]:
        return {
            p.relative_to(folder).as_posix(): p.read_bytes()
            for p in folder.rglob("*")
            if p.is_file() and p.name not in ("database.yaml", MANIFEST)
        }

    assert processes(out) == processes(out.parent / "fresh")
    kept, fresh = values(out), values(out.parent / "fresh")
    for field in ("name", "background", "methods"):
        assert kept[field] == fresh[field]
    assert "uses" not in kept and "uses" not in fresh


def test_a_field_a_person_wrote_is_kept_however_often_the_workbook_is_imported(
    workbook: Path, toy_release: Release, out: Path
) -> None:
    import_excel(workbook, toy_release, out)
    mine = edit(out)
    for _ in range(3):  # what the import wrote is what it compares with, not what it kept
        import_excel(workbook, toy_release, out)
        assert (out / "database.yaml").read_text() == mine


def test_the_same_workbook_twice_gives_the_same_bytes(
    workbook: Path, toy_release: Release, out: Path
) -> None:
    import_excel(workbook, toy_release, out)
    once = {p.name: p.read_bytes() for p in out.rglob("*") if p.is_file()}

    import_excel(workbook, toy_release, out)

    assert {p.name: p.read_bytes() for p in out.rglob("*") if p.is_file()} == once


def test_name_background_and_uses_are_the_imports_whatever_the_file_says(
    workbook: Path, toy_release: Release, out: Path
) -> None:
    import_excel(workbook, toy_release, out)
    edit(out)
    path = out / "database.yaml"
    pin = f"toy-background@{toy_release.hash[:12]}"
    path.write_text(
        path.read_text()
        .replace(f"background: {pin}", "background: toy-background@000000000000")
        .replace("name: toy-foreground", "name: other")
        + "uses: [dyes]\n"
    )

    import_excel(workbook, toy_release, out)

    assert values(out)["background"] == pin
    assert values(out)["name"] == "toy-foreground"
    assert "uses" not in values(out)  # the workbook buys from no other database
    assert values(out)["description"] == "One T-shirt, for internal use"


def test_when_only_one_line_changes_the_rest_of_the_file_and_its_comments_stay(
    workbook: Path, toy_release: Release, toy_release_with_places: Release, out: Path
) -> None:
    import_excel(workbook, toy_release, out)
    mine = edit(out)

    import_excel(workbook, toy_release_with_places, out)  # the same toy, frozen with places

    old = f"background: toy-background@{toy_release.hash[:12]}\n"
    new = f"background: toy-background@{toy_release_with_places.hash[:12]}\n"
    assert old in mine and toy_release.hash != toy_release_with_places.hash
    assert (out / "database.yaml").read_text() == mine.replace(old, new)


def _chemicals(folder: Path, release: Release, write_study) -> None:
    """The database `textile-chemicals`, made by hand next to where the workbook is imported."""
    write_study(
        folder,
        {
            "reactive-dye": "e01,reference,textile-chemicals:dye,1,kg,\n"
            "e02,input,mini:heat,1,MJ,mini:heat-glo",
        },
        flows=["textile-chemicals:dye,dye,product,kg,,"],
        background=f"{release.name}@{release.hash[:12]}",
    )
    text = (folder / "database.yaml").read_text().replace("toy-climate", "mini-climate")
    (folder / "database.yaml").write_text(text)
    (folder / "processes" / "reactive-dye.yaml").write_text(
        "name: reactive dye\nlocation: GLO\nkind: ordinary_transforming_activity\n"
    )


def test_uses_follows_the_workbook_and_is_taken_out_when_the_database_no_longer_uses_another(
    workbooks: Workbooks, mini_release: Release, out: Path, tmp_path: Path, write_study
) -> None:
    _chemicals(out.parent / "textile-chemicals", mini_release, write_study)
    mapping = tmp_path / "mapping.csv"
    mapping.write_text(
        "type,name,reference_product,location,categories,target,who,when,comment\n"
        "database,Textile Chemicals,,,,textile-chemicals,leo,2026-10-12,shared\n"
    )
    dye = line("reactive dye", 0.1, "kilogram", "GLO", database="Textile Chemicals", product="dye")
    buys = workbooks.write({"a": [*database(), *block("widget", dye)]})
    buys_nothing = workbooks.write({"a": [*database(), *block("widget")]})

    import_excel(buys, mini_release, out, mapping=mapping)
    assert values(out)["uses"] == ["textile-chemicals"]
    mine = edit(out)

    import_excel(buys_nothing, mini_release, out, mapping=mapping)

    assert "uses" not in values(out)
    assert values(out)["description"] == "One T-shirt, for internal use"
    assert (out / "database.yaml").read_text() == mine.replace(
        "uses: [textile-chemicals]  # databases next to this one that it buys from\n", ""
    )


def test_methods_a_person_wrote_are_kept_and_the_report_says_the_option_was_not_applied(
    workbook: Path, toy_release: Release, out: Path
) -> None:
    import_excel(workbook, toy_release, out)
    path = out / "database.yaml"
    path.write_text(
        path.read_text().replace("methods: [toy-climate]", "methods: [toy-climate, extra]")
    )

    done = import_excel(workbook, toy_release, out, method="toy-climate")

    assert values(out)["methods"] == ["toy-climate", "extra"]
    assert (
        "database.yaml keeps `methods: [toy-climate, extra]`, which a person wrote. This import "
        "would have written `methods: [toy-climate]`."
    ) in notes(done)
    assert done.validation is not None
    assert "method-mismatch" in [p.code for p in done.validation.errors]  # validate says so too


def test_a_database_yaml_that_was_deleted_is_written_again(
    workbook: Path, toy_release: Release, out: Path
) -> None:
    import_excel(workbook, toy_release, out)
    (out / "database.yaml").unlink()

    import_excel(workbook, toy_release, out)

    assert values(out)["name"] == "toy-foreground"


def test_a_database_yaml_that_is_not_yaml_stops_the_import_before_anything_is_written(
    workbooks: Workbooks, toy_release: Release, out: Path
) -> None:
    book = workbooks.write(
        {"inventory": with_cell(sheet("tshirt"), "electricity production", 3, "RER")}
    )
    import_excel(book, toy_release, out)
    (out / "database.yaml").write_text("name: [unclosed\n")
    before = {p: p.read_bytes() for p in out.rglob("*") if p.is_file()}

    with pytest.raises(InvalidDataError) as error:
        import_excel(book, toy_release, out, mapping=decide(out))

    assert [p.code for p in error.value.problems] == ["bad-yaml"]
    assert error.value.problems[0].location is not None
    assert error.value.problems[0].location.startswith("database.yaml:")
    assert {p: p.read_bytes() for p in out.rglob("*") if p.is_file()} == before


def test_the_command_line_reports_what_it_kept_and_exits_2_for_a_database_yaml_that_is_not_yaml(
    workbook: Path, toy_release: Release, home: Path, out: Path, invoke: Invoke
) -> None:
    pin = f"toy-background@{toy_release.hash[:12]}"
    invoke("database", "import-excel", workbook, "--release", pin, "--out", out)
    path = out / "database.yaml"
    path.write_text(
        path.read_text().replace("methods: [toy-climate]", "methods: [toy-climate, extra]")
    )
    code, output = invoke("database", "import-excel", workbook, "--release", pin, "--out", out)
    assert code == 1
    assert "database.yaml keeps `methods: [toy-climate, extra]`, which a person wrote" in output

    path.write_text("name: [unclosed\n")
    code, output = invoke("database", "import-excel", workbook, "--release", pin, "--out", out)
    assert code == 2 and "database.yaml" in output


def test_a_bom_and_windows_line_ends_in_database_yaml_are_left_as_they_are(
    workbook: Path, toy_release: Release, out: Path
) -> None:
    import_excel(workbook, toy_release, out)
    mine = edit(out).replace("\n", "\r\n")
    (out / "database.yaml").write_bytes(b"\xef\xbb\xbf" + mine.encode("utf-8"))

    import_excel(workbook, toy_release, out)

    assert (out / "database.yaml").read_bytes() == b"\xef\xbb\xbf" + mine.encode("utf-8")


def test_a_list_written_over_several_lines_is_taken_out_whole(
    workbook: Path, toy_release: Release, out: Path
) -> None:
    import_excel(workbook, toy_release, out)
    edit(out, "uses:\n  - dyes\n  - paints\ndescription: Me\n")
    path = out / "database.yaml"

    import_excel(workbook, toy_release, out)

    assert "uses" not in values(out)
    assert values(out)["description"] == "Me"
    assert "dyes" not in path.read_text() and "paints" not in path.read_text()


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("background:", '"background":'),
        ("uses: [dyes]\n", "uses:\n  - dyes\n# - old\n  - paints\n"),
    ],
    ids=["a key in quotes", "a comment at the margin inside a list"],
)
def test_a_layout_the_import_cannot_follow_stops_it_and_writes_nothing(
    workbook: Path,
    toy_release: Release,
    toy_release_with_places: Release,
    out: Path,
    old: str,
    new: str,
) -> None:
    import_excel(workbook, toy_release, out)
    path = out / "database.yaml"
    path.write_text((path.read_text() + "uses: [dyes]\n").replace(old, new))
    before = {p: p.read_bytes() for p in out.rglob("*") if p.is_file()}

    with pytest.raises(InvalidDataError) as error:  # the background and uses must change
        import_excel(workbook, toy_release_with_places, out)

    assert [p.code for p in error.value.problems] == ["database-yaml-layout"]
    assert "`field: value` at the start of a line" in str(error.value)
    assert {p: p.read_bytes() for p in out.rglob("*") if p.is_file()} == before


def test_a_person_who_edits_database_yaml_while_the_import_runs_loses_nothing(
    workbook: Path, toy_release: Release, out: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import_excel(workbook, toy_release, out)
    edit(out)
    import headwater.io.excel.importer as importer

    real = importer.convert

    def slow(*args: object, **kwargs: object):  # noqa: ANN202
        found = real(*args, **kwargs)  # type: ignore[arg-type]
        path = out / "database.yaml"  # the person saves while the import is still working
        path.write_text(path.read_text() + "# saved while it ran\n")
        return found

    monkeypatch.setattr(importer, "convert", slow)

    import_excel(workbook, toy_release, out)

    assert values(out)["description"] == "One T-shirt, for internal use"
    assert "# saved while it ran\n" in (out / "database.yaml").read_text()


def test_a_record_with_no_memory_never_takes_back_a_value_a_person_wrote(
    workbook: Path, toy_release: Release, out: Path
) -> None:
    import_excel(workbook, toy_release, out)
    record = json.loads((out / MANIFEST).read_text())
    del record["study"]
    (out / MANIFEST).write_text(json.dumps(record))
    path = out / "database.yaml"
    path.write_text(path.read_text().replace("methods: [toy-climate]", "methods: [other]"))

    import_excel(workbook, toy_release, out)

    assert values(out)["methods"] == ["other"]


def test_methods_the_import_wrote_are_taken_out_when_it_is_no_longer_given_any() -> None:
    """The import wrote `methods`, nobody changed it, and the next import has no --method: the
    field follows the import and goes. A person's own field stays."""
    from headwater.io.excel.convert import DatabaseDraft
    from headwater.io.excel.database_yaml import merge

    given = DatabaseDraft("shirt", "toy-background@1234567890ab", ("toy-climate",), ())
    first = merge(given, "inventory.xlsx")
    text = first.text + "description: Shirts, per unit\n"
    values = yaml.safe_load(text)

    again = merge(
        DatabaseDraft("shirt", "toy-background@1234567890ab", (), ()),
        "inventory.xlsx",
        text=text,
        values=values,
        written=first.written,
    )

    assert yaml.safe_load(again.text) == {
        "name": "shirt",
        "background": "toy-background@1234567890ab",
        "description": "Shirts, per unit",
    }
