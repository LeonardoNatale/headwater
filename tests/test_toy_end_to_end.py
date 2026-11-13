"""Exit tests of M0: the toy T-shirt from its files, through the public API.

The hand-checked numbers are in docs/calculation.md: 1.19205, 1.25205 with the waste line,
0.13245 for the electricity sensitivity.
"""

import re
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

import headwater as hw
from headwater.calc import SingularMatrixError, Step
from headwater.model import InvalidDataError, Role
from tests.conftest import add_lines, open_database, toy_database

TOY = Path(__file__).resolve().parent / "fixtures" / "toy"
REL = 1e-9  # AGENTS.md: relative 1e-9 for toy fixtures


def load_toy(root: Path = TOY) -> hw.Background:
    return hw.load(root / "background", method=root / "methods" / "toy-climate", namespace="toy")


def calc(study: str, root: Path = TOY, background: hw.Background | None = None) -> hw.Result:
    return open_database(toy_database(root, study)).calc(
        background or load_toy(root), "tshirt:tshirt"
    )


def edit(path: Path, old: str, new: str) -> None:
    """Replace text in a file. Fails if the text is not there, so a typo cannot pass silently."""
    text = path.read_text()
    assert old in text, f"{old!r} not found in {path}"
    path.write_text(text.replace(old, new, 1))


def walk(node: hw.Contribution) -> Iterator[hw.Contribution]:
    yield node
    for child in node.children:
        yield from walk(child)


def lever(levers: tuple[hw.Sensitivity, ...], process_id: str, line_id: str) -> hw.Sensitivity:
    (found,) = [x for x in levers if (x.process_id, x.line_id) == (process_id, line_id)]
    return found


@pytest.fixture(scope="module")
def background() -> hw.Background:
    return load_toy()


@pytest.fixture
def toy(tmp_path: Path) -> Path:
    """A writable copy of the toy fixtures, for tests that change one thing."""
    target = tmp_path / "toy"
    shutil.copytree(TOY, target)
    return target


# E1: the scores


def test_tshirt_scores_1_19205(background: hw.Background) -> None:
    result = calc("tshirt", background=background)

    assert result.score == pytest.approx(1.19205, rel=REL)
    assert result.process_id == "tshirt:tshirt"
    assert result.indicator == "climate-change"
    assert result.unit == "kg CO2-eq"


def test_tshirt_with_the_waste_line_scores_1_25205(background: hw.Background) -> None:
    result = calc("tshirt-waste", background=background)

    assert result.score == pytest.approx(1.25205, rel=REL)


# E2: two roads to the same number


@pytest.mark.parametrize("study", ["tshirt", "tshirt-waste"])
def test_forward_road_equals_backward_road(background: hw.Background, study: str) -> None:
    result = calc(study, background=background)

    # forward: h · s from one forward solve. backward: x[T-shirt].
    assert result.forward_score == pytest.approx(result.score, rel=1e-12)


# E3: sensitivity


def test_sensitivity_to_kwh_per_kg_fabric_is_0_13245(background: hw.Background) -> None:
    result = calc("tshirt", background=background)

    found = lever(result.sensitivity(), "toy:fabric-production", "e02")

    assert (found.flow_id, found.supplier_id, found.unit) == (
        "toy:electricity",
        "toy:electricity-production",
        "kWh",
    )
    assert found.amount == pytest.approx(3.0, rel=REL)
    # x[electricity] × s[fabric] = 0.5298 × 0.25, positive: one more kWh per kg adds to the score.
    assert found.sensitivity == pytest.approx(0.13245, rel=REL)
    assert found.elasticity == pytest.approx(1 / 3, rel=REL)


def test_sensitivity_matches_a_finite_difference(toy: Path) -> None:
    before = calc("tshirt", root=toy)
    sensitivity = lever(before.sensitivity(), "toy:fabric-production", "e02").sensitivity
    edit(
        toy / "background" / "exchanges.csv",
        "e02,input,toy:electricity,3,kWh",
        "e02,input,toy:electricity,4,kWh",
    )

    after = calc("tshirt", root=toy)

    assert after.score == pytest.approx(1.32450, rel=REL)
    assert after.score - before.score == pytest.approx(sensitivity * 1.0, rel=REL)


