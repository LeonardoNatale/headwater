"""Reading one method from an LCIA implementation workbook, on the fixture workbook."""

import re
import zipfile
from pathlib import Path

import pytest

from headwater.io.lcia import WorkbookCategory, WorkbookRow, read_workbook
from headwater.model import InvalidDataError

GWP = "global warming potential (GWP100)"
URBAN = "air/urban air close to ground"


def _problems(path: Path, method: str = "EF v3.1") -> list[tuple[str, str, str | None]]:
    with pytest.raises(InvalidDataError) as error:
        read_workbook(path, method)
    return [(p.code, p.item, p.location) for p in error.value.problems]


def _message(path: Path, method: str = "EF v3.1") -> str:
    with pytest.raises(InvalidDataError) as error:
        read_workbook(path, method)
    return str(error.value)


def _cfs_with(workbooks, *rows: list[str | int | float | None]) -> Path:
    return workbooks.write(cfs=[*workbooks.sheet("cfs"), *rows])


def test_reads_the_rows_of_one_method_only(workbook: Path) -> None:
    book = read_workbook(workbook, "EF v3.1")

    assert book.method == "EF v3.1"
    assert book.version == "3.10"
    assert book.rows == (
        WorkbookRow("climate change", "Carbon dioxide, fossil", "air", 1.0, "CFs!A2"),
        WorkbookRow("climate change", "Carbon dioxide, fossil", URBAN, 1.0, "CFs!A3"),
        WorkbookRow(
            "climate change", "Carbon dioxide, in air", "natural resource/in air", 0.0, "CFs!A4"
        ),
        WorkbookRow("climate change", "Dinitrogen monoxide", URBAN, 273.0, "CFs!A5"),
        WorkbookRow("climate change", "Methane, fossil", URBAN, 29.8, "CFs!A6"),
        WorkbookRow("climate change: fossil", "Carbon dioxide, fossil", URBAN, 1.0, "CFs!A7"),
        WorkbookRow(
            "climate change: fossil",
            "Methane, fossil",
            "air/non-urban air or from high stacks",
            29.8,
            "CFs!A8",
        ),
        WorkbookRow("climate change: fossil", "Methane, fossil", URBAN, 29.8, "CFs!A9"),
        WorkbookRow("ecotoxicity: freshwater", "Zinc", "soil/agricultural", 12.5, "CFs!A10"),
    )


def test_whole_numbers_are_read_as_floats(workbook: Path) -> None:
    book = read_workbook(workbook, "EF v3.1")

    assert all(type(row.factor) is float for row in book.rows)


def test_categories_carry_the_indicator_name_and_unit(workbook: Path) -> None:
    book = read_workbook(workbook, "EF v3.1")

    assert book.categories == (
        WorkbookCategory("climate change", GWP, "kg CO2-Eq"),
        WorkbookCategory("climate change: fossil", GWP, "kg CO2-Eq"),
        WorkbookCategory(
            "ecotoxicity: freshwater", "comparative toxic unit for ecosystems (CTUe)", "CTUe"
        ),
    )


def test_another_method_of_the_same_workbook(workbook: Path) -> None:
    book = read_workbook(workbook, "IPCC 2021")

    assert book.rows == (WorkbookRow("climate change", "Methane, fossil", URBAN, 30.5, "CFs!A12"),)


def test_an_unknown_method_lists_the_methods_of_the_workbook(workbook: Path) -> None:
    message = _message(workbook, "EF v3.2")

    assert "method EF v3.2" in message
    assert "Did you mean 'EF v3.1'?" in message
    assert "EF v3.1 no LT" in message
    assert "IPCC 2021" in message


def test_a_row_written_twice_is_an_error_naming_both_rows(workbooks) -> None:
    path = _cfs_with(
        workbooks,
        [
            "EF v3.1",
            "climate change",
            GWP,
            "Methane, fossil",
            "air",
            "urban air close to ground",
            30,
        ],
    )

    assert _problems(path) == [
        (
            "duplicate-factor",
            "climate change: Methane, fossil (air/urban air close to ground)",
            "CFs!A6, CFs!A13",
        )
    ]


def test_a_factor_that_is_not_a_number_is_an_error(workbooks) -> None:
    path = _cfs_with(
        workbooks,
        ["EF v3.1", "climate change", GWP, "Ethane", "air", "urban air close to ground", "n/a"],
        ["EF v3.1", "climate change", GWP, "Propane", "air", "urban air close to ground", None],
    )

    assert _problems(path) == [
        ("bad-factor", "climate change: Ethane (air/urban air close to ground)", "CFs!G13"),
        ("bad-factor", "climate change: Propane (air/urban air close to ground)", "CFs!G14"),
    ]


def test_a_row_without_a_flow_name_or_compartment_is_an_error(workbooks) -> None:
    path = _cfs_with(
        workbooks,
        ["EF v3.1", "climate change", GWP, None, "air", "urban air close to ground", 1],
        ["EF v3.1", "climate change", GWP, "Ethane", None, "urban air close to ground", 1],
    )

    assert [code for code, _, _ in _problems(path)] == ["missing-cell", "missing-cell"]


def test_a_category_with_two_units_is_an_error(workbooks) -> None:
    indicators = [*workbooks.sheet("indicators"), ["EF v3.1", "climate change", GWP, "kg CO2"]]
    path = workbooks.write(indicators=indicators)

    assert _problems(path) == [("conflicting-unit", "category climate change", None)]


def test_a_category_with_no_unit_is_an_error(workbooks) -> None:
    indicators = [r for r in workbooks.sheet("indicators") if r[1] != "ecotoxicity: freshwater"]
    path = workbooks.write(indicators=indicators)

    assert _problems(path) == [("missing-unit", "category ecotoxicity: freshwater", None)]


