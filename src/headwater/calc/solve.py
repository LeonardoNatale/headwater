"""Factorize the technosphere matrix once, then solve forward (supply) and transposed (scores).

Every solve checks its residual row by row, refines once if needed, and falls back to a
factorization with pivoting before giving up with an error that names the rows involved.
"""

import math
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Protocol

import numpy as np
from numpy.typing import ArrayLike, NDArray
from scipy import sparse
from scipy.sparse import csgraph
from scipy.sparse.linalg import SuperLU, splu

from headwater.calc.arrays import MatrixLike, Vector, as_matrix, as_vector
from headwater.calc.errors import ResidualError, SingularMatrixError

ORDERING = "MMD_AT_PLUS_A"
"""SuperLU column ordering: minimum degree on Aᵀ + A."""

TOLERANCE = 1e-12
"""Default largest accepted residual of a row, relative to the size of that row's terms.

On ecoinvent 3.12 cut-off the largest is 4e-15 (bench/parity_numerics.py, listed in
docs/appendix/benchmarks.md)."""

CONDITION_LIMIT = 1e10
"""Default largest accepted condition estimate. Above it the matrix counts as nearly singular.

ecoinvent 3.12 cut-off is at 30 (bench/parity_numerics.py). In the toy, a loop whose gain is
1 - 1e-12 is at 4e12."""

type SparseMatrix = sparse.csc_array | sparse.csr_array
"""A or its transpose: CSC transposes to CSR."""

# Used only to name the processes of a singular matrix: the shift added to the scaled diagonal,
# and the share of the largest entry of a null vector above which a process counts as involved.
_SHIFT = 1e-8
_INVOLVED = 1e-6

# Below this many processes the condition is computed exactly, from one solve per process.
_EXACT_CONDITION = 100


class Step(StrEnum):
    """One step taken to produce a result, recorded for provenance."""

    lu = "lu"
    """Solved with LU factors, no rows swapped."""

    pivoted_lu = "pivoted_lu"
    """Solved with LU factors whose rows were swapped for stability."""

    refined = "refined"
    """One step of iterative refinement."""

    low_rank = "low_rank"
    """Updated from stored scores for changed columns, without refactorizing."""

    refactorized = "refactorized"
    """A scenario's matrix was factorized again: it changed more columns than an update is
    faster for, or its update failed a check."""


@dataclass(frozen=True, slots=True)
class SolverSettings:
    """How a factorization was made, recorded for provenance."""

    solver: str
    ordering: str
    diag_pivot_thresh: float
    scaling: str
    tolerance: float
    condition_limit: float


@dataclass(frozen=True, eq=False)
class Factorization:
    """LU factors of the scaled matrix `row_scale * A * col_scale`, plus A itself for checks."""

    matrix: sparse.csc_array
    magnitude: sparse.csc_array
    """|A|, entry by entry, for the residual checks."""
    row_scale: Vector
    col_scale: Vector
    lu: SuperLU[np.float64]
    settings: SolverSettings
    pivoted: bool
    """True if SuperLU swapped rows, whatever the pivoting threshold asked for."""
    condition: float
    """Estimate of the largest relative change of a score per relative change of the entries
    of A and of the emissions, for an emission of 1 per run of every process (see
    docs/calculation.md, Factorize). Example: 3.8 for the toy, 30 for ecoinvent 3.12 cut-off
    (bench/parity_numerics.py)."""

    @property
    def n(self) -> int:
        return self.matrix.shape[0]

    @property
    def step(self) -> Step:
        return Step.pivoted_lu if self.pivoted else Step.lu

    def solve(self, b: Vector, *, transposed: bool = False) -> Vector:
        """Solve A y = b, or Aᵀ y = b, with no residual check. `b` may hold several columns.

        Use `supply` and `scores` for checked results.
        """
        r = self.row_scale.reshape(-1, *[1] * (b.ndim - 1))
        c = self.col_scale.reshape(-1, *[1] * (b.ndim - 1))
        if transposed:
            return r * self.lu.solve(c * b, trans="T")
        return c * self.lu.solve(r * b)


