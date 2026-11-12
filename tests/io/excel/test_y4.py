"""Exit test Y4: a Brightway-format Excel inventory converts into a study that calculates the same
as the toy T-shirt of docs/calculation.md: 1.19205, and 1.25205 with the waste line.

The other half of Y4, each line that cannot be mapped is reported and not dropped, is here too.
The real workbook is run locally, not here.
"""

from pathlib import Path

import pytest

import headwater as hw
from headwater.io.excel import Status, import_excel
from headwater.model import Role
from headwater.release import Release
from headwater.study import read_database
from tests.conftest import only_process, open_database

from .conftest import Workbooks, row_of, sheet, with_cell, without

REL = 1e-9  # AGENTS.md: relative 1e-9 for toy fixtures


def score(folder: Path, release: Release) -> float:
    database = open_database(folder)
    return database.calc(hw.load(release.path), only_process(database)).score


def test_the_converted_tshirt_scores_as_the_toy_with_its_waste_line(
    workbook: Path, toy_release: Release, out: Path
) -> None:
    done = import_excel(workbook, toy_release, out)
    assert done.validation is not None
    assert done.validation.errors == ()
    assert score(out, toy_release) == pytest.approx(1.25205, rel=REL)


def test_the_converted_tshirt_scores_as_the_toy_without_the_waste_line(
    workbooks: Workbooks, toy_release: Release, out: Path
) -> None:
    rows = without(sheet("tshirt"), "incineration of textile waste")
    import_excel(workbooks.write({"inventory": rows}), toy_release, out)
    assert score(out, toy_release) == pytest.approx(1.19205, rel=REL)


def test_a_negative_amount_to_a_treatment_becomes_a_waste_line(
    workbook: Path, toy_release: Release, out: Path
) -> None:
    import_excel(workbook, toy_release, out)
    study = read_database(out)
    (tshirt,) = study.processes
    waste = next(e for e in tshirt.exchanges if e.role is Role.waste_to_treatment)
    assert waste.amount == pytest.approx(0.05, rel=REL)
    assert waste.supplier_id == "toy:incineration-textile-waste"
    assert waste.unit == "kg"
    assert [e.role for e in tshirt.exchanges if e.role is not Role.waste_to_treatment] == [
        Role.reference,
        Role.input,
        Role.input,
    ]


def test_a_formula_round_trips_into_a_parameter(
    workbook: Path, toy_release: Release, out: Path
) -> None:
    import_excel(workbook, toy_release, out)
    study = read_database(out)
    fabric = next(e for e in study.processes[0].exchanges if e.flow_id == "toy:cotton-fabric")
    assert fabric.formula == "fabric_kg"
    assert fabric.amount == 0.25
    assert study.parameters.values["fabric_kg"] == 0.25
    # The study now says "0.30 kg of fabric" in one place, and the formula follows it.
    path = out / "parameters.yaml"
    assert "value: 0.25" in path.read_text()
    path.write_text(path.read_text().replace("value: 0.25", "value: 0.3"))
    assert score(out, toy_release) == pytest.approx(0.3 * 3.7086 + 0.5 * 0.5298 + 0.06, rel=REL)


def test_every_line_of_the_workbook_has_one_outcome(
    workbooks: Workbooks, toy_release: Release, out: Path
) -> None:
    book = workbooks.write({"inventory": sheet("tshirt"), "cases": sheet("cases")})
    done = import_excel(book, toy_release, out)
    report = done.report
    assert report.exchange_rows == 4 + 19
    assert len(report.outcomes) == report.exchange_rows
    assert sorted(o.source for o in report.outcomes) == sorted({o.source for o in report.outcomes})
    assert sum(report.count(s) for s in Status) == report.exchange_rows


