"""`parameters.yaml`, and formulas in the amounts of a process file."""

from collections.abc import Callable
from pathlib import Path

import pytest

from headwater.model import Problem
from headwater.study import read_database, read_process_folder

Write = Callable[[Path, str], None]
Problems = Callable[..., tuple[Problem, ...]]

DATABASE = "tshirt/tshirt"
PARAMETERS = f"{DATABASE}/parameters.yaml"
TSHIRT_CSV = f"{DATABASE}/processes/tshirt.csv"


def only(problems: tuple[Problem, ...]) -> Problem:
    assert len(problems) == 1, [str(p) for p in problems]
    return problems[0]


# --- reading ---------------------------------------------------------------------------------


def test_values_and_formulas_of_the_database_and_of_a_process(toy: Path, write: Write) -> None:
    write(
        toy / PARAMETERS,
        """\
database:
  fabric_kg:
    value: 0.25
    unit: kg
    comment: From the bill of materials
  loss: 0.05
  gross: fabric_kg * (1 + loss)
  same_as_loss: "0.05"
processes:
  tshirt:tshirt:
    cut_ratio: {value: 0.1}
    used: fabric_kg * (1 - cut_ratio)
""",
    )

    folder = read_database(toy / DATABASE)

    assert dict(folder.parameters.values) == pytest.approx(
        {
            "fabric_kg": 0.25,
            "loss": 0.05,
            "gross": 0.2625,
            "same_as_loss": 0.05,
            "tshirt:tshirt/cut_ratio": 0.1,
            "tshirt:tshirt/used": 0.225,
        },
        rel=1e-12,
    )
    fabric = folder.parameters.get("fabric_kg")
    assert fabric is not None
    assert (fabric.unit, fabric.comment) == ("kg", "From the bill of materials")
    gross = folder.parameters.get("gross")
    assert gross is not None
    assert gross.formula == "fabric_kg * (1 + loss)"
    same = folder.parameters.get("same_as_loss")
    assert same is not None and same.formula is None  # a text that is a number is a value


def test_a_database_without_parameters_has_an_empty_set(toy: Path) -> None:
    assert len(read_database(toy / DATABASE).parameters) == 0


@pytest.mark.parametrize("text", ["", "# nothing yet\n", "database: {}\n", "processes: {}\n"])
def test_an_empty_parameters_file_is_fine(toy: Path, write: Write, text: str) -> None:
    write(toy / PARAMETERS, text)

    assert len(read_database(toy / DATABASE).parameters) == 0


def test_an_amount_can_be_a_formula(toy: Path, write: Write, edit: Callable[..., None]) -> None:
    write(toy / PARAMETERS, "database:\n  fabric_kg: 0.25\n  loss: 0.05\n")
    edit(toy / TSHIRT_CSV, "0.25,kg", "fabric_kg * (1 + loss),kg")

    folder = read_database(toy / DATABASE)

    (process,) = folder.processes
    line = next(e for e in process.exchanges if e.line_id == "e02")
    assert line.amount == pytest.approx(0.2625, rel=1e-12)
    assert line.formula == "fabric_kg * (1 + loss)"
    other = next(e for e in process.exchanges if e.line_id == "e03")
    assert (other.amount, other.formula) == (0.5, None)  # a plain number stays a number


def test_a_process_parameter_is_used_by_its_own_process(
    toy: Path, write: Write, edit: Callable[..., None]
) -> None:
    write(toy / PARAMETERS, "processes:\n  tshirt:tshirt:\n    kwh: 0.4\n")
    edit(toy / TSHIRT_CSV, "0.5,kWh", "kwh * 2,kWh")

    folder = read_database(toy / DATABASE)

    line = next(e for e in folder.processes[0].exchanges if e.line_id == "e03")
    assert line.amount == pytest.approx(0.8, rel=1e-12)


def test_a_parameter_for_an_overridden_background_process_is_allowed(
    toy: Path, write: Write
) -> None:
    write(toy / PARAMETERS, "processes:\n  toy:fabric-production:\n    kwh_per_kg: 2.5\n")
    write(
        toy / DATABASE / "overrides/weaving.csv",
        "process,line,role,flow,amount,unit,supplier\n"
        "toy:fabric-production,e02,input,toy:electricity,kwh_per_kg,kWh,toy:electricity-production\n",
    )

    folder = read_database(toy / DATABASE)

    (override,) = folder.overrides
    assert (override.lines[0].amount, override.lines[0].formula) == (2.5, "kwh_per_kg")


# --- mistakes in parameters.yaml -------------------------------------------------------------


