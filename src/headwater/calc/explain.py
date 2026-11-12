"""Explain a score from stored vectors: what it buys, where it is emitted, which inputs matter."""

from dataclasses import dataclass

import numpy as np
from numpy.typing import ArrayLike, NDArray
from scipy import sparse

from headwater.calc.arrays import MatrixLike, Vector, as_matrix, as_vector
from headwater.calc.errors import CalculationError, ResidualError, SingularMatrixError
from headwater.calc.solve import TOLERANCE


@dataclass(frozen=True, eq=False)
class Node:
    """One product in an explanation tree: score = direct + sum of children's scores + rest."""

    index: int
    """The process, and its reference product."""

    amount: float
    """Units of the product bought, in matrix terms: a waste sent to treatment is negative."""

    runs: float
    """How many times the process runs: amount / A[index, index]. For a treatment, kg treated."""

    score: float
    """amount x stored score of the product."""

    direct: float
    """runs x the process's direct characterised emissions."""

    rest: float
    """Purchases left out by `min_share` or `max_depth`, summed."""

    children: tuple["Node", ...]
    """Purchases kept, largest first."""


@dataclass(frozen=True, eq=False)
class Levers:
    """Every entry of a matrix with its effect on one score, largest elasticity first.

    Rows are products for the technosphere matrix A, and elementary flows for the biosphere
    matrix B. Columns are processes in both.
    """

    rows: NDArray[np.intp]
    cols: NDArray[np.intp]
    derivative: Vector
    """d score / d A[row, col] = -x[row] x s[col], or d score / d B[row, col] = c[row] x s[col]."""
    elasticity: Vector
    """% change of the score per 1% change of the entry."""


@dataclass(frozen=True, eq=False)
class LineEffects:
    """The effect on a score of each of some lines, in the order the lines were given."""

    present: NDArray[np.bool_]
    """False for a line whose matrix cell holds nothing: its amount is 0, or the lines of its
    cell cancel out. Such a line has no effect, and its other values are 0."""
    sensitivity: Vector
    """Change of the score per one more unit of the line's amount."""
    elasticity: Vector
    """% change of the score per 1% change of the line's amount."""


def explain(
    A: MatrixLike,
    h: ArrayLike,
    x: ArrayLike,
    index: int,
    *,
    amount: float = 1.0,
    min_share: float = 0.03,
    max_depth: int = 3,
    tolerance: float = TOLERANCE,
) -> Node:
    """Explain `amount` units of product `index` as a tree of what it buys, with no solve.

    Example, the T-shirt: 1.19205 = 0.25 kg fabric x 3.7086 + 0.5 kWh x 0.5298.

    Each node is direct emissions plus, for each purchase, amount x stored score of the
    supplier. Purchases smaller than `min_share` of the root score, and everything below
    `max_depth`, are summed into `rest`, so each node still adds up to its score. A purchase of
    a process's own product is on the diagonal and handled there. Loops between processes end
    at `max_depth`, and the tree grows with the number of purchases kept, so keep `max_depth`
    small when `min_share` is 0.

    `x` must be the scores of A and h. Each node is checked like a row of the solve, against
    `tolerance`, and ResidualError names the first process that does not add up.
    """
    if not min_share >= 0:
        raise ValueError(f"min_share must be zero or positive, got {min_share}")
    if max_depth < 0:
        raise ValueError(f"max_depth must be zero or positive, got {max_depth}")
    if not np.isfinite(amount):
        raise ValueError(f"amount must be a finite number, got {amount}")
    matrix = _square(A)
    n = matrix.shape[0]
    if not 0 <= index < n:
        raise ValueError(f"index must be between 0 and {n - 1}, got {index}")
    hv = as_vector(h, n, "h")
    xv = as_vector(x, n, "x")
    diagonal = matrix.diagonal()
    cutoff = min_share * abs(amount * xv[index])

    def node(i: int, bought: float, depth: int) -> Node:
        if diagonal[i] == 0:
            raise SingularMatrixError("processes with no reference output", [i])
        runs = bought / diagonal[i]
        start, end = matrix.indptr[i], matrix.indptr[i + 1]
        rows = matrix.indices[start:end]
        keep = rows != i
        suppliers = rows[keep]
        amounts = -matrix.data[start:end][keep] * runs
        contributions = amounts * xv[suppliers]
        score = bought * xv[i]
        direct = runs * hv[i]
        mismatch = abs(score - direct - contributions.sum())
        size = abs(score) + abs(direct) + np.abs(contributions).sum()
        if mismatch > tolerance * size:
            raise ResidualError(
                f"x is not the scores of A and h within tolerance {tolerance:g}, at process",
                [i],
                float(mismatch / size),
            )
        kept = (
            np.flatnonzero(np.abs(contributions) >= cutoff)
            if depth < max_depth
            else np.array([], dtype=np.intp)
        )
        kept = kept[np.argsort(-np.abs(contributions[kept]), kind="stable")]
        left_out = np.ones(contributions.size, dtype=bool)
        left_out[kept] = False
        return Node(
            index=i,
            amount=bought,
            runs=float(runs),
            score=float(score),
            direct=float(direct),
            rest=float(contributions[left_out].sum()),
            children=tuple(node(int(suppliers[k]), float(amounts[k]), depth + 1) for k in kept),
        )

    return node(index, float(amount), 0)


