"""Matrices from a release: DuckDB maps ids to row and column numbers and applies the role signs.

The toy, as a release: `toy:electricity-production` is column 0, `toy:fabric-production` column
1 and `toy:incineration-textile-waste` column 2 (ids sorted). Fabric production's line e02 buys
3 kWh of electricity, so A holds -3 in row 0, column 1. The signs come from a small table of
role entries that `calc.role_entries` builds with `calc.matrix_entry`: this module decides none.

DuckDB reads the release's Parquet files, joins the lines with the process index, converts each
amount into the unit of its flow, and returns arrays through Arrow. No record is made for any line.
A and B are built from the same rows that `sensitivity` ranks later: their amounts, signs, roles and
line ids come back in the same query, ordered by process and line id so that ties between lines fall
the same way every time, and no later query is needed. A line that needs a conversion between
quantities (a flow counted in m3 and bought in kg) is rare, and goes through `Catalog.convert`, the
one place that holds that rule.
"""

from collections.abc import Callable, Iterable, Sequence
from contextlib import closing
from dataclasses import dataclass
from typing import Any, cast

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
from numpy.typing import NDArray
from scipy import sparse

from headwater.calc import Matrix, RoleEntry, role_entries
from headwater.calc.arrays import Vector
from headwater.model import (
    Catalog,
    Exchange,
    InvalidDataError,
    LinkStatus,
    Method,
    Problem,
    Process,
    Role,
    Units,
)
from headwater.query._system import ROLE_ORDER, Assembled, Block, Emissions, Lines
from headwater.query.result import NotCharacterised
from headwater.release import Release
from headwater.release.schema import read_flow_properties, read_flows, read_units
from headwater.study import ProcessFolder

SHOWN_PROBLEMS = 50
"""The most problems one error lists. The rest are counted."""

_NO_ENTRY = "none"
_SKIPPED = ", ".join(f"'{s.value}'" for s in (LinkStatus.cut_off, LinkStatus.data_gap))
_OWN_ROW = ", ".join(f"'{r.value}'" for r in (Role.reference, Role.own_use))


def assemble_release(release: Release, method: Method) -> Assembled:
    """Build A and B from the release's Parquet files.

    Raises InvalidDataError naming each line that has no matrix entry: a line of a process that
    is not in the release's index, or a role that has no sign in its process (see
    `_Query._check_lines`). Skips `cut_off` and `data_gap` lines: they have no supplier and buy
    nothing.
    """
    with closing(_Query(release)) as query:
        query.raise_for_problems()
        index = query.index()
        technosphere = query.table(_TECHNOSPHERE)
        flows = query.flows()
        flow_units = query.flow_units()
        biosphere = query.table(_BIOSPHERE)
        folder = _folder(release, query)
        uncovered = _uncovered(query, method)
    n = len(index.ids)
    a = {name: _numpy(technosphere, name) for name in ("row", "col", "value", "amount", "sign")}
    b = {name: _numpy(biosphere, name) for name in ("row", "col", "value")}
    A = sparse.csc_array((a["value"], (a["row"], a["col"])), shape=(n, n))
    B = sparse.csc_array((b["value"], (b["row"], b["col"])), shape=(len(flows), n))
    block = Block(
        ids=index.ids,
        names=index.names,
        flow_ids=index.flow_ids,
        A=A,
        B=B,
        flows=flows,
        flow_units=flow_units,
        reference_sign=index.reference_sign,
        units=index.units,
        lines=Lines(
            row=a["row"].astype(np.intp),
            col=a["col"].astype(np.intp),
            amount=a["amount"],
            sign=a["sign"],
            role=_role_codes(technosphere),
            has_supplier=_numpy(technosphere, "has_supplier").astype(np.bool_),
            line_id=_strings_array(technosphere, "line_id"),
        ),
        emissions=Emissions(
            row=b["row"].astype(np.intp),
            col=b["col"].astype(np.intp),
            amount=b["value"],  # a biosphere entry is its amount: every sign is +1
            role=_role_codes(biosphere),
            line_id=_strings_array(biosphere, "line_id"),
        ),
    )
    return Assembled(block, folder, uncovered)


# --- the queries ----------------------------------------------------------------------------

# Rows and columns of A. A reference and an own use line sit in the process's own row, every
# other line in its supplier's. A cut-off or data-gap line has no supplier and is left out.
_TECHNOSPHERE = f"""
SELECT CASE WHEN role IN ({_OWN_ROW}) THEN col ELSE supplier_idx END AS row,
       col, sign * amount AS value, amount, sign, role, supplier_id IS NOT NULL AS has_supplier,
       line_id
FROM line
WHERE matrix = '{Matrix.technosphere.value}'
  AND coalesce(link_status, '') NOT IN ({_SKIPPED})
ORDER BY col, line_id
"""

