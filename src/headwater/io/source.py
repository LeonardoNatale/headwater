"""What a reader gives: an inventory or a method as its source writes it, nothing linked or mapped.

Example: the toy fabric process buys 3 kWh of electricity. An ecoSpold2 reader writes the line
with `supplier_id` set to the electricity process's id. A SimaPro reader reads the text
`electricity {GLO}| electricity production` and writes `supplier_name`, `supplier_product` and
`supplier_location` as `electricity production`, `electricity` and `GLO`. The shared stages in
`headwater.io.inventory` turn both into the same release row.

A reader keeps the source's own ids and keys. It never links a line, maps a flow or splits a
process: those are the stages, one implementation each, in a fixed order. Processes, lines and
allocation factors are Arrow tables, because ecoinvent has 750,000 lines. Flows, properties,
units and factors are records: there are thousands, and every check wants records.
"""

import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

import pyarrow as pa
import pyarrow.compute as pc

from headwater.io.exclude import Exclusion
from headwater.link.lines import DataGap
from headwater.link.mapping import SUPPLIER_KEY, FlowKey
from headwater.model import Flow, FlowProperty, Indicator, Problem, Units
from headwater.release import ReleaseInfo
from headwater.release.schema import EXCHANGES, FLOW_NAME, GEOGRAPHIES, PROCESSES, TableSpec

compute: Any = pc

_TEXT = pa.string()

_schema: Any = PROCESSES.schema
PROCESSES_READ = TableSpec(
    pa.schema([f for f in _schema if f.name != "reference_flow_id"]),
    PROCESSES.key,
)
"""The processes table of a release without `reference_flow_id`: a process read from a source
may make several products, until `allocate` splits it."""

_exchanges: Any = EXCHANGES.schema
LINES = TableSpec(
    pa.schema(
        [
            *(f for f in _exchanges if f.name != "link_status"),
            pa.field(FLOW_NAME, _TEXT),
            *(pa.field(name, _TEXT) for name in SUPPLIER_KEY),
        ]
    ),
    EXCHANGES.key,
)
"""The lines of a source, before linking.

The columns of `exchanges.parquet` without `link_status`, which only the link stages set, plus:

- `flow_name`: the flow's name as written on the line, checked against the flow, then dropped.
- `supplier_id`: the supplier by id, as the source gives it.
- `supplier_name`, `supplier_product`, `supplier_location`, `supplier_unit`: the supplier by
  name, with what the line gives of the supplier's process name, reference product, location
  and reference unit.

A line names its supplier by id, by name, or not at all, never both ways.
"""

ALLOCATION = TableSpec(
    pa.schema(
        [
            pa.field("process_id", _TEXT, nullable=False),
            pa.field("product", _TEXT, nullable=False),
            pa.field("line_id", _TEXT),
            pa.field("factor", pa.float64(), nullable=False),
        ]
    ),
    ("process_id", "product", "line_id"),
)
"""Allocation factors the source gives for a process that makes several products.

`product` is the line id of one of the products (a `reference` line). A row with no `line_id`
is the product's factor for every other line of the process; a row with a `line_id` is its
factor for that line only. Example: a cotton gin makes fibre (line p1) and seed (line p2):
rows `gin,p1,,0.9` and `gin,p2,,0.1`.
"""


def flow_id(namespace: str, name: str, compartment: str | None) -> str:
    """The id of a flow that its source names only by name and compartment.

    Example: `flow_id("agribalyse-3.2", "Carbon dioxide, fossil", "air/high population
    density")` is `agribalyse-3.2:flow:<uuid5>`. The same key always gives the same id, so the
    same source always gives the same release.
    """
    key = f"{name}\x00{compartment or ''}"
    return f"{namespace}:flow:{uuid.uuid5(uuid.NAMESPACE_URL, key)}"


def product_flow_id(namespace: str, name: str, quantity: str) -> str:
    """The id of a product that its source names only by name, counted in units of one quantity.

    Example: `product_flow_id("bafu-2026", "natural gas", "volume")` is `bafu-2026:flow:<uuid5>`. A
    product counted in kg and in m3 is two flows, so the quantity is part of the key: the quantity
    name of the flow's reference unit, which `flows.parquet` and the unit table give back. The
    key starts with `product`, so it never equals the key of an elementary flow.
    """
    return f"{namespace}:flow:{uuid.uuid5(uuid.NAMESPACE_URL, f'product\x00{name}\x00{quantity}')}"


@dataclass(frozen=True)
class InventorySource:
    """An inventory as its source writes it: the source's ids and keys, nothing linked."""

    info: ReleaseInfo
    """What the release is: its default name, source, version, system model, licence and flow
    list."""
    processes: pa.Table
    """`PROCESSES_READ`."""
    lines: pa.Table
    """`LINES`."""
    flows: tuple[Flow, ...]
    flow_properties: tuple[FlowProperty, ...]
    units: Units
    """Every unit the source names."""
    geographies: pa.Table = field(default_factory=GEOGRAPHIES.schema.empty_table)
    """`GEOGRAPHIES`: the places the source names. Empty when it names none."""
    allocation: pa.Table = field(default_factory=ALLOCATION.schema.empty_table)
    """`ALLOCATION`: factors for the processes that make several products."""
    left_out: Mapping[str, DataGap] = field(default_factory=dict[str, DataGap])
    """Suppliers left out on purpose, by id: a line that buys from one is a data gap."""
    supplier_hint: Callable[[str], str] | None = None
    """A sentence about a supplier id that is not in the source, such as the file it would come
    from: `There is no file datasets/<id>.spold.`"""
    warnings: tuple[Problem, ...] = ()


