"""The distributions of the lines of A and B of a release, for Monte Carlo.

DuckDB numbers the lines of A and of B exactly as `_release.assemble_release` does, by process
and line id, and returns those that carry a distribution, sorted by process id, then line id:
the order of their uniforms. So a line's draw never depends on matrix indices (AGENTS.md rule
1). Example, the toy with a lognormal on fabric production's 3 kWh: one uncertain line, the
second line of A (fabric's column, after electricity's reference), with loc ln 3.

A distribution is in the unit of its line. It is taken to the reference unit of the flow by the
same factor as the amount: 10.8 MJ of electricity with loc ln 10.8 becomes 3 kWh with loc ln 3.
"""

from collections import Counter
from collections.abc import Sequence
from contextlib import closing
from dataclasses import dataclass
from typing import cast

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc

from headwater.calc import Matrix
from headwater.calc.monte_carlo import DISTRIBUTIONS, Uncertain
from headwater.model import Distribution, InvalidDataError, Problem, Role
from headwater.query._release import _SKIPPED, _Query  # pyright: ignore[reportPrivateUsage]
from headwater.query._system import Block
from headwater.release import Release


@dataclass(frozen=True, eq=False)
class UncertainLines:
    """The lines of a release that carry a distribution, in the order of their uniforms."""

    uncertain: Uncertain
    in_a: np.ndarray
    position: np.ndarray
    reference: np.ndarray
    counts: dict[str, int]
    """How many lines carry each distribution: `{"lognormal": 1}`."""
    process_id: pa.StringArray
    line_id: pa.StringArray

    def indices(self, lines: Sequence[tuple[str, str]]) -> list[int | None]:
        """The index of each line, by process id and line id, among these lines: the place of
        its uniform. None for a line that has no distribution here."""
        if not lines:
            return []
        wanted = pa.array(sorted({p for p, _ in lines}), pa.string())
        hit = pc.is_in(self.process_id, value_set=wanted)  # pyright: ignore[reportUnknownMemberType]
        at = pa.array(np.flatnonzero(hit.to_numpy(zero_copy_only=False)), pa.int64())
        processes = [str(p) for p in self.process_id.take(at).to_pylist()]
        line_ids = [str(i) for i in self.line_id.take(at).to_pylist()]
        positions = np.flatnonzero(hit.to_numpy(zero_copy_only=False)).tolist()
        found = dict(zip(zip(processes, line_ids, strict=True), positions, strict=True))
        return [found.get(line) for line in lines]


_QUERY = f"""
WITH a AS (
    SELECT row_number() OVER (ORDER BY col, line_id) - 1 AS position, TRUE AS in_a,
           process_id, line_id, col, role, amount, raw_amount, line_unit, flow_unit
    FROM line
    WHERE matrix = '{Matrix.technosphere.value}'
      AND coalesce(link_status, '') NOT IN ({_SKIPPED})
), b AS (
    SELECT row_number() OVER (ORDER BY l.col, l.line_id) - 1 AS position, FALSE AS in_a,
           l.process_id, l.line_id, l.col, l.role, l.amount, l.raw_amount, l.line_unit,
           l.flow_unit
    FROM line l JOIN flow_idx f ON f.flow_id = l.flow_id
    WHERE l.matrix = '{Matrix.biosphere.value}'
)
SELECT x.position, x.in_a, x.process_id, x.line_id, x.col, x.role, x.amount,
       CASE WHEN x.raw_amount <> 0 THEN x.amount / x.raw_amount
            WHEN x.line_unit = x.flow_unit THEN 1.0 END AS factor,
       e.uncertainty.distribution AS distribution, e.uncertainty.loc AS loc,
       e.uncertainty.scale AS scale, e.uncertainty.minimum AS minimum,
       e.uncertainty.maximum AS maximum
FROM (SELECT * FROM a UNION ALL SELECT * FROM b) x
JOIN exchanges e ON e.process_id = x.process_id AND e.line_id = x.line_id
WHERE e.uncertainty.distribution IS NOT NULL
ORDER BY x.process_id, x.line_id
"""


