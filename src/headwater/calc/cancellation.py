"""The positive and negative parts of a score, and whether the score is near 0 because they cancel.

Example, the carbon toy: 1 kg of fabric takes 1.5 kg of CO2 from the air, which counts -1.5,
and its residue emits 0.4 kg of non-fossil CO2, which counts +0.4. The net biogenic score is
-1.1, of parts +0.4 and -1.5: it does not cancel. If the residue emitted 1.5 kg, the net would
be 0 with parts of 1.5 each: a reader who sees 0 should know it is +1.5 - 1.5.
"""

from dataclasses import dataclass

import numpy as np

from headwater.calc.arrays import MatrixLike, Vector, as_matrix

CANCELS_BELOW = 1e-3
"""A score cancels when it is smaller than this share of its gross, the sum of the sizes of its
parts. The share docs/appendix/benchmarks.md checks on ecoinvent, under "Numerics on ecoinvent"."""


@dataclass(frozen=True)
class Cancellation:
    """The positive and the negative part of a score: the sums of its positive and of its
    negative terms, one term per flow and process."""

    positive: float
    negative: float

    @property
    def net(self) -> float:
        """The score: positive + negative."""
        return self.positive + self.negative

    @property
    def gross(self) -> float:
        """The sum of the sizes of the parts: positive - negative."""
        return self.positive - self.negative

    @property
    def cancels(self) -> bool:
        """True when the parts are not 0 and the score is less than `CANCELS_BELOW` of their
        size. A score with no parts, an exact zero, does not cancel."""
        return self.gross > 0 and abs(self.net) < CANCELS_BELOW * self.gross

    def __add__(self, other: "Cancellation") -> "Cancellation":
        return Cancellation(self.positive + other.positive, self.negative + other.negative)


def cancellation(B: MatrixLike, c: Vector, s: Vector) -> Cancellation:
    """The parts of the score `c · B s`, term by term: `c[f] × B[f, p] × s[p]` for every flow f
    and process p.

    `B` has one row per elementary flow and one column per process, `c` holds the factors of one
    indicator and `s` how much each process runs. Terms are taken per flow, not per process, so
    a process that takes CO2 from the air and emits it again has two parts, not a net of 0.
    """
    plus, minus = process_parts(B, c)
    return cancellation_of(plus, minus, s)


def process_parts(B: MatrixLike, c: Vector) -> tuple[Vector, Vector]:
    """For each process, the sum of its positive terms `c[f] × B[f, p]` and of its negative
    ones, per unit it runs. They depend on `B` and `c` only, so a caller can keep them for every
    `s`."""
    matrix = as_matrix(B, "B", copy=False)
    n_flows = matrix.shape[0]
    if c.shape != (n_flows,):
        raise ValueError(f"c must have one factor per flow of B ({n_flows} flows), got {c.shape}")
    terms = matrix.multiply(c.reshape(-1, 1)).tocsc()
    plus = np.asarray(terms.maximum(0).sum(axis=0), dtype=np.float64).ravel()
    minus = np.asarray(terms.minimum(0).sum(axis=0), dtype=np.float64).ravel()
    return plus, minus


def cancellation_of(plus: Vector, minus: Vector, s: Vector) -> Cancellation:
    """The parts of a score from `process_parts` and how much each process runs. A term is
    positive when its process's term and its run have the same sign, so a process that runs
    backwards turns its terms around."""
    if s.shape != plus.shape:
        raise ValueError(
            f"s must have one entry per process ({plus.shape[0]} processes), got {s.shape}"
        )
    up, down = np.maximum(s, 0.0), np.minimum(s, 0.0)
    return Cancellation(
        positive=float(plus @ up + minus @ down), negative=float(minus @ up + plus @ down)
    )