def test_a_flat_file_says_where_parameters_go(
    toy: Path, write: Write, problems_of: Problems
) -> None:
    write(toy / PARAMETERS, "kwh_per_kg: 3\n")

    problem = only(problems_of(toy / DATABASE))

    assert problem.code == "invalid-field"
    assert problem.item == "parameters.yaml: kwh_per_kg"
    assert "database" in problem.message and "processes" in problem.message
    assert problem.location == "parameters.yaml"


def test_a_misspelt_section_gets_a_suggestion(
    toy: Path, write: Write, problems_of: Problems
) -> None:
    write(toy / PARAMETERS, "databases:\n  a: 1\n")

    problem = only(problems_of(toy / DATABASE))

    assert problem.item == "parameters.yaml: databases"
    assert "Did you mean 'database'?" in problem.message


def test_a_study_section_says_to_write_database(
    toy: Path, write: Write, problems_of: Problems
) -> None:
    write(toy / PARAMETERS, "study:\n  fabric_kg: 0.25\n")

    problem = only(problems_of(toy / DATABASE))

    assert problem.code == "invalid-field"
    assert problem.item == "parameters.yaml: study"
    assert "`database:`" in problem.message


def test_a_section_must_map_names_to_entries(
    toy: Path, write: Write, problems_of: Problems
) -> None:
    write(toy / PARAMETERS, "database: [1, 2]\n")

    problem = only(problems_of(toy / DATABASE))

    assert problem.item == "parameters.yaml: database"
    assert "mapping" in problem.message


def test_an_entry_with_a_value_and_a_formula(
    toy: Path, write: Write, problems_of: Problems
) -> None:
    write(toy / PARAMETERS, "database:\n  x: {value: 1, formula: '2'}\n")

    problem = only(problems_of(toy / DATABASE))

    assert problem.item == "parameter x"
    assert "value or a formula" in problem.message


def test_an_entry_key_that_does_not_exist(toy: Path, write: Write, problems_of: Problems) -> None:
    write(toy / PARAMETERS, "database:\n  x: {valu: 1}\n")

    problem = only(problems_of(toy / DATABASE))

    assert problem.item == "parameter x"
    assert "'valu'" in problem.message
    assert "Did you mean 'value'?" in problem.message


def test_a_yes_as_a_value(toy: Path, write: Write, problems_of: Problems) -> None:
    write(toy / PARAMETERS, "database:\n  x: yes\n")

    problem = only(problems_of(toy / DATABASE))

    assert problem.item == "parameter x"
    assert "yes/no" in problem.message


def test_a_name_that_yaml_reads_as_a_yes_or_no(
    toy: Path, write: Write, problems_of: Problems
) -> None:
    write(toy / PARAMETERS, "database:\n  on: 1\n")

    problem = only(problems_of(toy / DATABASE))

    assert problem.code == "invalid-field"
    assert "quotes" in problem.message


def test_a_name_that_is_not_a_name(toy: Path, write: Write, problems_of: Problems) -> None:
    write(toy / PARAMETERS, "database:\n  fabric kg: 1\n")

    problem = only(problems_of(toy / DATABASE))

    assert problem.item == "parameter fabric kg"
    assert "'fabric kg'" in problem.message


def test_a_parameter_written_twice_is_a_yaml_error(
    toy: Path, write: Write, problems_of: Problems
) -> None:
    write(toy / PARAMETERS, "database:\n  x: 1\n  x: 2\n")

    problem = only(problems_of(toy / DATABASE))

    assert problem.code == "bad-yaml"
    assert "duplicate key" in problem.message


def test_a_loop_names_the_parameters_in_it(toy: Path, write: Write, problems_of: Problems) -> None:
    write(toy / PARAMETERS, "database:\n  a: b\n  b: c\n  c: a + 1\n")

    problem = only(problems_of(toy / DATABASE))

    assert problem.code == "parameter-cycle"
    assert "a → b → c → a" in problem.message
    assert problem.location == "parameters.yaml"


def test_an_unknown_name_in_a_parameter_formula(
    toy: Path, write: Write, problems_of: Problems
) -> None:
    write(toy / PARAMETERS, "database:\n  fabric_kg: 0.25\n  gross: fabric_kgg * 2\n")

    problem = only(problems_of(toy / DATABASE))

    assert problem.code == "unknown-parameter"
    assert problem.item == "parameter gross"
    assert "Did you mean 'fabric_kg'?" in problem.message


def test_a_scope_that_is_not_a_process_of_the_database(
    toy: Path, write: Write, problems_of: Problems
) -> None:
    write(toy / PARAMETERS, "processes:\n  tshirt:tshirtt:\n    x: 1\n")

    problem = only(problems_of(toy / DATABASE))

    assert problem.code == "unknown-scope"
    assert problem.item == "parameters.yaml: processes: tshirt:tshirtt"
    assert "Did you mean 'tshirt:tshirt'?" in problem.message


