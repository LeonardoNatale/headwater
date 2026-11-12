"""`overrides/*.csv`: changed lines of background processes, read and checked."""

from collections.abc import Callable
from pathlib import Path

import pytest

from headwater.model import Problem, Role
from headwater.study import (
    ProcessFolder,
    read_database,
    read_process_folder,
    validate,
    validate_database,
)

Write = Callable[[Path, str], None]
Problems = Callable[..., tuple[Problem, ...]]

DATABASE = "tshirt/tshirt"
HEADER = "process,line,role,flow,amount,unit,supplier,comment\n"
FABRIC_E02 = "toy:fabric-production,e02,input,toy:electricity,{},kWh,toy:electricity-production,\n"
ELECTRICITY_E02 = "toy:electricity-production,e02,emission,toy:co2,{},kg,,\n"


def only(problems: tuple[Problem, ...]) -> Problem:
    assert len(problems) == 1, [str(p) for p in problems]
    return problems[0]


def codes(problems: tuple[Problem, ...]) -> list[str]:
    return [p.code for p in problems]


@pytest.fixture
def background(toy: Path) -> ProcessFolder:
    return read_process_folder(toy / "background", namespace="toy")


# --- reading ---------------------------------------------------------------------------------


def test_the_lines_of_one_file_become_one_override_per_process(toy: Path, write: Write) -> None:
    write(
        toy / DATABASE / "overrides/mill.csv",
        HEADER + FABRIC_E02.format(2.5) + ELECTRICITY_E02.format(0.3),
    )

    folder = read_database(toy / DATABASE)

    assert [o.process_id for o in folder.overrides] == [
        "toy:electricity-production",
        "toy:fabric-production",
    ]
    fabric = folder.overrides[1].lines[0]
    assert (fabric.line_id, fabric.role, fabric.amount, fabric.unit) == (
        "e02",
        Role.input,
        2.5,
        "kWh",
    )
    assert fabric.supplier_id == "toy:electricity-production"
    assert folder.overrides[0].lines[0].supplier_id is None  # a blank supplier is no supplier


def test_the_lines_of_a_process_may_come_from_several_files(toy: Path, write: Write) -> None:
    write(
        toy / DATABASE / "overrides/a.csv",
        HEADER
        + "toy:fabric-production,e02,input,toy:electricity,2.5,kWh,toy:electricity-production,\n",
    )
    write(
        toy / DATABASE / "overrides/b.csv",
        HEADER + "toy:fabric-production,e03,emission,toy:co2,1.5,kg,,\n",
    )

    (override,) = read_database(toy / DATABASE).overrides

    assert [e.line_id for e in override.lines] == ["e02", "e03"]


def test_the_comment_and_flow_name_columns_are_optional(toy: Path, write: Write) -> None:
    write(
        toy / DATABASE / "overrides/mill.csv",
        "process,line,role,flow,amount,unit,supplier,flow_name\n"
        'toy:electricity-production,e02,emission,toy:co2,0.3,kg,,"Carbon dioxide, fossil"\n',
    )

    (override,) = read_database(toy / DATABASE).overrides

    assert override.lines[0].flow_name == "Carbon dioxide, fossil"


def test_an_override_amount_may_be_a_formula(toy: Path, write: Write) -> None:
    write(toy / DATABASE / "parameters.yaml", "database:\n  co2: 0.3\n")
    write(toy / DATABASE / "overrides/mill.csv", HEADER + ELECTRICITY_E02.format("co2 * 2"))

    (override,) = read_database(toy / DATABASE).overrides

    assert (override.lines[0].amount, override.lines[0].formula) == (0.6, "co2 * 2")


@pytest.mark.parametrize("name", [".gitkeep", ".DS_Store"])
def test_hidden_files_are_ignored(toy: Path, write: Write, name: str) -> None:
    write(toy / DATABASE / "overrides" / name, "")

    assert read_database(toy / DATABASE).overrides == ()


def test_an_empty_overrides_file_is_fine(toy: Path, write: Write) -> None:
    write(toy / DATABASE / "overrides/none-yet.csv", HEADER)

    assert read_database(toy / DATABASE).overrides == ()


# --- mistakes in the files -------------------------------------------------------------------


def test_a_file_that_is_not_a_csv(toy: Path, write: Write, problems_of: Problems) -> None:
    write(toy / DATABASE / "overrides/notes.txt", "hello")

    problem = only(problems_of(toy / DATABASE))

    assert problem.code == "unknown-file"
    assert problem.item == "overrides/notes.txt"
    assert ".csv" in problem.message


