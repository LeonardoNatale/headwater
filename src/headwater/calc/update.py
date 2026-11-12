"""Update every score after an edit, reusing the stored factorization."""

import math
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Literal, overload

import numpy as np
from numpy.typing import ArrayLike, NDArray
from scipy import sparse

from headwater.calc.arrays import MatrixLike, Vector, as_matrix, as_vector
from headwater.calc.errors import ResidualError, SingularMatrixError
from headwater.calc.solve import (
    Factorization,
    Solution,
    SolverSettings,
    Step,
    check_factors,
    check_reference_outputs,
    condition_estimate,
    factorize,
    failing_rows,
    involved_processes,
    passes,
    refine_if_needed,
    solve_checked,
    structural_zeros,
)

CROSSOVER = 120
"""A scenario that changes this many columns of A or fewer is solved by a low-rank update, one
that changes more by factorizing the edited A again. On ecoinvent 3.10 cut-off (23,522 processes;
3.12 is not yet measured) it is the largest count where the update worked and beat refactorizing
in every measured set. Beyond it the update starts to refuse edits, before it gets slower:
bench/scenario_crossover.py, results and machine in
docs/appendix/benchmarks.md#scenario-crossover."""


@dataclass(frozen=True, slots=True)
class Route:
    """How the matrix of a scenario is solved, and why. `str` gives the line for provenance.

    Example, a scenario changing 140 processes, with a crossover of 60:
    `refactorized: 140 changed processes, above the crossover of 60`.
    """

    changed: int
    """The columns of A the scenario changes. A change to the emissions of a process only does
    not count: it needs no change of A."""
    crossover: int
    refactorized: bool
    """True when the edited A is factorized again, False when the stored factorization is
    updated (or, with no changed column, used as it is)."""
    cause: str | None = None
    """Why an update that was within the crossover was refactorized: it failed its check."""

    def failed(self, cause: str) -> "Route":
        """This route after its update failed a check, so A is factorized again."""
        if self.refactorized:
            raise ValueError(f"route is already a refactorization: {self}")
        return replace(self, refactorized=True, cause=cause)

    def __str__(self) -> str:
        if self.changed == 0:
            return "stored factorization: no purchase changed"
        count = f"{self.changed} changed process{'' if self.changed == 1 else 'es'}"
        if self.cause is not None:
            return (
                f"refactorized: {count}, within the crossover of {self.crossover}, "
                f"but the update failed its check: {self.cause}"
            )
        if self.refactorized:
            return f"refactorized: {count}, above the crossover of {self.crossover}"
        return f"updated: {count}, within the crossover of {self.crossover}"


def choose_route(changed: int, crossover: int = CROSSOVER) -> Route:
    """Update the stored factorization for `changed` columns of A up to `crossover`, factorize
    again above it. At exactly the crossover the update runs.

    Example: with a crossover of 60, 60 changed columns are updated and 61 are refactorized.
    """
    if changed < 0:
        raise ValueError(f"changed must be zero or more, got {changed}")
    if crossover < 0:
        raise ValueError(f"crossover must be zero or more, got {crossover}")
    return Route(changed, crossover, refactorized=changed > crossover)


