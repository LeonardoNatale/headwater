"""Parameters and formulas: copied into `parameters.yaml` and the amount column with headwater's
own syntax, and reported when they cannot be."""

from pathlib import Path

import pytest

from headwater.io.excel import ExcelImport, Status, import_excel
from headwater.model import Role
from headwater.release import Release
from headwater.study import read_database

from .conftest import Cell, Sheet, Workbooks, block, database, line

PARAMETER_HEADER: list[Cell] = ["name", "amount", "formula", "unit", "comment"]


def parameters(label: str, *rows: list[Cell], group: str | None = None) -> Sheet:
    return [[label, group] if group else [label], list(PARAMETER_HEADER), *rows, []]


def run(workbooks: Workbooks, release: Release, out: Path, *parts: Sheet) -> ExcelImport:
    rows = [*database(), *(row for part in parts for row in part)]
    return import_excel(workbooks.write({"a": rows}), release, out)


def sand(amount: Cell, formula: str | None, **kwargs: str | None) -> list[Cell]:
    return line("market for sand", amount, "kilogram", "GLO", product="sand", formula=formula)


def lines(done: ExcelImport, process: str = "widget"):  # noqa: ANN201
    return next(p for p in read_database(done.folder).processes if p.name == process).exchanges


def test_project_database_and_activity_parameters_land_in_their_scopes(
    workbooks: Workbooks, mini_release: Release, out: Path
) -> None:
    done = run(
        workbooks,
        mini_release,
        out,
        parameters("Project parameters", ["loss", 0.05, None, None, "Scrap rate"]),
        parameters("Database parameters", ["gross", 1.05, "1 + loss", None, None]),
        block(
            "widget",
            sand(0.0, "gross * share"),
            parameters=parameters("Parameters", ["share", 0.5], group="widget group")[:-1],
        ),
    )
    study = read_database(done.folder)
    assert study.parameters.values["loss"] == 0.05
    assert study.parameters.values["gross"] == pytest.approx(1.05)
    assert study.parameters.values["mine:widget-glo/share"] == 0.5
    text = (done.folder / "parameters.yaml").read_text()
    assert "gross: 1 + loss" in text
    assert "comment: Scrap rate" in text
    assert "mine:widget-glo:" in text
    assert lines(done)[1].formula == "gross * share"
    assert lines(done)[1].amount == pytest.approx(0.525)
    assert done.report.parameters_copied == 3 and not done.report.parameters_failed


def test_a_formula_follows_the_parameter_it_names(
    workbooks: Workbooks, mini_release: Release, out: Path
) -> None:
    done = run(
        workbooks,
        mini_release,
        out,
        parameters("Project parameters", ["kg_sand", 2, None, None, None]),
        block("widget", sand(2, "kg_sand * 3")),
    )
    assert lines(done)[1].amount == 6.0
    assert any("gives 6" in n.message and "says 2" in n.message for n in done.report.notes)


def test_a_formula_that_agrees_with_the_amount_next_to_it_makes_no_note(
    workbooks: Workbooks, mini_release: Release, out: Path
) -> None:
    done = run(
        workbooks,
        mini_release,
        out,
        parameters("Project parameters", ["kg_sand", 2, None, None, None]),
        block("widget", sand(6, "kg_sand * 3")),
    )
    assert not [n for n in done.report.notes if "formula" in n.message]
    assert done.report.clean


def test_a_formula_with_no_amount_next_to_it_gives_the_amount(
    workbooks: Workbooks, mini_release: Release, out: Path
) -> None:
    done = run(
        workbooks,
        mini_release,
        out,
        parameters("Project parameters", ["kg_sand", 2, None, None, None]),
        block("widget", sand(None, "kg_sand * 3")),
    )
    assert lines(done)[1].amount == 6.0 and lines(done)[1].formula == "kg_sand * 3"
    assert done.report.clean


def test_waste_sent_for_treatment_keeps_its_formula_with_the_sign_turned(
    workbooks: Workbooks, mini_release: Release, out: Path
) -> None:
    done = run(
        workbooks,
        mini_release,
        out,
        parameters("Project parameters", ["water_m3", 0.5, None, None, None]),
        block(
            "widget",
            line(
                "treatment of wastewater",
                -0.5,
                "cubic meter",
                "RoW",
                product="wastewater",
                formula="-water_m3",
            ),
        ),
    )
    waste = lines(done)[1]
    assert waste.role is Role.waste_to_treatment
    assert waste.formula == "-(-water_m3)" and waste.amount == 0.5
    assert done.report.clean
    # Moving the parameter moves the line: more water, more waste.
    path = done.folder / "parameters.yaml"
    path.write_text(path.read_text().replace("0.5", "0.8"))
    assert lines(done)[1].amount == pytest.approx(0.8)


@pytest.mark.parametrize(
    ("formula", "code", "words"),
    [
        ("kg_sand ^ 2", "formula-unsupported", "exclusive or"),
        ("sin(kg_sand)", "formula-unsupported", "unknown function"),
        ("np.sqrt(kg_sand)", "formula-unsupported", "np"),
        ("kg_sand / 0", "formula-unsupported", "zero"),
        ("kg_sand + nope", "formula-unknown-parameter", "nope"),
        ("[1, 2]", "formula-unsupported", "list"),
    ],
)
def test_a_formula_the_syntax_cannot_express_is_reported_and_the_number_is_kept(
    formula: str, code: str, words: str, workbooks: Workbooks, mini_release: Release, out: Path
) -> None:
    done = run(
        workbooks,
        mini_release,
        out,
        parameters("Project parameters", ["kg_sand", 2, None, None, None]),
        block("widget", sand(7, formula)),
    )
    (found,) = [o for o in done.report.outcomes if o.code.startswith("formula")]
    assert (found.status, found.code) == (Status.unexpressible, code)
    assert words in found.message
    sand_line = lines(done)[1]
    assert sand_line.amount == 7.0 and sand_line.formula is None
    assert sand_line.supplier_id == "mini:sand-glo"  # still linked
    assert f"formula not copied: {formula}" in (sand_line.comment or "")
    assert not done.report.clean


