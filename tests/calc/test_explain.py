import numpy as np
import pytest

from headwater.calc import (
    CalculationError,
    Node,
    ResidualError,
    emission_levers,
    emission_line_levers,
    explain,
    factorize,
    levers,
    line_levers,
    matrix_entry,
    scores,
    supply,
    where,
)
from headwater.calc.explain import largest, sums_by_group
from headwater.model.roles import Role


def solve_toy(A, h, i: int):
    fact = factorize(A)
    f = np.eye(A.shape[0])[i]
    return scores(fact, h).values, supply(fact, f).values, f


def walk(node: Node):
    yield node
    for child in node.children:
        yield from walk(child)


def assert_sums_at_every_level(root: Node, x: np.ndarray) -> int:
    """Each node's score is amount x stored score, and equals direct + children + rest."""
    count = 0
    for node in walk(root):
        assert node.score == pytest.approx(node.amount * x[node.index], rel=1e-12)
        parts = node.direct + sum(child.score for child in node.children) + node.rest
        assert parts == pytest.approx(node.score, rel=1e-12, abs=1e-15)
        count += 1
    return count


# E4 and the worked example


def test_tshirt_tree_matches_the_worked_example(toy) -> None:
    x, _, _ = solve_toy(toy.A, toy.h, toy.tshirt)

    root = explain(toy.A, toy.h, x, toy.tshirt, min_share=0.0, max_depth=3)

    assert root.score == pytest.approx(1.19205, rel=1e-9)
    assert root.direct == pytest.approx(0.0, abs=1e-15)
    fabric, electricity = root.children
    assert (fabric.index, electricity.index) == (toy.fabric, toy.electricity)
    assert fabric.amount == pytest.approx(0.25)
    assert fabric.score == pytest.approx(0.92715, rel=1e-9)
    assert electricity.amount == pytest.approx(0.5)
    assert electricity.score == pytest.approx(0.26490, rel=1e-9)
    # 77.8% and 22.2%: exactly 7/9 and 2/9.
    assert fabric.score / root.score == pytest.approx(7 / 9, rel=1e-9)
    assert electricity.score / root.score == pytest.approx(2 / 9, rel=1e-9)
    # Inside the T-shirt, the fabric node is 0.25 kg: direct 0.25 x 2.1192, 0.75 kWh.
    assert fabric.direct == pytest.approx(0.5298, rel=1e-9)
    (fabric_electricity,) = fabric.children
    assert fabric_electricity.amount == pytest.approx(0.75)
    assert fabric_electricity.score == pytest.approx(0.39735, rel=1e-9)


def test_fabric_tree_per_kg(toy) -> None:
    x, _, _ = solve_toy(toy.A, toy.h, toy.fabric)

    root = explain(toy.A, toy.h, x, toy.fabric, min_share=0.0)

    assert root.score == pytest.approx(3.7086, rel=1e-9)
    assert root.direct == pytest.approx(2.1192, rel=1e-9)
    (electricity,) = root.children
    assert electricity.amount == pytest.approx(3.0)
    assert electricity.score == pytest.approx(1.5894, rel=1e-9)


@pytest.mark.parametrize("fixture", ["toy", "toy_waste"])
def test_explanation_sums_to_the_score_at_every_level(
    fixture: str, request: pytest.FixtureRequest
) -> None:
    t = request.getfixturevalue(fixture)
    x, _, _ = solve_toy(t.A, t.h, t.tshirt)

    root = explain(t.A, t.h, x, t.tshirt, min_share=0.0, max_depth=5)

    assert assert_sums_at_every_level(root, x) >= 4
    assert all(node.rest == pytest.approx(0.0, abs=1e-15) for node in walk(root))


def test_waste_child_shows_positive_kg_sent_and_positive_burden(toy_waste) -> None:
    x, _, _ = solve_toy(toy_waste.A, toy_waste.h, toy_waste.tshirt)

    root = explain(toy_waste.A, toy_waste.h, x, toy_waste.tshirt, min_share=0.0)

    (waste,) = [c for c in root.children if c.index == toy_waste.incineration]
    assert waste.amount == pytest.approx(-0.05)  # ecoinvent: a waste sent is a negative input
    assert waste.runs == pytest.approx(0.05)  # kg treated
    assert waste.score == pytest.approx(0.06, rel=1e-9)
    assert waste.score / waste.runs == pytest.approx(1.2, rel=1e-9)  # burden per kg treated