# --- mistakes in the formula of an amount ----------------------------------------------------


def test_an_amount_with_an_unknown_parameter(
    toy: Path, write: Write, edit: Callable[..., None], problems_of: Problems
) -> None:
    write(toy / PARAMETERS, "database:\n  fabric_kg: 0.25\n")
    edit(toy / TSHIRT_CSV, "0.25,kg", "fabric_kgg,kg")

    problem = only(problems_of(toy / DATABASE))

    assert problem.code == "unknown-parameter"
    assert problem.item == "tshirt:tshirt line e02"
    assert "'fabric_kgg'" in problem.message
    assert "Did you mean 'fabric_kg'?" in problem.message
    assert problem.location == "processes/tshirt.csv:3"


def test_a_word_in_an_amount_when_the_database_has_no_parameters(
    toy: Path, edit: Callable[..., None], problems_of: Problems
) -> None:
    edit(toy / TSHIRT_CSV, "0.25,kg", "fabric_kg,kg")

    problem = only(problems_of(toy / DATABASE))

    assert problem.code == "bad-number"  # most likely a mistyped number
    assert problem.item == "tshirt:tshirt line e02"
    assert "parameters.yaml" in problem.message


def test_a_formula_without_names_needs_no_parameters(toy: Path, edit: Callable[..., None]) -> None:
    edit(toy / TSHIRT_CSV, "0.25,kg", "1 / 4,kg")

    line = next(
        e for e in read_database(toy / DATABASE).processes[0].exchanges if e.line_id == "e02"
    )

    assert (line.amount, line.formula) == (0.25, "1 / 4")


def test_an_amount_that_is_not_a_formula(
    toy: Path, edit: Callable[..., None], problems_of: Problems
) -> None:
    edit(toy / TSHIRT_CSV, "0.25,kg", "0.25 *,kg")

    problem = only(problems_of(toy / DATABASE))

    assert problem.code == "bad-formula"
    assert problem.item == "tshirt:tshirt line e02"
    assert "'0.25 *'" in problem.message
    assert problem.location == "processes/tshirt.csv:3"


def test_an_amount_that_divides_by_zero(
    toy: Path, write: Write, edit: Callable[..., None], problems_of: Problems
) -> None:
    write(toy / PARAMETERS, "database:\n  zero: 0\n")
    edit(toy / TSHIRT_CSV, "0.25,kg", "1 / zero,kg")

    problem = only(problems_of(toy / DATABASE))

    assert problem.code == "bad-formula"
    assert "division by zero" in problem.message


def test_a_formula_that_gives_a_negative_input(
    toy: Path, write: Write, edit: Callable[..., None], problems_of: Problems
) -> None:
    write(toy / PARAMETERS, "database:\n  a: 0.25\n")
    edit(toy / TSHIRT_CSV, "0.25,kg", "a - 1,kg")

    problem = only(problems_of(toy / DATABASE))

    assert problem.code == "negative-amount"
    assert "'a - 1'" in problem.message


def test_a_decimal_comma_is_still_a_number_mistake(
    toy: Path, write: Write, edit: Callable[..., None], problems_of: Problems
) -> None:
    write(toy / PARAMETERS, "database:\n  a: 1\n")
    edit(toy / TSHIRT_CSV, "0.25,kg", '"0,25",kg')

    problem = only(problems_of(toy / DATABASE))

    assert problem.code == "bad-number"
    assert "decimal separator" in problem.message


def test_a_problem_in_parameters_does_not_bury_the_lines_that_use_them(
    toy: Path, write: Write, edit: Callable[..., None], problems_of: Problems
) -> None:
    write(toy / PARAMETERS, "database:\n  a: b\n  b: a\n")
    edit(toy / TSHIRT_CSV, "0.25,kg", "a,kg")

    problem = only(problems_of(toy / DATABASE))

    assert problem.code == "parameter-cycle"


def test_a_formula_in_a_folder_that_is_not_a_database_is_not_a_number(
    toy: Path, edit: Callable[..., None], problems_of: Problems
) -> None:
    edit(toy / "background/processes/fabric-production.csv", "3,kWh", "kwh_per_kg,kWh")

    problem = only(problems_of(toy / "background", read_process_folder))

    assert problem.code == "bad-number"
    assert "kwh_per_kg" in problem.message
    assert "parameters.yaml" in problem.message


def test_a_value_text_and_a_formula_are_not_merged(
    toy: Path, write: Write, problems_of: Problems
) -> None:
    write(toy / PARAMETERS, "database:\n  a: 1\n  x: {value: 'a * 2', formula: '3'}\n")

    problem = only(problems_of(toy / DATABASE))

    assert problem.item == "parameter x"
    assert "value or a formula" in problem.message