def test_a_parameter_headwater_cannot_hold_is_reported_and_so_is_what_uses_it(
    workbooks: Workbooks, mini_release: Release, out: Path
) -> None:
    done = run(
        workbooks,
        mini_release,
        out,
        parameters(
            "Project parameters",
            ["max", 3, None, None, None],  # the name of a function
            ["loop_a", None, "loop_b + 1", None, None],
            ["loop_b", None, "loop_a + 1", None, None],
            ["empty", None, None, None, None],
            ["fine", 2, None, None, None],
            ["uses_max", None, "max + 1", None, None],
        ),
        block("widget", sand(4, "max * 2")),
    )
    failed = {p.name: p for p in done.report.parameters_failed}
    assert set(failed) == {"max", "loop_a", "loop_b", "empty", "uses_max"}
    assert "function" in failed["max"].message
    assert "loop" in failed["loop_a"].message + failed["loop_b"].message
    assert "no value" in failed["empty"].message
    assert done.report.parameters_copied == 1
    assert read_database(done.folder).parameters.values == {"fine": 2.0}
    (formula,) = [o for o in done.report.outcomes if o.code.startswith("formula")]
    assert formula.status is Status.unexpressible


def test_a_database_parameter_takes_the_place_of_a_project_parameter_of_the_same_name(
    workbooks: Workbooks, mini_release: Release, out: Path
) -> None:
    done = run(
        workbooks,
        mini_release,
        out,
        parameters("Project parameters", ["x", 1, None, None, None]),
        parameters("Database parameters", ["x", 2, None, None, None]),
        block("widget", sand(2, "x")),
    )
    assert read_database(done.folder).parameters.values == {"x": 2.0}
    (shadowed,) = done.report.parameters_failed
    assert shadowed.code == "parameter-shadowed" and shadowed.source == "a!A5"


def test_an_activity_parameter_that_hides_a_study_parameter_is_not_copied_and_nor_is_its_formula(
    workbooks: Workbooks, mini_release: Release, out: Path
) -> None:
    """In Brightway the activity's `x` wins in its own formulas. headwater refuses two meanings
    for one name, and must not let the formula quietly use the other one."""
    done = run(
        workbooks,
        mini_release,
        out,
        parameters("Project parameters", ["x", 1, None, None, None]),
        block(
            "widget",
            sand(5, "x * 5"),
            parameters=parameters("Parameters", ["x", 5])[:-1],
        ),
    )
    (hidden,) = done.report.parameters_failed
    assert (
        hidden.name == "x" and hidden.scope == "widget" and hidden.code == "parameter-unsupported"
    )
    (formula,) = [o for o in done.report.outcomes if o.code.startswith("formula")]
    assert formula.code == "formula-unknown-parameter" and "x" in formula.message
    assert lines(done)[1].amount == 5.0  # the number Excel stored, not 1 * 5 by accident


def test_two_activities_may_each_have_a_parameter_of_the_same_name(
    workbooks: Workbooks, mini_release: Release, out: Path
) -> None:
    done = run(
        workbooks,
        mini_release,
        out,
        block(
            "one", sand(1, "share * 2"), parameters=parameters("Parameters", ["share", 0.5])[:-1]
        ),
        block(
            "two", sand(3, "share * 2"), parameters=parameters("Parameters", ["share", 1.5])[:-1]
        ),
    )
    assert lines(done, "one")[1].amount == 1.0 and lines(done, "two")[1].amount == 3.0
    assert done.report.clean


def test_a_parameter_unit_headwater_knows_is_written_and_one_it_does_not_is_noted(
    workbooks: Workbooks, mini_release: Release, out: Path
) -> None:
    done = run(
        workbooks,
        mini_release,
        out,
        parameters(
            "Project parameters",
            ["a", 1, None, "kilogram", None],
            ["b", 1, None, "furlong", None],
        ),
        block("widget", sand(1, "a + b - 1")),
    )
    study = read_database(done.folder)
    assert study.parameters.get("a") is not None and study.parameters.get("a").unit == "kg"  # type: ignore[union-attr]
    assert study.parameters.get("b") is not None and study.parameters.get("b").unit is None  # type: ignore[union-attr]
    assert any("furlong" in n.message for n in done.report.notes)


def test_a_parameter_of_an_activity_that_was_not_converted_is_reported(
    workbooks: Workbooks, mini_release: Release, out: Path
) -> None:
    done = run(
        workbooks,
        mini_release,
        out,
        block("widget"),
        block("broken", unit="furlong", parameters=parameters("Parameters", ["x", 1])[:-1]),
    )
    (failed,) = done.report.parameters_failed
    assert failed.code == "activity-not-converted" and failed.scope == "broken"


def test_a_workbook_with_no_parameters_has_no_parameters_file(
    workbooks: Workbooks, mini_release: Release, out: Path
) -> None:
    done = run(workbooks, mini_release, out, block("widget", sand(1, None)))
    assert not (done.folder / "parameters.yaml").exists()
    assert done.report.parameter_rows == 0
