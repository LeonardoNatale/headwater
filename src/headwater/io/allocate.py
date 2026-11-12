"""Split each process that makes several products into one process per product, with the
allocation factors its source gives.

Example: a cotton gin takes 1 kg of seed cotton and 0.2 kWh, and makes 0.35 kg of fibre (line
p1) and 0.6 kg of seed (line p2). Its source gives the economic factors 0.9 and 0.1. The gin
becomes two processes:

| Process | Makes | Takes |
|---|---|---|
| `gin:p1` | 0.35 kg fibre | 0.9 kg seed cotton, 0.18 kWh |
| `gin:p2` | 0.6 kg seed | 0.1 kg seed cotton, 0.02 kWh |

Each keeps its product line as it is, and every other line scaled by its product's factor, or by
the factor the source gives for that line and product. A source that gives a factor for every
line and product, as openLCA's causal allocation does, needs no factor for the products. A line
that bought from the gin by id buys from the split that makes its flow. The factors of a process
must sum to 1 within 1e-9. A process whose source gives no factors, a line with no factor, or
factors that do not sum to 1, is not split: it is an open issue with the reason, never a guess.
ecoinvent's datasets each make one product, so nothing is split.

Own use of a product is bought from the split that makes it. Example: a CHP plant makes
electricity (line p1, factor 0.6) and heat (line p2, factor 0.4), and uses 0.05 kWh of its own
electricity. `chp:p1` keeps 0.03 kWh as its own use, and `chp:p2` buys 0.02 kWh from `chp:p1`.
"""

import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, cast

import pyarrow as pa
import pyarrow.compute as pc

from headwater.link.mapping import SUPPLIER_KEY
from headwater.model import InvalidDataError, Issue, IssueStatus, Problem, Role

compute: Any = pc

TOLERANCE = 1e-9
"""Largest distance from 1 of the sum of a process's factors."""


@dataclass(frozen=True)
class Allocated:
    processes: pa.Table
    """The processes, with each split one replaced by one process per product."""
    lines: pa.Table
    """The lines, with those of each split process scaled into its splits."""
    issues: tuple[Issue, ...]
    """One open issue per process that could not be split, or whose factors are wrong."""
    problems: tuple[Problem, ...]
    """The reason of each open issue."""


type _Products = dict[str, list[tuple[str, str]]]
"""The products of each process: its reference lines, as (line id, flow id), in line order."""

type _Factors = dict[str, dict[tuple[str, str | None], float]]
"""The factors of each process, by product line and other line, as in `ALLOCATION`: the line is
None for the product's factor for every other line."""


def allocate(processes: pa.Table, lines: pa.Table, factors: pa.Table) -> Allocated:
    """Split every process with several `reference` lines by `factors`, an `ALLOCATION` table.

    Raises `InvalidDataError` for a factor that names no process, no product or no line of its
    process, or that is given twice: that is a mistake of the reader, not of the data.
    """
    references = lines.select(["process_id", "line_id", "role", "flow_id"])
    references = references.filter(compute.equal(references.column("role"), "reference"))
    makers = compute.count_distinct(references.column("process_id")).as_py()
    if not factors.num_rows and makers == references.num_rows:
        return Allocated(processes, lines, (), ())
    products: _Products = defaultdict(list)
    for r in references.to_pylist():
        products[r["process_id"]].append((r["line_id"], r["flow_id"]))
    given = _factors(factors, processes, lines, products)
    several = {p for p, made in products.items() if len(made) > 1}
    others, uses = _lines_of(lines, several)
    issues: list[Issue] = []
    problems: list[Problem] = []
    split: set[str] = set()
    for process_id in sorted(several | set(given)):
        made, own = products[process_id], given.get(process_id, {})
        if len(made) == 1:
            reason = _why_not_whole(made[0][0], own)
        else:
            reason = _why_not_split(made, own, others[process_id]) or _why_not_own(
                made, uses[process_id]
            )
        if reason is None:
            if len(made) > 1:
                split.add(process_id)
            continue
        line, flow = made[0]
        issues.append(
            Issue(process_id=process_id, line_id=line, flow_id=flow, status=IssueStatus.open)
        )
        problems.append(
            Problem(
                "not-allocated",
                f"process {process_id}",
                f"{reason}. It is not split: an open issue.",
            )
        )
    if not split:
        return Allocated(processes, lines, tuple(issues), tuple(problems))
    ids = pa.array(sorted(split), pa.string())
    return Allocated(
        _split_processes(processes, ids, products),
        _split_lines(lines, ids, products, given),
        tuple(issues),
        tuple(problems),
    )


