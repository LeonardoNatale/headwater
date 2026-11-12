"""`scenarios/<name>.yaml` of a study: a named set of changes, read and checked.

The scenarios here are in the study `tshirt-parameter/tshirt-footprint`, next to the database
`tshirt-parameter/tshirt` that its product `tshirt:tshirt` is in.
"""

from collections.abc import Callable
from pathlib import Path

import pytest

import headwater as hw
from headwater.model import LineChange, ParameterChange, Problem, Severity
from headwater.study import ProcessFolder, build_catalog, owner_of, read_study
from headwater.study.validate import check_scenarios, id_prefixes
from tests.conftest import open_study_folder

from .conftest import TOY_DIR

Write = Callable[[Path, str], None]
Problems = Callable[..., tuple[Problem, ...]]

DATABASE = "tshirt-parameter/tshirt"
STUDY = "tshirt-parameter/tshirt-footprint"
NO_SCENARIOS = "tshirt-dyed/tshirt-and-dye"
ELECTRICITY = "toy:electricity-production"


def only(problems: tuple[Problem, ...]) -> Problem:
    assert len(problems) == 1, [str(p) for p in problems]
    return problems[0]


@pytest.fixture
def scenario(toy: Path, write: Write) -> Callable[[str, str], Path]:
    def _write(name: str, text: str) -> Path:
        """Write `scenarios/<name>.yaml` into the study, and return the study's folder."""
        path = toy / STUDY / "scenarios" / f"{name}.yaml"
        write(path, text)
        return toy / STUDY

    return _write


@pytest.fixture(scope="module")
def background() -> hw.Background:
    """The toy background as committed. Read only."""
    return hw.load(
        TOY_DIR / "background", method=TOY_DIR / "methods" / "toy-climate", namespace="toy"
    )


def errors(study: Path, background: hw.Background) -> tuple[Problem, ...]:
    """The errors `validate` finds in the study at `study`, its scenarios checked against
    `background`."""
    return open_study_folder(study).validate(background).errors


# --- reading ---------------------------------------------------------------------------------


def test_every_kind_of_change(
    scenario: Callable[[str, str], Path], toy: Path, write: Write
) -> None:
    write(
        toy / DATABASE / "parameters.yaml",
        "database:\n  fabric_kg: 0.25\n  loss: 0.05\nprocesses:\n  tshirt:tshirt:\n    cut: 0.1\n",
    )
    study = scenario(
        "everything",
        """\
description: One of each
changes:
  - process: toy:electricity-production
    line: e02
    amount: 0.3
  - process: toy:fabric-production
    line: e02
    amount: 2 * fabric_kg
  - process: tshirt:tshirt
    line: e03
    supplier: toy:electricity-production
  - process: toy:fabric-production
    line: e03
    amount: 2000
    unit: g
  - parameter: fabric_kg
    value: 0.30
  - parameter: loss
    value: "fabric_kg / 10"
  - parameter: cut
    process: tshirt:tshirt
    value: 0.2
""",
    )

    folder = read_study(study)

    everything = next(s for s in folder.scenarios if s.name == "everything")
    assert everything.description == "One of each"
    lines = {c.label: c for c in everything.line_changes}
    assert lines["toy:electricity-production line e02"].amount == 0.3
    worked_out = lines["toy:fabric-production line e02"]
    assert (worked_out.amount, worked_out.formula) == (
        pytest.approx(0.6, rel=1e-12),
        "2 * fabric_kg",
    )
    assert lines["tshirt:tshirt line e03"].supplier_id == ELECTRICITY
    grams = lines["toy:fabric-production line e03"]
    assert (grams.amount, grams.unit) == (2000, "g")
    changes = everything.parameter_changes
    assert [(c.parameter, c.database, c.value, c.formula) for c in changes] == [
        ("fabric_kg", "tshirt", 0.3, None),
        ("loss", "tshirt", None, "fabric_kg / 10"),
        ("tshirt:tshirt/cut", "tshirt", 0.2, None),
    ]