def test_sensitivity_of_the_waste_line_is_the_burden_per_kg_treated(
    background: hw.Background,
) -> None:
    result = calc("tshirt-waste", background=background)

    found = lever(result.sensitivity(), "tshirt:tshirt", "e04")

    assert (found.flow_id, found.supplier_id, found.unit) == (
        "toy:textile-waste",
        "toy:incineration-textile-waste",
        "kg",
    )
    assert found.amount == pytest.approx(0.05, rel=REL)
    assert found.sensitivity == pytest.approx(1.2, rel=REL)
    assert found.elasticity == pytest.approx(0.06 / 1.25205, rel=REL)


def test_levers_are_ranked_by_elasticity_and_cover_every_technosphere_line(
    background: hw.Background,
) -> None:
    result = calc("tshirt", background=background)

    levers = result.sensitivity(include_reference=True)

    sizes = [abs(x.elasticity) for x in levers]
    assert sizes == sorted(sizes, reverse=True)
    technosphere = [x for x in levers if x.role not in (Role.emission, Role.resource)]
    assert sorted((x.process_id, x.line_id) for x in technosphere) == [
        ("toy:electricity-production", "e01"),
        ("toy:fabric-production", "e01"),
        ("toy:fabric-production", "e02"),
        ("toy:incineration-textile-waste", "e01"),
        ("tshirt:tshirt", "e01"),
        ("tshirt:tshirt", "e02"),
        ("tshirt:tshirt", "e03"),
    ]
    # Doubling what a process makes per run halves what each unit costs.
    assert lever(levers, "tshirt:tshirt", "e01").elasticity == pytest.approx(-1.0, rel=REL)
    assert lever(levers, "tshirt:tshirt", "e02").elasticity == pytest.approx(7 / 9, rel=REL)
    # The T-shirt sends no waste, so incineration does not run and is no lever.
    incineration = lever(levers, "toy:incineration-textile-waste", "e01")
    assert incineration.elasticity == pytest.approx(0.0, abs=1e-15)
    top = [(x.process_id, x.line_id) for x in result.sensitivity(top=2, include_reference=True)]
    assert top == [(x.process_id, x.line_id) for x in levers[:2]]


# E4: the explanation tree


@pytest.mark.parametrize("study", ["tshirt", "tshirt-waste"])
@pytest.mark.parametrize(("min_share", "max_depth"), [(0.0, 10), (0.03, 3), (0.5, 1)])
def test_explanation_sums_to_the_score_at_every_level(
    background: hw.Background, study: str, min_share: float, max_depth: int
) -> None:
    result = calc(study, background=background)

    root = result.contributions(min_share=min_share, max_depth=max_depth)

    assert root.score == pytest.approx(result.score, rel=1e-12)
    for node in walk(root):
        parts = node.direct + sum(child.score for child in node.children) + node.rest
        assert parts == pytest.approx(node.score, rel=1e-12, abs=1e-15)
        assert node.score == pytest.approx(node.amount * node.per_unit, rel=1e-12)


def test_explanation_matches_the_worked_example(background: hw.Background) -> None:
    result = calc("tshirt", background=background)

    root = result.contributions(min_share=0.0)

    assert (root.process_id, root.name, root.flow_id, root.unit) == (
        "tshirt:tshirt",
        "T-shirt",
        "tshirt:tshirt",
        "unit",
    )
    assert root.amount == pytest.approx(1.0, rel=REL)
    assert root.direct == pytest.approx(0.0, abs=1e-15)
    fabric, electricity = root.children
    assert (fabric.process_id, fabric.unit) == ("toy:fabric-production", "kg")
    assert fabric.amount == pytest.approx(0.25, rel=REL)
    assert fabric.per_unit == pytest.approx(3.7086, rel=REL)
    assert fabric.score == pytest.approx(0.92715, rel=REL)
    assert (electricity.process_id, electricity.unit) == ("toy:electricity-production", "kWh")
    assert electricity.amount == pytest.approx(0.5, rel=REL)
    assert electricity.per_unit == pytest.approx(0.5298, rel=REL)
    assert electricity.score == pytest.approx(0.26490, rel=REL)
    # Inside the T-shirt, the fabric node is 0.25 kg: direct 0.25 × 2.1192, plus 0.75 kWh.
    assert fabric.direct == pytest.approx(0.5298, rel=REL)
    (fabric_electricity,) = fabric.children
    assert fabric_electricity.amount == pytest.approx(0.75, rel=REL)
    assert fabric_electricity.score == pytest.approx(0.39735, rel=REL)