def _factors(
    factors: pa.Table, processes: pa.Table, lines: pa.Table, products: _Products
) -> _Factors:
    """The factors by process. Checks every row names a process, one of its products, and one of
    its other lines, once."""
    found: _Factors = defaultdict(dict)
    if not factors.num_rows:
        return found
    known = set(processes.column("id").to_pylist())
    named = pa.array(sorted(set(factors.column("process_id").to_pylist())), pa.string())
    of = lines.select(["process_id", "line_id"])
    of = of.filter(compute.is_in(of.column("process_id"), value_set=named))
    line_ids: dict[str, set[str]] = defaultdict(set)
    for r in of.to_pylist():
        line_ids[r["process_id"]].add(r["line_id"])
    problems: list[Problem] = []
    for r in factors.to_pylist():
        process_id: str = r["process_id"]
        product: str = r["product"]
        line_id: str | None = r["line_id"]
        item = f"allocation of {process_id} product {product}"
        if line_id is not None:
            item += f" line {line_id}"
        own = {line for line, _ in products.get(process_id, [])}
        if process_id not in known:
            problems.append(Problem("unknown-process", item, f"{process_id} is not a process"))
        elif product not in own:
            problems.append(
                Problem("unknown-product", item, f"line {product} is not a product of the process")
            )
        elif line_id is not None and (line_id not in line_ids[process_id] or line_id in own):
            problems.append(
                Problem(
                    "unknown-line",
                    item,
                    f"line {line_id} is not a line of the process other than its products",
                )
            )
        elif (product, line_id) in found[process_id]:
            problems.append(Problem("duplicate-factor", item, "is given twice"))
        else:
            found[process_id][product, line_id] = r["factor"]
    if problems:
        raise InvalidDataError(problems)
    return found


def _why_not_whole(product: str, factors: dict[tuple[str, str | None], float]) -> str | None:
    """Why the factors of a process that makes one product are wrong, or None."""
    if any(line is not None for _, line in factors):
        return "makes one product, but the source gives it factors for single lines"
    given = factors.get((product, None), 1.0)
    if abs(given - 1.0) > TOLERANCE:
        return f"makes one product, but the source gives it the factor {given:.12g}"
    return None


def _lines_of(
    lines: pa.Table, processes: set[str]
) -> tuple[dict[str, list[str]], dict[str, list[tuple[str, str]]]]:
    """The lines of each of these processes that are not its products, and its own uses as
    (line id, flow id)."""
    of = lines.select(["process_id", "line_id", "role", "flow_id"])
    ids = pa.array(sorted(processes), pa.string())
    of = of.filter(compute.is_in(of.column("process_id"), value_set=ids))
    others: dict[str, list[str]] = defaultdict(list)
    uses: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for r in of.to_pylist():
        if r["role"] != Role.reference.value:
            others[r["process_id"]].append(r["line_id"])
        if r["role"] == Role.own_use.value:
            uses[r["process_id"]].append((r["line_id"], r["flow_id"]))
    return others, uses


def _why_not_split(
    made: Sequence[tuple[str, str]],
    factors: dict[tuple[str, str | None], float],
    others: Sequence[str],
) -> str | None:
    """Why the factors cannot split a process that makes several products, or None. Each of its
    other lines needs a factor from each product: its own, or the product's."""
    lines = ", ".join(line for line, _ in made)
    products = f"makes {len(made)} products (lines {lines})"
    flows = [flow for _, flow in made]
    twice = sorted({flow for flow in flows if flows.count(flow) > 1})
    if twice:
        return (
            f"{products}, and makes {', '.join(twice)} on more than one of them, so a line that "
            "buys it could not tell which split to buy from"
        )
    if not factors:
        return f"{products}, and the source gives no allocation factor"
    missing = [
        product
        for product, _ in made
        if (product, None) not in factors and any((product, o) not in factors for o in others)
    ]
    if missing:
        return f"{products}, and has no factor for {', '.join(missing)}"
    outside = [f for f in factors.values() if not (0.0 <= f <= 1.0) or not math.isfinite(f)]
    if outside:
        return f"has factors outside 0 and 1: {', '.join(f'{f:.12g}' for f in outside)}"
    whole = [None] if all((product, None) in factors for product, _ in made) else []
    for other in [*whole, *sorted({line for _, line in factors if line is not None})]:
        total = math.fsum(_factor(factors, p, other) for p, _ in made)
        if abs(total - 1.0) > TOLERANCE:
            which = f"lines {lines}" if other is None else f"line {other}"
            return f"has factors for {which} that sum to {total:.12g}, not 1"
    return None