def test_the_scenarios_of_a_study_are_listed_by_name(
    scenario: Callable[[str, str], Path],
) -> None:
    study = scenario("b-second", "changes:\n  - {parameter: fabric_kg, value: 0.4}\n")
    scenario("a-first", "changes:\n  - {parameter: fabric_kg, value: 0.2}\n")

    assert [s.name for s in read_study(study).scenarios] == [
        "a-first",
        "b-second",
        "more-fabric",
    ]


def test_a_scenario_formula_is_worked_out_with_the_parameters_of_the_scenario(
    scenario: Callable[[str, str], Path],
) -> None:
    study = scenario(
        "heavy",
        """\
changes:
  - parameter: fabric_kg
    value: 0.30
  - process: toy:fabric-production
    line: e02
    amount: fabric_kg * 10
""",
    )

    heavy = next(s for s in read_study(study).scenarios if s.name == "heavy")

    (change,) = heavy.line_changes
    assert change.amount == pytest.approx(3.0, rel=1e-12)  # not 0.25 * 10


def test_a_study_without_scenarios_has_none(toy: Path) -> None:
    assert read_study(toy / NO_SCENARIOS).scenarios == ()


@pytest.mark.parametrize("name", [".gitkeep", ".DS_Store"])
def test_hidden_files_are_ignored(toy: Path, write: Write, name: str) -> None:
    write(toy / NO_SCENARIOS / "scenarios" / name, "")

    assert read_study(toy / NO_SCENARIOS).scenarios == ()


# --- mistakes in the files -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "hint"), [("notes.txt", ".yaml"), ("grid.yml", ".yaml"), ("grid.json", ".yaml")]
)
def test_a_file_that_is_not_a_yaml_file(
    toy: Path, write: Write, problems_of: Problems, name: str, hint: str
) -> None:
    write(toy / STUDY / "scenarios" / name, "changes: []\n")

    problem = only(problems_of(toy / STUDY, read_study))

    assert problem.code == "unknown-file"
    assert problem.item == f"scenarios/{name}"
    assert hint in problem.message


def test_a_scenario_name_that_is_not_a_name(
    scenario: Callable[[str, str], Path], problems_of: Problems
) -> None:
    study = scenario("bad name", "changes:\n  - {parameter: fabric_kg, value: 0.4}\n")

    problem = only(problems_of(study, read_study))

    assert problem.code == "invalid-field"
    assert "bad name" in problem.item


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        ("description: nothing here\n", "changes"),
        ("changes: []\n", "at least one change"),
        ("changes: {a: 1}\n", "list"),
        ("changes:\n  - 5\n", "mapping"),
        ("changes:\n  - {value: 1}\n", "line"),
        ("chages:\n  - {parameter: fabric_kg, value: 1}\n", "Did you mean 'changes'?"),
    ],
)
def test_a_scenario_file_with_the_wrong_shape(
    scenario: Callable[[str, str], Path], problems_of: Problems, text: str, fragment: str
) -> None:
    study = scenario("broken", text)

    problem = only(problems_of(study, read_study))

    assert problem.code in ("invalid-field", "bad-yaml")
    assert fragment in str(problem)
    assert "broken" in str(problem)


def test_a_key_that_does_not_exist_in_a_change(
    scenario: Callable[[str, str], Path], problems_of: Problems
) -> None:
    study = scenario(
        "broken", "changes:\n  - {process: toy:fabric-production, line: e02, ammount: 2}\n"
    )

    problem = only(problems_of(study, read_study))

    assert "'ammount'" in problem.message
    assert "Did you mean 'amount'?" in problem.message
    assert problem.item == "scenario broken: change 1"