def test_children_sorted_by_size(toy) -> None:
    x, _, _ = solve_toy(toy.A, toy.h, toy.tshirt)

    root = explain(toy.A, toy.h, x, toy.tshirt, min_share=0.0)

    sizes = [abs(c.score) for c in root.children]
    assert sizes == sorted(sizes, reverse=True)


def test_min_share_prunes_into_rest(toy) -> None:
    x, _, _ = solve_toy(toy.A, toy.h, toy.tshirt)

    # Electricity bought directly is 22.2% of the T-shirt, below 25%.
    root = explain(toy.A, toy.h, x, toy.tshirt, min_share=0.25)

    assert [c.index for c in root.children] == [toy.fabric]
    assert root.rest == pytest.approx(0.26490, rel=1e-9)
    assert_sums_at_every_level(root, x)


def test_max_depth_zero_puts_every_purchase_in_rest(toy) -> None:
    x, _, _ = solve_toy(toy.A, toy.h, toy.tshirt)

    root = explain(toy.A, toy.h, x, toy.tshirt, max_depth=0)

    assert root.children == ()
    assert root.rest == pytest.approx(1.19205, rel=1e-9)


def test_amount_scales_the_tree(toy) -> None:
    x, _, _ = solve_toy(toy.A, toy.h, toy.tshirt)

    root = explain(toy.A, toy.h, x, toy.tshirt, amount=10.0, min_share=0.0)

    assert root.score == pytest.approx(11.9205, rel=1e-9)
    assert_sums_at_every_level(root, x)


@pytest.mark.parametrize(
    ("index", "amount", "min_share", "max_depth"),
    [
        (0, 1.0, -0.1, 3),
        (0, 1.0, 0.03, -1),
        (0, float("nan"), 0.03, 3),
        (-1, 1.0, 0.03, 3),
        (4, 1.0, 0.03, 3),
    ],
)
def test_bad_arguments_are_errors(
    toy, index: int, amount: float, min_share: float, max_depth: int
) -> None:
    x, _, _ = solve_toy(toy.A, toy.h, toy.tshirt)

    with pytest.raises(ValueError):
        explain(toy.A, toy.h, x, index, amount=amount, min_share=min_share, max_depth=max_depth)


def test_non_square_matrix_is_an_error(toy) -> None:
    x, _, _ = solve_toy(toy.A, toy.h, toy.tshirt)

    with pytest.raises(ValueError, match="square"):
        explain(toy.A.toarray()[:, :3], toy.h, x, toy.tshirt)


def test_scores_of_another_matrix_are_an_error_naming_the_process(toy) -> None:
    # Stored scores for 3 kWh per kg fabric, explained against a matrix with 2.5 kWh.
    x, _, _ = solve_toy(toy.A, toy.h, toy.tshirt)
    edited = toy.A.toarray()
    edited[toy.electricity, toy.fabric] = -2.5

    with pytest.raises(ResidualError) as error:
        explain(edited, toy.h, x, toy.tshirt, min_share=0.0)

    assert error.value.indices == (toy.fabric,)


# Loops


def test_process_buying_its_own_product_explains(toy) -> None:
    # Fabric production uses 0.1 kg of its own fabric per kg made: the diagonal is 0.9.
    A = toy.A.toarray()
    A[toy.fabric, toy.fabric] = 0.9
    x, _, _ = solve_toy(A, toy.h, toy.tshirt)

    root = explain(A, toy.h, x, toy.tshirt, min_share=0.0, max_depth=10)

    # 0.25 kg fabric x 3.7086 / 0.9 + 0.5 kWh x 0.5298
    assert root.score == pytest.approx(0.92715 / 0.9 + 0.2649, rel=1e-9)
    fabric = next(c for c in root.children if c.index == toy.fabric)
    assert fabric.runs == pytest.approx(0.25 / 0.9)
    assert fabric.direct == pytest.approx(0.25 / 0.9 * 2.1192, rel=1e-9)
    assert all(c.index != toy.fabric for c in fabric.children)
    assert_sums_at_every_level(root, x)


