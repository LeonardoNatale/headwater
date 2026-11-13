"""Brightway's uncertainty columns: what they mean as a study's distribution, and a workbook's
distributions reaching its study.

`tshirt-uncertain.csv` is the toy T-shirt with plain amounts: its fabric line is lognormal with a
GSD of 1.1 (`loc` ln 0.25, `scale` ln 1.1), and its waste line, -0.05 kg, is uniform between
-0.06 and -0.04, as Brightway writes it.
"""

import hashlib
import math
from pathlib import Path

import pytest

import headwater as hw
from headwater.io.excel import import_excel, read_workbook
from headwater.io.excel.importer import ExcelImport
from headwater.io.excel.uncertainty import cells
from headwater.model import Distribution, Exchange, Uncertainty
from headwater.release import Release
from headwater.study import read_database
from tests.conftest import only_process, open_database

from .conftest import Sheet, Workbooks, sheet, with_cell

REL = 1e-9
TSHIRT = "toy-foreground:t-shirt-glo"
TYPE, LOC, SCALE, MINIMUM, MAXIMUM = 10, 11, 12, 13, 14
"""The columns of the uncertainty fields in `tshirt-uncertain.csv`."""


def lines(folder: Path) -> dict[str, Exchange]:
    (process,) = [p for p in read_database(folder).processes if p.id == TSHIRT]
    return {e.supplier_id or e.flow_id: e for e in process.exchanges}


def notes(done: ExcelImport) -> list[str]:
    return [f"{n.source}: {n.message}" for n in done.report.notes]


# --- the fields as a distribution ----------------------------------------------------------------


@pytest.mark.parametrize("fields", [{}, {"uncertainty type": 0}, {"uncertainty type": 1}])
def test_no_uncertainty(fields: dict[str, object]) -> None:
    assert cells(fields, 0.5, flip=False) is None


def test_a_lognormal_keeps_its_scale() -> None:
    fields = {"uncertainty type": 2, "loc": math.log(0.5), "scale": 0.1}
    assert cells(fields, 0.5, flip=False) == {"distribution": "lognormal", "scale": 0.1}


def test_a_lognormal_of_a_negative_amount_is_centred_on_its_size() -> None:
    fields = {"uncertainty type": 2, "loc": math.log(0.5), "scale": 0.1, "negative": True}
    assert cells(fields, -0.5, flip=True) == {"distribution": "lognormal", "scale": 0.1}


def test_a_type_written_as_a_float_is_read() -> None:
    fields = {"uncertainty type": 3.0, "loc": 0.5, "scale": 0.05}
    assert cells(fields, 0.5, flip=False) == {"distribution": "normal", "scale": 0.05}


def test_a_normal_keeps_its_standard_deviation() -> None:
    fields = {"uncertainty type": 3, "loc": 0.5, "scale": 0.05}
    assert cells(fields, 0.5, flip=False) == {"distribution": "normal", "scale": 0.05}


def test_a_normal_or_triangular_with_no_loc_is_centred_on_the_amount_as_brightway_does() -> None:
    """bw_processing takes the amount as `loc` when an exchange has none."""
    assert cells({"uncertainty type": 3, "scale": 0.05}, 0.5, flip=False) == {
        "distribution": "normal",
        "scale": 0.05,
    }
    assert cells({"uncertainty type": 5, "minimum": 0.4, "maximum": 0.7}, 0.5, flip=False) == {
        "distribution": "triangular",
        "minimum": 0.4,
        "maximum": 0.7,
    }


def test_a_uniform_turns_with_the_sign() -> None:
    fields = {"uncertainty type": 4, "minimum": -0.06, "maximum": -0.04}
    assert cells(fields, -0.05, flip=True) == {
        "distribution": "uniform",
        "minimum": 0.04,
        "maximum": 0.06,
    }
    assert cells(fields, -0.05, flip=False) == {
        "distribution": "uniform",
        "minimum": -0.06,
        "maximum": -0.04,
    }


def test_a_triangular_turns_with_the_sign() -> None:
    fields = {"uncertainty type": 5, "minimum": -0.06, "loc": -0.05, "maximum": -0.03}
    assert cells(fields, -0.05, flip=True) == {
        "distribution": "triangular",
        "minimum": 0.03,
        "maximum": 0.06,
    }