def test_a_column_that_is_missing(toy: Path, write: Write, problems_of: Problems) -> None:
    write(toy / DATABASE / "overrides/mill.csv", "line,role,flow,amount,unit,supplier\n")

    problem = only(problems_of(toy / DATABASE))

    assert problem.code == "bad-columns"
    assert "process" in problem.message


def test_an_amount_that_is_not_a_number(toy: Path, write: Write, problems_of: Problems) -> None:
    write(toy / DATABASE / "overrides/mill.csv", HEADER + ELECTRICITY_E02.format('"0,3"'))

    problem = only(problems_of(toy / DATABASE))

    assert problem.code == "bad-number"
    assert problem.item == "toy:electricity-production line e02"
    assert problem.location == "overrides/mill.csv:2"


def test_a_negative_input_amount(toy: Path, write: Write, problems_of: Problems) -> None:
    write(toy / DATABASE / "overrides/mill.csv", HEADER + FABRIC_E02.format(-1))

    problem = only(problems_of(toy / DATABASE))

    assert problem.code == "negative-amount"
    assert problem.item == "toy:fabric-production line e02"


def test_an_override_of_a_process_of_the_database(
    toy: Path, write: Write, problems_of: Problems
) -> None:
    write(
        toy / DATABASE / "overrides/own.csv",
        HEADER + "tshirt:tshirt,e03,input,toy:electricity,0.4,kWh,toy:electricity-production,\n",
    )

    problem = only(problems_of(toy / DATABASE))

    assert problem.code == "override-database-process"
    assert problem.item == "tshirt:tshirt line e03"
    assert "processes/" in problem.message
    assert problem.location == "overrides/own.csv:2"


def test_the_same_line_twice(toy: Path, write: Write, problems_of: Problems) -> None:
    write(toy / DATABASE / "overrides/a.csv", HEADER + ELECTRICITY_E02.format(0.3))
    write(toy / DATABASE / "overrides/b.csv", HEADER + ELECTRICITY_E02.format(0.2))

    problem = only(problems_of(toy / DATABASE))

    assert problem.code == "override-duplicate"
    assert problem.item == "toy:electricity-production line e02"
    assert "overrides/a.csv:2" in problem.message
    assert problem.location == "overrides/b.csv:2"


def test_the_reference_line_cannot_be_overridden(
    toy: Path, write: Write, problems_of: Problems
) -> None:
    write(
        toy / DATABASE / "overrides/mill.csv",
        HEADER + "toy:electricity-production,e01,reference,toy:electricity,2,kWh,,\n",
    )

    problem = only(problems_of(toy / DATABASE))

    assert problem.code == "override-reference-line"
    assert problem.item == "toy:electricity-production line e01"


def test_a_blank_process_cell(toy: Path, write: Write, problems_of: Problems) -> None:
    write(toy / DATABASE / "overrides/mill.csv", HEADER + ",e02,emission,toy:co2,0.3,kg,,\n")

    problem = only(problems_of(toy / DATABASE))

    assert problem.code == "bad-row"
    assert "process" in problem.message


# --- checks against the background -----------------------------------------------------------


def test_good_overrides_validate(toy: Path, write: Write, background: ProcessFolder) -> None:
    write(
        toy / DATABASE / "overrides/mill.csv",
        HEADER + FABRIC_E02.format(2.5) + ELECTRICITY_E02.format(0.3),
    )

    report = validate(read_database(toy / DATABASE), background=background)

    assert report.errors == ()


def test_an_override_of_a_process_that_is_not_in_the_background(
    toy: Path, write: Write, background: ProcessFolder
) -> None:
    write(
        toy / DATABASE / "overrides/mill.csv",
        HEADER
        + "toy:fabric-productoin,e02,input,toy:electricity,2.5,kWh,toy:electricity-production,\n",
    )

    problem = only(validate(read_database(toy / DATABASE), background=background).errors)

    assert problem.code == "override-unknown-process"
    assert problem.item == "toy:fabric-productoin line e02"
    assert problem.location == "overrides/mill.csv:2"
    assert "Did you mean 'toy:fabric-production'?" in problem.message


def test_an_override_of_a_line_the_process_does_not_have(
    toy: Path, write: Write, background: ProcessFolder
) -> None:
    write(
        toy / DATABASE / "overrides/mill.csv",
        HEADER
        + "toy:fabric-production,e20,input,toy:electricity,2.5,kWh,toy:electricity-production,\n",
    )

    problem = only(validate(read_database(toy / DATABASE), background=background).errors)

    assert problem.code == "override-unknown-line"
    assert problem.item == "toy:fabric-production line e20"
    assert "e01, e02, e03, e04" in problem.message


