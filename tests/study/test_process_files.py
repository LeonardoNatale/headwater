"""A database's processes are two tables (D48): `processes.csv`, one row per process (id, name,
location, kind, optional time and comment), and `exchanges.csv`, one row per line, the process's id
first. Each mistake names its row."""

from collections.abc import Callable
from datetime import date
from pathlib import Path

import pytest

from headwater.model import InvalidDataError, Problem, ProcessKind
from headwater.study import read_database, read_process_folder

Edit = Callable[[Path, str, str], None]

STUDY = "tshirt/tshirt"
PROCESSES = "tshirt/tshirt/processes.csv"
EXCHANGES = "tshirt/tshirt/exchanges.csv"
ROW = "tshirt,T-shirt,GLO,ordinary_transforming_activity"


def problems_of(path: Path) -> tuple[Problem, ...]:
    with pytest.raises(InvalidDataError) as raised:
        read_database(path)
    return raised.value.problems


def set_row(toy: Path, header: str, row: str) -> None:
    (toy / PROCESSES).write_text(f"{header}\n{row}\n")


def test_the_toy_tshirt_is_one_row_and_three_lines(toy: Path) -> None:
    [process] = read_database(toy / STUDY).processes
    assert (process.id, process.name, process.location) == ("tshirt:tshirt", "T-shirt", "GLO")
    assert [e.line_id for e in process.exchanges] == ["e01", "e02", "e03"]


def test_a_line_whose_process_is_not_in_processes_csv(toy: Path) -> None:
    with (toy / EXCHANGES).open("a") as f:
        f.write("sewing,e01,reference,tshirt:sewing,1,unit,\n")

    (problem,) = problems_of(toy / STUDY)

    assert problem.code == "unknown-process"
    assert problem.item == "process sewing"
    assert problem.location == "exchanges.csv:5"
    assert "processes.csv" in problem.message


def test_a_process_with_no_reference_line(toy: Path, edit: Edit) -> None:
    with (toy / PROCESSES).open("a") as f:
        f.write("sewing,Sewing,GLO,ordinary_transforming_activity\n")

    (problem,) = problems_of(toy / STUDY)

    assert problem.code == "reference-count"
    assert problem.item == "process tshirt:sewing"
    assert problem.location == "processes.csv:3"


def test_a_process_listed_twice(toy: Path) -> None:
    with (toy / PROCESSES).open("a") as f:
        f.write(f"{ROW}\n")

    (problem,) = problems_of(toy / STUDY)

    assert problem.code == "duplicate-process"
    assert "processes.csv:2 and processes.csv:3" in problem.message


def test_a_line_id_used_twice_in_one_process(toy: Path) -> None:
    with (toy / EXCHANGES).open("a") as f:
        f.write("tshirt,e02,input,toy:electricity,0.1,kWh,toy:electricity-production\n")

    (problem,) = problems_of(toy / STUDY)

    assert problem.code == "duplicate-line"
    assert problem.item == "tshirt:tshirt line e02"
    assert "exchanges.csv:3 and exchanges.csv:5" in problem.message


@pytest.mark.parametrize("column", ["id", "process"])
def test_a_whole_id_in_a_table_says_to_write_the_part_after_the_name(
    toy: Path, edit: Edit, column: str
) -> None:
    if column == "id":
        edit(toy / PROCESSES, "\ntshirt,", "\ntshirt:tshirt,")
    else:
        edit(toy / EXCHANGES, "\ntshirt,e02", "\ntshirt:tshirt,e02")

    problems = problems_of(toy / STUDY)

    assert any("write 'tshirt'" in p.message for p in problems)


def test_rows_in_any_order_read_the_same(toy: Path) -> None:
    before = read_database(toy / STUDY).processes
    lines = (toy / EXCHANGES).read_text().splitlines(keepends=True)
    (toy / EXCHANGES).write_text("".join([lines[0], *reversed(lines[1:])]))

    [process] = read_database(toy / STUDY).processes

    assert [e.line_id for e in process.exchanges] == ["e03", "e02", "e01"]
    assert sorted(process.exchanges, key=lambda e: e.line_id) == list(before[0].exchanges)


def test_processes_are_read_sorted_by_id(tmp_path: Path) -> None:
    (tmp_path / "processes.csv").write_text(
        "id,name,location,kind\nb,B,GLO,market_activity\na,A,GLO,market_activity\n"
    )
    (tmp_path / "exchanges.csv").write_text(
        "process,line,role,flow,amount,unit,supplier\n"
        "b,e01,reference,x:b,1,kg,\na,e01,reference,x:a,1,kg,\n"
    )

    assert [p.id for p in read_process_folder(tmp_path, namespace="x").processes] == ["x:a", "x:b"]


def test_dates_are_read_as_iso_dates(toy: Path) -> None:
    set_row(toy, "id,name,location,kind,time_start,time_end", f"{ROW},2024-01-01,2024-12-31")

    process = read_database(toy / STUDY).processes[0]

    assert process.time_start == date(2024, 1, 1)
    assert process.time_end == date(2024, 12, 31)