class Factors(Protocol):
    """What solves with a matrix A: a `Factorization`, or one with some columns replaced."""

    @property
    def matrix(self) -> sparse.csc_array: ...
    @property
    def magnitude(self) -> sparse.csc_array: ...
    @property
    def settings(self) -> SolverSettings: ...
    @property
    def n(self) -> int: ...
    def solve(self, b: Vector, *, transposed: bool = False) -> Vector: ...


@dataclass(frozen=True, eq=False)
class Solution:
    """A solved vector and how it was obtained."""

    values: Vector
    residual: float
    """Largest residual of a row, relative to the size of that row's terms."""
    path: tuple[Step, ...]
    exact_zeros: NDArray[np.bool_]
    """Rows that are exactly 0 whatever the values of the matrix, set to 0 rather than solved:
    for scores, the products with no emission of the indicator anywhere upstream. See
    `structural_zeros`."""
    fallback: Factorization | None = None
    """The factorization with pivoting made for this solve, when the one given failed the
    residual check. Use it for later solves: factorizing again costs more than a solve."""


def factorize(
    A: MatrixLike,
    *,
    pivoting: bool = False,
    tolerance: float = TOLERANCE,
    condition_limit: float = CONDITION_LIMIT,
) -> Factorization:
    """Factorize the technosphere matrix A once, for any number of solves.

    Rows and columns are scaled so the largest entry of each is 1, then SuperLU factorizes with
    a fill-reducing ordering and, unless `pivoting` is set, no pivoting. The factors are checked
    on one solve, the scores for an emission of 1 per run of every process. If SuperLU finds the
    matrix exactly singular without pivoting, or the check fails after one refinement, it is
    factorized again with pivoting. Then the condition is estimated from the factors kept.

    Raises SingularMatrixError naming every process with no reference output (a zero on the
    diagonal), or the processes involved when the matrix is singular, or nearly singular: its
    condition estimate is above `condition_limit`. Raises ResidualError naming the rows when
    the check still fails with pivoting.
    """
    if not tolerance >= 0:
        raise ValueError(f"tolerance must be zero or positive, got {tolerance}")
    if not condition_limit >= 1:
        raise ValueError(f"condition_limit must be 1 or more, got {condition_limit}")
    matrix = as_matrix(A)
    if matrix.shape[0] != matrix.shape[1] or matrix.shape[0] == 0:
        raise ValueError(f"matrix must be square and not empty, got shape {matrix.shape}")
    check_reference_outputs(matrix)
    magnitude = abs(matrix)
    row_scale, col_scale, scaled = _scale(matrix)
    error = np.zeros(0)
    for pivot in (True,) if pivoting else (False, True):
        thresh = 1.0 if pivot else 0.0
        try:
            lu = splu(scaled, permc_spec=ORDERING, diag_pivot_thresh=thresh)
        except RuntimeError as failure:
            if "singular" not in str(failure):
                raise
            if not pivot:
                continue
            raise SingularMatrixError(
                "matrix is singular; processes involved", _singular_indices(scaled)
            ) from failure
        settings = SolverSettings(
            solver="scipy.sparse.linalg.splu",
            ordering=ORDERING,
            diag_pivot_thresh=thresh,
            scaling="rows, then columns, so the largest entry of each is 1",
            tolerance=tolerance,
            condition_limit=condition_limit,
        )
        pivoted = not np.array_equal(lu.perm_r, lu.perm_c)
        factorization = Factorization(
            matrix, magnitude, row_scale, col_scale, lu, settings, pivoted, math.nan
        )
        unit_scores, error = check_factors(factorization)
        if passes(error, tolerance):
            condition = condition_estimate(factorization, unit_scores)
            if not condition <= condition_limit:
                raise SingularMatrixError(
                    f"matrix is nearly singular: condition estimate {condition:.3g} is above "
                    f"{condition_limit:g}; processes involved",
                    _singular_indices(scaled),
                )
            return replace(factorization, condition=condition)
    raise ResidualError(
        f"residual above tolerance {tolerance:g} after refinement and pivoting, in rows",
        failing_rows(error, tolerance),
        float(np.max(error)),
    )


