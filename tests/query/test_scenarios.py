"""Named scenarios of a study: a change to a background process changes it for everything,
through a low-rank update of the stored factorization, or through a new factorization when many
processes change. Each one must give what a full refactorization of the edited background
gives, within 1e-12, whichever way it is solved: every test here runs on both.

Each scenario is in the study `tshirt-footprint` of a toy case, next to its T-shirt, which the
study scores.
"""

import shutil
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from numpy.typing import ArrayLike

import headwater as hw
from headwater.calc import (
    Factorization,
    ResidualError,
    SingularMatrixError,
    Solution,
    Step,
    factorize,
)
from headwater.calc import study as study_calc
from headwater.query import _scenario
from headwater.query._internals import internals
from headwater.query._scenario import Changed
from tests.conftest import open_database, open_study_folder, toy_database

from .conftest import TOY_DIR, pinned_study

REL = 1e-9
SAME = 1e-12
ELECTRICITY = "toy:electricity-production"
FABRIC = "toy:fabric-production"
WIND = "toy:electricity-wind"
WIND_CSV = (
    "line,role,flow,amount,unit,supplier\n"
    "e01,reference,toy:electricity,1,kWh,\n"
    "e02,input,toy:cotton-fabric,0.01,kg,toy:fabric-production\n"
    "e03,emission,toy:co2,0.05,kg,\n"
)
WIND_YAML = "name: electricity, wind\nlocation: GLO\nkind: ordinary_transforming_activity\n"
STUDY_YAML = """\
name: tshirt-footprint
goal: Footprint of one T-shirt, internal use, not a public comparison
functional_unit: 1 T-shirt
products:
  - {process: tshirt:tshirt, amount: 1}
boundary: cradle-to-gate
methods: [toy-climate]
cut_off: {criteria: [mass, energy], max_cumulative: 0.03}
"""


