"""Reading a Brightway-format workbook: the layout rules of bw2io's ExcelImporter."""

from pathlib import Path

import openpyxl
import pytest

from headwater.io.excel import read_workbook
from headwater.model import InvalidDataError

from .conftest import Cell, Sheet, Workbooks, sheet

HEADER: list[Cell] = ["name", "amount", "unit", "location", "type", "database", "reference product"]


def activity(name: str, *lines: list[object]) -> Sheet:
    return [
        ["Activity", name],
        ["production amount", 1],
        ["reference product", name],
        ["location", "GLO"],
        ["unit", "kilogram"],
        ["Exchanges"],
        HEADER,
        [name, 1, "kilogram", "GLO", "production", "db", name],
        *lines,  # type: ignore[list-item]
        [],
    ]


def test_the_toy_tshirt_sheet_is_read_as_written(workbook: Path) -> None:
    book = read_workbook(workbook)
    assert book.database == "toy-foreground"
    assert [p.name for p in book.project_parameters] == ["fabric_kg"]
    assert book.project_parameters[0].amount == 0.25
    (tshirt,) = book.activities
    assert tshirt.name == "T-shirt"
    assert tshirt.text("reference product") == "T-shirt"
    assert tshirt.text("unit") == "unit"
    assert tshirt.metadata["production amount"] == 1
    assert [e.name for e in tshirt.exchanges] == [
        "T-shirt",
        "cotton fabric production",
        "electricity production",
        "incineration of textile waste",
    ]
    fabric = tshirt.exchanges[1]
    assert (fabric.amount, fabric.unit, fabric.location) == (0.25, "kilogram", "GLO")
    assert (fabric.type, fabric.database, fabric.formula) == (
        "technosphere",
        "toy-background",
        "fabric_kg",
    )
    assert fabric.reference_product == "cotton fabric"
    assert tshirt.exchanges[3].amount == -0.05
    assert book.exchange_rows == 4
    assert book.parameter_rows == 1


def test_every_row_knows_where_it_was_read(workbook: Path) -> None:
    book = read_workbook(workbook)  # see tests/fixtures/excel/tshirt.csv for the rows
    assert book.activities[0].exchanges[3].source == "inventory!A18"
    assert book.activities[0].source == "inventory!A7"
    assert book.project_parameters[0].source == "inventory!A5"


def test_a_sheet_marked_skip_is_not_read_and_is_listed(workbooks: Workbooks) -> None:
    book = read_workbook(workbooks.write({"inventory": sheet("tshirt"), "notes": sheet("skipped")}))
    assert [a.name for a in book.activities] == ["T-shirt"]
    assert book.skipped_sheets == (("notes", "its first cell says skip"),)


def test_a_sheet_that_starts_with_a_number_is_not_read_and_is_listed(
    workbooks: Workbooks,
) -> None:
    book = read_workbook(
        workbooks.write({"inventory": sheet("tshirt"), "numbers": [[1, 2], [3, 4]]})
    )
    assert book.skipped_sheets == (("numbers", "its first cell is not text"),)


def test_a_sheet_with_a_cutoff_row_keeps_only_that_many_columns(workbooks: Workbooks) -> None:
    rows: Sheet = [
        ["cutoff", 6],
        *activity("widget", ["steel", 2, "kilogram", "GLO", "technosphere", "db", "steel"]),
    ]
    rows[8] += ["junk"] * 3  # beyond the sixth column
    book = read_workbook(workbooks.write({"a": [["Database", "db"]], "b": rows}))
    exchanges = book.activities[0].exchanges
    assert [e.name for e in exchanges] == ["widget", "steel"]
    assert exchanges[1].database == "db"
    assert exchanges[1].reference_product is None  # the seventh column was cut


def test_a_cutoff_that_is_not_a_number_is_an_error(workbooks: Workbooks) -> None:
    rows: Sheet = [["Database", "db"], [], ["cutoff", "many"]]
    with pytest.raises(InvalidDataError, match="cutoff"):
        read_workbook(workbooks.write({"a": [["cutoff", "many"], *rows]}))


def test_an_activity_ends_at_the_next_activity_not_at_a_blank_row(workbooks: Workbooks) -> None:
    rows: Sheet = [
        ["Database", "db"],
        [],
        *activity("one", [], ["steel", 2, "kilogram", "GLO", "technosphere", "db", "steel"], []),
        *activity("two"),
    ]
    book = read_workbook(workbooks.write({"a": rows}))
    assert [a.name for a in book.activities] == ["one", "two"]
    assert [e.name for e in book.activities[0].exchanges] == ["one", "steel"]


def test_labels_are_not_case_sensitive_and_their_spaces_are_ignored(workbooks: Workbooks) -> None:
    rows: Sheet = [
        [" DATABASE ", "db"],
        ["project PARAMETERS"],
        ["Name", "AMOUNT"],
        ["x", 1],
        [],
        ["activity", "one"],
        ["Reference Product", "one"],
        ["EXCHANGES"],
        ["NAME", "Amount", "Type"],
        ["one", 1, "Production"],
    ]
    book = read_workbook(workbooks.write({"a": rows}))
    assert book.database == "db"
    assert book.project_parameters[0].name == "x"
    assert book.activities[0].text("reference product") == "one"
    assert book.activities[0].exchanges[0].type == "production"


