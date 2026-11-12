"""Numerical safeguards: condition estimate, exact zeros, the kept pivoted factorization, copies."""

import numpy as np
import pytest
from scipy import sparse

import headwater.calc.solve as solve_module
from headwater.calc import (
    CONDITION_LIMIT,
    Factorization,
    SingularMatrixError,
    Step,
    factorize,
    scores,
    solve_study,
    supply,
    update_column,
)
from headwater.calc.arrays import as_matrix
from headwater.calc.solve import backward_error, structural_zeros


def exact_condition(A: np.ndarray) -> float:
    """The condition by dense algebra, as factorize defines it, for the scores x of Aᵀ x = 1.

    Each row's change is relative to half the sum of the absolute terms of its equation, which
    is |x_j| when no term cancels another.
    """
    h = np.ones(A.shape[0])
    x = np.linalg.solve(A.T, h)
    terms = np.abs(A.T) @ np.abs(x) + h
    inverse = np.abs(np.linalg.inv(A.T))
    return float(np.max(inverse @ terms / (terms / (2 * np.abs(np.diag(A))))))


def with_loop(toy, gain: float) -> np.ndarray:
    """Electricity buys fabric, closing a loop whose gain is 3 kWh per kg x the fabric per kWh."""
    A = toy.A.toarray()
    A[toy.fabric, toy.electricity] = -gain / 3.0
    return A


# Condition estimate


@pytest.mark.parametrize("fixture", ["toy", "toy_waste"])
def test_condition_estimate_equals_the_exact_value(
    fixture: str, request: pytest.FixtureRequest
) -> None:
    t = request.getfixturevalue(fixture)

    fact = factorize(t.A)

    assert fact.condition == pytest.approx(exact_condition(t.A.toarray()), rel=1e-9)
    assert fact.settings.condition_limit == CONDITION_LIMIT


def test_condition_is_the_componentwise_one_when_nothing_cancels(toy) -> None:
    A = toy.A.toarray()
    x = np.linalg.solve(A.T, np.ones(4))
    usual = np.abs(np.linalg.inv(A.T)) @ (np.abs(A.T) @ np.abs(x) + 1) / np.abs(x)

    assert factorize(A).condition == pytest.approx(usual.max(), rel=1e-9)


def test_a_credit_that_cancels_a_burden_is_not_near_singular() -> None:
    # Process 0 avoids 1 unit of product 1, made by process 1: with a burden of 1 per run for
    # both, process 0 scores 1 - 1 = 0. The matrix is as well conditioned as can be.
    A = np.array([[1.0, 0.0], [1.0, 1.0]])

    fact = factorize(A)

    assert fact.condition == pytest.approx(exact_condition(A), rel=1e-9)
    assert fact.condition < 10


def test_condition_grows_as_a_loop_gain_nears_one(toy) -> None:
    # A loop that loses 1e-4 of what it makes: every score in it is multiplied by about 1e4.
    A = with_loop(toy, 1 - 1e-4)

    fact = factorize(A)

    assert fact.condition == pytest.approx(exact_condition(A), rel=1e-9)
    assert 1e4 < fact.condition < 1e5


def test_loop_gain_within_1e_12_of_one_is_nearly_singular(toy) -> None:
    with pytest.raises(SingularMatrixError, match="nearly singular") as error:
        factorize(with_loop(toy, 1 - 1e-12))

    assert error.value.indices == (toy.fabric, toy.electricity)


def productive_economy(n: int, seed: int) -> np.ndarray:
    """A background where each process uses less than it makes, with units spread over 1e12."""
    rng = np.random.default_rng(seed)
    P = sparse.random_array((n, n), density=0.05, rng=rng).toarray()
    np.fill_diagonal(P, 0.0)
    P *= 0.9 / np.maximum(P.sum(axis=0), 1e-300)
    units = 10.0 ** rng.uniform(-6, 6, n)
    return (np.eye(n) - P) * units[:, None] / units[None, :]


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_condition_estimate_of_a_large_matrix_is_close_to_the_exact_value(seed: int) -> None:
    # 150 processes, above the size where the condition is computed exactly.
    A = productive_economy(150, seed)

    estimate = factorize(A).condition

    exact = exact_condition(A)
    assert exact / 3 <= estimate <= exact * (1 + 1e-9)


