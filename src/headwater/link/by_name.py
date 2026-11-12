"""Link lines to suppliers by name, the way SimaPro and ecoSpold1 name what a process buys.

A line names its supplier by what it gives of the supplier's process name, reference product,
location and reference unit. Example: an ecoSpold1 line buys `electricity, medium voltage`, at
`CH`, in `kWh`: the supplier is the one process that makes `electricity, medium voltage` at `CH`
with `kWh` as the unit of its reference line.

- A row of the supplier mapping decides first: buy from a process, or a data gap.
- A process fits when it matches every field the line gives and, when the units are known, makes
  its product in a unit of the same quantity as the line's: natural gas bought in kg does not fit
  a process that makes it in m3.
- Exactly one process fits: the line is linked.
- None matches: an open issue, `no-supplier`, with processes a person might have meant. They are
  only to read, never applied.
- Several match: an open issue, `ambiguous-supplier`, naming them.
- Some match, but only in other quantities: an open issue, `supplier-other-quantity`, naming the
  quantities.

Matching is exact: no case folding, no fuzzy match, no geography fallback.
"""

import difflib
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, fields
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc

from headwater.link.lines import LINE_COLUMNS, Linked, Outcomes, buys, issue, label, unlinked
from headwater.link.mapping import SUPPLIER_KEY, SupplierKey, SupplierMapping, SupplierRow
from headwater.model import Flow, InvalidDataError, IssueStatus, Problem, Units
from headwater.release import Release, ReleaseKind

compute: Any = pc

_SHOWN = 5
"""Processes listed in a problem, before the rest are counted."""


@dataclass(frozen=True)
class Supplier:
    """A process a line can buy from, by its name, the name of the product it makes, where,
    and the unit of its reference line."""

    id: str
    name: str
    product: str
    location: str
    unit: str

    @property
    def label(self) -> str:
        return f"{self.id} ({self.name} | {self.product} | {self.location} | {self.unit})"

    def fits(self, key: SupplierKey) -> bool:
        """Every field the key gives is this supplier's, exactly."""
        given = ((getattr(key, f.name), getattr(self, f.name)) for f in fields(key))
        return all(value is None or value == own for value, own in given)


class Suppliers:
    """The processes lines can buy from, looked up by name or by product. With the units of the
    source, a line's unit must be of the quantity of the supplier's."""

    def __init__(self, suppliers: Iterable[Supplier], units: Units | None = None) -> None:
        self._units = units
        self._all = tuple(sorted(suppliers, key=lambda s: s.id))
        self._suggested: dict[SupplierKey, tuple[str, ...]] = {}
        self._ids = frozenset(s.id for s in self._all)
        self._by_product: dict[str, list[Supplier]] = defaultdict(list)
        self._by_name: dict[str, list[Supplier]] = defaultdict(list)
        for supplier in self._all:
            self._by_product[supplier.product].append(supplier)
            self._by_name[supplier.name].append(supplier)

    @classmethod
    def of_tables(
        cls, processes: pa.Table, lines: pa.Table, flows: Sequence[Flow], units: Units | None = None
    ) -> "Suppliers":
        """The processes of a source: one supplier per reference line, so a process that makes
        two products and was not split offers both. A line whose flow is not a flow offers
        nothing: freezing refuses it."""
        names = {f.id: f.name for f in flows}
        process = {r["id"]: r for r in processes.select(["id", "name", "location"]).to_pylist()}
        references = lines.select(["process_id", "role", "flow_id", "unit"])
        references = references.filter(compute.equal(references.column("role"), "reference"))
        found: list[Supplier] = []
        for r in references.to_pylist():
            made_by = process.get(r["process_id"])
            product = names.get(r["flow_id"])
            if made_by is None or product is None:
                continue
            found.append(
                Supplier(
                    id=r["process_id"],
                    name=made_by["name"],
                    product=product,
                    location=made_by["location"],
                    unit=r["unit"],
                )
            )
        return cls(found, units)

    @classmethod
    def of_release(cls, release: Release) -> "Suppliers":
        """The processes of a source release, read with DuckDB, with no record per line."""
        if release.kind is not ReleaseKind.source:
            raise InvalidDataError(
                [
                    Problem(
                        "indicators-release",
                        f"release {release.name}",
                        "holds scores only, with no processes to link to. Use its source "
                        f"release {release.parent}.",
                    )
                ]
            )
        con = release.connect()
        try:
            rows = con.sql(
                "SELECT p.id, p.name, f.name, p.location, e.unit FROM processes p "
                "JOIN exchanges e ON e.process_id = p.id AND e.role = 'reference' "
                "JOIN flows f ON f.id = e.flow_id ORDER BY p.id"
            ).fetchall()
        finally:
            con.close()
        return cls((Supplier(*row) for row in rows), release.units())

    def __contains__(self, process_id: object) -> bool:
        return process_id in self._ids

    def quantity_of(self, unit: str) -> str | None:
        """The quantity of a unit, or None when the units are not known."""
        if self._units is None or unit not in self._units:
            return None
        return self._units.quantity_of(unit)

    def find(self, key: SupplierKey) -> tuple[Supplier, ...]:
        """Every supplier that fits the key exactly."""
        if key.product is not None:
            found = self._by_product.get(key.product, [])
        elif key.name is not None:
            found = self._by_name.get(key.name, [])
        else:
            found = list(self._all)
        return tuple(s for s in found if s.fits(key))

    def suggestions(self, key: SupplierKey) -> tuple[str, ...]:
        """Processes a person might have meant: the same product or name elsewhere, then close
        product names. Only to read. Worked out once per key."""
        if key in self._suggested:
            return self._suggested[key]
        found: dict[str, Supplier] = {}
        for text, index in ((key.product, self._by_product), (key.name, self._by_name)):
            if text is None:
                continue
            for supplier in index.get(text, []):
                found.setdefault(supplier.id, supplier)
            for close in difflib.get_close_matches(text, list(index), n=_SHOWN, cutoff=0.8):
                for supplier in index[close][:1]:
                    found.setdefault(supplier.id, supplier)
        self._suggested[key] = tuple(s.label for s in list(found.values())[:_SHOWN])
        return self._suggested[key]


