"""Scores of an indicator that is a weighted sum of others, as a normalisation and weighting set
gives them, from the vectors already solved and with no new solve.

Example, the toy single score: 0.06 × climate change + 40 × ozone formation. Electricity scores
0.5298 and 0.001, so 0.06 × 0.5298 + 40 × 0.001 = 0.071788; fabric scores 3.7086 and 0.007, so
0.502516. The sum is checked row by row as a solve is, since its terms can cancel.
"""

from collections.abc import Sequence

import numpy as np

from headwater.calc.arrays import Vector
from headwater.calc.solve import Factorization, Solution, backward_error, passes, solve_checked


def weighted_sum(terms: Sequence[tuple[float, Vector]]) -> Vector:
    """The sum of each vector times its number, in the order given: `[(0.06, c1), (40, c2)]`
    gives 0.06 c1 + 40 c2. Works on factors, direct emissions and scores alike. Raises ValueError
    for no terms or vectors of different lengths."""
    if not terms:
        raise ValueError("a weighted sum needs at least one term")
    total = np.zeros(len(terms[0][1]), dtype=np.float64)
    for k, vector in terms:
        if len(vector) != len(total):
            raise ValueError(f"vectors of lengths {len(total)} and {len(vector)} cannot be added")
        total += k * vector
    return total


def weighted_scores(
    factorization: Factorization, terms: Sequence[tuple[float, Solution]], h: Vector
) -> Solution:
    """The scores of an indicator that is a weighted sum of solved ones: `Σ k x`, with no solve.

    `h` is the same sum of their direct emissions, `Σ k h`. A row is an exact zero when it is one
    in every term whose number is not 0. Every row is then checked against Aᵀ x = h with its
    backward error, as a solve is: terms of opposite sign can cancel, and leave a row less
    accurate than any of them. A row above the tolerance is solved again from the sum, with
    refinement and the fallback to pivoting of any solve (`solve_checked`), which raises
    ResidualError if it still fails. The path holds the steps of every term's solve.
    """
    values = weighted_sum([(k, s.values) for k, s in terms])
    zeros = np.ones(len(values), dtype=bool)
    for k, solution in terms:
        if k != 0:
            zeros &= solution.exact_zeros
    values[zeros] = 0.0
    path = tuple(step for _, s in terms for step in s.path)
    error = backward_error(factorization.matrix.T, values, h, factorization.magnitude.T)
    if passes(error, factorization.settings.tolerance):
        return Solution(values, float(np.max(error, initial=0.0)), path, zeros)
    solved = solve_checked(factorization, h, transposed=True, start=values)
    return Solution(
        solved.values, solved.residual, path + solved.path, solved.exact_zeros, solved.fallback
    )