_BIOSPHERE = f"""
SELECT f.idx AS row, l.col, l.sign * l.amount AS value, l.role, l.line_id
FROM line l JOIN flow_idx f ON f.flow_id = l.flow_id
WHERE l.matrix = '{Matrix.biosphere.value}'
ORDER BY l.col, l.line_id
"""


@dataclass(frozen=True)
class _Index:
    """The processes, in the order of the columns of A."""

    ids: tuple[str, ...]
    names: tuple[str, ...]
    flow_ids: tuple[str, ...]
    units: tuple[str, ...]
    reference_sign: Vector


class _Query:
    """A DuckDB connection over a release, with the processes numbered and every line joined to
    its process, flow, unit, supplier and role entry (temporary tables `proc` and `line`).

    `line.amount` is the amount in the unit of the line's flow. A, B and the lines that
    `sensitivity` ranks all read it, in one query, so they cannot disagree.
    """

    def __init__(self, release: Release, *, check: bool = True) -> None:
        self.release = release
        self.con = release.connect()
        try:
            self._register()
            self._number_processes()
            self._join_lines()
            self.problems, self.total = self._check_lines() if check else ([], 0)
            """Every problem found. `problems` holds at most `SHOWN_PROBLEMS` of the checks."""
        except BaseException:
            self.con.close()
            raise

    def close(self) -> None:
        self.con.close()

    def raise_for_problems(self) -> None:
        if not self.total:
            return
        shown = self.problems[:SHOWN_PROBLEMS]
        if self.total > len(shown):
            more = self.total - len(shown)
            shown.append(
                Problem(
                    "more-problems", f"release {self.release.name}", f"and {more} more, not shown"
                )
            )
        raise InvalidDataError(shown)

    # --- setup ---

    def _register(self) -> None:
        entries = role_entries()
        self.con.register("role_entry", _role_table(entries))
        self.reference_sign = {e.treatment: e.sign for e in entries if e.role is Role.reference}

    def _number_processes(self) -> None:
        self.con.execute(
            """
            CREATE TEMP TABLE proc AS
            SELECT p.id, row_number() OVER (ORDER BY p.id) - 1 AS idx, p.name,
                   p.reference_flow_id AS flow_id, f.unit AS unit, f.kind = 'waste' AS treatment
            FROM processes p JOIN flows f ON f.id = p.reference_flow_id
            """
        )

    def _join_lines(self) -> None:
        """Build `line`, then convert the amounts that need a unit of another quantity."""
        self.con.execute(
            """
            CREATE TEMP TABLE line AS
            SELECT e.process_id, e.line_id, e.role, e.flow_id, e.supplier_id, e.link_status,
                   e.unit AS line_unit, e.amount AS raw_amount, p.idx AS col, p.treatment,
                   f.kind AS flow_kind, f.unit AS flow_unit,
                   CASE WHEN e.unit = f.unit THEN e.amount
                        WHEN u.quantity = fu.quantity
                            THEN e.amount * u.factor_to_reference / fu.factor_to_reference
                   END AS amount,
                   s.idx AS supplier_idx, s.flow_id AS supplier_flow_id,
                   r.matrix, r.sign
            FROM exchanges e
            LEFT JOIN proc p ON p.id = e.process_id
            LEFT JOIN flows f ON f.id = e.flow_id
            LEFT JOIN units u ON u.unit = e.unit
            LEFT JOIN units fu ON fu.unit = f.unit
            LEFT JOIN proc s ON s.id = e.supplier_id
            LEFT JOIN role_entry r ON r.role = e.role AND r.treatment = p.treatment
            """
        )
        self.con.execute(
            f"""
            CREATE TEMP TABLE flow_idx AS
            SELECT flow_id, row_number() OVER (ORDER BY flow_id) - 1 AS idx
            FROM (SELECT DISTINCT flow_id FROM line WHERE matrix = '{Matrix.biosphere.value}')
            """
        )
        self._convert_between_quantities()

    def _convert_between_quantities(self) -> None:
        """Convert the lines in a unit of another quantity than their flow's, through the flow's
        properties. `build` refused a release with a line that does not convert."""
        needs = self._table(
            "SELECT process_id, line_id, role, flow_id, raw_amount, line_unit FROM line "
            "WHERE amount IS NULL AND flow_kind IS NOT NULL ORDER BY process_id, line_id"
        )
        if not needs.num_rows:
            return
        wanted = "(SELECT DISTINCT flow_id FROM line WHERE amount IS NULL)"
        catalog = Catalog(
            flows=read_flows(self._table(f"SELECT * FROM flows WHERE id IN {wanted}")),
            properties=read_flow_properties(
                self._table(f"SELECT * FROM flow_properties WHERE flow_id IN {wanted}")
            ),
            units=Units(read_units(self._table("SELECT * FROM units"))),
        )
        converted: list[tuple[str, str, float]] = []
        for row in needs.to_pylist():
            line = Exchange(
                process_id=row["process_id"],
                line_id=row["line_id"],
                role=Role(row["role"]),
                flow_id=row["flow_id"],
                amount=row["raw_amount"],
                unit=row["line_unit"],
            )
            converted.append((line.process_id, line.line_id, catalog.convert(line)))
        self.con.register(
            "converted",
            pa.Table.from_pydict(
                {
                    "process_id": [c[0] for c in converted],
                    "line_id": [c[1] for c in converted],
                    "amount": pa.array([c[2] for c in converted], pa.float64()),
                }
            ),
        )
        self.con.execute(
            "UPDATE line SET amount = converted.amount FROM converted "
            "WHERE line.process_id = converted.process_id AND line.line_id = converted.line_id"
        )

    # --- lines with no matrix entry ---

    def _check_lines(self) -> tuple[list[Problem], int]:
        """The lines that have no matrix entry, at most `SHOWN_PROBLEMS` per check, and how many
        there are.

        - `unknown-process`: the line's process is not in `proc`. `build` refuses such a line of
          a release; this guards the join of this module, which would leave the line out of A and
          B without an error.
        - `no-sign`: the role has no sign in the process that holds it. In a release only an own
          use in a treatment can. `build` refuses it now, but an older release can hold one.
        """
        problems: list[Problem] = []
        total = 0

        def check(
            code: str, where: str, columns: str, message: Callable[[dict[str, Any]], str]
        ) -> None:
            nonlocal total
            found = self._count(where)
            total += found
            if found:
                for row in self._rows(columns, where):
                    item = f"{row['process_id']} line {row['line_id']}"
                    problems.append(Problem(code, item, message(row)))

        check(
            "unknown-process",
            "col IS NULL",
            "role",
            lambda r: (
                "is a line of a process that has no row in processes, or whose reference "
                "flow is not in flows"
            ),
        )
        check(
            "no-sign",
            "col IS NOT NULL AND matrix IS NULL",
            "role, treatment",
            lambda r: (
                f"role {r['role']!r} has no sign in a "
                f"{'treatment' if r['treatment'] else 'process'}"
            ),
        )
        return problems, total

    def _count(self, where: str) -> int:
        # fetchall, not fetchone: duckdb 2 refuses `sql` on a connection whose last `execute` has
        # rows left to fetch.
        [(found,)] = self.con.execute(f"SELECT count(*) FROM line l WHERE {where}").fetchall()
        return int(found)

    def _rows(self, columns: str, where: str) -> list[dict[str, Any]]:
        table = self._table(
            f"SELECT l.process_id, l.line_id, {columns} FROM line l WHERE {where} "
            f"ORDER BY l.process_id, l.line_id LIMIT {SHOWN_PROBLEMS}"
        )
        return table.to_pylist()

    # --- results ---

    def _table(self, sql: str) -> pa.Table:
        # `sql`, not `execute`: duckdb 2 warns that Arrow from `execute` copies the rows.
        return self.con.sql(sql).to_arrow_table()

    def index(self) -> _Index:
        table = self._table("SELECT id, name, flow_id, unit, treatment FROM proc ORDER BY idx")
        treatment = _column(table, "treatment").to_numpy(zero_copy_only=False).astype(bool)
        sign = np.where(treatment, self.reference_sign[True], self.reference_sign[False]).astype(
            np.float64
        )
        return _Index(
            ids=_strings(table, "id"),
            names=_strings(table, "name"),
            flow_ids=_strings(table, "flow_id"),
            units=_strings(table, "unit"),
            reference_sign=sign,
        )

    def flows(self) -> tuple[str, ...]:
        """The elementary flows of B, sorted."""
        return _strings(self._table("SELECT flow_id FROM flow_idx ORDER BY idx"), "flow_id")

    def flow_units(self) -> tuple[str, ...]:
        """The reference unit of each flow of `flows`."""
        return _strings(
            self._table(
                "SELECT f.unit FROM flow_idx i JOIN flows f ON f.id = i.flow_id ORDER BY i.idx"
            ),
            "unit",
        )

    def arrays(self, sql: str, names: Sequence[str]) -> dict[str, NDArray[Any]]:
        """Columns of a query as NumPy arrays. A column with a null is an error, not a hidden
        value: DuckDB would otherwise hand SciPy a masked array and the hidden number."""
        table = self._table(sql)
        return {name: _numpy(table, name) for name in names}

    def table(self, sql: str) -> pa.Table:
        return self._table(sql)