def test_parameters_of_the_project_the_database_and_an_activity(workbooks: Workbooks) -> None:
    rows: Sheet = [
        ["Database", "db"],
        [],
        ["Project parameters"],
        ["name", "amount", "formula", "comment"],
        ["loss", 0.05, None, "Scrap rate"],
        ["gross", 1.05, "1 + loss"],
        [],
        ["Database parameters"],
        ["name", "amount"],
        ["mill_kwh", 3],
        [],
        ["Activity", "one"],
        ["Parameters", "group"],
        ["name", "amount", "formula"],
        ["share", 0.5],
        ["Exchanges"],
        HEADER,
        ["one", 1, "kilogram", "GLO", "production", "db", "one"],
    ]
    book = read_workbook(workbooks.write({"a": rows}))
    assert [(p.name, p.amount, p.formula, p.comment) for p in book.project_parameters] == [
        ("loss", 0.05, None, "Scrap rate"),
        ("gross", 1.05, "1 + loss", None),
    ]
    assert [p.name for p in book.database_parameters] == ["mill_kwh"]
    (one,) = book.activities
    assert [(p.name, p.scope) for p in one.parameters] == [("share", "one")]
    assert book.parameter_rows == 4


def test_categories_are_split_on_double_colons(workbooks: Workbooks) -> None:
    rows: Sheet = [
        ["Database", "db"],
        ["Activity", "one"],
        ["Exchanges"],
        ["name", "amount", "type", "categories"],
        ["Methane", 0.1, "biosphere", "air::urban air close to ground"],
        ["Water", 2, "biosphere", "water"],
    ]
    book = read_workbook(workbooks.write({"a": rows}))
    methane, water = book.activities[0].exchanges
    assert methane.categories == ("air", "urban air close to ground")
    assert water.categories == ("water",)


def test_text_around_cells_is_stripped_and_blank_cells_are_none(workbooks: Workbooks) -> None:
    rows: Sheet = [
        ["Database", " db "],
        *activity("one", ["  steel ", 2, "kilogram", "  ", "technosphere", " db", "steel"]),
    ]
    book = read_workbook(workbooks.write({"a": rows}))
    steel = book.activities[0].exchanges[1]
    assert (steel.name, steel.location, steel.database) == ("steel", None, "db")
    assert book.database == "db"


def test_uncertainty_columns_are_noticed_and_unknown_columns_are_counted(
    workbooks: Workbooks,
) -> None:
    rows = sheet("cases")
    book = read_workbook(workbooks.write({"inventory": sheet("tshirt"), "cases": rows}))
    cases = next(a for a in book.activities if a.name == "Cases")
    assert [dict(e.uncertainty) for e in cases.exchanges if e.uncertainty] == [
        {"uncertainty type": 2, "scale": 0.1}
    ]
    assert book.ignored_columns == {"tag": 1}


def test_a_workbook_without_a_database_row_is_refused(workbooks: Workbooks) -> None:
    with pytest.raises(InvalidDataError, match="no Database row") as error:
        read_workbook(workbooks.write({"a": activity("one")}))
    assert error.value.problems[0].code == "no-database"


def test_two_database_rows_are_refused_with_their_places(workbooks: Workbooks) -> None:
    rows: Sheet = [["Database", "one"], [], ["Database", "two"]]
    with pytest.raises(InvalidDataError) as error:
        read_workbook(workbooks.write({"a": rows}))
    (problem,) = error.value.problems
    assert problem.code == "multiple-databases"
    assert "a!A1" in problem.message and "a!A3" in problem.message


def test_a_table_with_an_unlabelled_column_is_refused(workbooks: Workbooks) -> None:
    rows: Sheet = [
        ["Database", "db"],
        ["Activity", "one"],
        ["Exchanges"],
        ["name", None, "unit"],
        ["one", 1, "kilogram"],
    ]
    with pytest.raises(InvalidDataError) as error:
        read_workbook(workbooks.write({"a": rows}))
    assert [p.code for p in error.value.problems] == ["bad-header"]
    assert error.value.problems[0].location == "a!A4"


def test_a_column_written_twice_is_refused(workbooks: Workbooks) -> None:
    rows: Sheet = [
        ["Database", "db"],
        ["Activity", "one"],
        ["Exchanges"],
        ["name", "amount", "Name"],
        ["one", 1, "x"],
    ]
    with pytest.raises(InvalidDataError, match="appears twice"):
        read_workbook(workbooks.write({"a": rows}))


def test_an_activity_with_no_name_is_refused(workbooks: Workbooks) -> None:
    rows: Sheet = [["Database", "db"], ["Activity", None], ["location", "GLO"]]
    with pytest.raises(InvalidDataError, match="needs the name of the activity"):
        read_workbook(workbooks.write({"a": rows}))


def test_a_file_that_is_not_a_workbook_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "notes.xlsx"
    path.write_text("not a workbook")
    with pytest.raises(InvalidDataError) as error:
        read_workbook(path)
    assert error.value.problems[0].code == "unreadable-file"


def test_a_missing_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(InvalidDataError) as error:
        read_workbook(tmp_path / "missing.xlsx")
    assert error.value.problems[0].code == "unreadable-file"


def test_formula_cells_are_read_as_the_value_excel_stored(tmp_path: Path) -> None:
    """The workbook is read with data_only, like bw2io: a cell with `=1+1` gives its stored result,
    which openpyxl does not write, so a workbook never opened in Excel has no value there."""
    book = openpyxl.Workbook()
    ws = book.active
    assert ws is not None
    for row in [["Database", "db"], ["Activity", "one"], ["Exchanges"], ["name", "amount"]]:
        ws.append(row)
    ws.append(["one", "=1+1"])
    path = tmp_path / "formula.xlsx"
    book.save(path)
    assert read_workbook(path).activities[0].exchanges[0].amount is None