@pytest.mark.parametrize(
    ("fields", "words"),
    [
        ({"uncertainty type": 2, "loc": math.log(0.4), "scale": 0.1}, "loc"),
        ({"uncertainty type": 2, "scale": 0.1}, "loc"),
        ({"uncertainty type": 3, "loc": 0.4, "scale": 0.1}, "loc"),
        ({"uncertainty type": 5, "minimum": 0, "loc": 0.4, "maximum": 1}, "loc"),
        ({"uncertainty type": 7, "minimum": 0, "maximum": 1}, "type 7"),
        ({"uncertainty type": "lognormal"}, "type 'lognormal'"),
        ({"uncertainty type": 3, "loc": 0.5, "scale": "wide"}, "scale"),
        ({"scale": 0.1}, "no uncertainty type"),
        ({"pedigree": "(1, 2, 1, 1, 1)"}, "no uncertainty type"),
        ({"uncertainty type": 3, "loc": math.nan, "scale": 0.1}, "loc nan"),
    ],
)
def test_what_a_study_cannot_hold_is_a_reason(fields: dict[str, object], words: str) -> None:
    found = cells(fields, 0.5, flip=False)
    assert isinstance(found, str)
    assert words in found


# --- a workbook ---------------------------------------------------------------------------------


def uncertain() -> Sheet:
    return sheet("tshirt-uncertain")


def test_a_workbooks_distributions_reach_the_study(
    workbooks: Workbooks, out: Path, toy_release: Release
) -> None:
    done = import_excel(workbooks.write({"inventory": uncertain()}), toy_release, out)
    assert done.clean
    assert not [n for n in notes(done) if "distribution" in n], notes(done)
    found = lines(out)
    assert found["toy:fabric-production"].uncertainty == Uncertainty(
        distribution=Distribution.lognormal, loc=math.log(0.25), scale=math.log(1.1)
    )
    assert found["toy:electricity-production"].uncertainty is None
    waste = found["toy:incineration-textile-waste"]
    assert waste.amount == pytest.approx(0.05, rel=REL)
    assert waste.uncertainty == Uncertainty(
        distribution=Distribution.uniform, minimum=0.04, maximum=0.06
    )
    database = open_database(out)
    score = database.calc(hw.load(toy_release.path), only_process(database)).score
    assert score == pytest.approx(1.25205, rel=REL)


def test_a_type_the_study_cannot_hold_is_reported_and_not_copied(
    workbooks: Workbooks, out: Path, toy_release: Release
) -> None:
    rows = with_cell(uncertain(), "electricity production", TYPE, 7)
    rows = with_cell(rows, "electricity production", MINIMUM, 0)
    rows = with_cell(rows, "electricity production", MAXIMUM, 1)
    done = import_excel(workbooks.write({"inventory": rows}), toy_release, out)
    assert [n for n in notes(done) if "distribution" in n] == [
        "inventory!A17: distribution not copied: it has uncertainty type 7 (discrete uniform). "
        "A database holds lognormal (2), normal (3), uniform (4) and triangular (5) only."
    ]
    found = lines(out)
    assert found["toy:electricity-production"].uncertainty is None
    assert found["toy:fabric-production"].uncertainty is not None


def test_text_cells_are_read_as_numbers_as_bw2io_reads_them(
    workbooks: Workbooks, out: Path, toy_release: Release
) -> None:
    rows = with_cell(uncertain(), "cotton fabric production", TYPE, "2")
    rows = with_cell(rows, "cotton fabric production", LOC, str(math.log(0.25)))
    rows = with_cell(rows, "cotton fabric production", SCALE, " 0.09531017980432493 ")
    import_excel(workbooks.write({"inventory": rows}), toy_release, out)
    fabric = lines(out)["toy:fabric-production"]
    assert fabric.uncertainty == Uncertainty(
        distribution=Distribution.lognormal, loc=math.log(0.25), scale=math.log(1.1)
    )