def test_two_process_loop_terminates_at_max_depth(toy) -> None:
    # Electricity production buys 0.01 kg fabric per kWh; fabric buys 3 kWh per kg.
    A = toy.A.toarray()
    A[toy.fabric, toy.electricity] = -0.01
    x, _, _ = solve_toy(A, toy.h, toy.tshirt)

    root = explain(A, toy.h, x, toy.tshirt, min_share=0.0, max_depth=8)

    # x_fabric = 3.7086 / 0.97, x_electricity = 0.5298 + 0.01 x_fabric
    x_fabric = 3.7086 / 0.97
    assert root.score == pytest.approx(0.25 * x_fabric + 0.5 * (0.5298 + 0.01 * x_fabric))
    count = assert_sums_at_every_level(root, x)
    assert count < 100
    deepest = [n for n in walk(root) if not n.children]
    assert any(abs(n.rest) > 1e-12 for n in deepest)


def test_two_process_loop_terminates_on_min_share(toy) -> None:
    A = toy.A.toarray()
    A[toy.fabric, toy.electricity] = -0.01
    x, _, _ = solve_toy(A, toy.h, toy.tshirt)

    root = explain(A, toy.h, x, toy.tshirt, min_share=0.03, max_depth=500)

    assert_sums_at_every_level(root, x)


# where


def test_where_emissions_happen(toy) -> None:
    x, s, _ = solve_toy(toy.A, toy.h, toy.tshirt)

    per_process = where(toy.h, s)

    np.testing.assert_allclose(per_process, [0.0, 0.5298, 0.66225, 0.0], rtol=1e-12)
    assert per_process.sum() == pytest.approx(x[toy.tshirt], rel=1e-12)


def test_the_largest_contributions_and_the_rest(toy) -> None:
    _, s, _ = solve_toy(toy.A, toy.h, toy.tshirt)
    per_process = where(toy.h, s)

    one = largest(per_process, 1)
    every = largest(per_process, None)
    none = largest(per_process, 0)

    # Electricity 0.66225, then fabric 0.5298. The T-shirt and incineration emit nothing.
    assert one.index.tolist() == [toy.electricity]
    assert one.rest == pytest.approx(0.5298, rel=1e-12)
    assert one.rest_count == 1
    assert every.index.tolist() == [toy.electricity, toy.fabric]
    assert (every.rest, every.rest_count) == (0.0, 0)
    assert none.index.tolist() == []
    assert none.rest == pytest.approx(1.19205, rel=1e-12)
    assert none.rest_count == 2


def test_the_largest_are_by_size_and_ties_keep_their_order() -> None:
    found = largest([0.5, -2.0, 0.0, 2.0, 1.0], 3)

    assert found.index.tolist() == [1, 3, 4]
    assert (found.rest, found.rest_count) == (0.5, 1)
    with pytest.raises(ValueError, match="n must be"):
        largest([1.0], -1)
    with pytest.raises(CalculationError, match="not finite"):
        largest([1.0, np.nan], 1)


def test_sums_by_group() -> None:
    # Fabric in group 1 (CN), electricity and the T-shirt in group 0 (GLO).
    sums, counts = sums_by_group([0.0, 0.5298, 0.66225, 0.0], [0, 1, 0, 0], 3)

    np.testing.assert_allclose(sums, [0.66225, 0.5298, 0.0], rtol=1e-12)
    assert counts.tolist() == [1, 1, 0]
    with pytest.raises(ValueError, match="groups"):
        sums_by_group([1.0, 2.0], [0, 3], 3)


# levers and E3


def test_sensitivity_to_kwh_per_kg_fabric_is_0_13245(toy) -> None:
    x, s, f = solve_toy(toy.A, toy.h, toy.tshirt)

    result = levers(toy.A, x, s, f)

    k = next(
        k
        for k in range(len(result.rows))
        if (result.rows[k], result.cols[k]) == (toy.electricity, toy.fabric)
    )
    # d score / d A[electricity, fabric] = -x[electricity] x s[fabric] = -0.5298 x 0.25.
    assert result.derivative[k] == pytest.approx(-0.13245, rel=1e-9)
    # The input amount (3 kWh) enters the matrix through the sign function as -3, so the
    # sensitivity to the positive amount is the derivative times d entry / d amount = -1.
    entry = matrix_entry(Role.input, 1.0)
    assert entry is not None
    sensitivity = result.derivative[k] * entry.value
    assert sensitivity == pytest.approx(0.13245, rel=1e-9)
    # Elasticity: 0.13245 x 3 / 1.19205 = 1/3 exactly.
    assert result.elasticity[k] == pytest.approx(1 / 3, rel=1e-9)

    # Finite difference on the amount, through the sign function.
    def tshirt_score(kwh: float) -> float:
        e = matrix_entry(Role.input, kwh)
        assert e is not None
        A = toy.A.toarray()
        A[toy.electricity, toy.fabric] = e.value
        return float(scores(factorize(A), toy.h).values[toy.tshirt])

    step = 1e-4
    finite_difference = (tshirt_score(3.0 + step) - tshirt_score(3.0 - step)) / (2 * step)
    assert abs(finite_difference - sensitivity) < 1e-6
    assert tshirt_score(4.0) == pytest.approx(1.32450, rel=1e-9)