def link_by_name(
    lines: pa.Table, suppliers: Suppliers, *, decided: SupplierMapping | None = None
) -> Linked:
    """Link every line that buys from a supplier by the name columns of `LINES`. A line that
    buys and names no supplier is an open issue. Lines that buy nothing are left as they are."""
    rows: Mapping[SupplierKey, SupplierRow] = decided.by_name if decided is not None else {}
    buying = buys(lines)
    matches: dict[tuple[SupplierKey, str], tuple[Supplier, ...]] = {}
    outcomes = Outcomes()
    for line in lines.select([*LINE_COLUMNS, "unit", *SUPPLIER_KEY]).filter(buying).to_pylist():
        key = SupplierKey(*(line[c] for c in SUPPLIER_KEY))
        row = rows.get(key)
        if key == SupplierKey():
            outcomes.add(None, issue(line, IssueStatus.open), unlinked(line))
        elif row is not None:
            outcomes.decide(line, row, suppliers)
        else:
            if (key, line["unit"]) not in matches:
                matches[key, line["unit"]] = suppliers.find(key)
            found = matches[key, line["unit"]]
            fit = _in_quantity(found, line["unit"], suppliers)
            if len(fit) == 1:
                outcomes.add(fit[0].id)
            else:
                problem = (
                    _other_quantity(line, key, found, suppliers)
                    if found and not fit
                    else _unmatched(line, key, fit, suppliers)
                )
                outcomes.add(None, issue(line, IssueStatus.open), problem)
    statuses = pa.nulls(lines.num_rows, pa.string())
    return outcomes.linked(lines, buying, lines.column("supplier_id"), statuses, rows)


def _in_quantity(
    found: tuple[Supplier, ...], unit: str, suppliers: Suppliers
) -> tuple[Supplier, ...]:
    """The suppliers that make their product in a unit of `unit`'s quantity, or all of them when
    the units are not known."""
    quantity = suppliers.quantity_of(unit)
    if quantity is None:
        return found
    return tuple(s for s in found if suppliers.quantity_of(s.unit) in (None, quantity))


def _other_quantity(
    line: Mapping[str, Any], key: SupplierKey, found: Sequence[Supplier], suppliers: Suppliers
) -> Problem:
    made = sorted({f"{suppliers.quantity_of(s.unit)} ({s.unit})" for s in found})
    shown = "; ".join(s.label for s in found[:_SHOWN])
    return Problem(
        "supplier-other-quantity",
        label(line),
        f"{line['role']} of flow {line['flow_id']} buys {key} in "
        f"{suppliers.quantity_of(line['unit'])} ({line['unit']}), but the processes that fit make "
        f"it in {', '.join(made)}: {shown}. Buy it in a unit of their quantity, or decide it in "
        "the supplier mapping. It is an open issue.",
    )


def _unmatched(
    line: Mapping[str, Any], key: SupplierKey, matches: Sequence[Supplier], suppliers: Suppliers
) -> Problem:
    what = f"{line['role']} of flow {line['flow_id']}"
    if matches:
        shown = "; ".join(s.label for s in matches[:_SHOWN])
        more = f" and {len(matches) - _SHOWN} more" if len(matches) > _SHOWN else ""
        return Problem(
            "ambiguous-supplier",
            label(line),
            f"{what} buys from {key}, which {len(matches)} processes match: {shown}{more}. Give "
            "more of the key, or decide it in the supplier mapping. It is an open issue.",
        )
    close = suppliers.suggestions(key)
    hint = f" Close: {'; '.join(close)}." if close else ""
    return Problem(
        "no-supplier",
        label(line),
        f"{what} buys from {key}, which no process of the source matches exactly.{hint} It is "
        "an open issue.",
    )