def supply(factorization: Factorization, f: ArrayLike) -> Solution:
    """How much each process runs to deliver the demand `f`: solves A s = f."""
    return solve_checked(factorization, f, transposed=False)


def scores(factorization: Factorization, h: ArrayLike) -> Solution:
    """Score per unit of every product, for one indicator: solves Aᵀ x = h.

    `h` holds each process's direct characterised emissions, Bᵀ c.
    """
    return solve_checked(factorization, h, transposed=True)


def solve_checked(
    factorization: Factorization,
    b: ArrayLike,
    *,
    transposed: bool,
    start: Vector | None = None,
) -> Solution:
    """Solve, check every row's residual, refine once, then fall back to pivoting.

    `start` is a solution for a nearby right-hand side: the solve then only computes the change.
    The rows whose exact value is zero whatever the values of the matrix (see
    `structural_zeros`) are set to 0, not solved: a relative check of rounding noise there fails
    whatever its size. They are listed in `Solution.exact_zeros`. When the fallback to pivoting
    is used, the pivoted factorization comes back in `Solution.fallback`. Raises ResidualError
    naming the rows still above the tolerance.
    """
    settings = factorization.settings
    tolerance = settings.tolerance
    b = as_vector(b, factorization.n, "right-hand side")
    matrix = factorization.matrix.T if transposed else factorization.matrix
    magnitude = factorization.magnitude.T if transposed else factorization.magnitude
    zero = structural_zeros(magnitude, b)

    def solver(f: Factorization) -> Callable[[Vector], Vector]:
        def apply(v: Vector) -> Vector:
            y = f.solve(v, transposed=transposed)
            y[zero] = 0.0
            return y

        return apply

    apply = solver(factorization)
    y = apply(b) if start is None else start + apply(b - matrix @ start)
    y[zero] = 0.0
    y, error, refined = refine_if_needed(apply, matrix, b, y, tolerance, magnitude)
    path = [factorization.step] + [Step.refined] * refined

    fallback = None
    if not passes(error, tolerance) and settings.diag_pivot_thresh < 1.0:
        fallback = factorize(
            factorization.matrix,
            pivoting=True,
            tolerance=tolerance,
            condition_limit=settings.condition_limit,
        )
        apply = solver(fallback)
        y, error, refined = refine_if_needed(apply, matrix, b, apply(b), tolerance, magnitude)
        path += [fallback.step] + [Step.refined] * refined

    if not passes(error, tolerance):
        raise ResidualError(
            f"residual above tolerance {tolerance:g} after refinement and pivoting, in rows",
            failing_rows(error, tolerance),
            float(np.max(error)),
        )
    return Solution(y, float(np.max(error)), tuple(path), zero, fallback)


def check_reference_outputs(matrix: sparse.csc_array) -> None:
    """Raise SingularMatrixError naming every process with a zero on the diagonal."""
    missing = np.flatnonzero(matrix.diagonal() == 0)
    if missing.size:
        raise SingularMatrixError("processes with no reference output", missing)


def backward_error(
    matrix: SparseMatrix, y: Vector, b: Vector, magnitude: SparseMatrix | None = None
) -> Vector:
    """Residual of each row of `matrix @ y = b`, relative to the size of that row's terms.

    For row i: |b[i] - (matrix @ y)[i]| / ((|matrix| @ |y|)[i] + |b[i]|). A row whose terms are
    all zero has error 0. A row that is not a finite number has error infinity. `magnitude` is
    |matrix|, when the caller already has it.
    """
    if magnitude is None:
        magnitude = abs(matrix)
    residual = np.abs(b - matrix @ y)
    size = magnitude @ np.abs(y) + np.abs(b)
    with np.errstate(divide="ignore", invalid="ignore"):
        error = residual / size
    error[(size == 0) & (residual == 0)] = 0.0
    error[np.isnan(error)] = np.inf
    return error