@pytest.fixture(autouse=True, params=["update", "refactorize"])
def way(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
    """Solve every scenario of this file by a low-rank update, then by refactorizing."""
    monkeypatch.setattr(_scenario, "CROSSOVER", 10**9 if request.param == "update" else 0)
    return request.param


@pytest.fixture
def changes(monkeypatch: pytest.MonkeyPatch) -> list[Changed]:
    """The background of each scenario the study changes, as `change` returned it."""
    seen: list[Changed] = []
    real = _scenario.change

    def spy(*args, **kwargs) -> Changed:  # noqa: ANN002, ANN003
        found = real(*args, **kwargs)
        seen.append(found)
        return found

    monkeypatch.setattr("headwater.query.database.change", spy)
    return seen


def with_scenario(root: Path, name: str, changes: str, *, case: str = "tshirt") -> None:
    """Add `scenarios/<name>.yaml` to the study `tshirt-footprint` of a case of the writable toy,
    next to its T-shirt. The study is written first when the case has none."""
    study = root / case / "tshirt-footprint"
    if not (study / "study.yaml").is_file():
        study.mkdir(exist_ok=True)
        (study / "study.yaml").write_text(STUDY_YAML)
    folder = study / "scenarios"
    folder.mkdir(exist_ok=True)
    (folder / f"{name}.yaml").write_text(f"changes:\n{changes}")


def footprint(case: str, background: hw.Background, *, root: Path = TOY_DIR) -> hw.Study:
    """The study `tshirt-footprint` of a toy case, each database it reaches pinned to the
    release of `background`."""
    return pinned_study(open_study_folder(root / case / "tshirt-footprint"), background)


def in_scenario(
    case: str, background: hw.Background, scenario: str, *, root: Path = TOY_DIR
) -> hw.Result:
    """The T-shirt of the study `tshirt-footprint` of a toy case, scored in `scenario`."""
    [result] = footprint(case, background, root=root).calc(background, scenario=scenario)
    return result


def edit_line(root: Path, process: str, line: str, new: str) -> None:
    """Replace one line of a background process file, found by its line id."""
    path = root / "background" / "processes" / f"{process}.csv"
    lines = path.read_text().splitlines()
    (at,) = [i for i, text in enumerate(lines) if text.startswith(f"{line},")]
    lines[at] = new
    path.write_text("\n".join(lines) + "\n")


def add_wind(root: Path) -> None:
    """A second electricity process that buys a little fabric, so fabric and wind make a loop."""
    processes = root / "background" / "processes"
    (processes / "electricity-wind.csv").write_text(WIND_CSV)
    (processes / "electricity-wind.yaml").write_text(WIND_YAML)


def refactorized(root: Path) -> hw.Result:
    """The T-shirt on the edited background, loaded and factorized from scratch."""
    background = hw.load(
        root / "background", method=root / "methods" / "toy-climate", namespace="toy"
    )
    return open_database(toy_database(root, "tshirt")).calc(background, "tshirt:tshirt")


@pytest.fixture
def edited(tmp_path: Path, toy: Path) -> Path:
    """A second writable copy of the toy, whose background the test edits by hand."""
    target = tmp_path / "edited"
    shutil.copytree(toy, target)
    return target


def same(result: hw.Result, expected: hw.Result) -> None:
    assert result.score == pytest.approx(expected.score, rel=SAME)
    assert result.forward_score == pytest.approx(expected.score, rel=SAME)


def test_clean_grid_equals_the_edited_background(edited: Path, background: hw.Background) -> None:
    edit_line(edited, "electricity-production", "e02", "e02,emission,toy:co2,0.3,kg,")

    result = in_scenario("tshirt-grid-scenario", background, "clean-grid")

    same(result, refactorized(edited))
    assert result.score == pytest.approx(0.94205, rel=REL)


def test_a_change_to_a_purchase_of_the_background_equals_the_edited_background(
    either: hw.Background, toy: Path, edited: Path, way: str
) -> None:
    with_scenario(toy, "weaving", f"  - process: {FABRIC}\n    line: e02\n    amount: 2.5\n")
    edit_line(
        edited,
        "fabric-production",
        "e02",
        "e02,input,toy:electricity,2.5,kWh,toy:electricity-production",
    )

    result = in_scenario("tshirt", either, "weaving", root=toy)

    same(result, refactorized(edited))
    assert result.score == pytest.approx(1.125825, rel=REL)
    assert (Step.low_rank in result.provenance.path) == (way == "update")
    assert (Step.refactorized in result.provenance.path) == (way == "refactorize")


def test_a_change_in_another_unit_is_converted(either: hw.Background, toy: Path) -> None:
    # 9 MJ is 2.5 kWh.
    with_scenario(
        toy, "weaving", f"  - process: {FABRIC}\n    line: e02\n    amount: 9\n    unit: MJ\n"
    )

    result = in_scenario("tshirt", either, "weaving", root=toy)

    assert result.score == pytest.approx(1.125825, rel=REL)


def test_two_processes_and_a_new_supplier_equal_the_edited_background(
    toy: Path, edited: Path, load_toy
) -> None:
    # Fabric now buys 2.5 kWh of wind power, which buys 0.02 kg of fabric per kWh: a loop.
    add_wind(toy)
    add_wind(edited)
    with_scenario(
        toy,
        "wind",
        f"  - process: {FABRIC}\n    line: e02\n    amount: 2.5\n    supplier: {WIND}\n"
        f"  - process: {WIND}\n    line: e02\n    amount: 0.02\n",
    )
    edit_line(edited, "fabric-production", "e02", f"e02,input,toy:electricity,2.5,kWh,{WIND}")
    edit_line(edited, "electricity-wind", "e02", f"e02,input,toy:cotton-fabric,0.02,kg,{FABRIC}")

    result = in_scenario("tshirt", load_toy(toy), "wind", root=toy)

    expected = refactorized(edited)
    same(result, expected)
    fabric = 2.1192 / (1 - 2.5 * 0.02) + 2.5 * 0.05 / (1 - 2.5 * 0.02)
    assert result.score == pytest.approx(0.25 * fabric + 0.5 * 0.5298, rel=REL)
    assert result.provenance.condition == pytest.approx(expected.provenance.condition, rel=1e-9)


def test_a_scenario_and_an_override_on_one_process(either: hw.Background, toy: Path) -> None:
    # The override sets CO2 locally, the scenario doubles methane for everyone. The copy is the
    # scenario's electricity with the override's line: 0.3 + 0.002 x 29.8 = 0.3596. The grid is
    # 0.5596, so fabric is 2.1192 + 3 x 0.5596 = 3.798. T-shirt: 0.25 x 3.798 + 0.5 x 0.3596.
    with_scenario(
        toy,
        "methane",
        f"  - process: {ELECTRICITY}\n    line: e03\n    amount: 0.002\n",
        case="tshirt-local-electricity",
    )

    result = in_scenario("tshirt-local-electricity", either, "methane", root=toy)

    assert result.score == pytest.approx(1.1293, rel=REL)
    assert result.forward_score == pytest.approx(result.score, rel=SAME)


def test_a_change_to_a_line_of_the_database_stays_in_the_database(
    either: hw.Background, toy: Path
) -> None:
    # 0.25 x 3.7086 + 1.0 x 0.5298
    with_scenario(toy, "more-power", "  - process: tshirt:tshirt\n    line: e03\n    amount: 1.0\n")

    result = in_scenario("tshirt", either, "more-power", root=toy)

    assert result.score == pytest.approx(1.45695, rel=REL)
    assert Step.low_rank not in result.provenance.path


def test_a_scenario_leaves_the_background_unchanged(either: hw.Background, calc) -> None:
    solved = internals(either).solved
    scores = {i: s.values.copy() for i, s in solved.x.items()}

    in_scenario("tshirt-grid-scenario", either, "clean-grid")

    assert internals(either).solved.block is solved.block
    for indicator, values in scores.items():
        np.testing.assert_array_equal(internals(either).solved.x[indicator].values, values)
    assert calc("tshirt-grid-scenario", either).score == pytest.approx(1.19205, rel=REL)


def test_explain_and_levers_see_the_scenario(either: hw.Background) -> None:
    result = in_scenario("tshirt-grid-scenario", either, "clean-grid")

    root = result.contributions(min_share=0)
    fabric, electricity = root.children
    assert fabric.per_unit == pytest.approx(3.1086, rel=REL)
    assert electricity.per_unit == pytest.approx(0.3298, rel=REL)
    assert root.score == pytest.approx(
        root.direct + sum(c.score for c in root.children) + root.rest, rel=REL
    )
    co2 = next(x for x in result.sensitivity() if (x.process_id, x.line_id) == (ELECTRICITY, "e02"))
    assert (co2.amount, co2.sensitivity) == pytest.approx((0.3, 1.25), rel=REL)


def test_the_scenario_is_part_of_the_input_hash(either: hw.Background) -> None:
    study = footprint("tshirt-grid-scenario", either)
    [plain] = study.calc(either)
    [scenario] = study.calc(either, scenario="clean-grid")

    assert plain.provenance.input_hash != scenario.provenance.input_hash
    assert "scenario clean-grid" in repr(scenario)


def test_the_condition_of_a_scenario_is_that_of_its_matrix(
    either: hw.Background, toy: Path
) -> None:
    with_scenario(toy, "weaving", f"  - process: {FABRIC}\n    line: e02\n    amount: 2.5\n")

    result = in_scenario("tshirt", either, "weaving", root=toy)

    A = internals(either).solved.block.A.toarray()
    column = list(internals(either).index).index(FABRIC)
    A[list(internals(either).index).index(ELECTRICITY), column] = -2.5
    assert result.provenance.condition == pytest.approx(factorize(A).condition, rel=1e-9)


def test_an_unknown_scenario_is_named_with_a_suggestion(either: hw.Background) -> None:
    with pytest.raises(ValueError, match="'clean-grid'"):
        in_scenario("tshirt-grid-scenario", either, "clean-grit")
    no_scenarios = pinned_study(open_study_folder(TOY_DIR / "tshirt-dyed/tshirt-and-dye"), either)
    with pytest.raises(ValueError, match="no scenarios"):
        no_scenarios.calc(either, scenario="clean-grid")


def test_a_scenario_line_the_background_does_not_have_is_refused(
    either: hw.Background, toy: Path
) -> None:
    with_scenario(toy, "typo", f"  - process: {FABRIC}\n    line: e09\n    amount: 2.5\n")

    with pytest.raises(hw.InvalidDataError) as raised:
        in_scenario("tshirt", either, "typo", root=toy)

    (problem,) = raised.value.problems
    assert problem.code == "scenario-unknown-line"
    assert problem.item == f"scenario typo: {FABRIC} line e09"
    assert problem.location == "scenarios/typo.yaml"


def test_a_change_to_one_process_of_the_database_leaves_its_other_processes_alone(
    either: hw.Background, toy: Path
) -> None:
    # The T-shirt buys one sewing step, which buys 0.2 kWh on its own line e02. The scenario
    # changes the T-shirt's e02 only: 0.30 x 3.7086 + 0.5 x 0.5298 + 0.2 x 0.5298.
    database = toy / "tshirt" / "tshirt"
    with (database / "flows.csv").open("a") as flows:
        flows.write("tshirt:sewing,sewing,product,unit,,\n")
    (database / "processes" / "sewing.csv").write_text(
        "line,role,flow,amount,unit,supplier\n"
        "e01,reference,tshirt:sewing,1,unit,\n"
        f"e02,input,toy:electricity,0.2,kWh,{ELECTRICITY}\n"
    )
    (database / "processes" / "sewing.yaml").write_text(
        WIND_YAML.replace("electricity, wind", "sewing")
    )
    with (database / "processes" / "tshirt.csv").open("a") as tshirt:
        tshirt.write("e04,input,tshirt:sewing,1,unit,tshirt:sewing\n")
    with_scenario(toy, "heavier", "  - process: tshirt:tshirt\n    line: e02\n    amount: 0.30\n")

    result = in_scenario("tshirt", either, "heavier", root=toy)

    assert result.score == pytest.approx(0.30 * 3.7086 + 0.7 * 0.5298, rel=REL)


def test_a_scenario_that_changes_a_purchase_of_an_overridden_process(
    either: hw.Background, toy: Path
) -> None:
    # The database overrides fabric's CO2 to 1.0 kg, the scenario makes fabric take 2.5 kWh for
    # everyone. The copy is the changed fabric with the override's line:
    # 1.0 + 0.004 x 29.8 + 2.5 x 0.5298 = 2.4437. T-shirt: 0.25 x 2.4437 + 0.5 x 0.5298.
    database = toy / "tshirt" / "tshirt"
    (database / "overrides").mkdir()
    (database / "overrides" / "mill.csv").write_text(
        "process,line,role,flow,amount,unit,supplier,comment\n"
        f"{FABRIC},e03,emission,toy:co2,1.0,kg,,\n"
    )
    with_scenario(toy, "weaving", f"  - process: {FABRIC}\n    line: e02\n    amount: 2.5\n")

    result = in_scenario("tshirt", either, "weaving", root=toy)

    assert result.score == pytest.approx(0.25 * 2.4437 + 0.5 * 0.5298, rel=REL)
    assert result.forward_score == pytest.approx(result.score, rel=SAME)


def levers_by_line(
    result: hw.Result,
) -> dict[tuple[str, str, str | None], tuple[float, str | None, float]]:
    return {
        (x.process_id, x.line_id, x.copy_of): (x.amount, x.supplier_id, x.sensitivity)
        for x in result.sensitivity(include_reference=True)
    }


def same_levers(result: hw.Result, expected: hw.Result) -> None:
    found, wanted = levers_by_line(result), levers_by_line(expected)
    assert found.keys() == wanted.keys()
    for key, (amount, supplier, sensitivity) in wanted.items():
        assert found[key][1] == supplier, key
        assert found[key][0] == pytest.approx(amount, rel=SAME), key
        assert found[key][2] == pytest.approx(sensitivity, rel=SAME, abs=1e-15), key


def adds_up(node: hw.Contribution) -> None:
    total = node.direct + sum(c.score for c in node.children) + node.rest
    assert node.score == pytest.approx(total, rel=1e-12, abs=1e-15)
    for child in node.children:
        adds_up(child)


def test_levers_and_explain_of_a_new_supplier_equal_the_edited_background(
    toy: Path, edited: Path, load_toy
) -> None:
    add_wind(toy)
    add_wind(edited)
    with_scenario(
        toy,
        "wind",
        f"  - process: {FABRIC}\n    line: e02\n    amount: 2.5\n    supplier: {WIND}\n"
        f"  - process: {WIND}\n    line: e02\n    amount: 0.02\n",
    )
    edit_line(edited, "fabric-production", "e02", f"e02,input,toy:electricity,2.5,kWh,{WIND}")
    edit_line(edited, "electricity-wind", "e02", f"e02,input,toy:cotton-fabric,0.02,kg,{FABRIC}")

    result = in_scenario("tshirt", load_toy(toy), "wind", root=toy)

    same_levers(result, refactorized(edited))
    amount, supplier, _ = levers_by_line(result)[(FABRIC, "e02", None)]
    assert (amount, supplier) == (pytest.approx(2.5, rel=SAME), WIND)
    adds_up(result.contributions(min_share=0, max_depth=4))


def test_levers_and_explain_of_a_changed_purchase_on_either_background(
    either: hw.Background, toy: Path, edited: Path
) -> None:
    with_scenario(toy, "weaving", f"  - process: {FABRIC}\n    line: e02\n    amount: 2.5\n")
    edit_line(
        edited,
        "fabric-production",
        "e02",
        "e02,input,toy:electricity,2.5,kWh,toy:electricity-production",
    )

    result = in_scenario("tshirt", either, "weaving", root=toy)

    same_levers(result, refactorized(edited))
    adds_up(result.contributions(min_share=0))


WEAVING = f"  - process: {FABRIC}\n    line: e02\n    amount: 2.5\n"
WIND_CHANGES = (
    f"  - process: {FABRIC}\n    line: e02\n    amount: 2.5\n    supplier: {WIND}\n"
    f"  - process: {WIND}\n    line: e02\n    amount: 0.02\n"
)


def one_change(toy: Path, *, wind: bool = False) -> None:
    """The scenario `s` of the toy T-shirt: one changed column of A, or two with `wind`."""
    if wind:
        add_wind(toy)
    with_scenario(toy, "s", WIND_CHANGES if wind else WEAVING)


@pytest.mark.parametrize(("crossover", "refactorizes"), [(0, True), (1, False), (2, False)])
def test_one_changed_column_is_on_its_side_of_the_crossover(
    either: hw.Background,
    toy: Path,
    changes: list[Changed],
    monkeypatch: pytest.MonkeyPatch,
    crossover: int,
    refactorizes: bool,
) -> None:
    one_change(toy)
    monkeypatch.setattr(_scenario, "CROSSOVER", crossover)

    result = in_scenario("tshirt", either, "s", root=toy)

    (changed,) = changes
    assert changed.route.refactorized == refactorizes
    assert (Step.refactorized in result.provenance.path) == refactorizes
    assert (Step.low_rank in result.provenance.path) == (not refactorizes)
    assert result.score == pytest.approx(1.125825, rel=REL)
    assert str(changed.route) == (
        f"refactorized: 1 changed process, above the crossover of {crossover}"
        if refactorizes
        else f"updated: 1 changed process, within the crossover of {crossover}"
    )


@pytest.mark.parametrize(("crossover", "refactorizes"), [(1, True), (2, False), (3, False)])
def test_two_changed_columns_are_on_their_side_of_the_crossover(
    toy: Path,
    edited: Path,
    load_toy,
    changes: list[Changed],
    monkeypatch: pytest.MonkeyPatch,
    crossover: int,
    refactorizes: bool,
) -> None:
    # The crossover is below the count by one, equal to it, and above it by one.
    one_change(toy, wind=True)
    add_wind(edited)
    edit_line(edited, "fabric-production", "e02", f"e02,input,toy:electricity,2.5,kWh,{WIND}")
    edit_line(edited, "electricity-wind", "e02", f"e02,input,toy:cotton-fabric,0.02,kg,{FABRIC}")
    monkeypatch.setattr(_scenario, "CROSSOVER", crossover)

    result = in_scenario("tshirt", load_toy(toy), "s", root=toy)

    (changed,) = changes
    expected = (
        "refactorized: 2 changed processes, above"
        if refactorizes
        else ("updated: 2 changed processes, within")
    )
    assert str(changed.route) == f"{expected} the crossover of {crossover}"
    assert changed.refactorized == refactorizes
    same(result, refactorized(edited))


def test_a_scenario_that_changes_no_purchase_keeps_the_stored_factorization(
    either: hw.Background, changes: list[Changed]
) -> None:
    result = in_scenario("tshirt-grid-scenario", either, "clean-grid")

    (changed,) = changes
    assert str(changed.route) == "stored factorization: no purchase changed"
    assert not changed.refactorized
    assert changed.solver is internals(either).solved.factorization
    assert Step.low_rank not in result.provenance.path
    assert Step.refactorized not in result.provenance.path


def test_an_update_that_fails_its_residual_check_is_refactorized(
    either: hw.Background,
    toy: Path,
    edited: Path,
    changes: list[Changed],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    one_change(toy)
    edit_line(
        edited,
        "fabric-production",
        "e02",
        "e02,input,toy:electricity,2.5,kWh,toy:electricity-production",
    )
    monkeypatch.setattr(_scenario, "CROSSOVER", 10**9)

    def failing(*args, **kwargs):  # noqa: ANN002, ANN003
        raise ResidualError(
            "low-rank update residual above tolerance 1e-12; refactorize. Rows", [1], 3e-9
        )

    monkeypatch.setattr(_scenario, "low_rank_update", failing)

    result = in_scenario("tshirt", either, "s", root=toy)

    (changed,) = changes
    assert changed.refactorized
    assert str(changed.route) == (
        "refactorized: 1 changed process, within the crossover of 1000000000, but the update "
        "failed its check: residual 3e-09 above tolerance 1e-12"
    )
    assert Step.refactorized in result.provenance.path
    same(result, refactorized(edited))


def test_an_update_whose_singularity_test_refuses_a_fine_matrix_is_refactorized(
    either: hw.Background,
    toy: Path,
    edited: Path,
    changes: list[Changed],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # On ecoinvent the update refuses valid edits from about 150 processes as singular, and
    # factorizing the same edits passes. Factorizing the edited A decides.
    one_change(toy)
    edit_line(
        edited,
        "fabric-production",
        "e02",
        "e02,input,toy:electricity,2.5,kWh,toy:electricity-production",
    )
    monkeypatch.setattr(_scenario, "CROSSOVER", 10**9)

    def refuses(*args, **kwargs):  # noqa: ANN002, ANN003
        raise SingularMatrixError("the changed processes make the matrix singular", [1])

    monkeypatch.setattr(_scenario, "low_rank_update", refuses)

    result = in_scenario("tshirt", either, "s", root=toy)

    (changed,) = changes
    assert changed.refactorized
    assert str(changed.route) == (
        "refactorized: 1 changed process, within the crossover of 1000000000, but the update "
        "failed its check: the update found the matrix singular"
    )
    assert result.provenance.update_route == str(changed.route)
    same(result, refactorized(edited))


def test_a_scenario_that_makes_the_matrix_singular_still_fails(load_toy, toy: Path) -> None:
    # Fabric buys 2.5 kWh from wind power, which buys 0.4 kg of fabric per kWh: a loop that
    # needs 2.5 x 0.4 = 1 of itself. The update refuses it, and so does the factorization.
    add_wind(toy)
    with_scenario(
        toy,
        "loop",
        f"  - process: {FABRIC}\n    line: e02\n    amount: 2.5\n    supplier: {WIND}\n"
        f"  - process: {WIND}\n    line: e02\n    amount: 0.4\n",
    )

    with pytest.raises(SingularMatrixError, match="singular"):
        in_scenario("tshirt", load_toy(toy), "loop", root=toy)


def test_a_refactorized_scenario_leaves_the_stored_factorization_alone(
    either: hw.Background, calc, toy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    one_change(toy)
    monkeypatch.setattr(_scenario, "CROSSOVER", 0)
    stored = internals(either).solved.factorization

    result = in_scenario("tshirt", either, "s", root=toy)

    assert internals(either).solved.factorization is stored
    assert result.provenance.condition != stored.condition
    plain = calc("tshirt", either)
    assert plain.score == pytest.approx(1.19205, rel=REL)
    assert plain.provenance.condition == stored.condition
    assert plain.provenance.solver == stored.settings
    assert plain.provenance.update_route is None


def test_pivoted_factors_of_a_refactorized_scenario_are_not_kept_by_the_background(
    either: hw.Background, toy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The background supply of the scenario fails its check and falls back to pivoted factors of
    # the scenario's A. They are the result's, not the background's.
    one_change(toy)
    monkeypatch.setattr(_scenario, "CROSSOVER", 0)
    stored = internals(either).solved.factorization
    real = study_calc.supply

    def pivoted(factorization: Factorization, f: ArrayLike) -> Solution:
        found = real(factorization, f)
        if factorization.n != stored.n:  # the database's own block
            return found
        return replace(found, fallback=factorize(factorization.matrix, pivoting=True))

    monkeypatch.setattr(study_calc, "supply", pivoted)

    result = in_scenario("tshirt", either, "s", root=toy)

    assert internals(either).solved.factorization is stored
    assert stored.settings.diag_pivot_thresh == 0.0
    assert result.provenance.solver.diag_pivot_thresh == 1.0
    assert result.score == pytest.approx(1.125825, rel=REL)


@pytest.mark.parametrize(
    ("crossover", "line"),
    [
        (0, "refactorized: 1 changed process, above the crossover of 0"),
        (1, "updated: 1 changed process, within the crossover of 1"),
    ],
)
def test_the_result_says_which_path_ran_and_why(
    either: hw.Background,
    toy: Path,
    monkeypatch: pytest.MonkeyPatch,
    crossover: int,
    line: str,
) -> None:
    one_change(toy)
    monkeypatch.setattr(_scenario, "CROSSOVER", crossover)

    result = in_scenario("tshirt", either, "s", root=toy)

    assert result.provenance.update_route == line


def test_a_result_outside_a_scenario_has_no_route(either: hw.Background, calc) -> None:
    assert calc("tshirt", either).provenance.update_route is None
    [plain] = footprint("tshirt-grid-scenario", either).calc(either)
    assert plain.provenance.update_route is None
    clean = in_scenario("tshirt-grid-scenario", either, "clean-grid")
    assert clean.provenance.update_route == "stored factorization: no purchase changed"
