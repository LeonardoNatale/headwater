"""Local overrides: the study buys its own copy of a background process, and nothing else does.

Example: the mill's wind contract, electricity at 0.3 kg CO2 per kWh instead of 0.5. The T-shirt's
own 0.5 kWh uses the copy (0.3298 per kWh); the fabric still buys the grid's (0.5298).
"""

import shutil
from pathlib import Path

import numpy as np
import pytest

import headwater as hw
from headwater.model import Flow, FlowKind, Role
from headwater.query._internals import internals
from tests.conftest import toy_database

REL = 1e-9
ELECTRICITY = "toy:electricity-production"
FABRIC = "toy:fabric-production"
COPY = "tshirt:copy:toy:electricity-production"
WIND = (
    "process,line,role,flow,amount,unit,supplier,comment\n"
    "toy:electricity-production,e02,emission,toy:co2,0.3,kg,,Wind power\n"
)
WEAVING = (
    "process,line,role,flow,amount,unit,supplier,comment\n"
    "toy:fabric-production,e02,input,toy:electricity,2.5,kWh,toy:electricity-production,Mill\n"
)


def study_with(root: Path, name: str, files: dict[str, str]) -> None:
    """A copy of the plain T-shirt study under `name`, with more files."""
    target = toy_database(root, name)
    shutil.copytree(root / "tshirt" / "tshirt", target)
    for path, text in files.items():
        (target / path).parent.mkdir(parents=True, exist_ok=True)
        (target / path).write_text(text)


def test_a_local_override_leaves_the_background_unchanged(either: hw.Background, calc) -> None:
    solved = internals(either).solved
    scores = {i: s.values.copy() for i, s in solved.x.items()}
    A = solved.block.A.toarray()

    calc("tshirt-local-electricity", either)
    calc("tshirt-local-fabric", either)

    assert internals(either).solved.block is solved.block
    np.testing.assert_array_equal(internals(either).solved.block.A.toarray(), A)
    for indicator, values in scores.items():
        np.testing.assert_array_equal(internals(either).solved.x[indicator].values, values)
    assert either.contributions(FABRIC).score == pytest.approx(3.7086, rel=REL)
    assert either.contributions(ELECTRICITY).score == pytest.approx(0.5298, rel=REL)
    assert calc("tshirt", either).score == pytest.approx(1.19205, rel=REL)


def test_every_other_buyer_keeps_the_background_process(either: hw.Background, calc) -> None:
    root = calc("tshirt-local-electricity", either).contributions(min_share=0)

    fabric, electricity = root.children
    assert (electricity.process_id, electricity.copy_of) == (COPY, ELECTRICITY)
    assert electricity.per_unit == pytest.approx(0.3298, rel=REL)
    (grid,) = fabric.children
    assert (fabric.copy_of, grid.process_id, grid.copy_of) == (None, ELECTRICITY, None)
    assert grid.per_unit == pytest.approx(0.5298, rel=REL)
    assert "electricity production (local copy): 0.16490" in str(root)


def test_levers_tell_the_copy_from_the_background_process(either: hw.Background, calc) -> None:
    levers = calc("tshirt-local-electricity", either).sensitivity()

    by_line = {(x.process_id, x.line_id): x for x in levers}
    copy, grid = by_line[COPY, "e02"], by_line[ELECTRICITY, "e02"]
    # The copy runs for the T-shirt's 0.5 kWh, the grid for the fabric's 0.75 kWh.
    assert (copy.role, copy.copy_of, grid.copy_of) == (Role.emission, ELECTRICITY, None)
    assert (copy.amount, copy.sensitivity) == pytest.approx((0.3, 0.5), rel=REL)
    assert (grid.amount, grid.sensitivity) == pytest.approx((0.5, 0.75), rel=REL)
    tshirt = by_line["tshirt:tshirt", "e03"]
    assert (tshirt.supplier_id, tshirt.copy_of) == (COPY, None)
    assert tshirt.sensitivity == pytest.approx(0.3298, rel=REL)
    assert len(by_line) == len(levers)  # one id names one line