def where(h: ArrayLike, s: ArrayLike) -> Vector:
    """Direct emissions of each process for one demand: h x s. Sums to the score."""
    hv = np.asarray(h, dtype=np.float64)
    return as_vector(hv, hv.size, "h") * as_vector(s, hv.size, "s")


@dataclass(frozen=True, eq=False)
class Largest:
    """The largest of some values by size, and the others summed."""

    index: NDArray[np.intp]
    """Positions of the values kept, largest |value| first."""
    rest: float
    """The values left out, summed."""
    rest_count: int
    """How many values were left out. Values of exactly 0 are not counted."""


def largest(values: ArrayLike, n: int | None) -> Largest:
    """The `n` largest values by size, and the others summed into one rest.

    Example, where the T-shirt is emitted: [0, 0.5298, 0.66225, 0] with n = 1 keeps position 2,
    electricity, and sums the fabric's 0.5298 into the rest, of 1 value. A value of exactly 0 is
    never kept or counted. Ties keep the order of the values. `n = None` keeps every value that
    is not 0.
    """
    if n is not None and n < 0:
        raise ValueError(f"n must be zero or positive, got {n}")
    v = np.asarray(values, dtype=np.float64)
    v = as_vector(v, v.size, "values")
    nonzero = np.flatnonzero(v != 0)
    order = nonzero[np.argsort(-np.abs(v[nonzero]), kind="stable")]
    kept = order if n is None else order[:n]
    left = order[kept.size :]
    return Largest(kept, float(v[left].sum()), int(left.size))


def sums_by_group(
    values: ArrayLike, groups: ArrayLike, n_groups: int
) -> tuple[Vector, NDArray[np.intp]]:
    """The sum of the values in each group, and how many of them are not 0.

    `groups[k]` is the group of value k, between 0 and `n_groups` - 1. Example: the T-shirt's
    emissions [0, 0.5298, 0.66225, 0], with the fabric in group 1 and the rest in group 0, sum to
    [0.66225, 0.5298].
    """
    group = np.asarray(groups, dtype=np.intp)
    v = as_vector(values, group.size, "values")
    if group.ndim != 1 or (group.size and not (0 <= group.min() and group.max() < n_groups)):
        raise ValueError(f"groups must be a vector of numbers between 0 and {n_groups - 1}")
    sums = np.bincount(group, weights=v, minlength=n_groups).astype(np.float64)
    counts = np.bincount(group[v != 0], minlength=n_groups).astype(np.intp)
    return sums, counts


def levers(A: MatrixLike, x: ArrayLike, s: ArrayLike, f: ArrayLike) -> Levers:
    """Rank every technosphere entry by its elasticity on the score x · f.

    The elasticity of A[i, j] is -x[i] x s[j] x A[i, j] / score. Example: for the T-shirt, the
    3 kWh per kg of fabric has elasticity 1/3.
    """
    matrix = _square(A)
    n = matrix.shape[0]
    xv = as_vector(x, n, "x")
    sv = as_vector(s, n, "s")
    score = float(xv @ as_vector(f, n, "f"))
    if score == 0:
        raise CalculationError("score is zero, so elasticities are undefined")
    rows = matrix.indices.astype(np.intp)
    cols = np.repeat(np.arange(n, dtype=np.intp), np.diff(matrix.indptr))
    derivative = -xv[rows] * sv[cols]
    elasticity = derivative * matrix.data / score
    order = np.argsort(-np.abs(elasticity), kind="stable")
    return Levers(rows[order], cols[order], derivative[order], elasticity[order])


def line_levers(
    A: MatrixLike,
    x: ArrayLike,
    s: ArrayLike,
    f: ArrayLike,
    rows: ArrayLike,
    cols: ArrayLike,
    signs: ArrayLike,
    amounts: ArrayLike,
) -> LineEffects:
    """The effect on the score x · f of each given technosphere line, with no loop over them.

    Line k sits in cell (`rows[k]`, `cols[k]`) of A. Its matrix entry is `signs[k]` × its
    amount, so the score moves by -x[row] × s[col] × sign per one more unit of the amount, and by
    that times the amount over the score per 1%. Example, the T-shirt's 3 kWh of electricity per
    kg of fabric (sign -1): 0.5298 × 0.25 = 0.13245 per kWh, and an elasticity of 1/3.

    Several lines may share a cell, such as a reference and an own use line on the diagonal: each
    gets the cell's derivative times its own sign. A line whose cell holds nothing after the lines
    of the cell are summed is not `present`, and has no effect.
    """
    matrix = _square(A)
    n = matrix.shape[0]
    xv = as_vector(x, n, "x")
    sv = as_vector(s, n, "s")
    score = float(xv @ as_vector(f, n, "f"))
    if score == 0:
        raise CalculationError("score is zero, so elasticities are undefined")
    row, col, sign, amount = _lines(n, n, rows, cols, signs, amounts)
    present = _in_matrix(matrix, row, col)
    sensitivity = np.where(present, -xv[row] * sv[col] * sign, 0.0)
    return LineEffects(present, sensitivity, sensitivity * amount / score)