@pytest.mark.parametrize("seed", range(10))
def test_one_norm_estimate_is_a_lower_bound_close_to_the_norm(seed: int) -> None:
    M = np.random.default_rng(seed).normal(size=(120, 120))

    estimate = solve_module._one_norm_estimate(lambda v: M @ v, lambda v: M.T @ v, 120)

    exact = float(np.abs(M).sum(axis=0).max())
    assert exact / 3 <= estimate <= exact * (1 + 1e-12)


def test_condition_limit_is_a_setting(toy) -> None:
    A = with_loop(toy, 1 - 1e-4)

    with pytest.raises(SingularMatrixError, match="nearly singular"):
        factorize(A, condition_limit=1e3)


def test_condition_limit_must_be_at_least_one() -> None:
    with pytest.raises(ValueError, match="condition_limit"):
        factorize(np.eye(2), condition_limit=0.5)


def test_condition_estimate_is_the_same_every_time() -> None:
    # 150 processes: the estimate, not the exact value. NumPy's global generator plays no part.
    A = productive_economy(150, 4)
    first = factorize(A).condition
    np.random.seed(1)

    assert factorize(A).condition == first


def test_a_study_block_uses_the_condition_limit_of_its_background(toy) -> None:
    # Two study processes buy from each other with a loop gain of 1 - 1e-4: condition about 4e4.
    background = factorize(toy.A, condition_limit=1e3)
    x_b = scores(background, toy.h).values
    A_ss = np.array([[1.0, -(1 - 1e-4)], [-1.0, 1.0]])

    with pytest.raises(SingularMatrixError, match="nearly singular"):
        solve_study(background, x_b, A_ss, np.zeros((toy.n, 2)), np.zeros(2), np.array([1.0, 0]))


# Exact zeros


def h_without_incineration(toy) -> np.ndarray:
    """Incineration buys nothing and emits nothing here: its score is exactly 0."""
    h = toy.h.copy()
    h[toy.incineration] = 0.0
    return h


def test_structural_zeros_of_scores(toy) -> None:
    zero = structural_zeros(abs(toy.A.T), h_without_incineration(toy))

    assert zero.tolist() == [False, False, False, True]


def test_every_solve_reports_its_exact_zeros(toy) -> None:
    # No check fails here: the zeros are found and reported all the same.
    fact = factorize(toy.A)

    x = scores(fact, h_without_incineration(toy))
    s = supply(fact, np.eye(toy.n)[toy.fabric])

    assert x.path == (Step.lu,)
    assert x.exact_zeros.tolist() == [False, False, False, True]
    assert s.exact_zeros.tolist() == [True, False, False, True]


def test_exact_zeros_are_per_indicator(toy) -> None:
    # Methane only: incineration emits CO2 and no methane, so it is an exact zero for methane
    # and not for climate change.
    fact = factorize(toy.A)
    methane = toy.B.T @ np.array([0.0, 29.8])

    assert scores(fact, toy.h).exact_zeros.sum() == 0
    assert scores(fact, methane).exact_zeros.tolist() == [False, False, False, True]


def test_structural_zeros_of_supply(toy) -> None:
    # One kg of fabric runs fabric and electricity production, never the T-shirt or incineration.
    zero = structural_zeros(abs(toy.A), np.eye(toy.n)[toy.fabric])

    assert zero.tolist() == [True, False, False, True]


def leave_noise(monkeypatch: pytest.MonkeyPatch, rows: list[int]) -> None:
    """Make every solve leave noise of 1e-9 of its largest value in `rows`, refinement solves
    too, as pivoted LU does on ecoinvent in products whose exact score is 0.

    A relative check of such a row compares noise with noise and fails, whatever its size.
    """
    real = Factorization.solve

    def solve(self: Factorization, b: np.ndarray, *, transposed: bool = False) -> np.ndarray:
        y = real(self, b, transposed=transposed)
        y[rows] += 1e-9 * np.abs(y).max()
        return y

    monkeypatch.setattr(Factorization, "solve", solve)


def test_a_score_with_no_burden_upstream_is_exactly_zero(toy, monkeypatch) -> None:
    fact = factorize(toy.A)
    leave_noise(monkeypatch, [toy.incineration])

    x = scores(fact, h_without_incineration(toy))

    assert x.values[toy.incineration] == 0.0
    assert x.values[toy.tshirt] == pytest.approx(1.19205, rel=1e-12)
    assert x.residual <= fact.settings.tolerance
    assert x.fallback is None


