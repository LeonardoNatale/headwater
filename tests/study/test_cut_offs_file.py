"""`cut_offs.csv`: what a database leaves out on purpose, and the check against database.yaml."""

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import pytest

from headwater.model import CutOffCriterion, IssueStatus, LinkStatus, Problem
from headwater.study import ProcessFolder, read_database, read_process_folder, validate

Write = Callable[[Path, str], None]
Edit = Callable[[Path, str, str], None]
Problems = Callable[..., tuple[Problem, ...]]

DATABASE = "tshirt-cut-off/tshirt"
CUT_OFFS = f"{DATABASE}/cut_offs.csv"
TSHIRT_CSV = f"{DATABASE}/processes/tshirt.csv"
HEADER = "process,line,flow,criterion,share,reason,who,when\n"
TAPE = "tshirt:tshirt,e04,tshirt:packaging-tape,mass,{share},No supplier data,leo,2026-10-11\n"


def only(problems: tuple[Problem, ...]) -> Problem:
    assert len(problems) == 1, [str(p) for p in problems]
    return problems[0]


@pytest.fixture
def background(toy: Path) -> ProcessFolder:
    return read_process_folder(toy / "background", namespace="toy")


def more_lines(toy: Path, write: Write) -> None:
    """Add glue (kg) and heat (MJ) to the T-shirt, with no supplier."""
    flows = toy / DATABASE / "flows.csv"
    flows.write_text(
        flows.read_text() + "tshirt:glue,glue,product,kg,,\ntshirt:heat,heat,product,MJ,,\n"
    )
    csv = toy / TSHIRT_CSV
    csv.write_text(
        csv.read_text() + "e05,input,tshirt:glue,0.005,kg,\ne06,input,tshirt:heat,0.1,MJ,\n"
    )


# --- reading ---------------------------------------------------------------------------------


def test_the_cut_off_of_the_fixture_is_an_issue_and_a_line_that_buys_nothing(toy: Path) -> None:
    folder = read_database(toy / DATABASE)

    (issue,) = folder.cut_offs
    assert issue.status is IssueStatus.cut_off
    assert issue.criterion is CutOffCriterion.mass
    assert issue.resolved_by == "leo"
    assert issue.resolved_at == datetime(2026, 10, 11)
    assert issue.reason.startswith("No supplier for packaging tape")  # pyright: ignore[reportOptionalMemberAccess]
    tape = next(e for e in folder.processes[0].exchanges if e.line_id == "e04")
    assert tape.link_status is LinkStatus.cut_off


def test_a_database_without_cut_offs_has_none(toy: Path) -> None:
    assert read_database(toy / "tshirt/tshirt").cut_offs == ()


def test_a_header_only_file_is_fine(toy: Path, write: Write) -> None:
    write(toy / "tshirt/tshirt/cut_offs.csv", HEADER)

    assert read_database(toy / "tshirt/tshirt").cut_offs == ()


def test_the_cumulative_share_runs_by_criterion_in_the_order_of_the_lines(
    toy: Path, write: Write
) -> None:
    more_lines(toy, write)
    write(
        toy / CUT_OFFS,
        HEADER
        # Written out of order on purpose: the running total follows the lines, not the rows.
        + "tshirt:tshirt,e06,tshirt:heat,energy,0.03,Small,leo,2026-10-11\n"
        + "tshirt:tshirt,e05,tshirt:glue,mass,0.015,Small,leo,2026-10-11\n"
        + TAPE.format(share=0.02),
    )

    folder = read_database(toy / DATABASE)

    assert [(i.line_id, i.criterion, i.share, i.cumulative_share) for i in folder.cut_offs] == [
        ("e04", CutOffCriterion.mass, 0.02, 0.02),
        ("e05", CutOffCriterion.mass, 0.015, pytest.approx(0.035, rel=1e-12)),
        ("e06", CutOffCriterion.energy, 0.03, 0.03),
    ]


def test_each_criterion_has_its_own_budget(toy: Path, write: Write) -> None:
    more_lines(toy, write)
    write(
        toy / CUT_OFFS,
        HEADER
        + TAPE.format(share=0.04)
        + "tshirt:tshirt,e06,tshirt:heat,energy,0.04,Small,leo,2026-10-11\n",
    )

    folder = read_database(
        toy / DATABASE
    )  # 0.04 + 0.04 is above 0.05, but they are different bases

    assert [i.cumulative_share for i in folder.cut_offs] == [0.04, 0.04]


def test_the_time_of_a_cut_off_is_stored_in_utc_without_a_zone(toy: Path, write: Write) -> None:
    write(
        toy / CUT_OFFS,
        HEADER
        + "tshirt:tshirt,e04,tshirt:packaging-tape,mass,0.02,Small,leo,2026-10-11T14:30:00+02:00\n",
    )

    (issue,) = read_database(toy / DATABASE).cut_offs

    assert issue.resolved_at == datetime(2026, 10, 11, 12, 30)
    assert issue.resolved_at.tzinfo is None  # pyright: ignore[reportOptionalMemberAccess]


