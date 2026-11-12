"""A weighted sum of solved scores, on the toy matrices: the toy single score is 0.06 × climate
change + 40 × ozone formation (docs/calculation.md, "Single scores")."""

import numpy as np
import pytest

from headwater.calc import Solution, factorize, scores, weighted_scores, weighted_sum
from headwater.calc.solve import TOLERANCE

from .conftest import Toy

CLIMATE = np.array([1.0, 29.8])
OZONE = np.array([0.0, 1.0])


def test_the_toy_single_score_is_the_weighted_sum(toy_waste: Toy) -> None:
    f = factorize(toy_waste.A)
    h = [toy_waste.B.T @ CLIMATE, toy_waste.B.T @ OZONE]
    x = [scores(f, h[0]), scores(f, h[1])]

    single = weighted_scores(
        f, [(0.06, x[0]), (40.0, x[1])], weighted_sum([(0.06, h[0]), (40.0, h[1])])
    )

    # fabric 0.06 × 3.7086 + 40 × 0.007, electricity 0.06 × 0.5298 + 40 × 0.001
    assert single.values[toy_waste.fabric] == pytest.approx(0.502516, rel=1e-12)
    assert single.values[toy_waste.electricity] == pytest.approx(0.071788, rel=1e-12)
    # incineration emits no methane: an exact zero for ozone, not for the sum
    assert x[1].exact_zeros[toy_waste.incineration]
    assert not single.exact_zeros[toy_waste.incineration]
    assert single.values[toy_waste.incineration] == pytest.approx(-0.072, rel=1e-12)
    assert single.residual <= TOLERANCE
    assert single.path == x[0].path + x[1].path


def test_an_exact_zero_of_every_term_stays_exactly_zero(toy: Toy) -> None:
    f = factorize(toy.A)
    h = toy.B.T @ OZONE
    x = scores(f, h)

    normalised = weighted_scores(f, [(100.0, x)], 100.0 * h)

    assert normalised.exact_zeros[toy.incineration]
    assert normalised.values[toy.incineration] == 0.0


def test_a_sum_that_fails_its_check_is_solved_again(toy: Toy) -> None:
    """Scores off by 1e-6 fail the check: they are solved again from the sum, and come back
    right, with the steps of that solve."""
    f = factorize(toy.A)
    h = toy.B.T @ CLIMATE
    exact = scores(f, h)
    off = Solution(exact.values * (1 + 1e-6), exact.residual, exact.path, exact.exact_zeros)

    fixed = weighted_scores(f, [(1.0, off)], h)

    assert fixed.values == pytest.approx(exact.values, rel=1e-12)
    assert fixed.residual <= TOLERANCE
    assert len(fixed.path) > len(exact.path)


def test_a_weighted_sum_needs_terms_of_one_length() -> None:
    with pytest.raises(ValueError, match="at least one term"):
        weighted_sum([])
    with pytest.raises(ValueError, match="lengths 2 and 3"):
        weighted_sum([(1.0, np.ones(2)), (1.0, np.ones(3))])