CASES = {
    "case: mapped": (Status.mapped, "release"),
    "case: renamed supplier": (Status.unmapped, "not-in-release"),
    "case: wrong location": (Status.unmapped, "not-in-release"),
    "case: unknown unit": (Status.unmapped, "unknown-unit"),
    "case: wrong quantity": (Status.unmapped, "unit-mismatch"),
    "case: negative to a product": (Status.unmapped, "negative-to-product"),
    "case: positive to a treatment": (Status.unmapped, "positive-to-treatment"),
    "case: unknown type": (Status.unmapped, "unknown-type"),
    "case: no type": (Status.unmapped, "no-type"),
    "case: unknown biosphere flow": (Status.unmapped, "biosphere-not-found"),
    "case: no categories": (Status.unmapped, "no-categories"),
    "case: other foreground database": (Status.unmapped, "database-unresolved"),
    "case: activity not in the workbook": (Status.unmapped, "not-in-workbook"),
    "case: caret in a formula": (Status.unexpressible, "formula-unsupported"),
    "case: unknown function": (Status.unexpressible, "formula-unsupported"),
    "case: unknown parameter": (Status.unexpressible, "formula-unknown-parameter"),
    "case: empty amount": (Status.unmapped, "no-amount"),
    "case: text amount": (Status.unmapped, "bad-amount"),
}


@pytest.mark.parametrize("case", CASES)
def test_each_unmappable_case_is_reported_with_its_reason(
    case: str, workbooks: Workbooks, toy_release: Release, out: Path
) -> None:
    cases = sheet("cases")
    book = workbooks.write({"inventory": sheet("tshirt"), "cases": cases})
    report = import_excel(book, toy_release, out).report
    outcome = next(o for o in report.outcomes if o.source == f"cases!A{row_of(cases, case)}")
    assert (outcome.status, outcome.code) == CASES[case]
    assert outcome.activity == "Cases"
    assert outcome.message


def test_a_line_that_did_not_map_is_still_in_the_files_and_stops_validation(
    workbooks: Workbooks, toy_release: Release, out: Path
) -> None:
    cases = sheet("cases")
    book = workbooks.write({"inventory": sheet("tshirt"), "cases": cases})
    done = import_excel(book, toy_release, out)
    study = read_database(out)
    process = next(p for p in study.processes if p.name == "Cases")
    assert len(process.exchanges) == 19  # nothing was dropped
    assert done.validation is not None
    missing = {p.item for p in done.validation.errors if p.code == "supplier-missing"}
    unmapped = {
        f"{o.process_id} line {o.line_id}"
        for o in done.report.outcomes
        if o.status is Status.unmapped and o.activity == "Cases"
    }
    # Every unmapped line is in the files, and the ones that buy something name no supplier.
    assert unmapped and len(unmapped) == 14
    assert missing and missing <= unmapped
    assert {line.split(" line ")[1] for line in unmapped - missing} <= {"e11", "e12", "e18", "e19"}
    assert not done.report.clean


def test_a_clean_import_is_clean(workbook: Path, toy_release: Release, out: Path) -> None:
    done = import_excel(workbook, toy_release, out)
    assert done.report.clean
    assert done.report.count(Status.unmapped) == 0
    assert done.report.count(Status.unexpressible) == 0
    assert done.report.count(Status.mapped) == 4


def test_renaming_the_workbook_sheet_changes_nothing(
    workbooks: Workbooks, toy_release: Release, out: Path
) -> None:
    book = workbooks.write({"Sheet 1": sheet("tshirt")})
    import_excel(book, toy_release, out)
    assert score(out, toy_release) == pytest.approx(1.25205, rel=REL)


def test_an_amount_cell_that_is_text_of_a_number_is_a_number(
    workbooks: Workbooks, toy_release: Release, out: Path
) -> None:
    rows = with_cell(sheet("tshirt"), "electricity production", 1, "0.5")
    import_excel(workbooks.write({"inventory": rows}), toy_release, out)
    assert score(out, toy_release) == pytest.approx(1.25205, rel=REL)