@dataclass(frozen=True, eq=False)
class LowRankUpdate:
    """A stored factorization of A with whole columns replaced: A' = A + U Eᵀ, where U holds the
    change of each column and E the unit vectors of those columns. Solves with A' reuse the
    stored factors (Woodbury), so A' is never factorized.

    Example: a scenario where fabric production uses 2.5 kWh per kg instead of 3 changes one
    column of the toy. Every score of the edited background then costs one transposed solve,
    and how much each process runs costs two forward solves.

    Make one with `low_rank_update`, which checks it like a new factorization.
    """

    factorization: Factorization
    """The stored factors of A, before the edit."""
    matrix: sparse.csc_array
    """A', the edited matrix."""
    magnitude: sparse.csc_array
    """|A'|, entry by entry, for the residual checks."""
    columns: NDArray[np.intp]
    """The columns replaced."""
    U: NDArray[np.float64]
    """The change of each replaced column, one column per index in `columns`."""
    W: NDArray[np.float64]
    """A⁻ᵀ E, from one transposed solve per replaced column."""
    capacitance: NDArray[np.float64]
    """I + Uᵀ W: A' is singular exactly when this is."""
    condition: float
    """The condition estimate of A', as `Factorization.condition`. NaN only inside
    `update_column`, which does not estimate it."""

    @property
    def n(self) -> int:
        return self.factorization.n

    @property
    def settings(self) -> SolverSettings:
        return self.factorization.settings

    def solve(self, b: Vector, *, transposed: bool = False) -> Vector:
        """Solve A' y = b, or A'ᵀ y = b, with no residual check. `b` may hold several columns.

        Use `scores` and `supply` for checked results.
        """
        solve = self.factorization.solve
        if transposed:
            y = solve(b, transposed=True)
            return y - self.W @ np.linalg.solve(self.capacitance, self.U.T @ y)
        # A' s = b: s = A⁻¹ (b - U z), where z = Eᵀ s solves (I + Eᵀ A⁻¹ U) z = Eᵀ A⁻¹ b. The
        # matrix Eᵀ A⁻¹ U is (Uᵀ W)ᵀ, so I + Eᵀ A⁻¹ U is the transposed capacitance.
        s = solve(b)
        z = np.linalg.solve(self.capacitance.T, s[self.columns])
        return s - solve(self.U @ z)

    def scores(self, h: ArrayLike, *, start: ArrayLike | None = None) -> Solution:
        """Score per unit of every product of A', for one indicator: solves A'ᵀ x = h.

        `start` holds the scores of the unedited A for the same `h`, when they are known: then
        the update needs no solve besides the correction. Raises ResidualError if the result
        fails its residual check after one refinement.
        """
        hv = as_vector(h, self.n, "h")
        initial = None
        if start is not None:
            xv = as_vector(start, self.n, "start")
            initial = xv - self.W @ np.linalg.solve(self.capacitance, self.U.T @ xv)
        return self._checked(hv, transposed=True, initial=initial)

    def supply(self, f: ArrayLike) -> Solution:
        """How much each process runs to deliver the demand `f` with A': solves A' s = f.

        Raises ResidualError if the result fails its residual check after one refinement.
        """
        return self._checked(as_vector(f, self.n, "f"), transposed=False, initial=None)

    def _checked(self, b: Vector, *, transposed: bool, initial: Vector | None) -> Solution:
        tolerance = self.settings.tolerance
        matrix = self.matrix.T if transposed else self.matrix
        magnitude = self.magnitude.T if transposed else self.magnitude
        zero = structural_zeros(magnitude, b)

        def apply(v: Vector) -> Vector:
            y = self.solve(v, transposed=transposed)
            y[zero] = 0.0
            return y

        y = apply(b) if initial is None else initial
        y[zero] = 0.0
        y, error, refined = refine_if_needed(apply, matrix, b, y, tolerance, magnitude)
        if not passes(error, tolerance):
            raise ResidualError(
                f"low-rank update residual above tolerance {tolerance:g}; refactorize. Rows",
                failing_rows(error, tolerance),
                float(np.max(error)),
            )
        path = (self.factorization.step, Step.low_rank) + (Step.refined,) * refined
        return Solution(y, float(np.max(error)), path, zero)


def low_rank_update(
    factorization: Factorization, columns: Sequence[int], new_columns: MatrixLike
) -> LowRankUpdate:
    """The stored factorization with whole columns of A replaced, checked like a factorization.

    `new_columns` holds the full new columns, one per index in `columns`. Making the update
    costs one transposed solve per column. It is checked like `factorize` checks its factors:
    on the scores for an emission of 1 per run of every process, then by its condition
    estimate, from about ten solves with the update.

    Raises SingularMatrixError naming every changed process with no reference output, the
    changed processes when the edit makes A singular, and the processes involved when its
    condition estimate is above the limit of the factorization's settings. Raises ResidualError
    when the check fails.
    """
    update = _update(factorization, columns, new_columns)
    settings = factorization.settings
    unit_scores, error = check_factors(update)
    if not passes(error, settings.tolerance):
        raise ResidualError(
            f"low-rank update residual above tolerance {settings.tolerance:g}; refactorize. Rows",
            failing_rows(error, settings.tolerance),
            float(np.max(error)),
        )
    condition = condition_estimate(update, unit_scores)
    if not condition <= settings.condition_limit:
        raise SingularMatrixError(
            f"matrix is nearly singular: condition estimate {condition:.3g} is above "
            f"{settings.condition_limit:g}; processes involved",
            involved_processes(update.matrix),
        )
    return LowRankUpdate(
        factorization,
        update.matrix,
        update.magnitude,
        update.columns,
        update.U,
        update.W,
        update.capacitance,
        condition,
    )


def _columns(
    n: int, columns: Sequence[int], new_columns: MatrixLike
) -> tuple[NDArray[np.intp], sparse.csc_array]:
    """The replaced columns as an array, and their new values, after checking both."""
    cols = np.asarray(columns, dtype=np.intp)
    k = cols.size
    if cols.ndim != 1 or k == 0:
        raise ValueError(f"columns must be a non-empty list of indices, got {columns}")
    if np.unique(cols).size != k:
        raise ValueError(f"each column may be changed only once, got {columns}")
    if cols.min() < 0 or cols.max() >= n:
        raise ValueError(f"columns must be between 0 and {n - 1}, got {columns}")
    new = as_matrix(new_columns, "new columns")
    if new.shape != (n, k):
        raise ValueError(f"new columns must have shape ({n}, {k}), got {new.shape}")
    return cols, new