def emission_line_levers(
    B: MatrixLike,
    c: ArrayLike,
    s: ArrayLike,
    score: float,
    rows: ArrayLike,
    cols: ArrayLike,
    amounts: ArrayLike,
) -> LineEffects:
    """The effect on the score of each given biosphere line, with no loop over them.

    Line k sits in cell (`rows[k]`, `cols[k]`) of B: flow row, process column. The score moves
    by c[row] × s[col] per one more unit of its amount. Example: 0.5 kg of CO2 per kWh of
    electricity, which runs 1.25 kWh, moves it by 1 × 1.25, and 1% more by 0.625 / 1.19205 %.
    A line whose cell holds nothing is not `present`. `c` and `s` are as for `emission_levers`.
    """
    matrix = as_matrix(B, "B", copy=False)
    cv = as_vector(c, matrix.shape[0], "c")
    sv = as_vector(s, matrix.shape[1], "s")
    if not np.isfinite(score):
        raise ValueError(f"score must be a finite number, got {score}")
    if score == 0:
        raise CalculationError("score is zero, so elasticities are undefined")
    row, col, _, amount = _lines(*matrix.shape, rows, cols, np.ones(np.shape(rows)), amounts)
    present = _in_matrix(matrix, row, col)
    sensitivity = np.where(present, cv[row] * sv[col], 0.0)
    return LineEffects(present, sensitivity, sensitivity * amount / score)


def emission_levers(B: MatrixLike, c: ArrayLike, s: ArrayLike, score: float) -> Levers:
    """Rank every biosphere entry by its elasticity on the score.

    The elasticity of B[k, j] is c[k] x s[j] x B[k, j] / score. Example: for the T-shirt, 0.5 kg
    of CO2 per kWh of electricity, which runs 1.25 kWh, has a derivative of 1 x 1.25 and an
    elasticity of 0.625 / 1.19205 = 0.524. The elasticities of all entries add up to 1, because
    the score is linear in B.

    `c` holds the characterisation factors of one indicator, one per row of B, and `s` how much
    each process runs, one per column. `score` is the score they give.
    """
    matrix = as_matrix(B, "B", copy=False)
    cv = as_vector(c, matrix.shape[0], "c")
    sv = as_vector(s, matrix.shape[1], "s")
    if not np.isfinite(score):
        raise ValueError(f"score must be a finite number, got {score}")
    if score == 0:
        raise CalculationError("score is zero, so elasticities are undefined")
    rows = matrix.indices.astype(np.intp)
    cols = np.repeat(np.arange(matrix.shape[1], dtype=np.intp), np.diff(matrix.indptr))
    derivative = cv[rows] * sv[cols]
    elasticity = derivative * matrix.data / score
    order = np.argsort(-np.abs(elasticity), kind="stable")
    return Levers(rows[order], cols[order], derivative[order], elasticity[order])


def _lines(
    n_rows: int,
    n_cols: int,
    rows: ArrayLike,
    cols: ArrayLike,
    signs: ArrayLike,
    amounts: ArrayLike,
) -> tuple[NDArray[np.intp], NDArray[np.intp], Vector, Vector]:
    """The columns of a table of lines, checked to be of one length and inside the matrix."""
    row = np.asarray(rows, dtype=np.intp)
    col = np.asarray(cols, dtype=np.intp)
    if not row.shape == col.shape == np.shape(signs) == np.shape(amounts) or row.ndim != 1:
        raise ValueError("rows, cols, signs and amounts must be vectors of the same length")
    if row.size and not (0 <= row.min() and row.max() < n_rows):
        raise ValueError(f"row index out of range for {n_rows} rows")
    if col.size and not (0 <= col.min() and col.max() < n_cols):
        raise ValueError(f"column index out of range for {n_cols} columns")
    return row, col, as_vector(signs, row.size, "signs"), as_vector(amounts, row.size, "amounts")


def _in_matrix(
    matrix: sparse.csc_array, rows: NDArray[np.intp], cols: NDArray[np.intp]
) -> NDArray[np.bool_]:
    """True where (row, col) is a stored entry of a canonical matrix: one search in the sorted
    cell numbers of its entries."""
    entry_cols = np.repeat(np.arange(matrix.shape[1], dtype=np.int64), np.diff(matrix.indptr))
    cells = entry_cols * matrix.shape[0] + matrix.indices
    wanted = cols.astype(np.int64) * matrix.shape[0] + rows
    at = np.minimum(np.searchsorted(cells, wanted), max(cells.size - 1, 0))
    return (cells[at] == wanted) if cells.size else np.zeros(wanted.shape, dtype=bool)


def _square(A: MatrixLike) -> sparse.csc_array:
    matrix = as_matrix(A, copy=False)
    if matrix.shape[0] != matrix.shape[1]:
        raise ValueError(f"matrix must be square, got shape {matrix.shape}")
    return matrix