def test_levers_sorted_by_elasticity(toy) -> None:
    x, s, f = solve_toy(toy.A, toy.h, toy.tshirt)

    result = levers(toy.A, x, s, f)

    sizes = np.abs(result.elasticity)
    assert np.all(sizes[:-1] >= sizes[1:])
    # Making 1% more T-shirt per run lowers the score by 1%.
    assert (result.rows[0], result.cols[0]) == (toy.tshirt, toy.tshirt)
    assert result.elasticity[0] == pytest.approx(-1.0, rel=1e-9)
    assert len(result.rows) == toy.A.nnz


def test_levers_of_a_zero_score_is_an_error(toy) -> None:
    x, s, f = solve_toy(toy.A, np.zeros(toy.n), toy.tshirt)

    with pytest.raises(CalculationError, match="zero"):
        levers(toy.A, x, s, f)


# emission levers


def test_sensitivity_to_an_emission_is_the_factor_times_how_much_the_process_runs(toy) -> None:
    x, s, f = solve_toy(toy.A, toy.h, toy.tshirt)
    score = float(x @ f)

    result = emission_levers(toy.B, toy.c, s, score)

    def entry(flow: int, process: int) -> int:
        return next(
            k
            for k in range(len(result.rows))
            if (result.rows[k], result.cols[k]) == (flow, process)
        )

    # Electricity runs 1.25 kWh, and carbon dioxide has a factor of 1: 1 x 1.25. Its elasticity
    # is that times the 0.5 kg per kWh, over the score.
    co2 = entry(0, toy.electricity)
    assert result.derivative[co2] == pytest.approx(1.25, rel=1e-9)
    assert result.elasticity[co2] == pytest.approx(0.625 / 1.19205, rel=1e-9)
    # Methane in fabric: 29.8 x 0.25 kg of fabric.
    ch4 = entry(1, toy.fabric)
    assert result.derivative[ch4] == pytest.approx(7.45, rel=1e-9)
    assert len(result.rows) == toy.B.nnz


def test_emission_levers_are_sorted_and_their_elasticities_add_up_to_one(toy) -> None:
    # The score is linear in B: every emission 1% larger makes the score 1% larger.
    x, s, f = solve_toy(toy.A, toy.h, toy.tshirt)

    result = emission_levers(toy.B, toy.c, s, float(x @ f))

    sizes = np.abs(result.elasticity)
    assert np.all(sizes[:-1] >= sizes[1:])
    assert result.elasticity.sum() == pytest.approx(1.0, rel=1e-9)


def test_emission_levers_of_a_zero_score_is_an_error(toy) -> None:
    _, s, _ = solve_toy(toy.A, toy.h, toy.tshirt)

    with pytest.raises(CalculationError, match="zero"):
        emission_levers(toy.B, np.zeros(2), s, 0.0)


def test_emission_levers_need_a_factor_and_a_supply_per_row_and_column(toy) -> None:
    with pytest.raises(ValueError, match="length 2"):
        emission_levers(toy.B, np.ones(3), np.ones(toy.n), 1.0)
    with pytest.raises(ValueError, match="length 4"):
        emission_levers(toy.B, toy.c, np.ones(3), 1.0)


# levers of single lines