def _replace_columns(
    A: sparse.csc_array, cols: NDArray[np.intp], new: sparse.csc_array
) -> sparse.csc_array:
    """A with the columns `cols` replaced by those of `new`, entry for entry."""
    old = A.tocoo()
    keep = ~np.isin(old.col, cols)
    put = new.tocoo()
    return sparse.csc_array(
        (
            np.concatenate([old.data[keep], put.data]),
            (
                np.concatenate([old.row[keep], put.row]),
                np.concatenate([old.col[keep], cols[put.col]]),
            ),
        ),
        shape=A.shape,
    )


def refactorize(
    factorization: Factorization, columns: Sequence[int], new_columns: MatrixLike
) -> Factorization:
    """Factors of A with whole columns replaced, made from scratch: the other way to solve a
    scenario, for when many processes change (see `choose_route`).

    `new_columns` holds the full new columns, one per index in `columns`, as for
    `low_rank_update`. The factors are made, checked and estimated as `factorize` does, with the
    tolerance, condition limit and pivoting of the factorization they replace.

    Raises what `factorize` raises: SingularMatrixError naming the processes involved, and
    ResidualError.
    """
    cols, new = _columns(factorization.n, columns, new_columns)
    settings = factorization.settings
    return factorize(
        _replace_columns(factorization.matrix, cols, new),
        pivoting=settings.diag_pivot_thresh >= 1.0,
        tolerance=settings.tolerance,
        condition_limit=settings.condition_limit,
    )


def _update(
    factorization: Factorization, columns: Sequence[int], new_columns: MatrixLike
) -> LowRankUpdate:
    """The update, with every check but those of its solves. Its condition is not estimated."""
    A = factorization.matrix
    n = factorization.n
    cols, new = _columns(n, columns, new_columns)
    k = cols.size

    U = (new - A[:, cols]).toarray()
    E = sparse.csc_array((np.ones(k), (cols, np.arange(k))), shape=(n, k))
    updated = _replace_columns(A, cols, new)
    check_reference_outputs(updated)

    W = factorization.solve(E.toarray(), transposed=True)
    UtW = U.T @ W
    capacitance = np.eye(k) + UtW
    # Entries of the capacitance carry rounding errors of about eps x its size. A singular value
    # that small is zero: the edited matrix is singular.
    singular_values = np.linalg.svd(capacitance, compute_uv=False)
    size = 1.0 + float(np.linalg.norm(UtW, 2))
    if not singular_values[-1] > 16 * k * np.finfo(np.float64).eps * size:
        raise SingularMatrixError("the changed processes make the matrix singular", np.sort(cols))
    return LowRankUpdate(factorization, updated, abs(updated), cols, U, W, capacitance, math.nan)


@overload
def update_column(
    factorization: Factorization,
    x: ArrayLike,
    h: ArrayLike,
    columns: Sequence[int],
    new_columns: MatrixLike,
    *,
    return_matrix: Literal[False] = False,
) -> Solution: ...


@overload
def update_column(
    factorization: Factorization,
    x: ArrayLike,
    h: ArrayLike,
    columns: Sequence[int],
    new_columns: MatrixLike,
    *,
    return_matrix: Literal[True],
) -> tuple[Solution, sparse.csc_array]: ...


def update_column(
    factorization: Factorization,
    x: ArrayLike,
    h: ArrayLike,
    columns: Sequence[int],
    new_columns: MatrixLike,
    *,
    return_matrix: bool = False,
) -> Solution | tuple[Solution, sparse.csc_array]:
    """Scores after replacing whole columns of A, without refactorizing.

    Example: the T-shirt buys 0.30 kg of fabric instead of 0.25. Column 0 changes, and every
    score is updated from the stored scores `x` (for A and `h`) with one extra transposed solve.

    Any number of edits inside one process change one column: Sherman-Morrison. Edits to k
    processes are Woodbury, with k extra transposed solves; for k = 1 it is Sherman-Morrison.
    `new_columns` holds the full new columns, one per index in `columns`. To change the direct
    emissions of the same processes too, call `update_emissions` first and pass its result.
    With `return_matrix`, the edited matrix comes back too, as `(solution, matrix)`. Unlike
    `low_rank_update`, the condition of the edited matrix is not estimated.

    Raises SingularMatrixError naming the changed processes if the edit makes A singular, and
    ResidualError if the result fails its residual check after one refinement.
    """
    update = _update(factorization, columns, new_columns)
    solution = update.scores(h, start=x)
    return (solution, update.matrix) if return_matrix else solution


def update_emissions(factorization: Factorization, x: ArrayLike, h_new: ArrayLike) -> Solution:
    """Scores after a change in direct emissions only, from the stored scores `x`.

    One transposed solve for the change, with no correction term: A does not change.
    """
    xv = as_vector(x, factorization.n, "x")
    return solve_checked(factorization, h_new, transposed=True, start=xv)
