import numpy as np
import pytest
from scipy import sparse

from headwater.calc import (
    SingularMatrixError,
    Step,
    factorize,
    scores,
    update_column,
    update_emissions,
)


def refactorized(A: np.ndarray, h: np.ndarray) -> np.ndarray:
    return scores(factorize(A), h).values


def test_one_changed_column_equals_refactorization(toy) -> None:
    # The T-shirt buys 0.30 kg of fabric instead of 0.25 (the README example).
    fact = factorize(toy.A)
    x = scores(fact, toy.h).values
    new = np.array([[1.0], [-0.30], [-0.5], [0.0]])

    updated = update_column(fact, x, toy.h, [toy.tshirt], new)

    A = toy.A.toarray()
    A[:, [toy.tshirt]] = new
    np.testing.assert_allclose(updated.values, refactorized(A, toy.h), rtol=1e-12, atol=0)
    assert updated.values[toy.tshirt] == pytest.approx(1.37748, rel=1e-9)
    assert updated.path == (Step.lu, Step.low_rank)
    assert updated.residual <= fact.settings.tolerance


def test_one_changed_background_column(toy) -> None:
    # Fabric production uses 2.5 kWh per kg instead of 3.
    fact = factorize(toy.A)
    x = scores(fact, toy.h).values
    new = np.array([0.0, 1.0, -2.5, 0.0])

    updated = update_column(fact, x, toy.h, [toy.fabric], new[:, None])

    A = toy.A.toarray()
    A[:, toy.fabric] = new
    np.testing.assert_allclose(updated.values, refactorized(A, toy.h), rtol=1e-12, atol=0)
    assert updated.path == (Step.lu, Step.low_rank)
    # 0.25 x (2.1192 + 2.5 x 0.5298) + 0.5 x 0.5298
    assert updated.values[toy.tshirt] == pytest.approx(1.125825, rel=1e-9)


def test_two_changed_columns_making_a_loop_equal_refactorization(toy_waste) -> None:
    # The T-shirt buys 0.30 kg fabric, and electricity production now buys 0.01 kg fabric per
    # kWh, which closes a loop between fabric and electricity.
    t = toy_waste
    fact = factorize(t.A)
    x = scores(fact, t.h).values
    new = sparse.csc_array(
        np.array(
            [
                [1.0, 0.0],
                [-0.30, -0.01],
                [-0.5, 1.0],
                [0.05, 0.0],
            ]
        )
    )

    updated = update_column(fact, x, t.h, [t.tshirt, t.electricity], new)

    A = t.A.toarray()
    A[:, [t.tshirt, t.electricity]] = new.toarray()
    np.testing.assert_allclose(updated.values, refactorized(A, t.h), rtol=1e-12, atol=0)
    assert updated.path == (Step.lu, Step.low_rank)
    x_fabric = 3.7086 / 0.97
    x_electricity = 0.5298 + 0.01 * x_fabric
    assert updated.values[t.tshirt] == pytest.approx(
        0.30 * x_fabric + 0.5 * x_electricity + 0.06, rel=1e-9
    )


def test_refinement_uses_the_updated_matrix(toy) -> None:
    # Stored scores carrying a small error fail the residual check after the update. The one
    # refinement step must solve with the updated matrix to land on the refactorized result.
    fact = factorize(toy.A)
    x = scores(fact, toy.h).values * (1.0 + 1e-9)
    new = np.array([[1.0], [-0.30], [-0.5], [0.0]])

    updated = update_column(fact, x, toy.h, [toy.tshirt], new)

    A = toy.A.toarray()
    A[:, [toy.tshirt]] = new
    assert updated.path == (Step.lu, Step.low_rank, Step.refined)
    np.testing.assert_allclose(updated.values, refactorized(A, toy.h), rtol=1e-12, atol=0)


def test_update_that_makes_the_matrix_singular_names_the_changed_column(toy) -> None:
    # Electricity buys 1/3 kg fabric per kWh: with 3 kWh per kg fabric the loop makes nothing net.
    fact = factorize(toy.A)
    x = scores(fact, toy.h).values
    new = np.array([[0.0], [-1.0 / 3.0], [1.0], [0.0]])

    with pytest.raises(SingularMatrixError) as error:
        update_column(fact, x, toy.h, [toy.electricity], new)

    assert error.value.indices == (toy.electricity,)


def test_update_removing_a_reference_output_names_the_process(toy) -> None:
    fact = factorize(toy.A)
    x = scores(fact, toy.h).values

    with pytest.raises(SingularMatrixError) as error:
        update_column(fact, x, toy.h, [toy.fabric], np.zeros((4, 1)))

    assert error.value.indices == (toy.fabric,)


def test_duplicate_columns_are_an_error(toy) -> None:
    fact = factorize(toy.A)
    x = scores(fact, toy.h).values

    with pytest.raises(ValueError, match="once"):
        update_column(fact, x, toy.h, [1, 1], np.zeros((4, 2)))


def test_new_columns_of_the_wrong_shape_are_an_error(toy) -> None:
    fact = factorize(toy.A)
    x = scores(fact, toy.h).values

    with pytest.raises(ValueError, match="shape"):
        update_column(fact, x, toy.h, [1], np.zeros((3, 1)))


def test_update_emissions_equals_a_new_solve(toy) -> None:
    # Electricity emits 0.4 kg CO2 per kWh instead of 0.5.
    fact = factorize(toy.A)
    x = scores(fact, toy.h).values
    h_new = toy.h.copy()
    h_new[toy.electricity] = 0.4 + 0.001 * 29.8

    updated = update_emissions(fact, x, h_new)

    np.testing.assert_allclose(updated.values, refactorized(toy.A, h_new), rtol=1e-12, atol=0)
    assert updated.path == (Step.lu,)
    # 0.25 x (2.1192 + 3 x 0.4298) + 0.5 x 0.4298
    assert updated.values[toy.tshirt] == pytest.approx(1.06705, rel=1e-9)