def test_line_levers_of_the_tshirt_lines_are_hand_checked(toy) -> None:
    x, s, f = solve_toy(toy.A, toy.h, toy.tshirt)
    # (row, column, sign of one unit, amount): the T-shirt buys 0.25 kg of fabric and 0.5 kWh,
    # fabric production buys 3 kWh per kg, and the T-shirt's own reference line.
    lines = [
        (toy.fabric, toy.tshirt, -1.0, 0.25),
        (toy.electricity, toy.tshirt, -1.0, 0.5),
        (toy.electricity, toy.fabric, -1.0, 3.0),
        (toy.tshirt, toy.tshirt, 1.0, 1.0),
    ]
    rows, cols, signs, amounts = (np.array(column) for column in zip(*lines, strict=True))

    found = line_levers(toy.A, x, s, f, rows.astype(int), cols.astype(int), signs, amounts)

    assert found.present.all()
    # x[supplier] x s[buyer]: 3.7086 x 1, 0.5298 x 1, 0.5298 x 0.25, and -1.19205 x 1.
    assert found.sensitivity == pytest.approx([3.7086, 0.5298, 0.13245, -1.19205], rel=1e-9)
    assert found.elasticity == pytest.approx([7 / 9, 2 / 9, 1 / 3, -1.0], rel=1e-9)


def test_line_levers_agree_with_the_levers_of_every_matrix_entry(toy_waste) -> None:
    x, s, f = solve_toy(toy_waste.A, toy_waste.h, toy_waste.tshirt)
    entries = levers(toy_waste.A, x, s, f)
    ones, amounts = np.ones(len(entries.rows)), toy_waste.A.toarray()[entries.rows, entries.cols]

    found = line_levers(toy_waste.A, x, s, f, entries.rows, entries.cols, ones, amounts)

    assert found.present.all()
    assert found.sensitivity == pytest.approx(entries.derivative, rel=1e-12)
    assert found.elasticity == pytest.approx(entries.elasticity, rel=1e-12)


def test_a_line_with_no_matrix_cell_is_not_present(toy) -> None:
    x, s, f = solve_toy(toy.A, toy.h, toy.tshirt)
    # A[incineration, tshirt] holds nothing: no waste is sent, so a line there has no effect.
    rows, cols = np.array([toy.incineration, toy.fabric]), np.array([toy.tshirt, toy.tshirt])

    found = line_levers(toy.A, x, s, f, rows, cols, np.array([1.0, -1.0]), np.array([0.05, 0.25]))

    assert found.present.tolist() == [False, True]
    assert found.sensitivity[0] == 0.0 and found.elasticity[0] == 0.0


def test_line_levers_of_a_zero_score_and_of_bad_input_are_errors(toy) -> None:
    x, s, f = solve_toy(toy.A, np.zeros(toy.n), toy.tshirt)
    with pytest.raises(CalculationError, match="zero"):
        line_levers(toy.A, x, s, f, np.array([1]), np.array([0]), np.array([-1.0]), np.array([1.0]))

    x, s, f = solve_toy(toy.A, toy.h, toy.tshirt)
    with pytest.raises(ValueError, match="same length"):
        line_levers(
            toy.A, x, s, f, np.array([1]), np.array([0, 1]), np.array([-1.0]), np.array([1.0])
        )
    with pytest.raises(ValueError, match="out of range"):
        line_levers(toy.A, x, s, f, np.array([9]), np.array([0]), np.array([-1.0]), np.array([1.0]))


def test_emission_line_levers_of_the_tshirt_are_hand_checked(toy) -> None:
    x, s, f = solve_toy(toy.A, toy.h, toy.tshirt)
    # (flow row, process): 0.5 kg CO2 in electricity, 0.004 kg CH4 in fabric, and CO2 in the
    # T-shirt, which has none.
    rows, cols = np.array([0, 1, 0]), np.array([toy.electricity, toy.fabric, toy.tshirt])

    found = emission_line_levers(
        toy.B, toy.c, s, float(x @ f), rows, cols, np.array([0.5, 0.004, 1.0])
    )

    assert found.present.tolist() == [True, True, False]
    assert found.sensitivity[:2] == pytest.approx([1.25, 7.45], rel=1e-9)  # c x how much it runs
    assert found.elasticity[:2] == pytest.approx([0.625 / 1.19205, 0.0298 / 1.19205], rel=1e-9)


def test_emission_line_levers_of_a_zero_score_is_an_error(toy) -> None:
    _, s, _ = solve_toy(toy.A, toy.h, toy.tshirt)

    with pytest.raises(CalculationError, match="zero"):
        emission_line_levers(toy.B, toy.c, s, 0.0, np.array([0]), np.array([2]), np.array([0.5]))
