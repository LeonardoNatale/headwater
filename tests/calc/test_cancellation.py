"""Positive and negative parts of a score, and the flag on a score that is near 0 because they
cancel. Hand-checked on the carbon toy: 1 kg of fabric takes 1.5 kg of CO2 from the air (factor
-1) and its residue emits 0.4 kg of non-fossil CO2 (factor 1)."""

import numpy as np
import pytest
from scipy import sparse

from headwater.calc.cancellation import CANCELS_BELOW, Cancellation, cancellation

C = np.array([-1.0, 1.0])
"""Carbon dioxide, in air, then Carbon dioxide, non-fossil."""
B = sparse.csc_array(np.array([[1.5, 0.0], [0.0, 0.4]]))
"""Fabric production takes 1.5 kg from the air, residue treatment emits 0.4 kg."""


def test_the_parts_of_the_fabric() -> None:
    found = cancellation(B, C, np.array([1.0, 1.0]))

    assert (found.positive, found.negative) == (0.4, -1.5)
    assert found.net == pytest.approx(-1.1, rel=1e-12)
    assert found.gross == pytest.approx(1.9, rel=1e-12)
    assert not found.cancels


def test_a_net_of_zero_with_parts_cancels() -> None:
    """The residue emits all the carbon the fabric took: 3.75 kg of residue, 1.5 kg of CO2."""
    found = cancellation(B, C, np.array([1.0, 3.75]))

    assert (found.positive, found.negative) == pytest.approx((1.5, -1.5), rel=1e-12)
    assert found.cancels


def test_near_is_a_share_of_the_gross() -> None:
    below = Cancellation(positive=1.0, negative=-(1.0 - 1.9 * CANCELS_BELOW))
    above = Cancellation(positive=1.0, negative=-(1.0 - 2.1 * CANCELS_BELOW))

    assert CANCELS_BELOW == 1e-3
    assert below.cancels
    assert not above.cancels


def test_an_uptake_and_an_emission_in_one_process_are_two_parts() -> None:
    """A per-process sum would see 0 here and no parts at all."""
    one_process = sparse.csc_array(np.array([[1.5], [1.5]]))

    found = cancellation(one_process, C, np.array([1.0]))

    assert (found.positive, found.negative, found.net) == (1.5, -1.5, 0.0)
    assert found.cancels


def test_an_exact_zero_has_no_parts_and_is_not_flagged() -> None:
    found = cancellation(B, C, np.zeros(2))

    assert (found.positive, found.negative) == (0.0, 0.0)
    assert not found.cancels


def test_a_score_of_one_sign_never_cancels() -> None:
    assert not cancellation(B, np.array([0.0, 1.0]), np.array([1.0, 1.0])).cancels
    assert not cancellation(B, np.array([-1.0, 0.0]), np.array([1.0, 1.0])).cancels


def test_a_process_that_runs_backwards_turns_its_terms_around() -> None:
    found = cancellation(B, C, np.array([-1.0, 1.0]))

    assert (found.positive, found.negative) == (pytest.approx(1.9, rel=1e-12), 0.0)


def test_parts_of_two_blocks_add_up() -> None:
    study = cancellation(B, C, np.array([1.0, 0.0]))
    background = cancellation(B, C, np.array([0.0, 3.75]))

    total = study + background

    assert (total.positive, total.negative) == pytest.approx((1.5, -1.5), rel=1e-12)
    assert total.cancels
    assert not study.cancels


def test_shapes_must_agree() -> None:
    with pytest.raises(ValueError, match="flows"):
        cancellation(B, np.array([1.0]), np.array([1.0, 1.0]))
    with pytest.raises(ValueError, match="processes"):
        cancellation(B, C, np.array([1.0]))