def test_a_change_that_changes_nothing(
    scenario: Callable[[str, str], Path], problems_of: Problems
) -> None:
    study = scenario("broken", "changes:\n  - {process: toy:fabric-production, line: e02}\n")

    problem = only(problems_of(study, read_study))

    assert "changes nothing" in problem.message
    assert problem.item == "scenario broken: toy:fabric-production line e02"


def test_a_yes_as_an_amount(scenario: Callable[[str, str], Path], problems_of: Problems) -> None:
    study = scenario(
        "broken", "changes:\n  - {process: toy:fabric-production, line: e02, amount: yes}\n"
    )

    problem = only(problems_of(study, read_study))

    assert "yes/no" in problem.message


def test_a_parameter_that_does_not_exist(
    scenario: Callable[[str, str], Path], problems_of: Problems
) -> None:
    study = scenario("broken", "changes:\n  - {parameter: fabric_kgg, value: 0.4}\n")

    problem = only(problems_of(study, read_study))

    assert problem.code == "unknown-parameter"
    assert problem.item == "scenario broken: parameter fabric_kgg"
    assert "Did you mean 'fabric_kg'?" in problem.message


def test_a_scenario_formula_with_an_unknown_name(
    scenario: Callable[[str, str], Path], problems_of: Problems
) -> None:
    study = scenario(
        "broken", "changes:\n  - {process: toy:fabric-production, line: e02, amount: nope * 2}\n"
    )

    problem = only(problems_of(study, read_study))

    assert problem.code == "unknown-parameter"
    assert problem.item == "scenario broken: toy:fabric-production line e02"


def test_a_change_that_makes_a_loop_of_parameters(
    scenario: Callable[[str, str], Path], toy: Path, write: Write, problems_of: Problems
) -> None:
    write(
        toy / DATABASE / "parameters.yaml", "database:\n  fabric_kg: 0.25\n  gross: fabric_kg * 2\n"
    )
    study = scenario("broken", "changes:\n  - {parameter: fabric_kg, value: gross}\n")

    problem = only(problems_of(study, read_study))

    assert problem.code == "parameter-cycle"
    assert problem.item.startswith("scenario broken: ")


def test_the_same_line_changed_twice(
    scenario: Callable[[str, str], Path], problems_of: Problems
) -> None:
    study = scenario(
        "broken",
        "changes:\n"
        "  - {process: toy:fabric-production, line: e02, amount: 2}\n"
        "  - {process: toy:fabric-production, line: e02, amount: 3}\n",
    )

    problem = only(problems_of(study, read_study))

    assert problem.code == "scenario-duplicate-change"
    assert problem.item == "scenario broken: toy:fabric-production line e02"


def test_the_same_parameter_changed_twice(
    scenario: Callable[[str, str], Path], problems_of: Problems
) -> None:
    study = scenario(
        "broken",
        "changes:\n  - {parameter: fabric_kg, value: 1}\n  - {parameter: fabric_kg, value: 2}\n",
    )

    problem = only(problems_of(study, read_study))

    assert problem.code == "scenario-duplicate-change"
    assert problem.item == "scenario broken: parameter fabric_kg"


def test_a_line_of_a_database_process_that_does_not_exist(
    scenario: Callable[[str, str], Path], problems_of: Problems
) -> None:
    study = scenario("broken", "changes:\n  - {process: 'tshirt:tshirt', line: e22, amount: 1}\n")

    problem = only(problems_of(study, read_study))

    assert problem.code == "scenario-unknown-line"
    assert problem.item == "scenario broken: tshirt:tshirt line e22"
    assert "e02" in problem.message


def test_the_reference_line_cannot_be_changed(
    scenario: Callable[[str, str], Path], problems_of: Problems
) -> None:
    study = scenario("broken", "changes:\n  - {process: 'tshirt:tshirt', line: e01, amount: 2}\n")

    problem = only(problems_of(study, read_study))

    assert problem.code == "scenario-reference-line"
    assert problem.item == "scenario broken: tshirt:tshirt line e01"