@pytest.mark.parametrize(
    ("row", "what"),
    [
        ("toy:fabric-production,e02,emission,toy:co2,2.5,kg,,\n", "role"),
        (
            "toy:fabric-production,e02,input,toy:cotton-fabric,2.5,kg,toy:fabric-production,\n",
            "flow",
        ),
    ],
)
def test_an_override_changes_the_amount_not_what_the_line_is(
    toy: Path, write: Write, background: ProcessFolder, row: str, what: str
) -> None:
    write(toy / DATABASE / "overrides/mill.csv", HEADER + row)

    problem = only(validate(read_database(toy / DATABASE), background=background).errors)

    assert problem.code == "override-changes-line"
    assert problem.item == "toy:fabric-production line e02"
    assert what in problem.message
    assert "amount" in problem.message  # says what an override may change


def test_an_override_with_a_unit_that_does_not_fit_the_flow(
    toy: Path, write: Write, background: ProcessFolder
) -> None:
    write(
        toy / DATABASE / "overrides/mill.csv",
        HEADER
        + "toy:fabric-production,e02,input,toy:electricity,2.5,kg,toy:electricity-production,\n",
    )

    problem = only(validate(read_database(toy / DATABASE), background=background).errors)

    assert problem.code == "unit-quantity-mismatch"
    assert problem.item == "toy:fabric-production line e02"


def test_an_override_with_a_supplier_of_another_flow(
    toy: Path, write: Write, background: ProcessFolder
) -> None:
    write(
        toy / DATABASE / "overrides/mill.csv",
        HEADER + "toy:fabric-production,e02,input,toy:electricity,2.5,kWh,toy:fabric-production,\n",
    )

    problem = only(validate(read_database(toy / DATABASE), background=background).errors)

    assert problem.code == "supplier-wrong-flow"
    assert problem.item == "toy:fabric-production line e02"


def test_a_blank_supplier_on_an_input_means_none_and_is_refused(
    toy: Path, write: Write, background: ProcessFolder
) -> None:
    write(
        toy / DATABASE / "overrides/mill.csv",
        HEADER + "toy:fabric-production,e02,input,toy:electricity,2.5,kWh,,\n",
    )

    problem = only(validate(read_database(toy / DATABASE), background=background).errors)

    assert problem.code == "supplier-missing"
    assert problem.item == "toy:fabric-production line e02"


def test_the_check_before_a_calculation_accepts_an_override(
    toy: Path, write: Write, background: ProcessFolder
) -> None:
    write(toy / DATABASE / "overrides/mill.csv", HEADER + ELECTRICITY_E02.format(0.3))
    database = read_database(toy / DATABASE)

    assert validate(database, background=background).errors == ()
    assert validate_database(database, background).problems == ()


def test_a_database_without_overrides_passes_the_calculation_check(
    toy: Path, background: ProcessFolder
) -> None:
    assert validate_database(read_database(toy / DATABASE), background).errors == ()


# --- a background that holds reference lines only (a release) -----------------------------------


def test_a_line_that_cannot_be_checked_is_a_warning_not_silence(
    toy: Path, write: Write, reference_only: ProcessFolder
) -> None:
    write(
        toy / DATABASE / "overrides/mill.csv",
        HEADER
        + "toy:fabric-production,e20,input,toy:electricity,2.5,kWh,toy:electricity-production,\n",
    )

    report = validate(read_database(toy / DATABASE), background=reference_only)

    assert report.errors == ()
    (warning,) = report.warnings
    assert warning.code == "line-not-checked"
    assert warning.item == "toy:fabric-production line e20"
    assert "reference line" in warning.message
    assert warning.location == "overrides/mill.csv:2"


def test_a_line_that_cannot_be_compared_is_still_checked_for_its_own_unit(
    toy: Path, write: Write, reference_only: ProcessFolder
) -> None:
    write(
        toy / DATABASE / "overrides/mill.csv",
        HEADER
        + "toy:fabric-production,e02,input,toy:electricity,2.5,kg,toy:electricity-production,\n",
    )

    report = validate(read_database(toy / DATABASE), background=reference_only)

    assert [p.code for p in report.errors] == ["unit-quantity-mismatch"]
    assert [p.code for p in report.warnings] == ["line-not-checked"]
