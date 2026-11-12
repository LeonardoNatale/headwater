"""The study block of the system: study scores on stored background scores, and what the study
asks of the background.

The toy matrices are split after the T-shirt: process 0 is the study, processes 1 to 3 (fabric,
electricity, incineration) are the background.
"""

import numpy as np
import pytest

import headwater.calc.study as study_module
from headwater.calc import (
    SingularMatrixError,
    Step,
    factorize,
    scores,
    solve_study,
)

STUDY = slice(0, 1)
BACKGROUND = slice(1, None)


def split(toy):
    """A_ss, A_bs, h_s for the T-shirt, the factorization and scores of the background."""
    A = toy.A.toarray()
    background = factorize(A[BACKGROUND, BACKGROUND])
    x_b = scores(background, toy.h[BACKGROUND]).values
    return A[STUDY, STUDY], A[BACKGROUND, STUDY], toy.h[STUDY], background, x_b


def test_study_scores_from_the_stored_background_scores(toy) -> None:
    A_ss, A_bs, h_s, background, x_b = split(toy)

    solved = solve_study(background, x_b, A_ss, A_bs, h_s, [1.0])

    # h_s - A_bsᵀ x_b = 0 + 0.25 × 3.7086 + 0.5 × 0.5298
    assert solved.scores.values[0] == pytest.approx(1.19205, rel=1e-9)


def test_study_with_the_waste_line_scores_1_25205(toy_waste) -> None:
    A_ss, A_bs, h_s, background, x_b = split(toy_waste)

    solved = solve_study(background, x_b, A_ss, A_bs, h_s, [1.0])

    assert solved.scores.values[0] == pytest.approx(1.25205, rel=1e-9)


@pytest.mark.parametrize(
    ("fixture", "incineration"), [("toy", 0.0), ("toy_waste", 0.05)], ids=["no waste", "waste"]
)
def test_what_the_study_asks_of_the_background(request, fixture: str, incineration: float) -> None:
    toy = request.getfixturevalue(fixture)
    A_ss, A_bs, h_s, background, x_b = split(toy)

    solved = solve_study(background, x_b, A_ss, A_bs, h_s, [1.0])

    # One T-shirt runs once. It buys 0.25 kg of fabric, which buys 0.75 kWh, and 0.5 kWh itself.
    np.testing.assert_allclose(solved.supply.values, [1.0], rtol=1e-12)
    np.testing.assert_allclose(
        solved.background_supply.values, [0.25, 1.25, incineration], rtol=1e-12, atol=1e-15
    )


def test_two_roads_agree_for_the_study_block(toy_waste) -> None:
    A_ss, A_bs, h_s, background, x_b = split(toy_waste)

    solved = solve_study(background, x_b, A_ss, A_bs, h_s, [1.0])

    forward = h_s @ solved.supply.values + toy_waste.h[BACKGROUND] @ solved.background_supply.values
    assert forward == pytest.approx(solved.scores.values[0], rel=1e-12)


def test_the_solutions_record_their_steps_and_residuals(toy) -> None:
    A_ss, A_bs, h_s, background, x_b = split(toy)

    solved = solve_study(background, x_b, A_ss, A_bs, h_s, [1.0])

    for solution in (solved.scores, solved.supply, solved.background_supply):
        assert solution.path
        assert all(isinstance(step, Step) for step in solution.path)
        assert 0.0 <= solution.residual <= background.settings.tolerance


def test_the_study_may_buy_from_itself_in_a_loop() -> None:
    # Two study processes: 0 buys 0.5 of 1, and 1 buys 0.2 of 0. The background is one process.
    A = np.array(
        [
            [1.0, -0.2, 0.0],
            [-0.5, 1.0, 0.0],
            [-0.1, 0.0, 1.0],
        ]
    )
    h = np.array([1.0, 2.0, 3.0])
    background = factorize(A[2:, 2:])
    x_b = scores(background, h[2:]).values

    solved = solve_study(background, x_b, A[:2, :2], A[2:, :2], h[:2], [1.0, 0.0])

    expected = scores(factorize(A), h).values
    np.testing.assert_allclose(solved.scores.values, expected[:2], rtol=1e-12)


def test_a_study_matrix_that_cannot_be_solved_names_the_study_process(toy) -> None:
    _, A_bs, _, background, x_b = split(toy)

    with pytest.raises(SingularMatrixError) as error:
        solve_study(background, x_b, np.array([[0.0]]), A_bs, [0.0], [1.0])

    assert error.value.indices == (0,)


def test_a_background_solve_error_counts_the_background_after_the_study(
    toy, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Indices of an error are those of the stacked system: the study's processes first. The
    # background's process 2 (electricity) is process 3 of the system, after the one study process.
    A_ss, A_bs, h_s, background, x_b = split(toy)
    real_supply = study_module.supply

    def supply_failing_on_the_background(factorization, f):
        if factorization is background:
            raise SingularMatrixError("processes with no reference output", [1, 2])
        return real_supply(factorization, f)

    monkeypatch.setattr(study_module, "supply", supply_failing_on_the_background)

    with pytest.raises(SingularMatrixError) as error:
        solve_study(background, x_b, A_ss, A_bs, h_s, [1.0])

    assert error.value.indices == (2, 3)


@pytest.mark.parametrize(
    ("A_bs", "x_b", "h_s", "f_s"),
    [
        (np.zeros((2, 1)), np.zeros(3), np.zeros(1), np.zeros(1)),  # A_bs rows != background
        (np.zeros((3, 2)), np.zeros(3), np.zeros(1), np.zeros(1)),  # A_bs columns != study
        (np.zeros((3, 1)), np.zeros(2), np.zeros(1), np.zeros(1)),  # x_b length
        (np.zeros((3, 1)), np.zeros(3), np.zeros(2), np.zeros(1)),  # h_s length
        (np.zeros((3, 1)), np.zeros(3), np.zeros(1), np.zeros(2)),  # f_s length
    ],
    ids=["A_bs rows", "A_bs columns", "x_b", "h_s", "f_s"],
)
def test_shapes_that_do_not_fit_are_an_error(toy, A_bs, x_b, h_s, f_s) -> None:
    _, _, _, background, _ = split(toy)

    with pytest.raises(ValueError, match="length|shape"):
        solve_study(background, x_b, np.array([[1.0]]), A_bs, h_s, f_s)


def test_the_solution_hands_back_the_factorization_of_the_study_block(toy) -> None:
    A_ss, A_bs, h_s, background, x_b = split(toy)

    solved = solve_study(background, x_b, A_ss, A_bs, h_s, [1.0])

    # A 1 x 1 block [1]: the score of an emission of 1 is 1, g = 1 + 1 = 2, so a 0.1% change of
    # the entry and of the emission moves it by 0.2%: the condition is 2.
    assert solved.factorization.n == 1
    assert solved.factorization.condition == pytest.approx(2.0, rel=1e-9)
    assert solved.factorization.settings.tolerance == background.settings.tolerance
