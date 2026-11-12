"""A SimaPro CSV process export as an inventory source.

Example: the process `TOY0000000001` makes `cotton fabric {GLO}| cotton fabric production` and
buys 3 kWh of `electricity {GLO}| electricity production`. With `--source toy --version 1.0`:

- the process is `toy:1.0:TOY0000000001`, named `cotton fabric production`, at `GLO`;
- its product line `p1` makes the flow `cotton fabric`;
- its line `e1` buys `electricity` with the supplier's name `electricity production`, product
  `electricity` and location `GLO`, for link by name.

Flows have no ids in SimaPro. An elementary flow gets `flow_id(<flow list>, name, compartment)`,
and a product or a waste `product_flow_id(<flow list>, name, quantity)`, where the flow list comes
from SimaPro's version (`simapro-9.5`), so the same name gives the same id in every export of that
version. A product is one flow wherever it is made, and two flows when lines give it in units of
two quantities, such as gas in m3 and in kg. A flow's unit is, in order: the unit its substance
block gives, the unit of a line that makes it, the unit of its first line.

SimaPro has no process kinds: every process is written `ordinary_transforming_activity`, and the
format lists `process_kind` in what it cannot carry. A process's name and location come from its
first product's text. Where the text gives no name, the process name of the block is used, else
the whole text; where it gives no location, the location is `unspecified`.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pyarrow as pa

from headwater.io.simapro_csv.export import Export, read_export
from headwater.io.simapro_csv.records import Line, ProcessRecord, Substance
from headwater.io.simapro_csv.text import ProductText
from headwater.io.simapro_csv.units import headwater_unit, read_units
from headwater.io.source import (
    ALLOCATION,
    LINES,
    PROCESSES_READ,
    InventoryOptions,
    InventorySource,
    flow_id,
    product_flow_id,
)
from headwater.model import (
    ROLES_WITH_SUPPLIER,
    Flow,
    FlowKind,
    InvalidDataError,
    Problem,
    ProcessKind,
    Units,
    with_article,
)
from headwater.release import ReleaseInfo

UNSPECIFIED_LOCATION = "unspecified"
"""The location of a process whose product text gives none."""

_KIND = ProcessKind.ordinary_transforming_activity.value

_REQUIRED = {"version": "--version", "source": "--source", "licence": "--licence"}


@dataclass
class _Flow:
    """A flow as the lines name it, before its unit is chosen."""

    id: str
    name: str
    kind: FlowKind
    compartment: str | None
    substance: Substance | None
    units: dict[str, tuple[str, str]] = field(default_factory=dict[str, tuple[str, str]])
    """Each unit its lines use, with the first line in that unit and where it is."""
    made_in: str | None = None
    """The unit of the first line that makes it."""

    @property
    def first(self) -> str:
        """The first line that names it."""
        return next(iter(self.units.values()))[0]

    @property
    def unit(self) -> str:
        if self.substance is not None:
            return headwater_unit(self.substance.unit)
        return self.made_in or next(iter(self.units))


def read(path: Path, options: InventoryOptions) -> InventorySource:
    """Read the process export at `path` as `<source>:<version>`. Links nothing: that is
    `headwater.io.inventory.link_inventory`. Raises `InvalidDataError` naming every problem."""
    missing = [
        Problem("missing-option", flag, f"is required for a SimaPro export: give {flag}")
        for name, flag in _REQUIRED.items()
        if not getattr(options, name)
    ]
    if missing:
        raise InvalidDataError(missing)
    assert options.source and options.version and options.licence
    export = read_export(path)
    if not export.processes:
        message = (
            "is a SimaPro method export: import it with headwater method import"
            if export.methods
            else "holds no Process block"
        )
        raise InvalidDataError([Problem("wrong-export-kind", f"file {export.file}", message)])
    prefix = f"{options.source}:{options.version}"
    units = read_units(export.units, _used_units(export, prefix))
    lines = _Lines(export, units)
    processes = [lines.add_process(p, f"{prefix}:{p.identifier}") for p in export.processes]
    if lines.problems:
        raise InvalidDataError(lines.problems)
    return InventorySource(
        info=ReleaseInfo(
            name=f"{options.source}-{options.version}",
            source=options.source,
            version=options.version,
            licence=options.licence,
            flow_list=export.header.flow_list,
        ),
        processes=pa.Table.from_pylist(processes, schema=PROCESSES_READ.schema),
        lines=pa.Table.from_pydict(lines.columns, schema=LINES.schema),
        flows=_flow_records(lines.flows.values(), units),
        flow_properties=(),
        units=units,
        allocation=pa.Table.from_pylist(
            [
                {"process_id": f"{prefix}:{p.identifier}", "product": r.id, "factor": r.allocation}
                for p in export.processes
                for r in p.references
                if r.allocation is not None
            ],
            schema=ALLOCATION.schema,
        ),
        warnings=export.warnings,
    )


def _used_units(export: Export, prefix: str) -> dict[str, str]:
    """Each SimaPro unit the lines use, with a line that uses it, and the unit of each substance
    an elementary line names."""
    used: dict[str, str] = {}
    for process in export.processes:
        for line in process.lines:
            used.setdefault(line.unit, f"{prefix}:{process.identifier} line {line.id}")
            substance = export.substances.get((line.section.compartment or "", line.text))
            if line.section.is_elementary and substance is not None:
                used.setdefault(substance.unit, f"substance {substance.name}")
    return used


class _Lines:
    """The lines of an export as the columns of `LINES`, and the flows they name. A product text
    and a flow's id are worked out once each: an export has a million lines but a few thousand
    flows and texts."""

    def __init__(self, export: Export, units: Units) -> None:
        self.export = export
        self.units = units
        self.columns: dict[str, list[Any]] = {name: [] for name in LINES.schema.names}
        self.flows: dict[tuple[str, str | None], _Flow] = {}
        """By name and compartment for an elementary flow, by name and quantity for a product or
        a waste."""
        self.problems: list[Problem] = []
        self._texts: dict[str, ProductText] = {}

    def text(self, text: str) -> ProductText:
        found = self._texts.get(text)
        if found is None:
            found = self._texts[text] = ProductText.parse(text)
        return found

    def add_process(self, process: ProcessRecord, id: str) -> dict[str, Any]:
        """Adds the process's lines, and returns its row of `PROCESSES_READ`. Every product of a
        process must give the same name and location, since the splits of a process that makes
        several keep the process's own."""
        texts = [self.text(line.text) for line in process.references]
        first = texts[0]
        if any((t.name, t.location) != (first.name, first.location) for t in texts[1:]):
            self.problems.append(
                Problem(
                    "products-disagree",
                    f"process {id}",
                    "makes products whose texts give different process names or locations: "
                    f"{'; '.join(line.text for line in process.references)}. A process has one "
                    "name and one location.",
                    location=process.where,
                )
            )
        for line in process.lines:
            self._add(line, id)
        return {
            "id": id,
            "name": first.name or process.process_name or process.references[0].text,
            "location": first.location or UNSPECIFIED_LOCATION,
            "kind": _KIND,
            "source_ref": process.where,
        }

    def _add(self, line: Line, process_id: str) -> None:
        """A line that buys names its supplier by what its text gives."""
        section = line.section
        text = None if section.is_elementary else self.text(line.text)
        name = line.text if text is None else text.product
        compartment = line.compartment
        unit = headwater_unit(line.unit)
        if text is None:
            key = (name, compartment)
            id = flow_id(self.export.header.flow_list, name, compartment)
        else:
            key = (name, self.units.quantity_of(unit))
            id = product_flow_id(self.export.header.flow_list, name, key[1])
        flow = self.flows.get(key)
        if flow is None:
            substance = self.export.substances.get((section.compartment or "", name))
            flow = self.flows[key] = _Flow(id, name, section.kind, compartment, substance)
        if flow.kind is not section.kind:
            self.problems.append(
                Problem(
                    "flow-kind-conflict",
                    f"{process_id} line {line.id}",
                    f"names {name!r} as {with_article(section.kind.value)} in {section.name}, "
                    f"but {flow.first} names it as {with_article(flow.kind.value)}",
                    location=line.where,
                )
            )
        if unit not in flow.units:
            flow.units[unit] = (f"{process_id} line {line.id}", line.where)
        if section.is_reference and flow.made_in is None:
            flow.made_in = unit
        supplier = text if section.role in ROLES_WITH_SUPPLIER else None
        u = line.uncertainty
        row: dict[str, Any] = {
            "process_id": process_id,
            "line_id": line.id,
            "role": section.role.value,
            "flow_id": flow.id,
            "amount": line.amount,
            "unit": unit,
            "supplier_id": None,
            "uncertainty": None
            if u is None
            else {
                "distribution": u.distribution.value,
                "loc": u.loc,
                "scale": u.scale,
                "shape": u.shape,
                "minimum": u.minimum,
                "maximum": u.maximum,
            },
            "pedigree": None,
            "properties": [],
            "comment": line.comment,
            "flow_name": name,
            "supplier_name": supplier.name if supplier else None,
            "supplier_product": supplier.product if supplier else None,
            "supplier_location": supplier.location if supplier else None,
            "supplier_unit": None,
        }
        for column, value in row.items():
            self.columns[column].append(value)


def _flow_records(flows: Iterable[_Flow], units: Units) -> tuple[Flow, ...]:
    """The flows, sorted by name and compartment. Raises `InvalidDataError` for a flow whose
    lines use units of two quantities: the same name in kg and in MJ is two flows."""
    found: list[Flow] = []
    problems: list[Problem] = []
    for flow in flows:
        unit = flow.unit
        for other, (item, where) in flow.units.items():
            if units.quantity_of(other) != units.quantity_of(unit):
                problems.append(
                    Problem(
                        "flow-unit-conflict",
                        item,
                        f"gives {flow.name!r} in {other}, but its unit is {unit} "
                        f"({units.quantity_of(unit)}). One name in units of two quantities is "
                        "two flows.",
                        location=where,
                    )
                )
        found.append(
            Flow(
                id=flow.id,
                name=flow.name,
                kind=flow.kind,
                unit=unit,
                compartment=flow.compartment,
                cas=flow.substance.cas if flow.substance else None,
            )
        )
    if problems:
        raise InvalidDataError(problems)
    return tuple(sorted(found, key=lambda f: (f.name, f.compartment or "", f.unit)))
