"""`no_factor.csv`: flows the database emits that the method has no factor for, acknowledged once.

The fixture `tshirt-no-factor` is the toy T-shirt with 0.002 kg of COD to water, which
toy-climate does not cover. Without its row, validation refuses the database. With it, the flow
counts 0.
"""

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import pytest

from headwater.model import Method, Problem, Report
from headwater.study import (
    ProcessFolder,
    read_database,
    read_method,
    read_process_folder,
    validate,
    validate_database,
)
from tests.conftest import add_lines

Write = Callable[[Path, str], None]
Edit = Callable[[Path, str, str], None]
Problems = Callable[..., tuple[Problem, ...]]

DATABASE = "tshirt-no-factor/tshirt"
NO_FACTOR = f"{DATABASE}/no_factor.csv"
HEADER = "flow,flow_name,method,reason,who,when\n"
REQUIRED = ["flow", "method", "reason", "who", "when"]
COD = (
    'tshirt:cod,"COD, Chemical Oxygen Demand",toy-climate,'
    "not characterised in toy-climate,Sam,2026-10-12\n"
)


def only(problems: tuple[Problem, ...], code: str) -> Problem:
    found = [p for p in problems if p.code == code]
    assert len(found) == 1, f"expected one {code!r} problem, got {[str(p) for p in problems]}"
    return found[0]


@pytest.fixture
def background(toy: Path) -> ProcessFolder:
    return read_process_folder(toy / "background", namespace="toy")


@pytest.fixture
def method(toy: Path) -> Method:
    return read_method(toy / "methods" / "toy-climate")


def check(toy: Path, background: ProcessFolder, method: Method) -> Report:
    return validate_database(read_database(toy / DATABASE), background, method=method)


# --- reading ---------------------------------------------------------------------------------


def test_the_row_of_the_fixture_is_read(toy: Path) -> None:
    (row,) = read_database(toy / DATABASE).no_factor

    assert row.flow_id == "tshirt:cod"
    assert row.flow_name == "COD, Chemical Oxygen Demand"
    assert row.method == "toy-climate"
    assert row.reason == "not characterised in toy-climate"
    assert row.who == "Sam"
    assert row.when == datetime(2026, 10, 12)


def test_flow_name_is_optional(toy: Path, write: Write) -> None:
    write(
        toy / NO_FACTOR,
        "flow,method,reason,who,when\n"
        "tshirt:cod,toy-climate,not characterised in toy-climate,Sam,2026-10-12\n",
    )

    (row,) = read_database(toy / DATABASE).no_factor

    assert row.flow_name is None


def test_a_time_with_a_zone_is_stored_in_utc_as_in_cut_offs(toy: Path, edit: Edit) -> None:
    edit(toy / NO_FACTOR, "2026-10-12", "2026-10-12T10:30+02:00")

    (row,) = read_database(toy / DATABASE).no_factor

    assert row.when == datetime(2026, 10, 12, 8, 30)


def test_a_database_without_the_file_has_no_rows(toy_dir: Path) -> None:
    assert read_database(toy_dir / "tshirt" / "tshirt").no_factor == ()


def test_a_file_with_only_the_header_has_no_rows(toy: Path, write: Write) -> None:
    write(toy / NO_FACTOR, HEADER)

    assert read_database(toy / DATABASE).no_factor == ()


@pytest.mark.parametrize("column", REQUIRED)
def test_every_column_is_required(
    toy: Path, write: Write, problems_of: Problems, column: str
) -> None:
    columns = [c for c in HEADER.strip().split(",") if c != column]
    write(toy / NO_FACTOR, ",".join(columns) + "\n")

    problem = only(problems_of(toy / DATABASE), "bad-columns")

    assert column in problem.message
    assert problem.location == "no_factor.csv:1"


@pytest.mark.parametrize("column", REQUIRED)
def test_every_cell_must_be_filled_in(
    toy: Path, write: Write, problems_of: Problems, column: str
) -> None:
    cells = {
        "flow": "tshirt:cod",
        "flow_name": "",
        "method": "toy-climate",
        "reason": "not characterised in toy-climate",
        "who": "Sam",
        "when": "2026-10-12",
    }
    cells[column] = ""
    write(toy / NO_FACTOR, HEADER + ",".join(cells.values()) + "\n")

    problem = only(problems_of(toy / DATABASE), "invalid-field")

    assert f"{column} is empty" in problem.message
    assert problem.location == "no_factor.csv:2"