def test_a_scenario_cannot_change_a_line_that_an_override_changes(
    scenario: Callable[[str, str], Path], toy: Path, write: Write, problems_of: Problems
) -> None:
    write(
        toy / DATABASE / "overrides/mill.csv",
        "process,line,role,flow,amount,unit,supplier\n"
        "toy:electricity-production,e02,emission,toy:co2,0.3,kg,\n",
    )
    study = scenario(
        "broken", "changes:\n  - {process: toy:electricity-production, line: e02, amount: 0.2}\n"
    )

    problem = only(problems_of(study, read_study))

    assert problem.code == "scenario-overlaps-override"
    assert problem.item == "scenario broken: toy:electricity-production line e02"
    assert "overrides/mill.csv" in problem.message


def test_a_parameter_change_that_makes_an_amount_negative(
    scenario: Callable[[str, str], Path], toy: Path, write: Write, problems_of: Problems
) -> None:
    write(
        toy / DATABASE / "processes/tshirt.csv",
        "line,role,flow,amount,unit,supplier\n"
        "e01,reference,tshirt:tshirt,1,unit,\n"
        "e02,input,toy:cotton-fabric,fabric_kg - 0.1,kg,toy:fabric-production\n"
        "e03,input,toy:electricity,0.5,kWh,toy:electricity-production\n",
    )
    study = scenario("small", "changes:\n  - {parameter: fabric_kg, value: 0.05}\n")

    problem = only(problems_of(study, read_study))

    assert problem.code == "negative-amount"
    assert problem.item == "scenario small: tshirt:tshirt line e02"
    assert "'fabric_kg - 0.1'" in problem.message


def test_a_scenario_that_puts_a_negative_amount_on_a_database_line(
    scenario: Callable[[str, str], Path], problems_of: Problems
) -> None:
    study = scenario("broken", "changes:\n  - {process: 'tshirt:tshirt', line: e03, amount: -1}\n")

    problem = only(problems_of(study, read_study))

    assert problem.code == "negative-amount"
    assert problem.item == "scenario broken: tshirt:tshirt line e03"


# --- checks against the background -----------------------------------------------------------


def test_the_scenarios_of_the_fixtures_validate(background: hw.Background) -> None:
    # cleaner-dye changes a line of dyes, a database the T-shirt uses.
    for case in ("tshirt-parameter", "tshirt-grid-scenario", "tshirt-dyed"):
        study = TOY_DIR / case / "tshirt-footprint"
        assert read_study(study).scenarios != ()
        assert errors(study, background) == ()


def test_a_scenario_that_changes_a_process_that_does_not_exist(
    scenario: Callable[[str, str], Path], background: hw.Background
) -> None:
    study = scenario(
        "broken", "changes:\n  - {process: toy:electricity-productoin, line: e02, amount: 0.3}\n"
    )

    problem = only(errors(study, background))

    assert problem.code == "scenario-unknown-process"
    assert problem.item == "scenario broken: toy:electricity-productoin line e02"
    assert "Did you mean 'toy:electricity-production'?" in problem.message


def test_a_scenario_that_changes_a_line_the_background_process_does_not_have(
    scenario: Callable[[str, str], Path], background: hw.Background
) -> None:
    study = scenario(
        "broken", "changes:\n  - {process: toy:electricity-production, line: e20, amount: 0.3}\n"
    )

    problem = only(errors(study, background))

    assert problem.code == "scenario-unknown-line"
    assert problem.item == "scenario broken: toy:electricity-production line e20"
    assert "e01, e02, e03" in problem.message


def test_a_scenario_supplier_that_makes_another_flow(
    scenario: Callable[[str, str], Path], background: hw.Background
) -> None:
    study = scenario(
        "broken",
        "changes:\n"
        "  - {process: toy:fabric-production, line: e02, supplier: toy:fabric-production}\n",
    )

    problem = only(errors(study, background))

    assert problem.code == "supplier-wrong-flow"
    assert problem.item == "scenario broken: toy:fabric-production line e02"
    assert problem.location == "scenarios/broken.yaml"


