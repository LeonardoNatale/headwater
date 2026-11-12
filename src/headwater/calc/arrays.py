"""Input checks shared by the calculation functions."""

import numpy as np
from numpy.typing import ArrayLike, NDArray
from scipy import sparse

from headwater.calc.errors import CalculationError

type Vector = NDArray[np.float64]
type MatrixLike = sparse.sparray | sparse.spmatrix | NDArray[np.float64]


def as_matrix(A: MatrixLike, name: str = "matrix", *, copy: bool = True) -> sparse.csc_array:
    """A float64 CSC copy of `A`, with duplicate entries summed and zeros removed.

    With `copy=False`, a matrix already in that form is returned as it is, not copied: for a
    caller that only reads it. `A` itself is never changed. Raises CalculationError naming the
    columns (processes) that hold a NaN or an infinity.
    """
    matrix = sparse.csc_array(A, dtype=np.float64, copy=copy)
    if matrix.ndim != 2:
        raise ValueError(f"{name} must be 2-dimensional, got shape {matrix.shape}")
    if copy or not matrix.has_canonical_format or not np.all(matrix.data):
        if not copy:
            matrix = matrix.copy()
        matrix.sum_duplicates()
        matrix.eliminate_zeros()
    bad = ~np.isfinite(matrix.data)
    if bad.any():
        columns = np.repeat(np.arange(matrix.shape[1]), np.diff(matrix.indptr))
        raise CalculationError(
            f"{name} has entries that are not finite in columns", np.unique(columns[bad])
        )
    return matrix


def as_vector(v: ArrayLike, n: int, name: str) -> Vector:
    """A float64 copy of `v`, checked to be finite and of length `n`."""
    vector = np.array(v, dtype=np.float64)
    if vector.shape != (n,):
        raise ValueError(f"{name} must be a vector of length {n}, got shape {vector.shape}")
    bad = np.flatnonzero(~np.isfinite(vector))
    if bad.size:
        raise CalculationError(f"{name} has entries that are not finite at indices", bad)
    return vector