def _why_not_own(made: Sequence[tuple[str, str]], uses: Sequence[tuple[str, str]]) -> str | None:
    """Why a process cannot buy what it uses of its own from its splits, or None: each flow it
    uses must be the flow of one of its products."""
    flows = {flow for _, flow in made}
    for line, flow in uses:
        if flow not in flows:
            return f"uses {flow} (line {line}) as its own product, but makes no {flow}"
    return None


def _factor(factors: dict[tuple[str, str | None], float], product: str, line: str | None) -> float:
    """The product's factor for a line: its own, else the product's."""
    own = factors.get((product, line))
    return own if own is not None else factors[product, None]


def _split_id(process_id: str, product_line: str) -> str:
    """The id of the process that makes one product of a split process: `gin:p1`."""
    return f"{process_id}:{product_line}"


def _split_processes(processes: pa.Table, ids: Any, products: _Products) -> pa.Table:
    mask = compute.is_in(processes.column("id"), value_set=ids)
    rows = [
        {**row, "id": _split_id(row["id"], line)}
        for row in processes.filter(mask).to_pylist()
        for line, _ in products[row["id"]]
    ]
    added = pa.Table.from_pylist(rows, schema=processes.schema)
    return pa.concat_tables([processes.filter(compute.invert(mask)), added]).combine_chunks()


def _split_lines(lines: pa.Table, ids: Any, products: _Products, given: _Factors) -> pa.Table:
    mask = compute.is_in(lines.column("process_id"), value_set=ids)
    rows: list[dict[str, Any]] = []
    for row in lines.filter(mask).to_pylist():
        process_id, line_id = row["process_id"], row["line_id"]
        made = products[process_id]
        own = {line for line, _ in made}
        if row["role"] == Role.own_use.value:
            # bought by id from the process, which `_redirect` turns into its split
            row = {**row, **dict.fromkeys(SUPPLIER_KEY), "supplier_id": process_id}
        for product, flow in made:
            split = _split_id(process_id, product)
            if line_id == product:
                rows.append({**row, "process_id": split})
            elif line_id not in own:
                scaled = _scaled(row, split, _factor(given[process_id], product, line_id))
                if scaled["role"] == Role.own_use.value and row["flow_id"] != flow:
                    scaled["role"] = Role.input.value  # another split makes it
                rows.append(scaled)
    added = pa.Table.from_pylist(rows, schema=lines.schema)
    kept = pa.concat_tables([lines.filter(compute.invert(mask)), added]).combine_chunks()
    return _redirect(kept, ids, products)


def _scaled(row: dict[str, Any], process_id: str, factor: float) -> dict[str, Any]:
    """A line of a split process, its amount and distribution scaled by `factor`."""
    return {
        **row,
        "process_id": process_id,
        "amount": row["amount"] * factor,
        "uncertainty": _scaled_uncertainty(row["uncertainty"], factor),
    }


def _scaled_uncertainty(u: dict[str, Any] | None, factor: float) -> dict[str, Any] | None:
    """The distribution of an amount multiplied by `factor`. None for a factor of 0: the amount
    is then exactly 0."""
    if u is None or factor == 0.0:
        return None
    scaled = {k: (u[k] * factor if u[k] is not None else None) for k in ("minimum", "maximum")}
    if u["distribution"] == "lognormal":
        scaled["loc"] = u["loc"] + math.log(factor)  # the mean of ln|x|
    else:
        scaled["loc"] = u["loc"] * factor if u["loc"] is not None else None
    if u["distribution"] == "normal":
        scaled["scale"] = u["scale"] * factor
    return {**u, **scaled}


def _redirect(lines: pa.Table, ids: Any, products: _Products) -> pa.Table:
    """Lines that buy from a split process by id buy from the split that makes their flow."""
    supplier = lines.column("supplier_id")
    affected = compute.fill_null(compute.is_in(supplier, value_set=ids), False)
    if not compute.any(affected).as_py():
        return lines
    makes = {
        (process_id, flow): _split_id(process_id, line)
        for process_id in cast(list[str], ids.to_pylist())
        for line, flow in products[process_id]
    }
    pairs = lines.filter(affected).select(["supplier_id", "flow_id"]).to_pylist()
    redirected = [makes.get((r["supplier_id"], r["flow_id"]), r["supplier_id"]) for r in pairs]
    column = compute.replace_with_mask(
        supplier.combine_chunks(), affected.combine_chunks(), pa.array(redirected, pa.string())
    )
    return lines.set_column(lines.column_names.index("supplier_id"), "supplier_id", column)