def test_a_scenario_unit_that_does_not_fit_the_flow(
    scenario: Callable[[str, str], Path], background: hw.Background
) -> None:
    study = scenario(
        "broken", "changes:\n  - {process: toy:fabric-production, line: e02, unit: kg}\n"
    )

    problem = only(errors(study, background))

    assert problem.code == "unit-quantity-mismatch"
    assert problem.item == "scenario broken: toy:fabric-production line e02"


def test_a_scenario_amount_that_breaks_a_background_line(
    scenario: Callable[[str, str], Path], background: hw.Background
) -> None:
    study = scenario(
        "broken", "changes:\n  - {process: toy:fabric-production, line: e02, amount: -1}\n"
    )

    problem = only(errors(study, background))

    assert problem.code == "negative-amount"
    assert problem.item == "scenario broken: toy:fabric-production line e02"


def test_a_scenario_cannot_change_the_reference_line_of_a_background_process(
    scenario: Callable[[str, str], Path], background: hw.Background
) -> None:
    study = scenario(
        "broken", "changes:\n  - {process: toy:fabric-production, line: e01, amount: 2}\n"
    )

    problem = only(errors(study, background))

    assert problem.code == "scenario-reference-line"


def test_the_types_of_a_change_are_records(scenario: Callable[[str, str], Path]) -> None:
    study = scenario(
        "mixed",
        "changes:\n"
        "  - {parameter: fabric_kg, value: 1}\n"
        "  - {process: toy:fabric-production, line: e02, amount: 2}\n",
    )

    mixed = next(s for s in read_study(study).scenarios if s.name == "mixed")

    assert [type(c) for c in mixed.changes] == [ParameterChange, LineChange]


def test_a_scenario_line_that_cannot_be_checked_is_a_warning_not_silence(
    scenario: Callable[[str, str], Path], reference_only: ProcessFolder
) -> None:
    # A study's validate always has every line of a background process its scenarios change,
    # so only `check_scenarios` with an incomplete catalog meets such a line.
    study = scenario(
        "late", "changes:\n  - {process: toy:electricity-production, line: e20, amount: 0.3}\n"
    )
    folder = read_study(study)
    reached = list(folder.reached.values())
    catalog = build_catalog(*reached, reference_only)
    prefixes = id_prefixes(p.id for p in reference_only.processes)

    found = check_scenarios(folder.scenarios, catalog, False, owner_of(reached), prefixes)

    (warning,) = found
    assert warning.severity is Severity.warning
    assert warning.code == "line-not-checked"
    assert warning.item == "scenario late: toy:electricity-production line e20"
    assert warning.location == "scenarios/late.yaml"


@pytest.mark.parametrize("text", ["5", "[a, b]", "{a: 1}"])
def test_a_description_that_is_not_text(
    scenario: Callable[[str, str], Path], problems_of: Problems, text: str
) -> None:
    study = scenario(
        "odd", f"description: {text}\nchanges:\n  - {{parameter: fabric_kg, value: 1}}\n"
    )

    problem = only(problems_of(study, read_study))

    assert problem.code == "invalid-field"
    assert problem.item == "scenario odd: description"
    assert "text" in problem.message


@pytest.mark.parametrize(
    "entry", ["{line: e02, amount: 1}", "{process: 'tshirt:tshirt', amount: 1}"]
)
def test_a_line_change_needs_both_the_process_and_the_line(
    scenario: Callable[[str, str], Path], problems_of: Problems, entry: str
) -> None:
    study = scenario("broken", f"changes:\n  - {entry}\n")

    problem = only(problems_of(study, read_study))

    assert problem.item == "scenario broken: change 1"
    assert "`process` and `line`" in problem.message
