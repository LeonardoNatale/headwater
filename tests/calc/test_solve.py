import math

import numpy as np
import pytest
from scipy import sparse

import headwater.calc.solve as solve_module
from headwater.calc import (
    CalculationError,
    ResidualError,
    SingularMatrixError,
    Step,
    factorize,
    scores,
    supply,
)

# E1 at matrix level


def test_tshirt_scores_1_19205(toy) -> None:
    x = scores(factorize(toy.A), toy.h)

    assert x.values[toy.tshirt] == pytest.approx(1.19205, rel=1e-9)
    assert x.values[toy.fabric] == pytest.approx(3.7086, rel=1e-9)
    assert x.values[toy.electricity] == pytest.approx(0.5298, rel=1e-9)


def test_tshirt_with_waste_line_scores_1_25205(toy_waste) -> None:
    x = scores(factorize(toy_waste.A), toy_waste.h)

    assert x.values[toy_waste.tshirt] == pytest.approx(1.25205, rel=1e-9)
    assert x.values[toy_waste.incineration] == pytest.approx(-1.2, rel=1e-9)


def test_supply_of_one_tshirt(toy, toy_waste) -> None:
    s = supply(factorize(toy.A), np.eye(toy.n)[toy.tshirt])
    s_waste = supply(factorize(toy_waste.A), np.eye(toy.n)[toy.tshirt])

    np.testing.assert_allclose(s.values, [1.0, 0.25, 1.25, 0.0], rtol=1e-12, atol=1e-15)
    np.testing.assert_allclose(s_waste.values, [1.0, 0.25, 1.25, 0.05], rtol=1e-12, atol=1e-15)


# E2


@pytest.mark.parametrize("fixture", ["toy", "toy_waste"])
def test_h_dot_s_equals_x_tshirt(fixture: str, request: pytest.FixtureRequest) -> None:
    t = request.getfixturevalue(fixture)
    fact = factorize(t.A)

    s = supply(fact, np.eye(t.n)[t.tshirt]).values
    x = scores(fact, t.h).values

    assert float(t.h @ s) == pytest.approx(x[t.tshirt], rel=1e-12)


# Provenance


def test_result_records_its_path_and_residual(toy) -> None:
    fact = factorize(toy.A)
    x = scores(fact, toy.h)

    assert x.path == (Step.lu,)
    assert 0.0 <= x.residual <= fact.settings.tolerance
    assert fact.settings.ordering == "MMD_AT_PLUS_A"
    assert fact.settings.diag_pivot_thresh == 0.0
    assert not fact.pivoted


def test_factorize_with_pivoting_on_request(toy) -> None:
    fact = factorize(toy.A, pivoting=True)

    assert fact.settings.diag_pivot_thresh == 1.0
    assert scores(fact, toy.h).values[toy.tshirt] == pytest.approx(1.19205, rel=1e-9)


# Singular matrices


def test_process_with_no_reference_output_raises_naming_its_index(toy) -> None:
    A = toy.A.toarray()
    A[toy.electricity, toy.electricity] = 0.0

    with pytest.raises(SingularMatrixError, match=r"\b2\b") as error:
        factorize(A)

    assert error.value.indices == (2,)


def test_every_process_with_no_reference_output_is_named(toy) -> None:
    A = toy.A.toarray()
    A[toy.fabric, toy.fabric] = 0.0
    A[toy.incineration, toy.incineration] = 0.0

    with pytest.raises(SingularMatrixError) as error:
        factorize(A)

    assert error.value.indices == (1, 3)


def test_own_use_cancelling_the_reference_is_no_reference_output(toy) -> None:
    # Duplicate entries are summed: 1 kWh made and 1 kWh used on the diagonal is zero.
    A = toy.A.tocoo()
    rows = np.append(A.row, toy.electricity)
    cols = np.append(A.col, toy.electricity)
    data = np.append(A.data, -1.0)
    summed = sparse.csc_array((data, (rows, cols)), shape=A.shape)

    with pytest.raises(SingularMatrixError) as error:
        factorize(summed)

    assert error.value.indices == (2,)


def test_loop_making_exactly_what_it_uses_raises_naming_the_loop(toy) -> None:
    # Fabric uses 3 kWh per kg and electricity now uses 1/3 kg fabric per kWh: the loop makes
    # nothing net. The T-shirt buys from the loop and is not part of it.
    A = toy.A.toarray()
    A[toy.fabric, toy.electricity] = -1.0 / 3.0

    with pytest.raises(SingularMatrixError) as error:
        factorize(A)

    assert error.value.indices == (1, 2)