def test_the_messages_for_who_and_when_are_those_of_cut_offs(
    toy: Path, write: Write, problems_of: Problems
) -> None:
    write(toy / NO_FACTOR, HEADER + "tshirt:cod,,toy-climate,not characterised,,\n")
    write(
        toy / "tshirt-cut-off/tshirt/cut_offs.csv",
        "process,line,flow,criterion,share,reason,who,when\n"
        "tshirt:tshirt,e04,tshirt:packaging-tape,mass,0.03,No tape,,\n",
    )

    no_factor = {p.message for p in problems_of(toy / DATABASE)}
    cut_offs = {p.message for p in problems_of(toy / "tshirt-cut-off/tshirt")}

    assert (
        no_factor
        == cut_offs
        == {
            "who is empty. Say who decided it.",
            "when is empty. Say when, as 2026-10-11.",
        }
    )


def test_a_when_that_is_not_an_iso_date_is_an_error(
    toy: Path, edit: Edit, problems_of: Problems
) -> None:
    edit(toy / NO_FACTOR, "2026-10-12", "12/10/2026")

    problem = only(problems_of(toy / DATABASE), "bad-date")

    assert "when '12/10/2026' is not an ISO date or time" in problem.message
    assert problem.location == "no_factor.csv:2"


def test_a_flow_written_twice_for_the_same_method_is_an_error(
    toy: Path, write: Write, problems_of: Problems
) -> None:
    write(toy / NO_FACTOR, HEADER + COD + COD.replace("Sam", "Ana"))

    problem = only(problems_of(toy / DATABASE), "no-factor-duplicate")

    assert problem.item == "flow tshirt:cod"
    assert "no_factor.csv:2" in problem.message
    assert problem.location == "no_factor.csv:3"


def test_the_same_flow_for_two_methods_is_two_rows(toy: Path, write: Write) -> None:
    write(toy / NO_FACTOR, HEADER + COD + COD.replace("toy-climate", "EF-3.1"))

    rows = read_database(toy / DATABASE).no_factor

    assert [(r.flow_id, r.method) for r in rows] == [
        ("tshirt:cod", "toy-climate"),
        ("tshirt:cod", "EF-3.1"),
    ]


def test_a_misspelt_file_name_is_an_error(toy: Path, problems_of: Problems) -> None:
    (toy / NO_FACTOR).rename(toy / DATABASE / "no_factors.csv")

    problem = only(problems_of(toy / DATABASE), "unknown-file")

    assert "'no_factor.csv'" in problem.message


# --- validation ------------------------------------------------------------------------------


def test_with_the_row_the_database_validates_with_nothing_to_report(
    toy: Path, background: ProcessFolder, method: Method
) -> None:
    database = read_database(toy / DATABASE)

    assert validate_database(database, background, method=method).problems == ()
    assert validate(database, background=background, method=method).problems == ()


def test_without_the_row_the_flow_is_an_error_naming_the_flow_and_the_process(
    toy: Path, write: Write, background: ProcessFolder, method: Method
) -> None:
    write(toy / NO_FACTOR, HEADER)

    report = check(toy, background, method)

    problem = only(report.errors, "no-factor")
    assert problem.item == "flow tshirt:cod"
    assert "toy-climate" in problem.message
    assert "tshirt:tshirt" in problem.message
    assert "no_factor.csv" in problem.message  # says how to acknowledge it
    assert report.warnings == ()


def test_without_the_file_the_flow_is_an_error_too(
    toy: Path, background: ProcessFolder, method: Method
) -> None:
    (toy / NO_FACTOR).unlink()

    assert only(check(toy, background, method).errors, "no-factor").item == "flow tshirt:cod"


def test_validate_with_the_background_gives_the_same_error(
    toy: Path, write: Write, background: ProcessFolder, method: Method
) -> None:
    write(toy / NO_FACTOR, HEADER)
    database = read_database(toy / DATABASE)

    report = validate(database, background=background, method=method)

    assert report.errors == validate_database(database, background, method=method).errors


def test_a_row_for_a_flow_that_has_a_factor_is_a_warning(
    toy: Path, write: Write, background: ProcessFolder, method: Method
) -> None:
    write(toy / NO_FACTOR, HEADER + COD + "toy:co2,,toy-climate,thought so,Sam,2026-10-12\n")

    report = check(toy, background, method)

    assert report.errors == ()
    problem = only(report.warnings, "no-factor-not-needed")
    assert problem.item == "flow toy:co2"
    assert "row not needed" in problem.message
    assert "has a factor in method toy-climate" in problem.message
    assert problem.location == "no_factor.csv"