# --- the limit in database.yaml --------------------------------------------------------------


def test_the_cut_offs_must_stay_within_the_limit_of_the_database(
    toy: Path, edit: Edit, problems_of: Problems
) -> None:
    edit(toy / DATABASE / "database.yaml", "max_cumulative: 0.05", "max_cumulative: 0.03")

    problem = only(problems_of(toy / DATABASE))

    assert problem.code == "cut-off-over-limit"
    assert problem.item == "cut_offs.csv criterion mass"
    assert "0.03846" in problem.message
    assert "0.03" in problem.message
    assert "max_cumulative" in problem.message
    assert "tshirt:tshirt line e04" in problem.message  # the lines that add up


def test_the_limit_is_checked_for_every_criterion(
    toy: Path, write: Write, problems_of: Problems
) -> None:
    more_lines(toy, write)
    write(
        toy / CUT_OFFS,
        HEADER
        + TAPE.format(share=0.06)
        + "tshirt:tshirt,e06,tshirt:heat,energy,0.07,Small,leo,2026-10-11\n",
    )

    problems = problems_of(toy / DATABASE)

    assert [(p.code, p.item) for p in problems] == [
        ("cut-off-over-limit", "cut_offs.csv criterion energy"),
        ("cut-off-over-limit", "cut_offs.csv criterion mass"),
    ]


def test_exactly_the_limit_is_allowed(toy: Path, write: Write) -> None:
    write(toy / CUT_OFFS, HEADER + TAPE.format(share=0.05))

    assert read_database(toy / DATABASE).cut_offs[0].cumulative_share == 0.05


def test_rows_with_no_limit_in_database_yaml_are_an_error(
    toy: Path, edit: Edit, problems_of: Problems
) -> None:
    edit(
        toy / DATABASE / "database.yaml",
        "cut_off: {criteria: [mass, energy], max_cumulative: 0.05}\n",
        "",
    )

    problem = only(problems_of(toy / DATABASE))

    assert problem.code == "cut-off-no-limit"
    assert problem.item == "cut_offs.csv"
    assert "database.yaml" in problem.message
    assert "max_cumulative" in problem.message  # shows what to write
    assert problem.location == "cut_offs.csv"


def test_a_limit_with_no_rows_is_a_warning(
    toy: Path, write: Write, background: ProcessFolder
) -> None:
    path = toy / "tshirt/tshirt/database.yaml"
    write(path, path.read_text() + "cut_off: {criteria: [mass], max_cumulative: 0.05}\n")

    report = validate(read_database(toy / "tshirt/tshirt"), background=background)

    assert report.errors == ()
    (problem,) = report.warnings
    assert problem.code == "cut-off-not-needed"
    assert problem.item == "database tshirt: cut_off"
    assert problem.location == "database.yaml"


# --- mistakes in the rows --------------------------------------------------------------------


def test_a_line_that_the_process_does_not_have(
    toy: Path, write: Write, problems_of: Problems
) -> None:
    write(toy / CUT_OFFS, HEADER + TAPE.format(share=0.02).replace(",e04,", ",e40,"))

    problem = only(problems_of(toy / DATABASE))

    assert problem.code == "cut-off-unknown-line"
    assert problem.item == "tshirt:tshirt line e40"
    assert "e04" in problem.message
    assert problem.location == "cut_offs.csv:2"


def test_a_process_that_the_database_does_not_have(
    toy: Path, write: Write, problems_of: Problems
) -> None:
    write(
        toy / CUT_OFFS, HEADER + TAPE.format(share=0.02).replace("tshirt:tshirt", "tshirt:tshirtt")
    )

    problem = only(problems_of(toy / DATABASE))

    assert problem.code == "cut-off-unknown-line"
    assert problem.item == "tshirt:tshirtt line e04"
    assert "Did you mean 'tshirt:tshirt'?" in problem.message


def test_a_flow_that_is_not_the_flow_of_the_line(
    toy: Path, write: Write, problems_of: Problems
) -> None:
    write(
        toy / CUT_OFFS,
        HEADER + TAPE.format(share=0.02).replace("tshirt:packaging-tape", "toy:electricity"),
    )

    problem = only(problems_of(toy / DATABASE))

    assert problem.code == "cut-off-flow"
    assert problem.item == "tshirt:tshirt line e04"
    assert "'tshirt:packaging-tape'" in problem.message


def test_a_line_that_has_a_supplier_is_not_cut_off(
    toy: Path, write: Write, problems_of: Problems
) -> None:
    write(
        toy / CUT_OFFS,
        HEADER + "tshirt:tshirt,e03,toy:electricity,mass,0.02,Small,leo,2026-10-11\n",
    )

    problem = only(problems_of(toy / DATABASE))

    assert problem.code == "cut-off-supplier"
    assert problem.item == "tshirt:tshirt line e03"
    assert "toy:electricity-production" in problem.message


