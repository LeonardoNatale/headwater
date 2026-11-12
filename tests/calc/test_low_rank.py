"""A stored factorization with some columns of A replaced: scores and supply of the edited matrix,
without refactorizing. This is how a scenario changes the background for everything."""

import numpy as np
import pytest
from scipy import sparse

from headwater.calc import (
    SingularMatrixError,
    Step,
    factorize,
    low_rank_update,
    scores,
    supply,
)

REL = 1e-12


def edited(A: sparse.csc_array, columns: list[int], new: np.ndarray) -> np.ndarray:
    dense = A.toarray()
    dense[:, columns] = new
    return dense


def test_supply_after_a_changed_column_equals_refactorization(toy) -> None:
    # Fabric production uses 2.5 kWh per kg instead of 3. One T-shirt then runs electricity
    # for 0.5 + 0.25 x 2.5 = 1.125 kWh.
    fact = factorize(toy.A)
    new = np.array([[0.0], [1.0], [-2.5], [0.0]])
    f = np.eye(1, toy.n, toy.tshirt).ravel()

    update = low_rank_update(fact, [toy.fabric], new)
    found = update.supply(f)

    expected = supply(factorize(edited(toy.A, [toy.fabric], new)), f).values
    np.testing.assert_allclose(found.values, expected, rtol=REL, atol=0)
    assert found.values[toy.electricity] == pytest.approx(1.125, rel=REL)
    assert found.path == (Step.lu, Step.low_rank)
    assert found.residual <= fact.settings.tolerance


def test_scores_after_two_changed_columns_equal_refactorization(toy_waste) -> None:
    # The T-shirt buys 0.30 kg of fabric, and electricity buys 0.01 kg of fabric per kWh: a loop.
    t = toy_waste
    fact = factorize(t.A)
    new = np.array([[1.0, 0.0], [-0.30, -0.01], [-0.5, 1.0], [0.05, 0.0]])
    columns = [t.tshirt, t.electricity]

    update = low_rank_update(fact, columns, new)

    expected = scores(factorize(edited(t.A, columns, new)), t.h).values
    from_scratch = update.scores(t.h)
    from_stored = update.scores(t.h, start=scores(fact, t.h).values)
    np.testing.assert_allclose(from_scratch.values, expected, rtol=REL, atol=0)
    np.testing.assert_allclose(from_stored.values, expected, rtol=REL, atol=0)
    assert from_stored.path == (Step.lu, Step.low_rank)


def test_supply_with_a_loop_equals_refactorization(toy_waste) -> None:
    t = toy_waste
    fact = factorize(t.A)
    new = np.array([[1.0, 0.0], [-0.30, -0.01], [-0.5, 1.0], [0.05, 0.0]])
    columns = [t.tshirt, t.electricity]
    f = np.eye(1, t.n, t.tshirt).ravel()

    found = low_rank_update(fact, columns, new).supply(f)

    expected = supply(factorize(edited(t.A, columns, new)), f).values
    np.testing.assert_allclose(found.values, expected, rtol=REL, atol=0)


def test_the_update_holds_the_edited_matrix(toy) -> None:
    fact = factorize(toy.A)
    new = np.array([[0.0], [1.0], [-2.5], [0.0]])

    update = low_rank_update(fact, [toy.fabric], new)

    np.testing.assert_array_equal(update.matrix.toarray(), edited(toy.A, [toy.fabric], new))
    assert update.n == toy.n
    assert update.settings == fact.settings


def test_the_condition_is_that_of_the_edited_matrix(toy_waste) -> None:
    t = toy_waste
    fact = factorize(t.A)
    new = np.array([[1.0, 0.0], [-0.30, -0.01], [-0.5, 1.0], [0.05, 0.0]])
    columns = [t.tshirt, t.electricity]

    update = low_rank_update(fact, columns, new)

    fresh = factorize(edited(t.A, columns, new))
    assert update.condition == pytest.approx(fresh.condition, rel=1e-9)
    assert update.condition != pytest.approx(fact.condition, rel=1e-3)


def test_an_update_that_makes_the_matrix_nearly_singular_is_refused(toy) -> None:
    # Electricity buys (1 - 1e-12) / 3 kg of fabric per kWh: a loop that almost makes nothing net.
    fact = factorize(toy.A)
    new = np.array([[0.0], [-(1 - 1e-12) / 3], [1.0], [0.0]])

    with pytest.raises(SingularMatrixError, match="nearly singular") as error:
        low_rank_update(fact, [toy.electricity], new)

    assert set(error.value.indices) >= {toy.fabric, toy.electricity}


def test_an_update_that_makes_the_matrix_singular_names_the_changed_column(toy) -> None:
    fact = factorize(toy.A)
    new = np.array([[0.0], [-1.0 / 3.0], [1.0], [0.0]])

    with pytest.raises(SingularMatrixError) as error:
        low_rank_update(fact, [toy.electricity], new)

    assert error.value.indices == (toy.electricity,)


def test_a_bad_update_is_refused(toy) -> None:
    fact = factorize(toy.A)

    with pytest.raises(ValueError, match="once"):
        low_rank_update(fact, [1, 1], np.zeros((4, 2)))
    with pytest.raises(ValueError, match="shape"):
        low_rank_update(fact, [1], np.zeros((3, 1)))
    with pytest.raises(SingularMatrixError) as error:
        low_rank_update(fact, [toy.fabric], np.zeros((4, 1)))
    assert error.value.indices == (toy.fabric,)