def passes(error: Vector, tolerance: float) -> bool:
    """True if every row is within the tolerance. NaN never passes."""
    return bool(np.all(error <= tolerance))


def failing_rows(error: Vector, tolerance: float) -> NDArray[np.intp]:
    return np.flatnonzero(~(error <= tolerance))


def refine_if_needed(
    apply: Callable[[Vector], Vector],
    matrix: SparseMatrix,
    b: Vector,
    y: Vector,
    tolerance: float,
    magnitude: SparseMatrix | None = None,
) -> tuple[Vector, Vector, bool]:
    """Check `y`; if a row fails, do one step of iterative refinement and check again.

    Returns the solution, the error of each row, and whether refinement ran. `magnitude` is
    |matrix|, when the caller already has it.
    """
    error = backward_error(matrix, y, b, magnitude)
    if passes(error, tolerance):
        return y, error, False
    y = y + apply(b - matrix @ y)
    return y, backward_error(matrix, y, b, magnitude), True


def structural_zeros(magnitude: SparseMatrix, b: Vector) -> NDArray[np.bool_]:
    """The rows of the solution of `M y = b` that are exactly 0, whatever the values of M.

    `magnitude` is |M|. Row k of y can only be non-zero if k is reached from a non-zero of b,
    going from column l to row k wherever M[k, l] is not zero. Example, the toy scores with no
    emission for incineration: incineration buys nothing, so its score is exactly 0. In
    ecoinvent 3.12 cut-off, 2,041 products have no emission of CO2, methane or nitrous oxide
    anywhere upstream, among them the 97 recycled-content datasets (bench/parity_numerics.py).
    """
    n = magnitude.shape[0]
    seeds = np.flatnonzero(b)
    if seeds.size == 0:
        return np.ones(n, dtype=bool)
    if seeds.size == n:
        return np.zeros(n, dtype=bool)
    # csgraph follows an edge from row to column, so the graph is |M| transposed.
    distance = csgraph.dijkstra(
        sparse.csr_array(magnitude.T), indices=seeds, unweighted=True, min_only=True
    )
    return np.isinf(distance)


def check_factors(factorization: Factors) -> tuple[Vector, Vector]:
    """The scores for an emission of 1 per run of every process, refined once, and the error
    of each row. With no credit, every one of them is a sum of positive burdens."""
    ones = np.ones(factorization.n)
    matrix = factorization.matrix.T

    def apply(v: Vector) -> Vector:
        return factorization.solve(v, transposed=True)

    y, error, _ = refine_if_needed(
        apply,
        matrix,
        ones,
        apply(ones),
        factorization.settings.tolerance,
        factorization.magnitude.T,
    )
    return y, error


def condition_estimate(factorization: Factors, unit_scores: Vector) -> float:
    """Estimate the componentwise condition of the scores `unit_scores` of Aᵀ x = 1.

    It is max_j (|A⁻ᵀ| g)_j / (g_j / 2|A_jj|), with g = |Aᵀ| |x| + 1: the largest change of a
    score when every entry of A and of the emissions moves by a small relative amount, relative
    to half the sum of the absolute terms of the score's own equation. When no term cancels
    another, that half is |x_j|, and this is the usual componentwise condition. A credit can
    cancel a burden and make x_j zero: the sum of the terms stays a fair size for it. It does
    not change with the unit of a product. Example: a loop that loses 1e-4 of what it makes
    gives about 4e4. The norm of G = diag(2|A_jj| / g) A⁻ᵀ diag(g) is estimated from a few
    solves, without forming G, or computed exactly below `_EXACT_CONDITION` processes.
    """
    x = np.abs(unit_scores)
    if not np.all(np.isfinite(x)):
        return math.inf
    g = factorization.magnitude.T @ x + 1.0
    w = 2.0 * np.abs(factorization.matrix.diagonal()) / g

    n = factorization.n
    if n < _EXACT_CONDITION:
        G = w[:, None] * factorization.solve(np.diag(g), transposed=True)
        return float(np.abs(G).sum(axis=1).max())

    # ‖G‖∞ is the 1-norm of Gᵀ = diag(g) A⁻¹ diag(w).
    def forward(v: Vector) -> Vector:
        return g * factorization.solve(w * v)

    def backward(v: Vector) -> Vector:
        return w * factorization.solve(g * v, transposed=True)

    return _one_norm_estimate(forward, backward, n)