def test_exactly_singular_without_pivoting_retries_with_pivoting(
    toy, monkeypatch: pytest.MonkeyPatch
) -> None:
    # SuperLU already swaps rows when a diagonal pivot is exactly zero, so this is rare on real
    # matrices. The unpivoted call is made to fail to check the retry.
    real_splu = solve_module.splu

    def splu_failing_without_pivoting(A, permc_spec, diag_pivot_thresh):
        if diag_pivot_thresh == 0.0:
            raise RuntimeError("Factor is exactly singular")
        return real_splu(A, permc_spec=permc_spec, diag_pivot_thresh=diag_pivot_thresh)

    monkeypatch.setattr(solve_module, "splu", splu_failing_without_pivoting)

    fact = factorize(toy.A)

    assert fact.settings.diag_pivot_thresh == 1.0
    assert scores(fact, toy.h).values[toy.tshirt] == pytest.approx(1.19205, rel=1e-9)


def test_loop_making_what_it_uses_up_to_rounding_raises(toy) -> None:
    # 0.1 / 0.3 is 1/3 plus one rounding step: the loop gain is 1 + 2e-16, and the scores would
    # come out near -1e16 with a residual near 1e-16. The condition estimate is about 2e16.
    A = toy.A.toarray()
    A[toy.fabric, toy.electricity] = -(0.1 / 0.3)

    with pytest.raises(SingularMatrixError, match="nearly singular") as error:
        factorize(A)

    assert error.value.indices == (toy.fabric, toy.electricity)


def test_two_process_loop_singular() -> None:
    with pytest.raises(SingularMatrixError) as error:
        factorize(np.array([[1.0, -1.0], [-1.0, 1.0]]))

    assert error.value.indices == (0, 1)


# Bad input


def test_non_square_matrix_is_an_error() -> None:
    with pytest.raises(ValueError, match="square"):
        factorize(np.ones((2, 3)))


def test_non_finite_matrix_entry_is_an_error_naming_its_process(toy) -> None:
    A = toy.A.toarray()
    A[toy.electricity, toy.fabric] = math.nan

    with pytest.raises(CalculationError) as error:
        factorize(A)

    assert error.value.indices == (1,)


def test_non_finite_demand_is_an_error_naming_its_index(toy) -> None:
    h = toy.h
    h[toy.fabric] = math.inf

    with pytest.raises(CalculationError) as error:
        scores(factorize(toy.A), h)

    assert error.value.indices == (1,)


def test_wrong_vector_length_is_an_error(toy) -> None:
    with pytest.raises(ValueError, match="length"):
        supply(factorize(toy.A), np.ones(3))


def test_factorize_does_not_change_the_input_matrix(toy) -> None:
    A = toy.A.copy()
    before = A.toarray()

    factorize(A)

    np.testing.assert_array_equal(A.toarray(), before)


# Refinement and fallback to pivoting


# Process 2 has a reference output of 1e-8. Without pivoting the first solve is off by about
# 1e-8 relative, and one step of iterative refinement brings it within the tolerance.
NEEDS_REFINEMENT = np.array(
    [
        [1.0, -2.0, -2.0],
        [-2.0, 1.0, -2.0],
        [-2.0, -2.0, 1e-8],
    ]
)


@pytest.mark.parametrize("transposed", [False, True])
def test_one_step_of_refinement_fixes_a_rough_solve(transposed: bool) -> None:
    fact = factorize(NEEDS_REFINEMENT)
    b = np.ones(3)

    result = scores(fact, b) if transposed else supply(fact, b)

    matrix = NEEDS_REFINEMENT.T if transposed else NEEDS_REFINEMENT
    assert not fact.pivoted
    assert result.path == (Step.lu, Step.refined)
    assert result.residual <= fact.settings.tolerance
    np.testing.assert_allclose(result.values, np.linalg.solve(matrix, b), rtol=1e-9)


# Process 2 has a reference output of 1e-16. Without pivoting the factors are useless even after
# one step of iterative refinement; with pivoting the solve is accurate.
NEEDS_PIVOTING = np.array(
    [
        [1.0, -2.0, -2.0],
        [-2.0, 1.0, -2.0],
        [-2.0, -2.0, 1e-16],
    ]
)