def _role_table(entries: Iterable[RoleEntry]) -> pa.Table:
    entries = tuple(entries)
    return pa.Table.from_pydict(
        {
            "role": [e.role.value for e in entries],
            "treatment": [e.treatment for e in entries],
            "matrix": [e.matrix.value if e.matrix else _NO_ENTRY for e in entries],
            "sign": pa.array([e.sign for e in entries], pa.float64()),
        }
    )


def _column(table: pa.Table, name: str) -> "pa.ChunkedArray[Any]":
    column = table.column(name)
    if column.null_count:
        raise InvalidDataError(
            [
                Problem(
                    "release-incomplete",
                    f"column {name}",
                    f"has {column.null_count} empty values where the release needs a value. "
                    "A line points at a process, flow or unit the release does not hold: "
                    "check it with release.verify() and freeze it again.",
                )
            ]
        )
    return column


def _numpy(table: pa.Table, name: str) -> NDArray[Any]:
    """A column as a NumPy array. A column with a null is an error, not a hidden value: DuckDB
    would otherwise hand SciPy a masked array and the hidden number."""
    return _column(table, name).to_numpy(zero_copy_only=False)


def _strings_array(table: pa.Table, name: str) -> pa.StringArray:
    return cast(pa.StringArray, _column(table, name).combine_chunks())


