"""What a release offers to link to: its processes by name, reference product and location, and
its elementary flows by name and compartment.

The processes are the shared `Suppliers` of `headwater.link`, which every format links by name
with, read with DuckDB straight from the release's tables. This adds what a line of a study needs
of the supplier it buys from: the flow it makes, whether that is a product or a waste, and the
flow's unit. Building a record for every process, with its lines, is what `Release.processes()`
does, and for ecoinvent that is 700,000 lines nobody needs to link a spreadsheet.
"""

import difflib
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from headwater.link import SupplierKey, Suppliers
from headwater.model import DEFAULT_UNITS, FlowKind, Units
from headwater.release import Release

_SHOWN = 5
"""Suggestions listed for a line that did not match."""


@dataclass(frozen=True)
class Supplier:
    """A process of the release, as the workbook names it: by its name, the name of the product
    it makes, and where."""

    id: str
    name: str
    product: str
    location: str
    flow_id: str
    kind: FlowKind
    """`product`, or `waste` when the process treats a waste."""
    unit: str
    """The unit of the product it makes."""

    @property
    def label(self) -> str:
        return f"{self.id} ({self.name} | {self.product} | {self.location})"


@dataclass(frozen=True)
class ElementaryFlow:
    """An elementary flow of the release, with its compartment written `air/urban air close to
    ground`."""

    id: str
    name: str
    compartment: str
    unit: str

    @property
    def label(self) -> str:
        return f"{self.id} ({self.name} | {self.compartment})"


class ReleaseIndex:
    """Lookups over one source release."""

    def __init__(self, release: Release) -> None:
        self._named = Suppliers.of_release(release)  # refuses a release that holds scores only
        self.release = release
        self.units: Units = DEFAULT_UNITS.merge(release.units())
        con = release.connect()
        try:
            processes = con.sql(
                "SELECT p.id, p.name, f.name, p.location, f.id, f.kind, f.unit "
                "FROM processes p JOIN flows f ON f.id = p.reference_flow_id ORDER BY p.id"
            ).fetchall()
            flows = con.sql(
                "SELECT id, name, compartment, unit FROM flows "
                "WHERE kind = 'elementary' ORDER BY id"
            ).fetchall()
        finally:
            con.close()
        self._by_id: dict[str, Supplier] = {}
        self._by_name: dict[str, list[Supplier]] = defaultdict(list)
        self._by_product: dict[str, list[Supplier]] = defaultdict(list)
        for pid, name, product, location, flow_id, kind, unit in processes:
            supplier = Supplier(pid, name, product, location, flow_id, FlowKind(kind), unit)
            self._by_id[pid] = supplier
            self._by_name[name].append(supplier)
            self._by_product[product].append(supplier)
        self._matches: dict[SupplierKey, tuple[Supplier, ...]] = {}
        self._flows: dict[str, ElementaryFlow] = {}
        self._flows_by_key: dict[tuple[str, str], list[ElementaryFlow]] = defaultdict(list)
        self._flows_by_name: dict[str, list[ElementaryFlow]] = defaultdict(list)
        for fid, name, compartment, unit in flows:
            flow = ElementaryFlow(fid, name, compartment or "", unit)
            self._flows[fid] = flow
            self._flows_by_key[name, flow.compartment].append(flow)
            self._flows_by_name[name].append(flow)
        self._names = sorted(self._by_name)
        self._suggested: dict[tuple[str, str | None, str | None], tuple[str, ...]] = {}

    @property
    def process_count(self) -> int:
        return len(self._by_id)

    def supplier(self, process_id: str) -> Supplier | None:
        return self._by_id.get(process_id)

    def suppliers(
        self, name: str, product: str | None, location: str | None
    ) -> tuple[Supplier, ...]:
        """The processes with exactly this name, reference product and location.

        A line without a reference product matches on name and location alone, and a line with
        neither on name alone: it then matches only when that is not ambiguous, which the caller
        checks.
        """
        key = SupplierKey(name=name, product=product, location=location)
        if key not in self._matches:
            self._matches[key] = tuple(self._by_id[s.id] for s in self._named.find(key))
        return self._matches[key]

    def flow(self, flow_id: str) -> ElementaryFlow | None:
        return self._flows.get(flow_id)

    def flows(self, name: str, compartment: str) -> tuple[ElementaryFlow, ...]:
        return tuple(self._flows_by_key.get((name, compartment), ()))

    def suggestions(self, name: str, product: str | None, location: str | None) -> tuple[str, ...]:
        """Processes a person might have meant, for a line that matched none. Only to read, never
        applied. In order: the same name at other places or with another product; the same
        product; names that start the same way, since ecoinvent renames by adding to a name
        (`market for sand` became `market for sand, washed`); and names that are close to it."""
        key = (name, product, location)
        if key in self._suggested:
            return self._suggested[key]
        found: dict[str, Supplier] = {}

        def add(suppliers: Sequence[Supplier]) -> None:
            for supplier in sorted(suppliers, key=lambda s: (s.location != location, s.id)):
                found.setdefault(supplier.id, supplier)

        add(self._by_name.get(name, []))
        if product is not None:
            add(self._by_product.get(product, []))
        starts = [n for n in self._names if n.startswith(name) or name.startswith(n)]
        for similar in starts[:_SHOWN]:
            add(self._by_name[similar])
        for similar in difflib.get_close_matches(name, self._names, n=_SHOWN, cutoff=0.8):
            add(self._by_name[similar][:1])
        self._suggested[key] = tuple(s.label for s in list(found.values())[:_SHOWN])
        return self._suggested[key]

    def flow_suggestions(self, name: str) -> tuple[str, ...]:
        """Elementary flows with this name in other compartments."""
        return tuple(f.label for f in self._flows_by_name.get(name, [])[:_SHOWN])