def test_a_row_for_a_flow_the_database_does_not_use_is_a_warning(
    toy: Path, write: Write, background: ProcessFolder, method: Method
) -> None:
    write(toy / NO_FACTOR, HEADER + COD + "tshirt:bod,,toy-climate,no factor,Sam,2026-10-12\n")

    report = check(toy, background, method)

    assert report.errors == ()
    problem = only(report.warnings, "no-factor-not-needed")
    assert problem.item == "flow tshirt:bod"
    assert "row not needed" in problem.message
    assert "no process of the database" in problem.message
    assert "Did you mean 'tshirt:cod'?" in problem.message


def test_a_row_for_another_method_is_a_warning_and_does_not_acknowledge_the_flow(
    toy: Path, edit: Edit, background: ProcessFolder, method: Method
) -> None:
    # The database moved to a new method: the old row does not block it, and the flow needs a row
    # for the new method.
    edit(toy / NO_FACTOR, ",toy-climate,", ",toy-climate-0,")

    report = check(toy, background, method)

    assert only(report.errors, "no-factor").item == "flow tshirt:cod"
    problem = only(report.warnings, "no-factor-not-needed")
    assert "row not needed" in problem.message
    assert "toy-climate-0" in problem.message


def test_rows_are_not_checked_without_a_method(
    toy: Path, write: Write, background: ProcessFolder
) -> None:
    write(toy / NO_FACTOR, HEADER + "toy:co2,,toy-climate,thought so,Sam,2026-10-12\n")

    assert validate_database(read_database(toy / DATABASE), background).problems == ()


def test_a_background_flow_with_no_factor_stays_a_warning(
    toy: Path, background: ProcessFolder, method: Method
) -> None:
    with (toy / "background/flows.csv").open("a") as f:
        f.write("toy:n2o,Dinitrogen monoxide,elementary,kg,air,\n")
    add_lines(toy / "background", "electricity-production", "e04,emission,toy:n2o,0.0001,kg,")
    background = read_process_folder(toy / "background", namespace="toy")
    database = read_database(toy / DATABASE)

    report = validate(database, background=background, method=method)

    assert report.errors == ()
    assert only(report.warnings, "no-factor").item == "flow toy:n2o"
    assert validate_database(database, background, method=method).problems == ()


def test_a_flow_of_the_database_and_the_background_is_an_error_for_the_database_only(
    toy: Path, write: Write, method: Method
) -> None:
    # COD is also emitted by electricity production. The error names the database's process, the
    # warning the background's.
    with (toy / "background/flows.csv").open("a") as f:
        f.write('tshirt:cod,"COD, Chemical Oxygen Demand",elementary,kg,water,\n')
    add_lines(toy / "background", "electricity-production", "e04,emission,tshirt:cod,0.0001,kg,")
    (toy / DATABASE / "flows.csv").write_text(
        "id,name,kind,unit,compartment,cas\ntshirt:tshirt,T-shirt,product,unit,,\n"
    )
    write(toy / NO_FACTOR, HEADER)
    background = read_process_folder(toy / "background", namespace="toy")

    report = validate(read_database(toy / DATABASE), background=background, method=method)

    error = only(report.errors, "no-factor")
    assert "tshirt:tshirt" in error.message
    assert "toy:electricity-production" not in error.message
    warning = only(report.warnings, "no-factor")
    assert "toy:electricity-production" in warning.message
    assert "tshirt:tshirt" not in warning.message


def test_a_flow_name_that_is_not_the_flows_name_is_an_error(
    toy: Path, edit: Edit, background: ProcessFolder, method: Method
) -> None:
    edit(toy / NO_FACTOR, "COD, Chemical Oxygen Demand", "BOD5, Biological Oxygen Demand")

    report = check(toy, background, method)

    problem = only(report.errors, "flow-name-mismatch")
    assert problem.item == "flow tshirt:cod"
    assert "'BOD5, Biological Oxygen Demand'" in problem.message
    assert "'COD, Chemical Oxygen Demand'" in problem.message
    assert problem.location == "no_factor.csv"


def test_the_flow_name_of_a_row_not_needed_is_not_checked(
    toy: Path, write: Write, background: ProcessFolder, method: Method
) -> None:
    # An old row must not block: it gets the warning only.
    write(toy / NO_FACTOR, HEADER + COD + "toy:co2,Carbon dioxide,toy-climate,x,Sam,2026-10-12\n")

    report = check(toy, background, method)

    assert report.errors == ()
    assert [p.code for p in report.warnings] == ["no-factor-not-needed"]