@pytest.mark.parametrize("kind", ["undefined", "no uncertainty", 0, 1])
def test_no_uncertainty_written_as_a_word_or_number_is_none(
    workbooks: Workbooks, kind: str | int
) -> None:
    rows = with_cell(sheet("tshirt"), "electricity production", 10, kind)
    rows[13] = [*rows[13], "uncertainty type"]
    book = read_workbook(workbooks.write({"inventory": rows}))
    electricity = book.activities[0].exchanges[2]
    assert electricity.name == "electricity production"
    assert cells(electricity.uncertainty, 0.5, flip=False) is None


def test_a_distribution_on_a_formula_line_is_reported(
    workbooks: Workbooks, out: Path, toy_release: Release
) -> None:
    rows = with_cell(uncertain(), "cotton fabric production", 7, "fabric_kg")
    done = import_excel(workbooks.write({"inventory": rows}), toy_release, out)
    found = [n for n in notes(done) if "distribution" in n]
    assert len(found) == 1 and found[0].startswith("inventory!A16: ") and "formula" in found[0]
    assert lines(out)["toy:fabric-production"].uncertainty is None


def test_a_distribution_on_a_line_that_does_not_link_waits_for_it(
    workbooks: Workbooks, out: Path, toy_release: Release
) -> None:
    rows = with_cell(uncertain(), "cotton fabric production", 3, "RER")
    done = import_excel(workbooks.write({"inventory": rows}), toy_release, out)
    found = [n for n in notes(done) if "distribution" in n]
    assert len(found) == 1 and "not linked yet" in found[0], found


def test_values_with_no_uncertainty_type_are_reported(
    workbooks: Workbooks, out: Path, toy_release: Release
) -> None:
    rows = with_cell(uncertain(), "electricity production", SCALE, 0.1)
    done = import_excel(workbooks.write({"inventory": rows}), toy_release, out)
    found = [n for n in notes(done) if "distribution" in n]
    assert len(found) == 1 and found[0].startswith("inventory!A17: "), found
    assert "no uncertainty type" in found[0]


def test_a_parameters_distribution_reaches_the_study(
    workbooks: Workbooks, out: Path, toy_release: Release
) -> None:
    rows = uncertain()
    rows[3] = [*rows[3], "uncertainty type", "loc", "scale"]
    rows[4] = [*rows[4], 3, 0.25, 0.02]
    done = import_excel(workbooks.write({"inventory": rows}), toy_release, out)
    assert not any("not used by headwater" in n for n in notes(done)), notes(done)
    (fabric_kg,) = read_database(out).parameters
    assert fabric_kg.uncertainty == Uncertainty(
        distribution=Distribution.normal, loc=0.25, scale=0.02
    )


def test_a_workbook_with_no_uncertainty_imports_byte_for_byte_as_before(
    workbook: Path, out: Path, toy_release: Release
) -> None:
    """The sha256 of each file the toy T-shirt workbook gives. database.yaml, the flows, the
    parameters, and exchanges.csv without its process column are the bytes the import wrote before
    it read uncertainty. processes.csv and the record are pinned as the two tables are written."""
    import_excel(workbook, toy_release, out)
    written = {
        p.relative_to(out).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in out.rglob("*")
        if p.is_file()
    }
    assert written == {
        "database.yaml": "22873e9484a42d3a155fb2979b2cd7b94915dfc8d25c05447f3d3121ba015cb6",
        ".headwater-import.json": (
            "56f508363dc1c33a7196b88468f9b72cba9967cbafa769b4ef24698b32dcefc3"
        ),
        "exchanges.csv": "9edaa09e3f662d680586460019a710866186025931b7c033715ec305b9898fe3",
        "flows.csv": "7517b5b36cc220588f9cf547890939eff5f97513954ea2ed1c63d8cb2bc8896b",
        "parameters.yaml": "b3084a6269ce9e77df3617ea139c9a8155b2352e460706093e933b2118a5bbdb",
        "processes.csv": "c84085027680d4329bb016a8bd7696d2251c39618bd9d3c04a6d861237be078b",
    }
    table = (out / "exchanges.csv").read_text(encoding="utf-8").splitlines(keepends=True)
    lines = "".join(row.split(",", 1)[1] for row in table)  # the T-shirt's id has no comma
    assert hashlib.sha256(lines.encode("utf-8")).hexdigest() == (
        "5aaa04da942bc78a7f64efb3d15c1f19086f99e5291abfc1c88137105f919aa9"
    )  # the process CSV the import wrote before it read uncertainty