def test_waste_line_is_positive_kg_sent_and_positive_burden_per_kg_treated(
    background: hw.Background,
) -> None:
    result = calc("tshirt-waste", background=background)

    root = result.contributions(min_share=0.0)

    (incineration,) = [c for c in root.children if c.flow_id == "toy:textile-waste"]
    assert (incineration.process_id, incineration.name, incineration.unit) == (
        "toy:incineration-textile-waste",
        "incineration of textile waste",
        "kg",
    )
    assert incineration.amount == pytest.approx(0.05, rel=REL)
    assert incineration.per_unit == pytest.approx(1.2, rel=REL)
    assert incineration.score == pytest.approx(0.06, rel=REL)
    assert incineration.direct == pytest.approx(0.06, rel=REL)


# E5: errors that name the item


def test_input_with_no_supplier_raises_naming_the_line(toy: Path) -> None:
    edit(
        toy / "tshirt" / "tshirt" / "exchanges.csv",
        "0.25,kg,toy:fabric-production",
        "0.25,kg,",
    )
    study = open_database(toy / "tshirt" / "tshirt")

    with pytest.raises(hw.HeadwaterError, match="tshirt:tshirt line e02") as info:
        study.calc(load_toy(toy), "tshirt:tshirt")

    assert isinstance(info.value, InvalidDataError)
    assert [(p.code, p.item) for p in info.value.problems] == [
        ("supplier-missing", "tshirt:tshirt line e02")
    ]


def test_unknown_unit_raises_naming_the_line_and_the_unit(toy: Path) -> None:
    edit(toy / "tshirt" / "tshirt" / "exchanges.csv", "0.25,kg,", "0.25,kgs,")
    study = open_database(toy / "tshirt" / "tshirt")

    with pytest.raises(hw.HeadwaterError, match="tshirt:tshirt line e02") as info:
        study.calc(load_toy(toy), "tshirt:tshirt")

    assert isinstance(info.value, InvalidDataError)
    assert [(p.code, p.item) for p in info.value.problems] == [
        ("unknown-unit", "tshirt:tshirt line e02")
    ]
    assert "'kgs'" in str(info.value)


def test_flow_with_no_factor_raises_at_calculation_naming_the_flow(toy: Path) -> None:
    study_dir = toy / "tshirt" / "tshirt"
    with (study_dir / "flows.csv").open("a") as f:
        f.write("tshirt:n2o,Dinitrogen monoxide,elementary,kg,air,10024-97-2\n")
    add_lines(study_dir, "tshirt", "e04,emission,tshirt:n2o,0.001,kg,")
    study = open_database(study_dir)  # the files are fine; the method is what lacks the factor

    with pytest.raises(hw.HeadwaterError, match="flow tshirt:n2o") as info:
        study.calc(load_toy(toy), "tshirt:tshirt")

    assert isinstance(info.value, InvalidDataError)
    assert [(p.code, p.item) for p in info.value.problems] == [("no-factor", "flow tshirt:n2o")]
    assert "tshirt:tshirt" in str(info.value)


def test_background_flow_with_no_factor_is_listed_on_the_result_not_raised(toy: Path) -> None:
    with (toy / "background" / "flows.csv").open("a") as f:
        f.write("toy:n2o,Dinitrogen monoxide,elementary,kg,air,10024-97-2\n")
    add_lines(toy / "background", "electricity-production", "e04,emission,toy:n2o,0.0001,kg,")

    result = calc("tshirt", root=toy, background=load_toy(toy))  # ecoinvent has such flows

    assert result.score == pytest.approx(1.19205, rel=REL)  # it counts 0
    assert [(f.flow_id, f.processes) for f in result.not_characterised] == [
        ("toy:n2o", ("toy:electricity-production",))
    ]


def test_factor_for_one_location_is_refused_not_used_as_global(toy: Path) -> None:
    with (toy / "methods" / "toy-climate" / "factors.csv").open("a") as f:
        f.write("climate-change,toy:co2,CH,1.1\n")

    with pytest.raises(InvalidDataError, match="climate-change/toy:co2 at CH") as info:
        load_toy(toy)

    assert [p.code for p in info.value.problems] == ["not-supported-yet"]


