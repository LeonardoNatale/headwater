"""The study block of a system: a study's processes scored on a background that is already solved.

The system, with the study's processes first and the background's below them:

    [ A_ss   0    ]      A_ss: what the study's processes buy from each other
    [ A_bs   A_bb ]      A_bs: what they buy from the background, A_bb: the background

The background buys nothing from the study, so its scores and its factorization do not change
when a study is edited. Only the small block A_ss is factorized.
"""

from dataclasses import dataclass

import numpy as np
from numpy.typing import ArrayLike

from headwater.calc.arrays import MatrixLike, as_matrix, as_vector
from headwater.calc.errors import CalculationError
from headwater.calc.solve import Factorization, Solution, factorize, scores, supply
from headwater.calc.update import LowRankUpdate


@dataclass(frozen=True, eq=False)
class StudySolution:
    """The three solves that score a study on a background."""

    scores: Solution
    """Score per unit of each study process's product."""

    supply: Solution
    """How much each study process runs for the demand."""

    background_supply: Solution
    """How much each background process runs to serve what the study buys."""

    factorization: Factorization
    """The factors of the study block, made for this call: its condition belongs in provenance.
    The background's factors are the caller's own, and its `Solution.fallback`s are kept by the
    caller."""


def solve_study(
    background: Factorization | LowRankUpdate,
    x_b: ArrayLike,
    A_ss: MatrixLike,
    A_bs: MatrixLike,
    h_s: ArrayLike,
    f_s: ArrayLike,
) -> StudySolution:
    """Score a study's processes on stored background scores, and find what they ask of it.

    Example, the T-shirt: it is the only study process, so A_ss = [1], and it buys 0.25 kg of
    fabric and 0.5 kWh, so A_bs holds -0.25 and -0.5 in those rows. With the stored scores
    3.7086 and 0.5298 of the background, its score is 0 + 0.25 × 3.7086 + 0.5 × 0.5298 = 1.19205,
    and it asks the background for 0.25 kg of fabric and 1.25 kWh (0.5 and the fabric's 0.75).

    `x_b` are the scores of the background's products and `background` its factorization, solved
    before, or that factorization with some columns replaced, for a background that a scenario
    changes. `h_s` are the direct characterised emissions of each study process and `f_s` what
    is asked of the study.

    - Scores: A_ssᵀ x_s = h_s - A_bsᵀ x_b, one solve of the small study block.
    - Supply: A_ss s_s = f_s, the study's processes in any order, loops among them included.
    - Background supply: A_bb s_b = -A_bs s_s, one forward solve with the stored factorization,
      or two with an update.

    The study block is factorized here with the tolerance and condition limit of the
    background's factorization.
    Raises a calc error naming processes by index, counted as in the stacked system: the study's
    processes first, then the background's.
    """
    study = as_matrix(A_ss, "A_ss")
    n = study.shape[0]
    if study.shape != (n, n):
        raise ValueError(f"A_ss must be square, got shape {study.shape}")
    A_bs = as_matrix(A_bs, "A_bs")
    if A_bs.shape != (background.n, n):
        raise ValueError(
            f"A_bs must have shape ({background.n}, {n}), one row per background process and "
            f"one column per study process, got shape {A_bs.shape}"
        )
    x_b = as_vector(x_b, background.n, "x_b")
    h_s = as_vector(h_s, n, "h_s")
    f_s = as_vector(f_s, n, "f_s")

    factorization = factorize(
        study,
        tolerance=background.settings.tolerance,
        condition_limit=background.settings.condition_limit,
    )
    x_s = scores(factorization, h_s - np.asarray(A_bs.T @ x_b, dtype=np.float64))
    s_s = supply(factorization, f_s)
    demand = -np.asarray(A_bs @ s_s.values, dtype=np.float64)
    try:
        if isinstance(background, LowRankUpdate):
            s_b = background.supply(demand)
        else:
            s_b = supply(background, demand)
    except CalculationError as error:
        raise error.shifted(n) from error
    return StudySolution(x_s, s_s, s_b, factorization)