@pytest.mark.parametrize("transposed", [False, True])
def test_factorize_switches_to_pivoting_when_the_factors_fail_their_check(
    transposed: bool,
) -> None:
    fact = factorize(NEEDS_PIVOTING)
    b = np.ones(3)

    result = scores(fact, b) if transposed else supply(fact, b)

    matrix = NEEDS_PIVOTING.T if transposed else NEEDS_PIVOTING
    assert fact.settings.diag_pivot_thresh == 1.0
    assert fact.pivoted
    assert result.path[0] == Step.pivoted_lu
    assert result.fallback is None
    assert result.residual <= fact.settings.tolerance
    np.testing.assert_allclose(result.values, np.linalg.solve(matrix, b), rtol=1e-9)


def failing_on_row_1(matrix, y, b, magnitude=None):
    """Pivoted LU with refinement passes on any matrix small enough for a test, so the check
    is forced to fail on row 1 to reach the last branch."""
    error = np.zeros(len(b))
    error[1] = 1.0
    return error


def test_residual_still_too_large_after_pivoting_raises_naming_rows(
    toy, monkeypatch: pytest.MonkeyPatch
) -> None:
    fact = factorize(toy.A)
    monkeypatch.setattr(solve_module, "backward_error", failing_on_row_1)

    with pytest.raises(ResidualError, match="pivoting") as error:
        supply(fact, np.eye(toy.n)[toy.tshirt])

    assert error.value.indices == (1,)
    assert error.value.residual == pytest.approx(1.0)


def test_pivoted_solve_still_failing_raises_naming_rows(
    toy, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Row 1 fails for every right-hand side but the check solve of factorize (all ones), so the
    # pivoted factors are made and accepted, and the solve with them still fails.
    real = solve_module.backward_error

    def failing_but_for_the_check_solve(matrix, y, b, magnitude=None):
        if np.all(b == 1.0):
            return real(matrix, y, b, magnitude)
        return failing_on_row_1(matrix, y, b)

    fact = factorize(toy.A)
    monkeypatch.setattr(solve_module, "backward_error", failing_but_for_the_check_solve)

    with pytest.raises(ResidualError, match="after refinement and pivoting") as error:
        supply(fact, np.eye(toy.n)[toy.tshirt])

    assert error.value.indices == (1,)


def test_factors_failing_their_check_even_with_pivoting_raise_naming_rows(
    toy, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(solve_module, "backward_error", failing_on_row_1)

    with pytest.raises(ResidualError, match="pivoting") as error:
        factorize(toy.A)

    assert error.value.indices == (1,)


def test_backward_error_is_relative_to_each_row() -> None:
    # Row 1 is a million times smaller than row 0 and has the same relative error.
    matrix = sparse.csc_array(np.diag([1e6, 1.0]))
    b = np.array([1e6, 1.0])
    y = np.array([1.0 + 1e-10, 1.0 + 1e-10])

    error = solve_module.backward_error(matrix, y, b)

    np.testing.assert_allclose(error, [1e-10 / 2, 1e-10 / 2], rtol=1e-5)


def test_not_a_number_never_passes_the_residual_check() -> None:
    matrix = sparse.csc_array(np.eye(2))
    y = np.array([1.0, math.nan])

    error = solve_module.backward_error(matrix, y, np.ones(2))

    assert math.isinf(error[1])
    assert not solve_module.passes(error, 1e-12)
    assert not solve_module.passes(np.array([math.nan]), 1e-12)


# Scaling round trip


def test_badly_scaled_matrix_matches_dense_solve_both_ways() -> None:
    rng = np.random.default_rng(7)
    n = 40
    # A productive economy: each process uses less than it makes of every product.
    P = sparse.random_array((n, n), density=0.15, rng=rng).toarray()
    np.fill_diagonal(P, 0.0)
    P *= 0.9 / np.maximum(P.sum(axis=0), 1e-300)
    rows = 10.0 ** rng.uniform(-6, 6, n)
    cols = 10.0 ** rng.uniform(-6, 6, n)
    M = rows[:, None] * (np.eye(n) - P) * cols[None, :]
    b = rng.normal(size=n)

    core = np.eye(n) - P

    fact = factorize(M)

    # References from the well-scaled core, so they do not depend on how LAPACK handles scale.
    forward = np.linalg.solve(core, b / rows) / cols
    transposed = np.linalg.solve(core.T, b / cols) / rows
    np.testing.assert_allclose(supply(fact, b).values, forward, rtol=1e-9)
    np.testing.assert_allclose(scores(fact, b).values, transposed, rtol=1e-9)
