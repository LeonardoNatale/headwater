"""Records to arrays, for the calculations of this package.

Ids become matrix indices here, and the indices never leave the objects built here. Example, the
T-shirt: process `tshirt:tshirt` is column 0, and its line e02 buys 0.25 kg of fabric, so A holds
-0.25 in the row of `toy:fabric-production`. Each process's row is its reference product.
"""

from collections.abc import Generator, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import cached_property
from typing import TYPE_CHECKING

import numpy as np
import pyarrow as pa
from numpy.typing import NDArray
from scipy import sparse

from headwater.calc import (
    CalculationError,
    Factorization,
    Matrix,
    ResidualError,
    Solution,
    matrix_entry,
)
from headwater.calc.arrays import Vector, as_matrix
from headwater.calc.errors import SHOWN
from headwater.model import (
    Catalog,
    Exchange,
    InvalidDataError,
    LinkStatus,
    Method,
    Problem,
    Process,
    Role,
)
from headwater.study import ProcessFolder

if TYPE_CHECKING:
    from headwater.query.result import NotCharacterised


ROLE_ORDER: tuple[Role, ...] = tuple(Role)
"""The roles a line may have, in the order of the role codes of `Lines` and `Emissions`."""

REFERENCE_CODE = ROLE_ORDER.index(Role.reference)


@dataclass(frozen=True, eq=False)
class Lines:
    """The technosphere lines of a block as columns, in the order of the lines in the files.

    Line k sits in A at (`row[k]`, `col[k]`). Levers rank all of them at once, so a line is a
    position in these columns, not a Python object. Its process is the block's `ids[col[k]]`; for
    a line that buys, its flow and supplier are those of process `row[k]` of the calculation.
    Example, the T-shirt's e02, 0.25 kg of fabric: row of fabric production, column of the
    T-shirt, amount 0.25, sign -1, role input, with a supplier.
    """

    row: NDArray[np.intp]
    col: NDArray[np.intp]
    amount: Vector
    """In the reference unit of the flow, positive as in the file."""
    sign: Vector
    """The matrix entry of one unit of the line, from calc: -1 for an input, +1 for a waste."""
    role: NDArray[np.int8]
    """Index into `ROLE_ORDER`."""
    has_supplier: NDArray[np.bool_]
    line_id: pa.StringArray

    def __len__(self) -> int:
        return int(self.row.size)


@dataclass(frozen=True, eq=False)
class Emissions:
    """The emission and resource lines of a block as columns: line k sits in B at (`row[k]`,
    `col[k]`), the row of its flow and the column of its process."""

    row: NDArray[np.intp]
    col: NDArray[np.intp]
    amount: Vector
    """In the reference unit of the flow, as in the file: negative for a crop taking CO2."""
    role: NDArray[np.int8]
    line_id: pa.StringArray

    def __len__(self) -> int:
        return int(self.row.size)


@dataclass(frozen=True, eq=False)
class Block:
    """The columns of some processes in A and B. Column k is the process `ids[k]`.

    The rows of A are every process of the calculation, the rows of B are `flows`.
    """

    ids: tuple[str, ...]
    names: tuple[str, ...]
    flow_ids: tuple[str, ...]
    """Per column, the flow the process makes, or the waste it treats."""
    A: sparse.csc_array
    B: sparse.csc_array
    flows: tuple[str, ...]
    """Elementary flow ids, sorted."""
    flow_units: tuple[str, ...]
    """Per row of B, the reference unit of its flow."""
    reference_sign: Vector
    """Per column, the matrix entry of one unit of its reference, from calc: -1 for a treatment."""
    units: tuple[str, ...]
    """Per column, the reference unit of the flow it makes or treats."""
    lines: Lines
    emissions: Emissions


@dataclass(frozen=True)
class Assembled:
    """What `Background` needs from a folder or a release before it factorizes."""

    block: Block
    folder: ProcessFolder
    """The records that study validation resolves ids against. For a release, each process holds
    only its reference line: not the lines of the background."""
    uncovered: "tuple[NotCharacterised, ...]"
    """Elementary flows that no indicator of the method covers, with every process using them."""


@dataclass(frozen=True, eq=False)
class Solved:
    """A background factorized once, with h and x for each indicator of its method."""

    block: Block
    factorization: Factorization
    c: Mapping[str, Vector]
    """Characterisation factors of each indicator, one per flow of `block.flows`."""
    h: Mapping[str, Vector]
    x: Mapping[str, Solution]

    @property
    def ids(self) -> tuple[str, ...]:
        return self.block.ids


@dataclass(frozen=True, eq=False)
class Part:
    """One block of the system, the study's processes or the background's, with the factors of
    one indicator for its flows."""

    block: Block
    c: Vector
    offset: int
    """The system's index of this block's first process."""


