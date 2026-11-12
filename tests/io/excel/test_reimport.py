"""Importing again into the same folder: the loop of import, read the report, decide in the
mapping file, import again. And what a second import does to files a person has changed."""

import json
from pathlib import Path

import pytest

import headwater as hw
from headwater.io.excel import Status, import_excel
from headwater.io.excel.write import MANIFEST
from headwater.model import InvalidDataError
from headwater.release import Release
from headwater.study import read_database
from tests.conftest import only_process, open_database

from .conftest import Invoke, Sheet, Workbooks, block, line, sheet, with_cell

REL = 1e-9


def renamed_tshirt() -> Sheet:
    """The toy T-shirt whose electricity line says RER, which the release does not have."""
    return with_cell(sheet("tshirt"), "electricity production", 3, "RER")


def decide(folder: Path) -> Path:
    path = folder.parent / "mapping.csv"
    path.write_text(
        "type,name,reference_product,location,target,who,when,comment\n"
        "technosphere,electricity production,electricity,RER,toy:electricity-production,leo,"
        "2026-10-12,the GLO mix stands for RER\n"
    )
    return path


def files(folder: Path) -> dict[str, bytes]:
    return {
        p.relative_to(folder).as_posix(): p.read_bytes() for p in folder.rglob("*") if p.is_file()
    }


def test_the_lines_a_mapping_decides_fill_in_when_the_workbook_is_imported_again(
    workbooks: Workbooks, toy_release: Release, out: Path
) -> None:
    book = workbooks.write({"inventory": renamed_tshirt()})
    first = import_excel(book, toy_release, out)
    assert not first.replaced and not first.clean
    (unmapped,) = [o for o in first.report.outcomes if o.status is Status.unmapped]
    assert unmapped.code == "not-in-release"
    assert first.validation is not None
    assert [p.code for p in first.validation.errors] == ["supplier-missing"]
    waiting = next(
        e
        for e in read_database(out).processes[0].exchanges
        if e.supplier_id is None and e.role.value == "input"
    )
    # The line says what the workbook said, so the mapping row can be written from it.
    assert "electricity production | electricity | RER, 0.5 kilowatt hour" in (
        waiting.comment or ""
    )
    assert waiting.flow_name == "electricity"
    assert "UNMAPPED" not in (waiting.flow_name or "")

    second = import_excel(book, toy_release, out, mapping=decide(out))

    assert second.replaced and second.clean
    assert [e.supplier_id for e in read_database(out).processes[0].exchanges][2] == (
        "toy:electricity-production"
    )
    assert "unmapped" not in (out / "flows.csv").read_text()
    database = open_database(out)
    score = database.calc(hw.load(toy_release.path), only_process(database)).score
    assert score == pytest.approx(1.25205, rel=REL)
    # Importing again is the same as importing once with the mapping.
    import_excel(book, toy_release, out.parent / "fresh", mapping=decide(out))
    assert files(out) == files(out.parent / "fresh")


def test_a_file_that_a_person_edited_stops_the_import_and_nothing_is_written(
    workbooks: Workbooks, toy_release: Release, out: Path
) -> None:
    book = workbooks.write()
    import_excel(book, toy_release, out)
    edited = out / "processes" / "t-shirt-glo.csv"
    edited.write_text(edited.read_text().replace("0.5,kWh", "0.6,kWh"))
    before = files(out)
    with pytest.raises(InvalidDataError, match="processes/t-shirt-glo.csv") as error:
        import_excel(book, toy_release, out)
    assert [p.code for p in error.value.problems] == ["folder-edited"]
    assert "overwrite the edit" in str(error.value)
    assert files(out) == before  # the edit, and every other file, is as it was


def test_files_a_person_added_are_never_touched(
    workbooks: Workbooks, toy_release: Release, out: Path
) -> None:
    book = workbooks.write()
    import_excel(book, toy_release, out)
    (out / "cut_offs.csv").write_text("process,line,flow,criterion,share,reason,who,when\n")
    (out / "NOTES.md").write_text("mine")
    import_excel(book, toy_release, out)
    assert (out / "cut_offs.csv").read_text().startswith("process,line")
    assert (out / "NOTES.md").read_text() == "mine"


def test_a_process_the_workbook_no_longer_has_goes_with_its_files(
    workbooks: Workbooks, toy_release: Release, out: Path
) -> None:
    two = workbooks.write(
        {
            "a": [
                *sheet("tshirt"),
                [],
                *block(
                    "extra",
                    line(
                        "cotton fabric production",
                        1,
                        "kilogram",
                        "GLO",
                        product="cotton fabric",
                        database="toy-background",
                    ),
                    own="toy-foreground",
                ),
            ]
        }
    )
    import_excel(two, toy_release, out)
    assert (out / "processes" / "extra-glo.csv").is_file()
    import_excel(workbooks.write(), toy_release, out)
    assert not (out / "processes" / "extra-glo.csv").exists()
    assert not (out / "processes" / "extra-glo.yaml").exists()
    assert (out / "processes" / "t-shirt-glo.csv").is_file()


