"""The study folder an import writes: what is in it, that it reads back, that nothing is
overwritten, and that the same workbook gives the same files."""

from pathlib import Path

import pytest

from headwater.io.excel import import_excel
from headwater.model import InvalidDataError
from headwater.release import Release, build
from headwater.study import read_database, read_process_folder

from .conftest import Sheet, Workbooks, block, database, line, sheet


def files(folder: Path) -> list[str]:
    return sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file())


def test_the_toy_tshirt_is_written_as_a_database_folder(
    workbook: Path, toy_release: Release, out: Path
) -> None:
    import_excel(workbook, toy_release, out)
    assert files(out) == [
        ".headwater-import.json",
        "database.yaml",
        "flows.csv",
        "parameters.yaml",
        "processes/t-shirt-glo.csv",
        "processes/t-shirt-glo.yaml",
    ]
    assert (out / "processes" / "t-shirt-glo.csv").read_text() == (
        "line,role,flow,amount,unit,supplier,flow_name,comment\n"
        "e01,reference,toy-foreground:t-shirt-glo,1,unit,,T-shirt,\n"
        "e02,input,toy:cotton-fabric,fabric_kg,kg,toy:fabric-production,cotton fabric,"
        "Fabric in the bill of materials\n"
        "e03,input,toy:electricity,0.5,kWh,toy:electricity-production,electricity,\n"
        "e04,waste_to_treatment,toy:textile-waste,0.05,kg,toy:incineration-textile-waste,"
        "textile waste,Scraps sent to incineration\n"
    )
    assert (out / "processes" / "t-shirt-glo.yaml").read_text() == (
        "# One T-shirt\n"
        "# Read from inventory!A7 of inventory 1.xlsx.\n"
        "name: T-shirt\n"
        "location: GLO\n"
        "kind: ordinary_transforming_activity\n"
    )
    assert (out / "flows.csv").read_text() == (
        "id,name,kind,unit,compartment,cas\ntoy-foreground:t-shirt-glo,T-shirt,product,unit,,\n"
    )
    assert (out / "parameters.yaml").read_text() == (
        "database:\n  fabric_kg:\n    value: 0.25\n    comment: Fabric in one T-shirt\n"
    )


def test_database_yaml_has_the_name_the_pin_and_the_method(
    workbook: Path, toy_release: Release, out: Path
) -> None:
    import_excel(workbook, toy_release, out)
    text = (out / "database.yaml").read_text()
    pin = f"toy-background@{toy_release.hash[:12]}"
    assert text == (
        "# Imported by headwater from inventory 1.xlsx (Brightway Excel format).\n"
        "name: toy-foreground\n"
        f"background: {pin}\n"
        "methods: [toy-climate]\n"
    )
    database = read_database(out).database
    assert (database.name, str(database.background), database.methods) == (
        "toy-foreground",
        pin,
        ("toy-climate",),
    )


def test_a_database_made_without_a_method_says_so_and_is_not_validated(
    workbooks: Workbooks, toy_release: Release, out: Path, tmp_path: Path
) -> None:
    folder = read_process_folder(
        Path(__file__).parents[2] / "fixtures" / "toy" / "background", namespace="toy"
    )
    bare = build(
        toy_release.info,
        processes=folder.processes,
        flows=folder.flows,
        into=tmp_path / "bare",
    )
    done = import_excel(workbooks.write(), bare, out)
    assert done.validation is None
    assert "holds no method" in (done.validation_skipped or "")
    assert "methods" not in (out / "database.yaml").read_text()
    assert not done.clean


def test_the_same_workbook_gives_the_same_files(
    workbook: Path, toy_release: Release, tmp_path: Path
) -> None:
    import_excel(workbook, toy_release, tmp_path / "one")
    import_excel(workbook, toy_release, tmp_path / "two")
    assert files(tmp_path / "one") == files(tmp_path / "two")
    for name in files(tmp_path / "one"):
        assert (tmp_path / "one" / name).read_bytes() == (tmp_path / "two" / name).read_bytes()


def test_a_folder_that_is_not_empty_is_never_written_into(
    workbook: Path, toy_release: Release, out: Path
) -> None:
    out.mkdir()
    (out / "mine.txt").write_text("keep")
    with pytest.raises(InvalidDataError, match="never overwrites"):
        import_excel(workbook, toy_release, out)
    assert files(out) == ["mine.txt"]


def test_an_empty_folder_is_written_into_and_missing_parents_are_made(
    workbook: Path, toy_release: Release, tmp_path: Path
) -> None:
    empty = tmp_path / "studies" / "deep" / "tshirt"
    empty.mkdir(parents=True)
    import_excel(workbook, toy_release, empty)
    assert (empty / "database.yaml").is_file()
    import_excel(workbook, toy_release, tmp_path / "other" / "deeper")
    assert (tmp_path / "other" / "deeper" / "database.yaml").is_file()


