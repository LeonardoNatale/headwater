"""Cut-off and data-gap lines: skipped when assembling, and no "supplier missing" in validation.

A line that was cut off or marked as a data gap has no supplier and a resolved issue that says
why. It buys nothing, so it has no place in A.
"""

from pathlib import Path

import numpy as np
import pytest

import headwater as hw
from headwater.model import Role
from headwater.query._internals import internals
from headwater.study import ProcessFolder, validate
from tests.conftest import open_database

TOY = Path(__file__).resolve().parent.parent / "fixtures" / "toy"
REL = 1e-9
FABRIC = "toy:fabric-production"


def folder_of(records) -> ProcessFolder:
    return ProcessFolder(
        path=TOY,
        namespace="toy",
        processes=records.processes,
        flows=records.flows,
        flow_properties=records.flow_properties,
        units=(),
    )


def test_the_release_path_skips_cut_off_and_data_gap_lines(
    make_release, release, cut_off, tmp_path: Path, calc
) -> None:
    plain = hw.load(release.path, method="toy-climate")

    cut = hw.load(make_release(cut_off).path, method="toy-climate")

    assert calc("tshirt", cut).score == pytest.approx(1.19205, rel=REL)
    assert calc("tshirt-waste", cut).score == pytest.approx(1.25205, rel=REL)
    # The two lines add nothing to A: same shape, same entries.
    assert internals(cut).solved.block.A.shape == internals(plain).solved.block.A.shape
    assert internals(cut).solved.block.A.nnz == internals(plain).solved.block.A.nnz
    assert np.array_equal(
        internals(cut).solved.block.A.toarray(), internals(plain).solved.block.A.toarray()
    )
    assert internals(cut).solved.block.B.nnz == internals(plain).solved.block.B.nnz
    # They are no levers either: nothing can be changed on a line that buys nothing.
    lines = {
        (x.process_id, x.line_id) for x in calc("tshirt", cut).sensitivity(include_reference=True)
    }
    assert (FABRIC, "e05") not in lines
    assert (FABRIC, "e06") not in lines
    assert (FABRIC, "e02") in lines


def test_the_record_path_skips_them_too(cut_off, calc) -> None:
    # Records read back from a release carry their link status, and loading them as a folder
    # runs validation and the Python assembly.
    from headwater.study import read_method

    background = hw.Background(folder_of(cut_off), read_method(TOY / "methods" / "toy-climate"))

    assert calc("tshirt", background).score == pytest.approx(1.19205, rel=REL)
    levers = calc("tshirt", background).sensitivity(include_reference=True)
    assert (FABRIC, "e05") not in {(x.process_id, x.line_id) for x in levers}


def test_validation_does_not_report_a_missing_supplier_on_them(cut_off) -> None:
    report = validate(folder_of(cut_off))

    assert report.errors == ()
    cut_lines = [
        e
        for p in cut_off.processes
        for e in p.exchanges
        if e.link_status is not None and e.link_status.value != "linked"
    ]
    assert sorted((e.line_id, e.link_status.value) for e in cut_lines if e.link_status) == [
        ("e05", "cut_off"),
        ("e06", "data_gap"),
    ]
    assert all(e.supplier_id is None and e.role is Role.input for e in cut_lines)


def test_a_line_with_no_supplier_and_no_link_status_is_still_missing_a_supplier(
    records,
) -> None:
    unresolved = records.with_line("toy:fabric-production", "e02", supplier_id=None)

    report = validate(folder_of(unresolved))

    assert [(p.code, p.item) for p in report.errors] == [("supplier-missing", f"{FABRIC} line e02")]


def test_a_cut_off_line_may_not_name_a_supplier(cut_off) -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="takes no supplier"):
        cut_off.with_line(FABRIC, "e05", supplier_id="toy:electricity-production")


def test_a_study_line_that_was_cut_off_scores_without_it(background) -> None:
    from headwater.model import Exchange, Process
    from headwater.study import DatabaseFolder

    study = open_database(TOY / "tshirt" / "tshirt")
    process = study.folder.processes[0]
    dye = Exchange(
        process_id=process.id,
        line_id="e09",
        role=Role.input,
        flow_id="toy:cotton-fabric",
        amount=0.01,
        unit="kg",
        link_status="cut_off",  # pyright: ignore[reportArgumentType]
    )
    cut = Process.model_validate(
        {**process.model_dump(exclude={"exchanges"}), "exchanges": (*process.exchanges, dye)}
    )
    folder = DatabaseFolder(**{**vars(study.folder), "processes": (cut,)})

    result = hw.Database(folder).calc(background, "tshirt:tshirt")

    assert result.score == pytest.approx(1.19205, rel=REL)
    assert result.forward_score == pytest.approx(1.19205, rel=REL)


def test_the_indicators_of_a_release_with_cut_off_lines(make_release, cut_off) -> None:
    release = make_release(cut_off)

    derived = hw.load(release.path, method="toy-climate").write_indicators()

    table = derived.table("indicators/toy-climate")
    assert dict(
        zip(
            table.column("product_id").to_pylist(),
            table.column("climate-change").to_pylist(),
            strict=True,
        )
    ) == pytest.approx(
        {
            "toy:electricity-production": 0.5298,
            "toy:fabric-production": 3.7086,
            "toy:incineration-textile-waste": 1.2,
        },
        rel=REL,
    )