def test_a_file_that_was_deleted_is_written_again(
    workbooks: Workbooks, toy_release: Release, out: Path
) -> None:
    book = workbooks.write()
    import_excel(book, toy_release, out)
    (out / "parameters.yaml").unlink()
    done = import_excel(book, toy_release, out)
    assert done.replaced and (out / "parameters.yaml").is_file()


def test_a_folder_with_files_and_no_record_of_an_import_is_refused(
    workbook: Path, toy_release: Release, out: Path
) -> None:
    out.mkdir()
    (out / "study.yaml").write_text("name: mine\n")
    with pytest.raises(InvalidDataError, match="no import wrote"):
        import_excel(workbook, toy_release, out)
    assert (out / "study.yaml").read_text() == "name: mine\n"


def test_a_record_that_cannot_be_read_is_not_a_record(
    workbook: Path, toy_release: Release, out: Path
) -> None:
    import_excel(workbook, toy_release, out)
    (out / MANIFEST).write_text("{not json")
    with pytest.raises(InvalidDataError, match="no import wrote"):
        import_excel(workbook, toy_release, out)


def test_the_record_lists_each_file_with_its_hash_and_what_was_written_in_database_yaml(
    workbook: Path, toy_release: Release, out: Path
) -> None:
    import_excel(workbook, toy_release, out)
    record = json.loads((out / MANIFEST).read_text())
    assert record["format"] == "headwater-excel-import/1"
    assert sorted(record["files"]) == [
        "flows.csv",
        "parameters.yaml",
        "processes/t-shirt-glo.csv",
        "processes/t-shirt-glo.yaml",
    ]  # database.yaml is a person's to edit, so it has no hash
    assert all(len(digest) == 64 for digest in record["files"].values())
    assert record["study"]["name"] == "toy-foreground"
    assert record["study"]["methods"] == ["toy-climate"]
    assert record["notes"] == []  # every line linked by name, product and location


def test_a_note_on_how_a_line_linked_goes_to_the_record_and_the_comment_stays_the_workbooks(
    workbooks: Workbooks, toy_release: Release, out: Path
) -> None:
    rows = with_cell(sheet("tshirt"), "cotton fabric production", 6, None)  # no product
    book = workbooks.write({"inventory": rows})

    first = import_excel(book, toy_release, out)

    fabric = read_database(out).processes[0].exchanges[1]
    assert fabric.supplier_id == "toy:fabric-production"
    assert fabric.comment == "Fabric in the bill of materials"
    record = json.loads((out / MANIFEST).read_text())
    assert record["format"] == "headwater-excel-import/1"
    assert record["notes"] == [
        {
            "process": "toy-foreground:t-shirt-glo",
            "line": "e02",
            "supplier": "toy:fabric-production",
            "note": "linked without the workbook's reference product: the only process that fits",
        }
    ]
    # Importing the same workbook again changes no id and no file.
    before = files(out)
    second = import_excel(book, toy_release, out)
    assert second.replaced
    assert files(out) == before
    assert [p.id for p in first.conversion.processes] == ["toy-foreground:t-shirt-glo"]
    assert [p.id for p in second.conversion.processes] == ["toy-foreground:t-shirt-glo"]


def test_a_record_without_notes_from_an_earlier_import_still_reads(
    workbook: Path, toy_release: Release, out: Path
) -> None:
    import_excel(workbook, toy_release, out)
    record = json.loads((out / MANIFEST).read_text())
    del record["notes"]
    (out / MANIFEST).write_text(json.dumps(record))

    assert import_excel(workbook, toy_release, out).replaced


def test_the_loop_from_the_command_line(
    workbooks: Workbooks, toy_release: Release, home: Path, out: Path, invoke: Invoke
) -> None:
    book = workbooks.write({"inventory": renamed_tshirt()})
    pin = f"toy-background@{toy_release.hash[:12]}"
    code, output = invoke("database", "import-excel", book, "--release", pin, "--out", out)
    assert code == 1 and "replacing" not in output
    code, output = invoke(
        "database", "import-excel", book, "--release", pin, "--out", out, "--mapping", decide(out)
    )
    assert code == 0, output
    assert "(replacing the files of an earlier import)" in output
    assert "Validation: 0 errors, 0 warnings." in output
    edited = out / "flows.csv"
    edited.write_text(edited.read_text() + "x:y,Mine,product,kg\n")
    code, output = invoke(
        "database", "import-excel", book, "--release", pin, "--out", out, "--mapping", decide(out)
    )
    assert code == 2 and "flows.csv" in output and "was edited" in output
