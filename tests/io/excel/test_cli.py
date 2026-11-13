"""`headwater database import-excel`, from the command line."""

from pathlib import Path

import pytest

from headwater.release import Release

from .conftest import TOY, Invoke, Sheet, Workbooks, row_of, sheet, with_cell

PIN = "toy-background@{}"


def ref(release: Release) -> str:
    return PIN.format(release.hash[:12])


def test_a_clean_import_exits_zero_and_the_database_calculates(
    workbook: Path, toy_release: Release, home: Path, out: Path, invoke: Invoke
) -> None:
    code, output = invoke(
        "database", "import-excel", workbook, "--release", ref(toy_release), "--out", out
    )
    assert code == 0, output
    assert "mapped" in output and "unmapped" in output
    assert "Wrote database toy-foreground" in output
    assert "Linked to release toy-background@" in output and "Database toy-foreground." in output
    # a database holds no question: nothing to fill in, and no word of a study (D46)
    for word in ("TODO", "study", "Study", "goal", "functional unit"):
        assert word not in output, word
    assert "Validation: 0 errors, 0 warnings." in output
    code, calc = invoke("calc", out)  # the database pins the release it was linked to
    assert code == 0, calc
    assert "1.25205" in calc


def test_the_validation_count_is_what_the_import_lists(
    workbook: Path, toy_release: Release, home: Path, out: Path, invoke: Invoke
) -> None:
    code, output = invoke(
        "database", "import-excel", workbook, "--release", ref(toy_release), "--out", out
    )

    assert code == 0, output
    tail = output.split("Validation: ")[1].splitlines()
    assert tail[0] == "0 errors, 0 warnings."  # nothing is listed above
    assert not [line for line in tail[1:] if line.startswith("  warning: ")]