def _role_codes(table: pa.Table) -> NDArray[np.int8]:
    """The role of each line as an index into `ROLE_ORDER`."""
    known = pa.array([role.value for role in ROLE_ORDER], pa.string())
    codes: pa.Int32Array = pc.index_in(  # pyright: ignore[reportUnknownMemberType, reportAssignmentType]
        _column(table, "role").combine_chunks(), value_set=known
    )
    if codes.null_count:
        raise InvalidDataError(
            [Problem("unknown-role", "a line of the release", "has a role headwater does not know")]
        )
    return codes.to_numpy(zero_copy_only=False).astype(np.int8)


def _strings(table: pa.Table, name: str) -> tuple[str, ...]:
    return tuple(cast(list[str], _column(table, name).to_pylist()))


def _folder(release: Release, query: _Query) -> ProcessFolder:
    """What study validation needs: every process with its reference line only."""
    table = query.table(
        f"""
        SELECT p.id, p.name, p.location, p.time_start, p.time_end, p.kind, p.source_ref,
               e.line_id, e.flow_id, e.amount, e.unit
        FROM processes p
        JOIN exchanges e ON e.process_id = p.id AND e.role = '{Role.reference.value}'
        ORDER BY p.id
        """
    )
    processes = tuple(
        Process.model_validate(
            {
                "id": row["id"],
                "name": row["name"],
                "location": row["location"],
                "time_start": row["time_start"],
                "time_end": row["time_end"],
                "kind": row["kind"],
                "source_ref": row["source_ref"],
                "exchanges": [
                    {
                        "process_id": row["id"],
                        "line_id": row["line_id"],
                        "role": Role.reference,
                        "flow_id": row["flow_id"],
                        "amount": row["amount"],
                        "unit": row["unit"],
                    }
                ],
            }
        )
        for row in table.to_pylist()
    )
    return ProcessFolder(
        path=release.path,
        namespace=release.name,
        processes=processes,
        flows=release.flows(),
        flow_properties=release.flow_properties(),
        units=tuple(release.units()),
    )


def _uncovered(query: _Query, method: Method) -> tuple[NotCharacterised, ...]:
    """Elementary flows that no factor of the method covers, with every process that uses them."""
    covered = pa.array(sorted(method.characterised_flow_ids), pa.string())
    query.con.register("characterised", pa.Table.from_pydict({"flow_id": covered}))
    table = query.table(
        """
        SELECT f.id AS flow_id, f.name, list(DISTINCT e.process_id) AS processes
        FROM exchanges e JOIN flows f ON f.id = e.flow_id
        WHERE f.kind = 'elementary' AND f.id NOT IN (SELECT flow_id FROM characterised)
        GROUP BY f.id, f.name
        ORDER BY f.id
        """
    )
    return tuple(
        NotCharacterised(row["flow_id"], row["name"], tuple(sorted(row["processes"])))
        for row in table.to_pylist()
    )