@dataclass(frozen=True)
class FactorRow:
    """One factor as the source writes it."""

    indicator: str
    """The indicator's id."""
    flow: FlowKey
    factor: float
    unit: str | None = None
    """The unit the factor is per. None: per the flow's own unit."""
    location: str | None = None
    where: str | None = None
    """Where it is in the file, for example `CFs!A12`."""
    covers_subcompartments: bool = False
    """The factor also holds for every flow of the same name in a subcompartment of its
    compartment that has no factor of its own in the method: a SimaPro factor for `air`
    (unspecified) also counts `air/high. pop.`."""


@dataclass(frozen=True)
class MethodSource:
    """A method as its source writes it: indicators, and factors by the source's flow keys."""

    name: str
    """The method's name in the source, for example `EF v3.1`."""
    id: str
    version: str
    variant: str | None
    indicators: tuple[Indicator, ...]
    """Sorted by id."""
    factors: tuple[FactorRow, ...]
    """In the order of the source."""
    flow_list: str
    """The flow list its keys are written in, for example `ecoinvent-3.12`. A method maps onto a
    release of another flow list only with a mapping file."""
    origin: str
    """Where the factors came from, the first comment of `method.yaml`."""
    keyed_by: str
    """How the factors name their flows, for `method.yaml`: `id`, or `name, compartment and
    subcompartment`."""
    left_out: tuple[str, ...] = ()
    """Categories of the source's method that were not read, sorted."""


@dataclass(frozen=True)
class InventoryOptions:
    """What `headwater release import` passes to a reader."""

    version: str | None = None
    """The source's version, which goes into its ids."""
    source: str | None = None
    """The source's name, which goes into its ids, for a format whose files do not give it:
    `bafu` gives `bafu:<version>:<process uuid>`."""
    licence: str | None = None
    """The source's licence, for a format whose files do not give it."""
    exclude: tuple[Exclusion, ...] = ()
    """Source files left out on purpose."""
    workers: int | None = None
    """Processes that parse files. None: one per CPU."""


@dataclass(frozen=True)
class MethodOptions:
    """What `headwater method import` passes to a reader."""

    method: str | None = None
    """The method's name in the source."""
    categories: tuple[str, ...] | None = None
    """Categories to read, by name. None: every one."""
    id: str | None = None
    version: str | None = None
    variant: str | None = None


class InventoryReader(Protocol):
    def __call__(self, path: Path, options: InventoryOptions, /) -> InventorySource: ...


class MethodReader(Protocol):
    def __call__(self, path: Path, options: MethodOptions, /) -> MethodSource: ...


class SourceKind(StrEnum):
    inventory = "inventory"
    method = "method"


class Aspect(StrEnum):
    """What the conformance suite compares, beyond processes, flows and scores. A format lists
    the ones it cannot carry, and the suite skips exactly those."""

    process_kind = "process_kind"
    """The kind of each process: `market_activity` and the other ecoSpold2 activity types."""
    cas = "cas"
    """The CAS number of each elementary flow."""
    supplier_links = "supplier_links"
    """Lines that buy from another process. An aggregated dataset has none: its A is diagonal."""
    uncertainty = "uncertainty"
    """The distribution of each amount."""


@dataclass(frozen=True)
class Format:
    """One data format headwater reads. `headwater.io.formats.FORMATS` lists them, one line
    each."""

    name: str
    """What `--format` takes, for example `ecospold2`."""
    title: str
    """One line for `--help`."""
    detect: Callable[[Path], bool]
    """True when a path looks like this format: from names and first bytes, never a parse."""
    read_inventory: InventoryReader | None = None
    inventory_options: frozenset[str] = frozenset()
    """The fields of `InventoryOptions` its inventory reader takes: `version`, `exclude`."""
    read_method: MethodReader | None = None
    method_options: frozenset[str] = frozenset()
    """The fields of `MethodOptions` its method reader takes: `method`, `categories`."""
    cannot_carry: frozenset[Aspect] = frozenset()
    """What the format cannot hold, so the conformance suite does not compare it."""

    def reads(self, kind: SourceKind) -> bool:
        reader = self.read_inventory if kind is SourceKind.inventory else self.read_method
        return reader is not None

    def options(self, kind: SourceKind) -> frozenset[str]:
        return self.inventory_options if kind is SourceKind.inventory else self.method_options


def lookup(keys: Any, values: Mapping[str, str]) -> Any:
    """The value of each key, or null for a key not in `values`, as an Arrow array."""
    known = pa.array(list(values), _TEXT)
    found = pa.array(list(values.values()), _TEXT)
    return compute.take(found, compute.index_in(keys, value_set=known))


def as_lines(exchanges: pa.Table) -> pa.Table:
    """Lines made with `exchanges_table(..., flow_names=True)`, as `LINES`: without the link
    status, and with no supplier named by name."""
    table = exchanges.drop_columns(["link_status"])
    for name in SUPPLIER_KEY:
        table = table.append_column(name, pa.nulls(table.num_rows, _TEXT))
    return table