def test_supply_of_processes_not_upstream_is_exactly_zero(toy, monkeypatch) -> None:
    fact = factorize(toy.A)
    leave_noise(monkeypatch, [toy.tshirt, toy.incineration])

    s = supply(fact, np.eye(toy.n)[toy.fabric])

    assert s.values.tolist() == [0.0, 1.0, 3.0, 0.0]


def test_an_exact_zero_after_a_low_rank_update(toy, monkeypatch) -> None:
    # The T-shirt buys 0.30 kg of fabric and sends 0.05 kg of scraps to an incineration that
    # emits nothing.
    h = h_without_incineration(toy)
    fact = factorize(toy.A)
    x = scores(fact, h).values
    leave_noise(monkeypatch, [toy.incineration])

    updated = update_column(fact, x, h, [toy.tshirt], np.array([[1.0], [-0.30], [-0.5], [0.05]]))

    assert updated.values[toy.incineration] == 0.0
    assert updated.exact_zeros.tolist() == [False, False, False, True]
    assert updated.values[toy.tshirt] == pytest.approx(1.37748, rel=1e-12)


# The pivoted factorization is kept


def test_the_pivoted_factorization_is_returned_when_the_fallback_is_used(
    toy, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The unpivoted factors are made 0.1% wrong after factorize accepted them, so one solve
    # fails its check after refinement and falls back to pivoting.
    fact = factorize(toy.A)
    real = Factorization.solve

    def rough(self: Factorization, b: np.ndarray, *, transposed: bool = False) -> np.ndarray:
        y = real(self, b, transposed=transposed)
        return y if self.settings.diag_pivot_thresh == 1.0 else y * (1 + 1e-3)

    monkeypatch.setattr(Factorization, "solve", rough)

    x = scores(fact, toy.h)
    again = scores(x.fallback, toy.h) if x.fallback is not None else None

    assert x.path[:2] == (Step.lu, Step.refined)
    assert x.fallback is not None
    assert x.fallback.settings.diag_pivot_thresh == 1.0
    assert x.values[toy.tshirt] == pytest.approx(1.19205, rel=1e-12)
    assert again is not None and again.fallback is None


def test_no_fallback_when_the_first_solve_passes(toy) -> None:
    assert scores(factorize(toy.A), toy.h).fallback is None


# update_column returns the edited matrix


def test_update_column_can_return_the_edited_matrix(toy) -> None:
    fact = factorize(toy.A)
    x = scores(fact, toy.h).values
    new = np.array([[1.0], [-0.30], [-0.5], [0.0]])

    updated, matrix = update_column(fact, x, toy.h, [toy.tshirt], new, return_matrix=True)

    expected = toy.A.toarray()
    expected[:, [toy.tshirt]] = new
    np.testing.assert_array_equal(matrix.toarray(), expected)
    assert updated.values[toy.tshirt] == pytest.approx(1.37748, rel=1e-9)


# Copies


def test_as_matrix_without_copy_shares_a_canonical_matrix(toy) -> None:
    A = sparse.csc_array(toy.A)

    assert np.shares_memory(as_matrix(A, copy=False).data, A.data)
    assert not np.shares_memory(as_matrix(A).data, A.data)


def test_as_matrix_without_copy_never_changes_its_input() -> None:
    # An explicit zero and two entries in one cell: the canonical form needs a copy.
    A = sparse.csc_array((np.array([1.0, 0.0, 2.0, 3.0]), ([0, 1, 1, 1], [0, 0, 1, 1])))
    before = (A.data.copy(), A.indices.copy())

    matrix = as_matrix(A, copy=False)

    np.testing.assert_array_equal(matrix.toarray(), [[1.0, 0.0], [0.0, 5.0]])
    assert matrix.nnz == 2
    np.testing.assert_array_equal(A.data, before[0])
    np.testing.assert_array_equal(A.indices, before[1])


def test_stored_magnitude_gives_the_same_backward_error(toy_waste) -> None:
    fact = factorize(toy_waste.A)
    x = scores(fact, toy_waste.h).values * (1 + 1e-10)
    matrix = fact.matrix.T

    with_magnitude = backward_error(matrix, x, toy_waste.h, fact.magnitude.T)

    np.testing.assert_array_equal(with_magnitude, backward_error(matrix, x, toy_waste.h))


def test_factorize_keeps_the_magnitude_of_the_matrix(toy_waste) -> None:
    fact = factorize(toy_waste.A)

    np.testing.assert_array_equal(fact.magnitude.toarray(), np.abs(toy_waste.A.toarray()))