def test_a_workbook_or_mapping_that_is_refused_leaves_no_folder_and_no_scraps(
    workbooks: Workbooks, toy_release: Release, tmp_path: Path
) -> None:
    out = tmp_path / "parent" / "study"
    no_database = workbooks.write({"a": block("widget")})
    with pytest.raises(InvalidDataError):
        import_excel(no_database, toy_release, out)
    bad_mapping = tmp_path / "mapping.csv"
    bad_mapping.write_text("type,name\n")
    with pytest.raises(InvalidDataError):
        import_excel(workbooks.write(), toy_release, out, mapping=bad_mapping)
    assert not out.exists()
    assert not (tmp_path / "parent").exists() or files(tmp_path / "parent") == []


def test_names_with_colons_quotes_and_codes_that_yaml_reads_as_yes_or_no_come_back_unchanged(
    workbooks: Workbooks, mini_release: Release, out: Path
) -> None:
    rows: Sheet = [
        *database(),
        *block(
            'dye: "red", 50% solution',
            line("market for sand", 1, "kilogram", "GLO", product="sand", comment="a, b\nc"),
            location="NO",
            comment="first line\n  second line",
        ),
    ]
    done = import_excel(workbooks.write({"a": rows}), mini_release, out)
    (process,) = read_database(out).processes
    assert process.name == 'dye: "red", 50% solution'
    assert process.location == "NO"  # a string, not the boolean YAML reads for NO
    assert process.exchanges[1].comment == "a, b c"  # one line: a row of a file is one line
    text = (out / "processes" / f"{process.id.split(':')[1]}.yaml").read_text()
    assert text.startswith("# first line\n# second line\n# Read from a!A3")
    assert done.validation is not None
    assert [p.code for p in done.validation.errors] == ["unknown-location"]  # 'NO' is not in mini


def test_file_names_are_short_plain_and_never_collide(
    workbooks: Workbooks, mini_release: Release, out: Path
) -> None:
    long = "a very long name " * 12
    rows: Sheet = [
        *database(),
        *block(long + "one"),
        *block(long + "two"),
        *block("Ünïcode & symbols!"),
        *block("!!!"),
    ]
    import_excel(workbooks.write({"a": rows}), mini_release, out)
    names = [n for n in files(out) if n.startswith("processes/") and n.endswith(".csv")]
    stems = [n.removeprefix("processes/").removesuffix(".csv") for n in names]
    assert len(set(stems)) == 4 and all(len(s) <= 100 for s in stems)
    assert "unicode-symbols-glo" in stems
    assert any(s.startswith("item-") and s.endswith("-glo") for s in stems)
    assert all(set(s) <= set("abcdefghijklmnopqrstuvwxyz0123456789-") for s in stems)


def test_the_flows_file_has_every_column_and_the_compartment_when_a_flow_has_one(
    workbooks: Workbooks, mini_release: Release, out: Path, tmp_path: Path
) -> None:
    plain = import_excel(workbooks.write({"a": [*database(), *block("widget")]}), mini_release, out)
    header = "id,name,kind,unit,compartment,cas"
    assert (plain.folder / "flows.csv").read_text().splitlines()[0] == header
    odd = block(
        "widget",
        line("Radon-222", 1, "kilogram", None, "biosphere", "b", categories="air::high stacks"),
    )
    done = import_excel(
        workbooks.write({"a": [*database(), *odd]}), mini_release, tmp_path / "second"
    )
    lines = (done.folder / "flows.csv").read_text().splitlines()
    assert lines[0] == header
    (radon,) = [text for text in lines if "radon" in text]
    assert radon.endswith(",elementary,kg,air/high stacks,")


def test_the_toy_workbook_through_the_cases_sheet_is_still_a_readable_study(
    workbooks: Workbooks, toy_release: Release, out: Path
) -> None:
    book = workbooks.write({"inventory": sheet("tshirt"), "cases": sheet("cases")})
    import_excel(book, toy_release, out)
    study = read_database(out)  # every line, mapped or not, is a record the study format reads
    assert sorted(p.name for p in study.processes) == ["Cases", "T-shirt"]


def test_a_method_that_does_not_fit_the_release_is_refused_before_anything_is_written(
    workbook: Path, toy_release: Release, out: Path, tmp_path: Path
) -> None:
    method = tmp_path / "other-method"
    method.mkdir()
    for name in ("method.yaml", "factors.csv"):
        text = (
            Path(__file__).parents[2] / "fixtures" / "toy" / "methods" / "toy-climate" / name
        ).read_text()
        (method / name).write_text(text.replace("flow_list: toy", "flow_list: ecoinvent-3.12"))
    with pytest.raises(InvalidDataError, match="flow-list-mismatch|flow list"):
        import_excel(workbook, toy_release, out, method=method)
    assert not out.exists()
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".tmp-")]
    import_excel(workbook, toy_release, out)  # so the same command can be run again