def test_singular_matrix_error_names_the_process_by_id(toy: Path) -> None:
    # The T-shirt uses up all it makes, so it has no net output: the matrix cannot be solved.
    add_lines(toy / "tshirt" / "tshirt", "tshirt", "e04,own_use,tshirt:tshirt,1,unit,tshirt:tshirt")
    study = open_database(toy / "tshirt" / "tshirt")

    with pytest.raises(hw.HeadwaterError, match="tshirt:tshirt") as info:
        study.calc(load_toy(toy), "tshirt:tshirt")

    assert isinstance(info.value, SingularMatrixError)
    assert info.value.indices == ()  # matrix indices never leave the calculation


def test_own_use_in_a_treatment_raises_naming_the_line(toy: Path) -> None:
    # calc has no sign for own use in a treatment; validation stops it first, naming the line.
    add_lines(
        toy / "background",
        "incineration-textile-waste",
        "e03,own_use,toy:textile-waste,0.01,kg,toy:incineration-textile-waste",
    )

    with pytest.raises(InvalidDataError, match="toy:incineration-textile-waste line e03") as info:
        load_toy(toy)

    assert [p.code for p in info.value.problems] == ["role-flow-kind"]


def test_input_hash_ignores_the_order_of_lines_in_a_file(toy: Path) -> None:
    first = calc("tshirt", root=toy).provenance.input_hash
    factors = toy / "methods" / "toy-climate" / "factors.csv"
    header, *rows = factors.read_text().splitlines()
    factors.write_text("\n".join([header, *reversed(rows)]) + "\n")

    assert calc("tshirt", root=toy).provenance.input_hash == first


def test_method_with_several_indicators_needs_one_named(toy: Path) -> None:
    edit(
        toy / "methods" / "toy-climate" / "method.yaml",
        "    unit: kg CO2-eq\n",
        "    unit: kg CO2-eq\n  - id: methane\n    name: Methane\n    unit: kg CH4\n",
    )
    with (toy / "methods" / "toy-climate" / "factors.csv").open("a") as f:
        f.write("methane,toy:ch4,,1\n")
    background = load_toy(toy)
    study = open_database(toy / "tshirt" / "tshirt")

    with pytest.raises(ValueError, match="climate-change, methane"):
        study.calc(background, "tshirt:tshirt")

    assert study.calc(
        background, "tshirt:tshirt", indicator="climate-change"
    ).score == pytest.approx(1.19205, rel=REL)
    # CO2 has no methane factor, but the method covers it, so it counts 0 for that indicator:
    # 0.25 kg of fabric × 0.004 + 1.25 kWh in all × 0.001.
    methane = study.calc(background, "tshirt:tshirt", indicator="methane")
    assert methane.unit == "kg CH4"
    assert methane.score == pytest.approx(0.25 * 0.004 + 1.25 * 0.001, rel=REL)


# Provenance


def test_result_carries_its_provenance(background: hw.Background) -> None:
    result = calc("tshirt", background=background)

    p = result.provenance
    assert re.fullmatch(r"[0-9a-f]{64}", p.input_hash)
    assert p.engine_version == hw.__version__
    assert (p.method, p.method_version, p.indicator) == ("toy-climate", "1", "climate-change")
    assert p.pin == "toy-background@fixture"
    assert p.solver.solver == "scipy.sparse.linalg.splu"
    assert p.path  # one entry or more per solve: background scores, study scores, supply
    assert all(isinstance(step, Step) for step in p.path)
    assert 0.0 <= p.residual <= p.solver.tolerance


def test_input_hash_is_stable_and_changes_with_one_amount(toy: Path) -> None:
    first = calc("tshirt", root=toy).provenance.input_hash
    again = calc("tshirt", root=toy).provenance.input_hash
    edit(toy / "tshirt" / "tshirt" / "exchanges.csv", "0.25,kg", "0.30,kg")

    changed = calc("tshirt", root=toy)

    assert again == first
    assert changed.provenance.input_hash != first
    assert changed.score == pytest.approx(1.37748, rel=REL)  # README: 0.30 kg of fabric