def test_only_an_input_or_a_waste_can_be_cut_off(
    toy: Path, write: Write, problems_of: Problems
) -> None:
    write(
        toy / CUT_OFFS,
        HEADER + "tshirt:tshirt,e01,tshirt:tshirt,mass,0.02,Small,leo,2026-10-11\n",
    )

    problem = only(problems_of(toy / DATABASE))

    assert problem.code == "cut-off-role"
    assert problem.item == "tshirt:tshirt line e01"
    assert "reference" in problem.message
    assert "input" in problem.message


def test_a_criterion_that_does_not_exist(toy: Path, write: Write, problems_of: Problems) -> None:
    write(toy / CUT_OFFS, HEADER + TAPE.format(share=0.02).replace(",mass,", ",weight,"))

    problem = only(problems_of(toy / DATABASE))

    assert problem.code == "cut-off-criterion"
    assert problem.item == "tshirt:tshirt line e04"
    assert "'weight'" in problem.message
    assert "environmental_significance" in problem.message  # the valid ones are listed


def test_a_criterion_that_the_database_does_not_use(
    toy: Path, write: Write, problems_of: Problems
) -> None:
    write(
        toy / CUT_OFFS,
        HEADER + TAPE.format(share=0.02).replace(",mass,", ",environmental_significance,"),
    )

    problem = only(problems_of(toy / DATABASE))

    assert problem.code == "cut-off-criterion"
    assert "database.yaml" in problem.message
    assert "mass, energy" in problem.message


def test_a_line_cut_off_twice(toy: Path, write: Write, problems_of: Problems) -> None:
    write(toy / CUT_OFFS, HEADER + TAPE.format(share=0.01) + TAPE.format(share=0.02))

    problem = only(problems_of(toy / DATABASE))

    assert problem.code == "cut-off-duplicate"
    assert problem.item == "tshirt:tshirt line e04"
    assert "cut_offs.csv:2" in problem.message
    assert problem.location == "cut_offs.csv:3"


@pytest.mark.parametrize("share", ["a lot", "", "0,02"])
def test_a_share_that_is_not_a_number(
    toy: Path, write: Write, problems_of: Problems, share: str
) -> None:
    write(toy / CUT_OFFS, HEADER + TAPE.format(share=f'"{share}"'))

    problem = only(problems_of(toy / DATABASE))

    assert problem.code == "bad-number"
    assert problem.item == "tshirt:tshirt line e04"


def test_a_share_above_one(toy: Path, write: Write, problems_of: Problems) -> None:
    write(toy / CUT_OFFS, HEADER + TAPE.format(share=1.5))

    problem = only(problems_of(toy / DATABASE))

    assert problem.code == "invalid-field"
    assert problem.item == "tshirt:tshirt line e04"
    assert "share" in problem.message


def test_a_date_that_is_not_a_date(toy: Path, write: Write, problems_of: Problems) -> None:
    write(toy / CUT_OFFS, HEADER + TAPE.format(share=0.02).replace("2026-10-11", "yesterday"))

    problem = only(problems_of(toy / DATABASE))

    assert problem.code == "bad-date"
    assert problem.item == "tshirt:tshirt line e04"
    assert "'yesterday'" in problem.message
    assert "2026-10-11" in problem.message  # shows what a date looks like


@pytest.mark.parametrize("column", ["reason", "who", "when"])
def test_a_reason_a_person_and_a_date_are_required(
    toy: Path, write: Write, problems_of: Problems, column: str
) -> None:
    cells = {"reason": "No supplier data", "who": "leo", "when": "2026-10-11"}
    row = TAPE.format(share=0.02).replace(cells[column], "")
    write(toy / CUT_OFFS, HEADER + row)

    problem = only(problems_of(toy / DATABASE))

    assert problem.item == "tshirt:tshirt line e04"
    assert column in problem.message
    assert "empty" in problem.message


def test_a_column_that_is_missing(toy: Path, write: Write, problems_of: Problems) -> None:
    write(toy / CUT_OFFS, "process,line,flow,criterion,share,reason,who\n")

    problem = only(problems_of(toy / DATABASE))

    assert problem.code == "bad-columns"
    assert "when" in problem.message


# --- a line with no supplier and no cut-off --------------------------------------------------


def test_a_line_with_no_supplier_and_no_cut_off_points_at_the_file(
    toy: Path, write: Write, background: ProcessFolder
) -> None:
    write(toy / CUT_OFFS, HEADER)

    report = validate(read_database(toy / DATABASE), background=background)

    (problem,) = report.errors
    assert problem.code == "supplier-missing"
    assert problem.item == "tshirt:tshirt line e04"
    assert "cut_offs.csv" in problem.message
