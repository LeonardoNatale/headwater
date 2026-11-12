"""The two additions to calc: the table of role entries, and the sparse multiply for SKUs."""

import numpy as np
import pytest

from headwater.calc import CalculationError, Matrix, matrix_entry, role_entries, sku_scores
from headwater.model import Role


def test_the_table_holds_what_matrix_entry_says_for_every_role() -> None:
    table = {(e.role, e.treatment): e for e in role_entries()}

    for role in Role:
        for treatment in (False, True):
            if (role, treatment) not in table:
                continue
            entry = matrix_entry(role, 1.0, treatment=treatment)
            found = table[(role, treatment)]
            if entry is None:
                assert found.matrix is None
            else:
                assert (found.matrix, found.sign) == (entry.matrix, entry.value)


def test_the_signs_of_the_toy_roles() -> None:
    table = {(e.role, e.treatment): (e.matrix, e.sign) for e in role_entries()}

    assert table[(Role.reference, False)] == (Matrix.technosphere, 1.0)
    assert table[(Role.reference, True)] == (Matrix.technosphere, -1.0)  # a treatment
    assert table[(Role.input, False)] == (Matrix.technosphere, -1.0)
    assert table[(Role.waste_to_treatment, False)] == (Matrix.technosphere, 1.0)
    assert table[(Role.emission, False)] == (Matrix.biosphere, 1.0)
    assert table[(Role.recyclable, False)] == (None, 0.0)  # no entry, no burden


def test_a_pair_with_no_defined_sign_is_left_out() -> None:
    pairs = {(e.role, e.treatment) for e in role_entries()}

    assert (Role.own_use, True) not in pairs  # calc has no sign for own use in a treatment
    assert (Role.own_use, False) in pairs
    assert len(pairs) == 2 * len(Role) - 1


def test_sku_scores_are_the_amounts_times_the_vectors() -> None:
    vectors = np.array([[1.0, 10.0], [2.0, 20.0], [3.0, 30.0]])  # 3 products, 2 indicators

    out = sku_scores([0, 0, 1], [0, 2, 1], [2.0, 1.0, 0.5], vectors, n_skus=2)

    # SKU 0: 2 of product 0 and 1 of product 2. SKU 1: 0.5 of product 1.
    assert out.shape == (2, 2)
    assert out.tolist() == [[2 * 1 + 3, 2 * 10 + 30], [0.5 * 2, 0.5 * 20]]


def test_a_repeated_pair_adds_up_and_a_sku_with_no_row_scores_zero() -> None:
    vectors = np.array([[1.0], [2.0]])

    out = sku_scores([0, 0, 0], [1, 1, 0], [1.0, 2.0, 4.0], vectors, n_skus=3)

    assert out[:, 0].tolist() == [3 * 2.0 + 4.0, 0.0, 0.0]


def test_sku_scores_refuse_what_they_cannot_score() -> None:
    vectors = np.ones((2, 1))

    with pytest.raises(ValueError, match="product index"):
        sku_scores([0], [2], [1.0], vectors, n_skus=1)
    with pytest.raises(ValueError, match="sku index"):
        sku_scores([1], [0], [1.0], vectors, n_skus=1)
    with pytest.raises(ValueError, match="same length"):
        sku_scores([0, 0], [0], [1.0], vectors, n_skus=1)
    with pytest.raises(CalculationError, match="not finite"):
        sku_scores([0], [0], [float("nan")], vectors, n_skus=1)