@dataclass(frozen=True, eq=False)
class System:
    """A study and its background stacked for one indicator, study processes first.

    Everything `contributions` and `sensitivity` need, with no new solve. The background's A is
    shared, not copied: the stacked A is built once, when first asked for.
    """

    ids: tuple[str, ...]
    names: tuple[str, ...]
    flow_ids: tuple[str, ...]
    units: tuple[str, ...]
    A_ss: sparse.csc_array
    """The study's processes, in their own rows."""
    A_bs: sparse.csc_array
    """The study's purchases from the background, in the background's rows."""
    A_bb: sparse.csc_array
    """The background, which buys nothing from the study."""
    h: Vector
    x: Vector
    s: Vector
    f: Vector
    reference_sign: Vector
    parts: tuple[Part, ...]
    """The study's block and the background's. The lines of the study already hold rows and
    columns of the stacked system, those of the background are shifted by `offset`."""
    target: int
    """The study process the result is about."""
    copies: Mapping[int, str] = field(default_factory=dict[int, str])
    """The study's own copies of background processes, made by its overrides: the id of the
    background process each one copies, by the copy's index."""
    used: Mapping[int, str] = field(default_factory=dict[int, str])
    """The processes of the studies the study uses, and their copies: the name of the study each
    one belongs to, by its index. Example: `dyes` for `dyes:reactive-dye`."""

    @cached_property
    def A(self) -> sparse.csc_array:
        """The stacked matrix, built the first time it is asked for and canonical, so that
        calc reads it as it is. It is the background's own matrix when there is no study."""
        if self.A_ss.shape[0] == 0:
            return as_matrix(self.A_bb, copy=False)
        stacked = sparse.block_array([[self.A_ss, None], [self.A_bs, self.A_bb]], format="csc")
        return as_matrix(stacked, copy=False)


class StackedIndex(Mapping[str, int]):
    """The rows of a calculation: the study's processes first, then the background's, shifted by
    the number of study processes. The background's own index is shared, so nothing the size of
    the background is built for one study.

    An id in `study` hides the same id in `background`: the study's purchases from a process it
    overrides go to the row of its copy. `shift` is the number of the study's rows, when
    `study` holds such ids beside its own, or when it is only a part of the study's processes.
    The length is the number of rows of the calculation.
    """

    def __init__(
        self,
        study: Mapping[str, int],
        background: Mapping[str, int],
        *,
        shift: int | None = None,
    ) -> None:
        self._study = study
        self._background = background
        self._shift = len(study) if shift is None else shift

    def __getitem__(self, key: str) -> int:
        row = self._study.get(key)
        return row if row is not None else self._background[key] + self._shift

    def __iter__(self) -> Iterator[str]:
        yield from self._study
        yield from self._background

    def __len__(self) -> int:
        return self._shift + len(self._background)


def assemble(
    processes: Sequence[Process],
    catalog: Catalog,
    index: Mapping[str, int],
    *,
    suppliers: Mapping[str, Mapping[str, int]] | None = None,
) -> Block:
    """Build the columns of `processes`. `index` gives the row of every process they may name.

    `suppliers` gives, for some processes by id, the rows their purchases go to instead of
    `index`: the study's copy of a background process buys from the background as it is, not
    from the study's other copies. Their own reference and own use rows still come from `index`.

    Amounts go through `Catalog.convert` into the flow's reference unit, and signs through
    `calc.matrix_entry`. Raises InvalidDataError for a supplier that is not in `index`.
    """
    a_rows: list[int] = []
    a_cols: list[int] = []
    a_values: list[float] = []
    a_amounts: list[float] = []
    a_signs: list[float] = []
    a_roles: list[int] = []
    a_suppliers: list[bool] = []
    a_ids: list[str] = []
    emissions: list[tuple[Exchange, int, float]] = []
    signs = np.empty(len(processes), dtype=np.float64)
    units: list[str] = []
    problems: list[Problem] = []
    for col, process in enumerate(processes):
        if index.get(process.id) != col:
            raise ValueError(f"process {process.id} must have row {col} in the index")
        treatment = catalog.is_treatment(process)
        bought = suppliers.get(process.id, index) if suppliers else index
        signs[col] = _unit_entry(Role.reference, treatment)
        units.append(_flow_unit(catalog, process.reference_exchange.flow_id))
        for exchange in process.exchanges:
            if exchange.link_status in (LinkStatus.cut_off, LinkStatus.data_gap):
                continue  # buys nothing: no supplier, so no place in A
            amount = catalog.convert(exchange)
            entry = matrix_entry(exchange.role, amount, treatment=treatment)
            if entry is None:
                continue
            if entry.matrix is Matrix.biosphere:
                emissions.append((exchange, col, entry.value))
                continue
            row = _row(exchange, process, index, bought, problems)
            if row is None:
                continue
            a_rows.append(row)
            a_cols.append(col)
            a_values.append(entry.value)
            a_amounts.append(amount)
            a_signs.append(_unit_entry(exchange.role, treatment))
            a_roles.append(ROLE_ORDER.index(exchange.role))
            a_suppliers.append(exchange.supplier_id is not None)
            a_ids.append(exchange.line_id)
    if problems:
        raise InvalidDataError(problems)
    flows = tuple(sorted({e.flow_id for e, _, _ in emissions}))
    flow_row = {flow: i for i, flow in enumerate(flows)}
    A = sparse.csc_array(
        (np.array(a_values, dtype=np.float64), (a_rows, a_cols)),
        shape=(len(index), len(processes)),
    )
    b_rows = [flow_row[e.flow_id] for e, _, _ in emissions]
    b_cols = [col for _, col, _ in emissions]
    b_values = np.array([value for _, _, value in emissions], dtype=np.float64)
    B = sparse.csc_array((b_values, (b_rows, b_cols)), shape=(len(flows), len(processes)))
    return Block(
        ids=tuple(p.id for p in processes),
        names=tuple(p.name for p in processes),
        flow_ids=tuple(p.reference_exchange.flow_id for p in processes),
        A=A,
        B=B,
        flows=flows,
        flow_units=tuple(_flow_unit(catalog, flow) for flow in flows),
        reference_sign=signs,
        units=tuple(units),
        lines=Lines(
            row=np.array(a_rows, dtype=np.intp),
            col=np.array(a_cols, dtype=np.intp),
            amount=np.array(a_amounts, dtype=np.float64),
            sign=np.array(a_signs, dtype=np.float64),
            role=np.array(a_roles, dtype=np.int8),
            has_supplier=np.array(a_suppliers, dtype=np.bool_),
            line_id=pa.array(a_ids, pa.string()),
        ),
        emissions=Emissions(
            row=np.array(b_rows, dtype=np.intp),
            col=np.array(b_cols, dtype=np.intp),
            amount=b_values,
            role=np.array([ROLE_ORDER.index(e.role) for e, _, _ in emissions], dtype=np.int8),
            line_id=pa.array([e.line_id for e, _, _ in emissions], pa.string()),
        ),
    )


