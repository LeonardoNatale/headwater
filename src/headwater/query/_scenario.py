"""The background as a named scenario changes it, for everything that buys from it.

Example, `clean-grid`: electricity emits 0.3 kg CO2 per kWh instead of 0.5. Its column of B
changes, so every score is updated with one transposed solve from the stored ones, and fabric
scores 3.1086 instead of 3.7086. A change to a purchase changes a column of A. Up to `CROSSOVER`
changed columns, the scores come from a low-rank update of the stored factorization; above it,
or when the update fails its checks, from a new factorization of the edited A
(docs/calculation.md, "Edits and scenarios"). `Changed.route` says which, and why. The
background itself is never changed: the changed block is a new object.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

import numpy as np
import pyarrow as pa
from numpy.typing import NDArray
from scipy import sparse

from headwater.calc import (
    Factorization,
    LowRankUpdate,
    ResidualError,
    SingularMatrixError,
    Solution,
    Step,
    low_rank_update,
    scores,
    update_emissions,
)
from headwater.calc.arrays import Vector
from headwater.calc.update import CROSSOVER, Route, choose_route, refactorize
from headwater.model import Catalog, Process
from headwater.query._system import (
    Block,
    Emissions,
    Solved,
    StackedIndex,
    assemble,
    ids_in_errors,
    matvec,
)

_SAME = 4 * np.finfo(np.float64).eps
"""Entries of A closer than this, relative to their size, are the same entry."""


@dataclass(frozen=True, eq=False)
class Changed:
    """The background of one scenario, for one indicator."""

    block: Block
    """The background's block with the changed processes' columns in place."""
    columns: NDArray[np.intp]
    """The columns of the changed processes, in the order of `processes`."""
    h: Vector
    x: Solution
    """Every score of the changed background. Its path holds the steps of every solve."""
    solver: Factorization | LowRankUpdate
    """What solves with the changed A: the stored factorization when A did not change, the
    stored one with the changed columns replaced (an update), or new factors of the changed A
    when the scenario was refactorized."""
    fallback: Factorization | None
    """Pivoted factors of the unchanged A, made when a solve with the stored ones failed its
    check. The background keeps them."""
    route: Route
    """Which way the changed A was solved, and why: its `str` is the line for provenance."""

    @property
    def condition(self) -> float:
        return self.solver.condition

    @property
    def refactorized(self) -> bool:
        """True when `solver` holds factors of the changed A: they belong to this scenario, and
        the background must never keep them."""
        return self.route.refactorized


def change(
    solved: Solved,
    index: Mapping[str, int],
    processes: Sequence[Process],
    catalog: Catalog,
    indicator: str,
) -> Changed:
    """The background with `processes` in place of the processes of the same ids, for everyone.

    `processes` hold every line, with the scenario's changes made. Their columns are built as a
    study's are, stacked above the background's rows, then put back in the background's place.
    """
    block = solved.block
    n = len(block.ids)
    ids = [p.id for p in processes]
    columns = np.array([index[i] for i in ids], dtype=np.intp)
    patch = assemble(processes, catalog, StackedIndex({i: k for k, i in enumerate(ids)}, index))
    rows = np.concatenate([columns, np.arange(n, dtype=np.intp)])
    flows = _flow_rows(block, patch)

    coo = patch.A.tocoo()
    new_A = sparse.csc_array((coo.data, (rows[coo.row], coo.col)), shape=(n, len(ids)))
    coo = patch.B.tocoo()
    new_B = sparse.csc_array(
        (coo.data, (flows[coo.row], coo.col)), shape=(len(block.flows), len(ids))
    )

    c = solved.c[indicator]
    stored = solved.x[indicator]
    h = solved.h[indicator].copy()
    h[columns] = matvec(new_B.T, c)
    emissions_moved = not np.array_equal(h, solved.h[indicator])

    # A line the scenario leaves alone is built again here with `Catalog.convert`, and a release
    # converted it in DuckDB: the two can differ in the last bit. That is not a change. Only the
    # columns of A that differ count, for the crossover and for the update.
    matrix = block.A
    old_A = matrix[:, columns]
    difference = (abs(new_A - old_A) > _SAME * (abs(new_A) + abs(old_A))).tocoo()  # pyright: ignore[reportUnknownMemberType]
    moved = np.unique(difference.col[difference.data.astype(bool)])
    route = choose_route(moved.size, CROSSOVER)

    factorization = solved.factorization
    x, fallback, path = stored, None, stored.path
    solver: Factorization | LowRankUpdate = factorization
    if not route.refactorized:
        if emissions_moved:
            with ids_in_errors(block.ids):
                x = update_emissions(factorization, stored.values, h)
            fallback, path = x.fallback, path + x.path
            factorization = fallback or factorization
        if moved.size:
            try:
                with ids_in_errors(block.ids):
                    solver = low_rank_update(
                        factorization, columns[moved].tolist(), new_A[:, moved]
                    )
                    updated = solver.scores(h, start=x.values)
            except (ResidualError, SingularMatrixError) as error:
                # The update is not accurate enough, or finds A singular. On ecoinvent it refuses
                # valid edits from about 150 processes (benchmarks.md, scenario crossover), and
                # factorizing the same edits passes. Factorizing the changed A decides, and raises
                # its own error if the matrix really is singular.
                tolerance = factorization.settings.tolerance
                route = route.failed(
                    f"residual {error.residual:.3g} above tolerance {tolerance:g}"
                    if isinstance(error, ResidualError)
                    else "the update found the matrix singular"
                )
            else:
                path += updated.path
                x = Solution(
                    updated.values, max(x.residual, updated.residual), path, updated.exact_zeros
                )
                matrix = solver.matrix
        else:
            x = replace(x, path=path)
    if route.refactorized:
        with ids_in_errors(block.ids):
            fresh = refactorize(factorization, columns[moved].tolist(), new_A[:, moved])
            x = scores(fresh, h)
        # These factors are for this scenario's A, pivoted ones if the solve needed them.
        solver = x.fallback or fresh
        x = replace(x, path=(Step.refactorized, *x.path))
        matrix = solver.matrix
    return Changed(
        block=_replaced(block, patch, columns, rows, flows, matrix),
        columns=columns,
        h=h,
        x=x,
        solver=solver,
        fallback=fallback,
        route=route,
    )


def _flow_rows(block: Block, patch: Block) -> NDArray[np.intp]:
    """The row in the background's B of each elementary flow of `patch`."""
    known = {flow: i for i, flow in enumerate(block.flows)}
    missing = [f for f in patch.flows if f not in known]
    if missing:  # a scenario changes amounts, units and suppliers, never a line's flow
        raise ValueError(f"changed processes emit flows the background does not: {missing}")
    return np.array([known[f] for f in patch.flows], dtype=np.intp)