def test_a_method_folder_the_release_does_not_hold_is_named_for_the_next_commands(
    workbook: Path,
    toy_release: Release,
    home: Path,
    out: Path,
    invoke: Invoke,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("HEADWATER_METHOD_FOLDERS", raising=False)
    impacts = TOY / "methods" / "toy-impacts"  # the release holds toy-climate only
    code, output = invoke(
        "database", "import-excel", workbook, "--release", ref(toy_release), "--out", out,
        "--method", impacts,
    )  # fmt: skip

    assert code == 0, output
    assert f"--method-folder {impacts}" in output
    code, validated = invoke("validate", out, "--method-folder", impacts)
    assert code == 0, validated
    code, _ = invoke("validate", out)
    assert code == 1


def test_the_report_says_where_a_line_is_why_it_failed_and_what_it_might_be(
    workbooks: Workbooks, toy_release: Release, home: Path, out: Path, invoke: Invoke
) -> None:
    cases = sheet("cases")
    book = workbooks.write({"inventory": sheet("tshirt"), "cases": cases})
    code, output = invoke(
        "database", "import-excel", book, "--release", ref(toy_release), "--out", out
    )
    assert code == 1
    row = row_of(cases, "case: wrong location")
    assert (
        "- [not-in-release] electricity production | electricity | RER  (1 line, in Cases)"
        in output
    )
    assert f"at cases!A{row}" in output
    assert (
        "maybe: toy:electricity-production (electricity production | electricity | GLO)" in output
    )
    assert "[no-categories]" in output and "[formula-unsupported]" in output
    assert "TODO" not in output
    assert (out / "database.yaml").is_file()  # still written, for a person to fix


def test_lines_that_say_the_same_are_reported_once_with_all_their_places(
    workbooks: Workbooks, toy_release: Release, home: Path, out: Path, invoke: Invoke
) -> None:
    rows = sheet("tshirt")
    rows[16][3] = "RER"  # electricity production, in the T-shirt
    again: Sheet = [
        ["Activity", "Again"],
        ["reference product", "again"],
        ["location", "GLO"],
        ["unit", "unit"],
        ["Exchanges"],
        rows[13],
        ["Again", 1, "unit", "GLO", "production", "toy-foreground", "again"],
        rows[16],
    ]
    book = workbooks.write({"inventory": rows, "again": again})
    code, output = invoke(
        "database", "import-excel", book, "--release", ref(toy_release), "--out", out
    )
    assert code == 1
    assert output.count("- [not-in-release]") == 1
    assert "(2 lines, in 2 activities)" in output
    assert "at inventory!A17, again!A8" in output
    assert "Not mapped: 2 lines, 1 to decide" in output


def test_a_validation_error_that_is_not_an_unmapped_line_is_listed(
    workbooks: Workbooks, toy_release_with_places: Release, out: Path, invoke: Invoke
) -> None:
    rows: Sheet = [
        ["location", "Mars"] if row[:1] == ["location"] else row for row in sheet("tshirt")
    ]
    book = workbooks.write({"inventory": rows})
    code, output = invoke(
        "database", "import-excel", book, "--release", toy_release_with_places.path, "--out", out
    )
    assert code == 1
    assert "Validation: 1 error, 0 warnings.\n  error: " in output
    assert "location 'Mars' is not a geography of the background" in output


def test_a_line_with_no_supplier_is_counted_in_validation_but_listed_only_in_the_report(
    workbooks: Workbooks, toy_release: Release, out: Path, invoke: Invoke
) -> None:
    rows = with_cell(sheet("tshirt"), "electricity production", 3, "RER")
    code, output = invoke(
        "database",
        "import-excel",
        workbooks.write({"inventory": rows}),
        "--release",
        toy_release.path,
        "--out",
        out,
    )
    assert code == 1
    assert "Validation: 1 error, 0 warnings. 1 of them is listed above." in output
    assert output.count("has no supplier") == 0


def test_a_mapping_file_turns_the_lines_it_decides_into_mapped_ones(
    workbooks: Workbooks,
    toy_release: Release,
    home: Path,
    out: Path,
    invoke: Invoke,
    tmp_path: Path,
) -> None:
    rows = with_cell(sheet("tshirt"), "electricity production", 3, "RER")
    book = workbooks.write({"inventory": rows})
    args = ["database", "import-excel", book, "--release", ref(toy_release)]
    code, output = invoke(*args, "--out", out)
    assert code == 1 and "[not-in-release]" in output
    mapping = tmp_path / "mapping.csv"
    mapping.write_text(
        "type,name,reference_product,location,target,who,when,comment\n"
        "technosphere,electricity production,electricity,RER,toy:electricity-production,leo,"
        "2026-10-12,"
        "the RER mix is the GLO one in this toy\n"
    )
    code, output = invoke(*args, "--out", tmp_path / "second", "--mapping", mapping)
    assert code == 0, output
    assert "mapping 1" in output
    study = (tmp_path / "second" / "exchanges.csv").read_text()
    assert "toy:electricity-production" in study


def test_a_mapping_row_that_no_line_needed_is_reported(
    workbook: Path, toy_release: Release, home: Path, out: Path, invoke: Invoke, tmp_path: Path
) -> None:
    mapping = tmp_path / "mapping.csv"
    mapping.write_text(
        "type,name,target,who,when\ndatabase,Textile Chemicals,textile-chemicals,leo,2026-10-12\n"
    )
    code, output = invoke(
        "database",
        "import-excel",
        workbook,
        "--release",
        ref(toy_release),
        "--out",
        out,
        "--mapping",
        mapping,
    )
    assert code == 0
    assert "Mapping rows that no line needed" in output and "mapping.csv:2" in output


def test_a_release_given_as_a_folder_works_too(
    workbook: Path, toy_release: Release, out: Path, invoke: Invoke
) -> None:
    code, output = invoke(
        "database", "import-excel", workbook, "--release", toy_release.path, "--out", out
    )
    assert code == 0, output


def test_a_method_folder_names_the_method_and_validates(
    workbook: Path, toy_release: Release, home: Path, out: Path, invoke: Invoke
) -> None:
    code, output = invoke(
        "database",
        "import-excel",
        workbook,
        "--release",
        ref(toy_release),
        "--out",
        out,
        "--method",
        TOY / "methods" / "toy-climate",
    )
    assert code == 0, output
    assert "methods: [toy-climate]" in (out / "database.yaml").read_text()


def test_several_methods_are_named_together_and_validate(
    workbook: Path, toy_release: Release, home: Path, out: Path, invoke: Invoke, tmp_path: Path
) -> None:
    """`--method` once per method, as for `calc`: database.yaml lists them all."""
    methane = tmp_path / "toy-methane"
    methane.mkdir()
    (methane / "method.yaml").write_text(
        'id: toy-methane\nversion: "1"\nflow_list: toy\nindicators:\n'
        "  - id: methane\n    name: Methane\n    unit: kg CH4\n"
    )
    (methane / "factors.csv").write_text("indicator,flow,location,factor\nmethane,toy:ch4,,1\n")
    code, output = invoke(
        "database",
        "import-excel",
        workbook,
        "--release",
        ref(toy_release),
        "--out",
        out,
        "--method",
        "toy-climate",
        "--method",
        methane,
    )
    assert code == 0, output
    assert "methods: [toy-climate, toy-methane]" in (out / "database.yaml").read_text()
    code, calc = invoke("calc", out, "--method", "toy-climate", "--method", methane)
    assert code == 0, calc


def test_the_study_name_can_be_given(
    workbook: Path, toy_release: Release, home: Path, out: Path, invoke: Invoke
) -> None:
    code, output = invoke(
        "database",
        "import-excel",
        workbook,
        "--release",
        ref(toy_release),
        "--out",
        out,
        "--name",
        "my-tshirt",
    )
    assert code == 0, output
    assert "name: my-tshirt" in (out / "database.yaml").read_text()
    assert (out / "processes.csv").read_text().splitlines()[1].startswith("t-shirt-glo,")


def test_a_folder_that_is_not_empty_stops_the_import_and_nothing_is_written(
    workbook: Path, toy_release: Release, home: Path, out: Path, invoke: Invoke
) -> None:
    out.mkdir()
    (out / "notes.txt").write_text("mine")
    code, output = invoke(
        "database", "import-excel", workbook, "--release", ref(toy_release), "--out", out
    )
    assert code == 2
    assert "already holds files" in output
    assert [p.name for p in out.iterdir()] == ["notes.txt"]


def test_an_unknown_release_stops_the_import(
    workbook: Path, home: Path, out: Path, invoke: Invoke
) -> None:
    release = "toy-background@000000000000"
    code, output = invoke("database", "import-excel", workbook, "--release", release, "--out", out)
    assert code == 2
    assert "not in" in output
    assert not out.exists()


def test_a_release_that_is_neither_a_folder_nor_a_name_is_refused(
    workbook: Path, home: Path, out: Path, invoke: Invoke
) -> None:
    code, output = invoke(
        "database", "import-excel", workbook, "--release", "nowhere", "--out", out
    )
    assert code == 2
    assert "neither a release folder" in output


def test_a_workbook_with_no_database_row_stops_the_import(
    workbooks: Workbooks, toy_release: Release, home: Path, out: Path, invoke: Invoke
) -> None:
    rows = [row for row in sheet("tshirt") if not row or row[0] != "Database"]
    code, output = invoke(
        "database",
        "import-excel",
        workbooks.write({"a": rows}),
        "--release",
        ref(toy_release),
        "--out",
        out,
    )
    assert code == 2
    assert "no Database row" in output
    assert not out.exists()


def test_a_method_that_does_not_exist_stops_the_import(
    workbook: Path, toy_release: Release, home: Path, out: Path, invoke: Invoke
) -> None:
    code, output = invoke(
        "database",
        "import-excel",
        workbook,
        "--release",
        ref(toy_release),
        "--out",
        out,
        "--method",
        "nope",
    )
    assert code == 2
    assert "neither the id of a method" in output
    assert not out.exists()


def test_the_command_is_listed_under_database(invoke: Invoke) -> None:
    code, output = invoke("database", "--help")
    assert code == 0
    assert "import-excel" in output


def test_a_method_for_another_flow_list_stops_the_import_and_leaves_no_folder(
    workbook: Path, toy_release: Release, home: Path, out: Path, invoke: Invoke, tmp_path: Path
) -> None:
    method = tmp_path / "other-method"
    method.mkdir()
    for name in ("method.yaml", "factors.csv"):
        text = (TOY / "methods" / "toy-climate" / name).read_text()
        (method / name).write_text(text.replace("flow_list: toy", "flow_list: ecoinvent-3.12"))
    code, output = invoke(
        "database",
        "import-excel",
        workbook,
        "--release",
        ref(toy_release),
        "--out",
        out,
        "--method",
        method,
    )
    assert code == 2
    assert "ecoinvent-3.12" in output
    assert not out.exists()  # exit code 2 means nothing was written