def test_a_category_with_two_indicators_is_an_error(workbooks) -> None:
    path = _cfs_with(
        workbooks, ["EF v3.1", "climate change", "GWP20", "Ethane", "air", "unspecified", 1]
    )

    assert _problems(path) == [("two-indicators", "category climate change", None)]


def test_a_missing_sheet_is_an_error(workbooks) -> None:
    path = workbooks.write(leave_out=("Indicators",))

    assert _problems(path) == [("missing-sheet", "sheet Indicators", None)]


def test_a_missing_column_is_an_error(workbooks) -> None:
    path = workbooks.write(cfs=[row[:6] for row in workbooks.sheet("cfs")])

    message = _message(path)
    assert "sheet CFs" in message
    assert "missing column(s): CF" in message


def test_a_workbook_without_a_database_version_is_an_error(workbooks) -> None:
    path = workbooks.write(read_me=[["no version here", None]])

    assert _problems(path) == [("missing-version", "sheet Read me", None)]


def test_a_file_that_is_not_a_workbook_is_an_error(tmp_path: Path) -> None:
    path = tmp_path / "w.xlsx"
    path.write_text("not a workbook", encoding="utf-8")

    assert [code for code, _, _ in _problems(path)] == ["unreadable-file"]


def test_every_problem_is_reported_at_once(workbooks) -> None:
    path = _cfs_with(
        workbooks,
        ["EF v3.1", "climate change", GWP, "Ethane", "air", "urban air close to ground", "n/a"],
        [
            "EF v3.1",
            "climate change",
            GWP,
            "Methane, fossil",
            "air",
            "urban air close to ground",
            1,
        ],
    )

    assert [code for code, _, _ in _problems(path)] == ["bad-factor", "duplicate-factor"]


def test_rows_past_the_size_a_sheet_records_are_read(workbook: Path, tmp_path: Path) -> None:
    """A file can record a wrong sheet size. Read-only openpyxl would stop there."""
    shrunk = tmp_path / "shrunk.xlsx"
    with zipfile.ZipFile(workbook) as source, zipfile.ZipFile(shrunk, "w") as target:
        for item in source.infolist():
            data = source.read(item.filename)
            if item.filename.startswith("xl/worksheets/"):
                data = re.sub(rb'<dimension ref="[^"]*"/>', b'<dimension ref="A1:B2"/>', data)
            target.writestr(item, data)

    found, expected = read_workbook(shrunk, "EF v3.1"), read_workbook(workbook, "EF v3.1")
    assert (found.version, found.categories, found.rows) == (
        expected.version,
        expected.categories,
        expected.rows,
    )
    assert len(found.rows) == 9


# --- choosing categories --------------------------------------------------------------------

LUC = "climate change: direct land use change"
TOTAL = "climate change: total (excl. biogenic CO2)"


def _ipcc(workbooks) -> Path:
    """IPCC 2021 with a direct land-use change category, and a total with two indicators, as
    ecoinvent's 3.12 workbook has it (GWP100 and GWP20 here, five there)."""
    cfs = [
        *workbooks.sheet("cfs"),
        ["IPCC 2021", LUC, GWP, "Carbon dioxide, from soil or biomass stock", "air", None, 1],
        ["IPCC 2021", LUC, GWP, "Carbon dioxide, to soil or biomass stock", "soil", None, -1],
        ["IPCC 2021", TOTAL, GWP, "Methane, fossil", "air", "urban air close to ground", 29.8],
        ["IPCC 2021", TOTAL, "GWP20", "Methane, fossil", "air", "urban air close to ground", 82.5],
    ]
    indicators = [
        *workbooks.sheet("indicators"),
        ["IPCC 2021", LUC, GWP, "kg CO2-Eq"],
        ["IPCC 2021", TOTAL, GWP, "kg CO2-Eq"],
        ["IPCC 2021", TOTAL, "GWP20", "kg CO2-Eq"],
    ]
    return workbooks.write(cfs=cfs, indicators=indicators)


def test_chosen_categories_are_read_and_the_others_left_out(workbooks) -> None:
    path = _ipcc(workbooks)

    book = read_workbook(path, "IPCC 2021", categories=[LUC])

    assert book.categories == (WorkbookCategory(LUC, GWP, "kg CO2-Eq"),)
    assert book.rows == (
        WorkbookRow(LUC, "Carbon dioxide, from soil or biomass stock", "air", 1.0, "CFs!A13"),
        WorkbookRow(LUC, "Carbon dioxide, to soil or biomass stock", "soil", -1.0, "CFs!A14"),
    )
    assert book.left_out == ("climate change", TOTAL)


def test_a_category_left_out_is_not_checked(workbooks) -> None:
    """The total has two indicators, which is refused when it is read, not when it is left out.
    Its flows are written once per indicator, so they are also duplicates."""
    path = _ipcc(workbooks)

    codes = [code for code, _, _ in _problems(path, "IPCC 2021")]
    assert codes == ["duplicate-factor", "two-indicators"]
    assert read_workbook(path, "IPCC 2021", categories=[LUC]).rows


def test_every_category_is_read_when_none_is_chosen(workbook: Path) -> None:
    book = read_workbook(workbook, "EF v3.1")

    assert book.left_out == ()
    assert len(book.categories) == 3


def test_an_unknown_category_is_an_error_naming_the_method_s_categories(workbooks) -> None:
    path = _ipcc(workbooks)

    with pytest.raises(InvalidDataError) as error:
        read_workbook(path, "IPCC 2021", categories=[LUC, "climate change: direct land use"])

    [problem] = error.value.problems
    assert (problem.code, problem.item) == (
        "unknown-category",
        "category climate change: direct land use",
    )
    assert f"Did you mean '{LUC}'?" in problem.message
    assert TOTAL in problem.message