def _one_norm_estimate(
    apply: Callable[[Vector], Vector], apply_t: Callable[[Vector], Vector], n: int
) -> float:
    """Estimate the 1-norm of a matrix M known only through M v and Mᵀ v.

    Hager's method with Higham's extra test vector, as in LAPACK's dlacn2: a lower bound, exact
    in most cases, from about 5 products with M and its transpose. Deterministic, unlike SciPy's
    onenormest, which draws random vectors from NumPy's global generator.
    """
    y = apply(np.full(n, 1.0 / n))
    estimate = float(np.abs(y).sum())
    if n == 1:
        return estimate
    signs = np.where(y >= 0, 1.0, -1.0)
    j = int(np.argmax(np.abs(apply_t(signs))))
    for _ in range(4):
        y = apply(np.eye(1, n, j).ravel())
        previous, estimate = estimate, float(np.abs(y).sum())
        new_signs = np.where(y >= 0, 1.0, -1.0)
        if np.array_equal(new_signs, signs) or estimate <= previous:
            estimate = max(estimate, previous)
            break
        signs = new_signs
        z = np.abs(apply_t(signs))
        if z[j] >= z.max():
            break
        j = int(np.argmax(z))
    alternating = np.where(np.arange(n) % 2 == 0, 1.0, -1.0) * (1 + np.arange(n) / (n - 1))
    return max(estimate, 2 * float(np.abs(apply(alternating)).sum()) / (3 * n))


def _scale(matrix: sparse.csc_array) -> tuple[Vector, Vector, sparse.csc_array]:
    """Scale rows, then columns, so the largest entry of each is 1.

    After the row pass every entry is at most 1 and each row holds a 1. The column pass leaves
    the columns holding those 1s unchanged, so each row still holds a 1.
    """
    row_scale = 1.0 / abs(matrix).max(axis=1).toarray()
    scaled = sparse.csc_array(sparse.diags_array(row_scale) @ matrix)
    col_scale = 1.0 / abs(scaled).max(axis=0).toarray()
    scaled = sparse.csc_array(scaled @ sparse.diags_array(col_scale))
    return row_scale, col_scale, scaled


def involved_processes(matrix: sparse.csc_array) -> tuple[int, ...]:
    """The processes involved when `matrix` is singular or nearly so. See `_singular_indices`."""
    return _singular_indices(_scale(matrix)[2])


def _singular_indices(scaled: sparse.csc_array) -> tuple[int, ...]:
    """Name the processes of a singular matrix from its null vectors.

    Inverse iteration on the slightly shifted matrix finds the right null vector (processes
    whose runs cancel out) and the left one (products whose balances are dependent). The right
    one also covers suppliers of a loop, the left one its buyers; both cover the loop itself,
    so the processes in both are named.
    """
    n = scaled.shape[0]
    shifted = sparse.csc_array(scaled + _SHIFT * sparse.eye_array(n))
    try:
        lu = splu(shifted, permc_spec=ORDERING, diag_pivot_thresh=1.0)
    except RuntimeError:
        return ()
    start = np.random.default_rng(0).uniform(1.0, 2.0, n)
    supports: list[NDArray[np.bool_]] = []
    for trans in ("N", "T"):
        v = start
        for _ in range(3):
            v = lu.solve(v, trans=trans)
            v = v / np.max(np.abs(v))
        supports.append(np.abs(v) > _INVOLVED)
    both = supports[0] & supports[1]
    involved = both if both.any() else supports[0] | supports[1]
    return tuple(int(i) for i in np.flatnonzero(involved))