def _replaced(
    block: Block,
    patch: Block,
    columns: NDArray[np.intp],
    rows: NDArray[np.intp],
    flows: NDArray[np.intp],
    A: sparse.csc_array,
) -> Block:
    """`block` with the lines of the changed processes in place.

    A changed process keeps its lines, so each line takes the place of the line of the same id,
    and ties in `sensitivity` fall as before.
    """
    lines = block.lines
    at = _positions(lines.col, lines.line_id, patch.lines.col, patch.lines.line_id, columns)
    row, amount, sign = lines.row.copy(), lines.amount.copy(), lines.sign.copy()
    has_supplier = lines.has_supplier.copy()
    row[at] = rows[patch.lines.row]
    amount[at] = patch.lines.amount
    sign[at] = patch.lines.sign
    has_supplier[at] = patch.lines.has_supplier

    emissions = block.emissions
    found = patch.emissions
    at_e = _positions(emissions.col, emissions.line_id, found.col, found.line_id, columns)
    e_row, e_amount = emissions.row.copy(), emissions.amount.copy()
    e_row[at_e] = flows[found.row]
    e_amount[at_e] = found.amount
    B = sparse.csc_array((e_amount, (e_row, emissions.col)), shape=block.B.shape)
    return replace(
        block,
        A=A,
        B=B,
        lines=replace(lines, row=row, amount=amount, sign=sign, has_supplier=has_supplier),
        emissions=Emissions(e_row, emissions.col, e_amount, emissions.role, emissions.line_id),
    )


def _positions(
    cols: NDArray[np.intp],
    line_ids: pa.StringArray,
    patch_cols: NDArray[np.intp],
    patch_line_ids: pa.StringArray,
    columns: NDArray[np.intp],
) -> NDArray[np.intp]:
    """Where each line of the patch sits among the lines of the block, by column and line id."""
    where = np.flatnonzero(np.isin(cols, columns))
    old: dict[tuple[int, str], int] = {}
    for position, col, line in zip(
        where.tolist(),
        cols[where].tolist(),
        line_ids.take(pa.array(where, pa.int64())).to_pylist(),
        strict=True,
    ):
        old[(col, str(line))] = position
    found: list[int] = []
    for col, line in zip(columns[patch_cols].tolist(), patch_line_ids.to_pylist(), strict=True):
        position = old.pop((col, str(line)), None)
        if position is None:
            raise ValueError(f"line {line} of column {col} is not a line of the background")
        found.append(position)
    if old:
        raise ValueError(f"lines {sorted(old)} of the changed processes have no new value")
    return np.array(found, dtype=np.intp)