def test_each_copy_buys_from_the_background_as_it_is(
    either: hw.Background, calc, toy: Path
) -> None:
    # Both overrides: the fabric copy buys the grid's electricity, not the wind copy.
    # 0.25 x (2.1192 + 2.5 x 0.5298) + 0.5 x 0.3298
    study_with(toy, "both", {"overrides/wind.csv": WIND, "overrides/weaving.csv": WEAVING})

    result = calc("both", either, root=toy)

    assert result.score == pytest.approx(1.025825, rel=REL)
    assert result.forward_score == pytest.approx(result.score, rel=1e-12)
    copies = {x.process_id: x.copy_of for x in result.sensitivity() if x.copy_of is not None}
    assert copies == {"tshirt:copy:" + FABRIC: FABRIC, "tshirt:copy:" + ELECTRICITY: ELECTRICITY}
    weaving = next(
        x
        for x in result.sensitivity()
        if (x.process_id, x.line_id) == ("tshirt:copy:" + FABRIC, "e02")
    )
    assert weaving.supplier_id == ELECTRICITY  # the grid, not the wind copy


def test_an_override_amount_can_be_a_parameter_of_the_overridden_process(
    either: hw.Background, calc, toy: Path
) -> None:
    study_with(
        toy,
        "weaving-parameter",
        {
            "parameters.yaml": "processes:\n  toy:fabric-production:\n    mill_kwh: 2.5\n",
            "overrides/weaving.csv": WEAVING.replace(",2.5,kWh", ",mill_kwh,kWh"),
        },
    )

    result = calc("weaving-parameter", either, root=toy)

    assert result.score == pytest.approx(1.125825, rel=REL)
    (mill,) = result.parameters()
    assert (mill.parameter_id, mill.scope) == ("toy:fabric-production/mill_kwh", FABRIC)
    # One more kWh per kg of fabric copy: 0.25 kg x 0.5298.
    assert mill.sensitivity == pytest.approx(0.13245, rel=REL)


def test_an_override_line_the_background_does_not_have_is_refused(
    either: hw.Background, calc, toy: Path
) -> None:
    study_with(toy, "no-such-line", {"overrides/weaving.csv": WEAVING.replace(",e02,", ",e09,")})

    with pytest.raises(hw.InvalidDataError) as raised:
        calc("no-such-line", either, root=toy)

    (problem,) = raised.value.problems
    assert (problem.code, problem.item) == ("override-unknown-line", f"{FABRIC} line e09")
    assert "e01, e02, e03, e04" in problem.message
    assert problem.location == "overrides/weaving.csv:2"


def test_an_override_that_changes_what_the_line_is_is_refused(
    either: hw.Background, calc, toy: Path
) -> None:
    study_with(
        toy, "other-flow", {"overrides/wind.csv": WIND.replace("toy:co2,0.3", "toy:ch4,0.3")}
    )

    with pytest.raises(hw.InvalidDataError) as raised:
        calc("other-flow", either, root=toy)

    assert [p.code for p in raised.value.problems] == ["override-changes-line"]


def test_validation_of_an_override_on_a_release_leaves_nothing_unchecked(
    release_background: hw.Background, open_study
) -> None:
    report = open_study("tshirt-local-fabric", release_background).validate(release_background)

    assert report.problems == ()


def test_a_copy_that_emits_a_flow_with_no_factor_is_not_an_error(
    make_release, records, calc
) -> None:
    # Fabric production emits N2O, which toy-climate has no factor for. The study's copy of it
    # emits it too: it counts 0 and is listed, as for the background, not refused as the study's
    # own flow would be.
    n2o = Flow(id="toy:n2o", name="Dinitrogen monoxide", kind=FlowKind.elementary, unit="kg")
    release = make_release(
        records.with_flows(n2o).with_new_line(
            FABRIC, "e05", role="emission", flow_id=n2o.id, amount=0.002, unit="kg"
        )
    )
    background = hw.load(release.path, method="toy-climate")

    result = calc("tshirt-local-fabric", background)

    assert result.score == pytest.approx(1.125825, rel=REL)
    # Only the copy runs: the T-shirt buys all its fabric from it.
    assert [(f.flow_id, f.processes) for f in result.not_characterised] == [
        ("toy:n2o", ("tshirt:copy:toy:fabric-production",))
    ]