def test_a_blank_date_is_no_date(toy: Path) -> None:
    set_row(toy, "id,name,location,kind,time_start,time_end", f"{ROW},,")

    assert read_database(toy / STUDY).processes[0].time_start is None


def test_the_comment_is_text_for_people(toy: Path) -> None:
    set_row(toy, "id,name,location,kind,comment", f'{ROW},"Sewn in-house, by hand"')

    [process] = read_database(toy / STUDY).processes

    assert "comment" not in process.model_dump()


@pytest.mark.parametrize("column", ["name", "location", "kind"])
def test_required_columns(toy: Path, column: str) -> None:
    header = ",".join(c for c in ("id", "name", "location", "kind") if c != column)
    values = {"name": "T-shirt", "location": "GLO", "kind": "ordinary_transforming_activity"}
    set_row(toy, header, ",".join(["tshirt", *(v for c, v in values.items() if c != column)]))

    (problem,) = problems_of(toy / STUDY)

    assert problem.code == "bad-columns"
    assert column in problem.message


def test_a_blank_name_names_the_process(toy: Path) -> None:
    set_row(toy, "id,name,location,kind", "tshirt,,GLO,ordinary_transforming_activity")

    (problem,) = problems_of(toy / STUDY)

    assert problem.code == "invalid-field"
    assert problem.item == "process tshirt:tshirt"
    assert problem.message.startswith("name:")
    assert problem.location == "processes.csv:2"


def test_a_misspelled_column_gets_a_hint(toy: Path) -> None:
    set_row(toy, "id,name,locaton,kind", ROW)

    (problem,) = problems_of(toy / STUDY)

    assert "Did you mean 'location'?" in problem.message


def test_kind_must_be_an_ecospold2_activity_type(toy: Path) -> None:
    set_row(toy, "id,name,location,kind", "tshirt,T-shirt,GLO,treatment")

    (problem,) = problems_of(toy / STUDY)

    assert problem.item == "process tshirt:tshirt"
    assert "'treatment'" in problem.message
    assert "market_activity" in problem.message  # the allowed values are listed


@pytest.mark.parametrize("kind", [k.value for k in ProcessKind])
def test_every_ecospold2_activity_type_is_accepted(toy: Path, kind: str) -> None:
    set_row(toy, "id,name,location,kind", f"tshirt,T-shirt,GLO,{kind}")

    assert read_database(toy / STUDY).processes[0].kind.value == kind


def test_a_year_is_not_a_date(toy: Path) -> None:
    set_row(toy, "id,name,location,kind,time_start", f"{ROW},2024")

    (problem,) = problems_of(toy / STUDY)

    assert problem.message.startswith("time_start:")


def test_time_end_before_time_start(toy: Path) -> None:
    set_row(toy, "id,name,location,kind,time_start,time_end", f"{ROW},2024-12-31,2024-01-01")

    (problem,) = problems_of(toy / STUDY)

    assert problem.code == "invalid-field"
    assert problem.location == "processes.csv:2"
    assert "time_end 2024-01-01" in problem.message
    assert "time_start 2024-12-31" in problem.message


@pytest.mark.parametrize("code", ["NO", "no", "off"])
def test_a_location_yaml_read_as_yes_or_no_is_plain_text_in_a_table(toy: Path, code: str) -> None:
    set_row(toy, "id,name,location,kind", f"tshirt,T-shirt,{code},ordinary_transforming_activity")

    assert read_database(toy / STUDY).processes[0].location == code


def test_a_problem_in_each_table_is_reported(toy: Path, edit: Edit) -> None:
    edit(toy / PROCESSES, ",GLO,", ",,")
    edit(toy / EXCHANGES, "0.25,kg", "abc,kg")

    problems = problems_of(toy / STUDY)

    assert sorted(p.code for p in problems) == ["bad-number", "invalid-field"]


def test_no_tables_at_all(toy: Path) -> None:
    (toy / PROCESSES).unlink()
    (toy / EXCHANGES).unlink()

    (problem,) = problems_of(toy / STUDY)

    assert problem.code == "no-processes"
    assert "processes.csv" in problem.message
    assert "exchanges.csv" in problem.message


@pytest.mark.parametrize("table", ["processes.csv", "exchanges.csv"])
def test_one_table_missing(toy: Path, table: str) -> None:
    (toy / STUDY / table).unlink()

    problems = problems_of(toy / STUDY)

    assert ("missing-file", table) in [(p.code, p.item) for p in problems]


def test_the_old_layout_names_the_command_that_converts_it(toy: Path) -> None:
    (toy / STUDY / "processes").mkdir()

    (problem,) = problems_of(toy / STUDY)

    assert problem.code == "old-layout"
    assert "remove processes/" in problem.message  # the tables are already there