def uncertain_lines(release: Release, block: Block) -> UncertainLines:
    """The lines of `block`, built from `release`, that carry a distribution.

    Raises InvalidDataError for a line of amount 0 in a unit other than its flow's, whose
    distribution cannot be converted, and ValueError if the lines do not match `block`.
    """
    with closing(_Query(release, check=False)) as query:
        table = query.table(_QUERY)
    rows = table.num_rows

    def numbers(name: str) -> np.ndarray:
        column = table.column(name)
        return column.to_numpy(zero_copy_only=False).astype(np.float64)

    in_a = table.column("in_a").to_numpy(zero_copy_only=False).astype(np.bool_)
    position = table.column("position").to_numpy(zero_copy_only=False).astype(np.intp)
    col = table.column("col").to_numpy(zero_copy_only=False).astype(np.intp)
    line_ids = [str(i) for i in table.column("line_id").to_pylist()]
    _require_match(block, in_a, position, col, line_ids)

    factor = table.column("factor")
    if factor.null_count:
        unconverted = [
            r
            for r in table.select(["process_id", "line_id", "factor"]).to_pylist()
            if r["factor"] is None
        ]
        raise InvalidDataError(
            Problem(
                "not-supported-yet",
                f"{r['process_id']} line {r['line_id']}",
                "has a distribution and an amount of 0 in a unit other than its flow's, so its "
                "distribution cannot be converted",
            )
            for r in unconverted
        )
    k = numbers("factor")
    if not (k > 0).all():
        raise ValueError("a unit factor of an uncertain line is not above 0")
    amount = numbers("amount")
    names = [str(d) for d in table.column("distribution").to_pylist()]
    code = np.array([DISTRIBUTIONS.index(Distribution(d)) for d in names], dtype=np.int8)

    def parameter(name: str) -> np.ndarray:
        return np.nan_to_num(numbers(name), nan=0.0)

    loc, scale = parameter("loc"), parameter("scale")
    minimum, maximum = parameter("minimum"), parameter("maximum")
    lognormal = code == DISTRIBUTIONS.index(Distribution.lognormal)
    normal = code == DISTRIBUTIONS.index(Distribution.normal)
    # A unit factor is positive, so it moves ln|x| by ln k and scales every other parameter.
    loc = np.where(lognormal, loc + np.log(k, where=lognormal, out=np.zeros(rows)), loc * k)
    scale = np.where(normal, scale * k, scale)
    uncertain = Uncertain(
        distribution=code,
        loc=loc,
        scale=scale,
        minimum=minimum * k,
        maximum=maximum * k,
        negative=lognormal & (amount < 0),
    )
    roles = table.column("role").to_pylist()
    return UncertainLines(
        uncertain=uncertain,
        in_a=in_a,
        position=position,
        reference=np.array([r == Role.reference.value for r in roles], dtype=np.bool_),
        counts=dict(sorted(Counter(names).items())),
        process_id=cast(pa.StringArray, table.column("process_id").combine_chunks()),
        line_id=pa.array(line_ids, pa.string()),
    )


def _require_match(
    block: Block,
    in_a: np.ndarray,
    position: np.ndarray,
    col: np.ndarray,
    line_ids: list[str],
) -> None:
    """Each line found is the line of `block` at its position: same process, same line id."""
    for lines, chosen in ((block.lines, in_a), (block.emissions, ~in_a)):
        at = position[chosen]
        if at.size and int(at.max()) >= len(lines):
            raise ValueError("the uncertain lines do not match the lines of A and B")
        same_col = np.array_equal(lines.col[at], col[chosen])
        found = [line_ids[i] for i in np.flatnonzero(chosen)]
        same_id = lines.line_id.take(at).to_pylist() == found
        if not (same_col and same_id):
            raise ValueError("the uncertain lines do not match the lines of A and B")
