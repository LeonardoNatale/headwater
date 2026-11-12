"""The mapping file: decisions a person made about lines that do not match by themselves."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from headwater.io.excel.mapping import read_mapping
from headwater.model import InvalidDataError

HEADER = "type,name,reference_product,location,categories,target,who,when,comment\n"


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "mapping.csv"
    path.write_text(text, encoding="utf-8")
    return path


def test_each_kind_of_row_is_read_by_what_it_decides(tmp_path: Path) -> None:
    mapping = read_mapping(
        write(
            tmp_path,
            HEADER + "technosphere,market for steel,steel,RER,,ecoinvent:3.10:cutoff:a_b,leo,"
            "2026-10-12,renamed\n"
            + "technosphere,market for heat,,,,ecoinvent:3.10:cutoff:c_d,leo,2026-10-12,\n"
            + "biosphere,Water,,,water::fresh,ecoinvent:flow:e,leo,2026-10-12,\n"
            + "database,Textile Chemicals,,,,textile-chemicals,leo,2026-10-12,\n"
            + "database,ecoinvent 3.12,,,,release,leo,2026-10-12,\n"
            + "database,old name,,,,own,leo,2026-10-12,\n",
        )
    )
    steel = mapping.technosphere_row("market for steel", "steel", "RER")
    assert steel is not None
    assert (steel.target, steel.who, steel.comment, steel.source) == (
        "ecoinvent:3.10:cutoff:a_b",
        "leo",
        "renamed",
        "mapping.csv:2",
    )
    assert mapping.technosphere_row("market for heat", None, None) is not None
    assert mapping.technosphere_row("market for heat", "heat", None) is None
    water = mapping.biosphere_row("Water", ("water", "fresh"))
    assert water is not None and water.target == "ecoinvent:flow:e"
    chemicals = mapping.database("Textile Chemicals")
    assert chemicals is not None
    assert (chemicals.kind, chemicals.study) == ("database", "textile-chemicals")
    release, own = mapping.database("ecoinvent 3.12"), mapping.database("old name")
    assert release is not None and release.kind == "release" and release.study is None
    assert own is not None and own.kind == "own"
    assert len(mapping.rows) == 6


def test_an_empty_mapping_file_with_only_its_header_is_fine(tmp_path: Path) -> None:
    assert read_mapping(write(tmp_path, HEADER)).rows == ()


def test_the_optional_columns_may_be_left_out(tmp_path: Path) -> None:
    mapping = read_mapping(
        write(tmp_path, "type,name,target,who,when\ndatabase,x,release,leo,2026-10-12\n")
    )
    assert mapping.database("x") is not None


def test_a_row_without_who_is_refused_because_nobody_decided_it(tmp_path: Path) -> None:
    with pytest.raises(InvalidDataError, match="has no who") as error:
        read_mapping(write(tmp_path, HEADER + "technosphere,steel,,,,ecoinvent:x,,2026-10-12,\n"))
    assert error.value.problems[0].location == "mapping.csv:2"


@pytest.mark.parametrize(
    ("row", "message"),
    [
        ("wizardry,steel,,,,x,leo,2026-10-12,", "type 'wizardry'"),
        ("technosphere,,,,,x,leo,2026-10-12,", "has no name"),
        ("technosphere,steel,,,,,leo,2026-10-12,", "has no target"),
        ("database,Chemicals,,,,../chemicals,leo,2026-10-12,", "the name of a database folder"),
        (
            "database,Chemicals,,,,chemicals@v3,leo,2026-10-12,",
            "write the folder's name, chemicals",
        ),
    ],
)
def test_a_bad_row_is_refused_with_its_line(tmp_path: Path, row: str, message: str) -> None:
    with pytest.raises(InvalidDataError, match=message) as error:
        read_mapping(write(tmp_path, HEADER + row + "\n"))
    assert error.value.problems[0].location == "mapping.csv:2"


def test_a_line_decided_twice_is_refused(tmp_path: Path) -> None:
    with pytest.raises(InvalidDataError, match=r"mapping.csv:2 already does"):
        read_mapping(
            write(
                tmp_path,
                HEADER
                + "technosphere,steel,steel,RER,,ecoinvent:x,leo,2026-10-12,\n"
                + "technosphere,steel,steel,RER,,ecoinvent:y,leo,2026-10-12,\n",
            )
        )


def test_every_bad_row_is_reported_not_only_the_first(tmp_path: Path) -> None:
    with pytest.raises(InvalidDataError) as error:
        read_mapping(
            write(
                tmp_path,
                HEADER + "wizardry,a,,,,x,leo,2026-10-12,\ntechnosphere,b,,,,,leo,2026-10-12,\n",
            )
        )
    assert len(error.value.problems) == 2


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("", "file is empty"),
        ("type,name,target,who\n", "missing column\\(s\\): when"),
        ("type,name,target,who,colour\n", "unknown column 'colour'"),
        ("type,name,target,who,name\n", "column 'name' appears twice"),
        ("type,name,target,who,date\n", "unknown column 'date'. It is now called 'when'"),
    ],
)
def test_a_wrong_header_is_refused_with_the_columns_expected(
    tmp_path: Path, text: str, message: str
) -> None:
    with pytest.raises(InvalidDataError, match=message) as error:
        read_mapping(write(tmp_path, text))
    assert "expected columns: type, name, target, who, when" in str(error.value)
    assert [p.code for p in error.value.problems] == ["bad-columns"]


def test_a_missing_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(InvalidDataError, match="cannot be read"):
        read_mapping(tmp_path / "nope.csv")


def test_a_cell_that_spans_lines_is_refused_and_the_rows_after_it_keep_their_numbers(
    tmp_path: Path,
) -> None:
    text = (
        HEADER
        + 'technosphere,steel,steel,RER,,ecoinvent:x,leo,2026-10-12,"first line\nsecond line"\n'
        + "technosphere,steel,steel,RER,,ecoinvent:y,leo,2026-10-12,\n"
        + "technosphere,steel,steel,RER,,ecoinvent:z,leo,2026-10-12,\n"
    )
    with pytest.raises(InvalidDataError, match="a value spans several lines") as error:
        read_mapping(write(tmp_path, text))
    [spans, decided_again] = error.value.problems
    assert (spans.code, spans.location) == ("bad-row", "mapping.csv:2")
    assert (decided_again.code, decided_again.location) == ("bad-mapping", "mapping.csv:5")
    assert "mapping.csv:4 already does" in decided_again.message


def test_a_row_with_fewer_cells_than_the_header_is_refused(tmp_path: Path) -> None:
    with pytest.raises(InvalidDataError, match="row has 8 cells, but the header has 9") as error:
        read_mapping(write(tmp_path, HEADER + "technosphere,steel,,,,ecoinvent:x,leo,2026-10-12\n"))
    assert [(p.code, p.location) for p in error.value.problems] == [("bad-row", "mapping.csv:2")]


@pytest.mark.parametrize(
    ("when", "message"),
    [
        ("", "has no when"),
        ("12 October", "when '12 October' is not an ISO date or time"),
        ("2026-13-01", "when '2026-13-01' is not an ISO date or time"),
    ],
)
def test_a_row_needs_an_iso_date_or_time(tmp_path: Path, when: str, message: str) -> None:
    with pytest.raises(InvalidDataError, match=message):
        read_mapping(write(tmp_path, HEADER + f"technosphere,steel,,,,ecoinvent:x,leo,{when},\n"))


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2026-10-12", datetime(2026, 10, 12, tzinfo=UTC)),
        ("2026-10-12T09:30:00", datetime(2026, 10, 12, 9, 30, tzinfo=UTC)),
        ("2026-10-12T09:30:00+02:00", datetime(2026, 10, 12, 7, 30, tzinfo=UTC)),
    ],
)
def test_when_is_read_as_in_the_other_decision_files_and_kept_in_utc(
    tmp_path: Path, text: str, expected: datetime
) -> None:
    mapping = read_mapping(
        write(tmp_path, HEADER + f"technosphere,steel,,,,ecoinvent:x,leo,{text},\n")
    )
    assert mapping.rows[0].when == expected