def characterisation(flows: Sequence[str], method: Method, indicator: str) -> Vector:
    """The factor of each flow for one indicator. Call `require_global_factors` first.

    A flow with no factor for this indicator counts 0, whether the method covers it for other
    indicators or not: CO2 has no ozone depletion factor, so it adds nothing to that score. A
    flow that the method covers for no indicator at all is not an error here. The caller lists
    those with `flows_without_factor`, so a result can say which flows counted as 0. A study's own
    flows of that kind are errors of its validation until its `no_factor.csv` acknowledges them,
    so calc refuses them before this call.
    """
    factors = method.factors_by_indicator.get(indicator, {})
    return np.array([factors.get(flow, 0.0) for flow in flows], dtype=np.float64)


def require_global_factors(method: Method) -> None:
    """Raise for factors that hold for one location: they cannot be used as global ones."""
    located = [f for f in method.factors if f.location is not None]
    if located:
        raise InvalidDataError(
            Problem(
                "not-supported-yet",
                f.label,
                "factors for one location are not supported yet. Remove the location to use "
                "it everywhere, or remove the line.",
            )
            for f in located
        )


@contextmanager
def ids_in_errors(ids: Sequence[str]) -> Generator[None]:
    """Re-raise a calc error naming the processes by id instead of by matrix index."""
    try:
        yield
    except CalculationError as error:
        if not error.indices:
            raise
        shown = ", ".join(ids[i] for i in error.indices[:SHOWN])
        more = len(error.indices) - SHOWN
        message = f"{error.message}: {shown}" + (f" and {more} more" if more > 0 else "")
        if isinstance(error, ResidualError):
            raise ResidualError(message, (), error.residual) from error
        raise type(error)(message) from error


def matvec(matrix: sparse.csc_array | sparse.csr_array, vector: Vector) -> Vector:
    return np.asarray(matrix @ vector, dtype=np.float64)


def _unit_entry(role: Role, treatment: bool) -> float:
    entry = matrix_entry(role, 1.0, treatment=treatment)
    if entry is None:
        raise ValueError(f"role {role.value} has no matrix entry")
    return entry.value


def _flow_unit(catalog: Catalog, flow_id: str) -> str:
    flow = catalog.flow(flow_id)
    if flow is None:
        raise InvalidDataError([Problem("unknown-flow", f"flow {flow_id}", "is not defined")])
    return flow.unit


def _row(
    exchange: Exchange,
    process: Process,
    index: Mapping[str, int],
    suppliers: Mapping[str, int],
    problems: list[Problem],
) -> int | None:
    """The row of a technosphere line: the process's own for its reference and own use, else the
    supplier's. Validation checks that an own use line names the process itself."""
    if exchange.role in (Role.reference, Role.own_use):
        return index[process.id]
    supplier = exchange.supplier_id
    row = suppliers.get(supplier) if supplier is not None else None
    if row is None:
        problems.append(
            Problem(
                "supplier-not-found",
                exchange.label,
                f"supplier {supplier!r} of flow {exchange.flow_id!r} is not in this calculation",
            )
        )
        return None
    return row
