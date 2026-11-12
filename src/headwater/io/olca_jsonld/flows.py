"""The flows of a JSON-LD package, with their units, and its locations.

Example: the flow `electricity` has the reference flow property `Energy`, whose unit group `Units
of energy` has the reference unit MJ, so the flow is counted in MJ. A line may give it in kWh,
another unit of the group: `1 kWh = 3.6 MJ` is in the unit table.

A flow is `olca:flow:<uuid>` in every package: a UUID names one flow wherever it appears, so a
method of one package maps onto the flows of another by id, and a factor whose UUID is not there
is listed as unmapped, never guessed.
"""

import math
from dataclasses import dataclass
from typing import Any

import pyarrow as pa

from headwater.io.olca_jsonld.package import DataSet, Package, field, objs, ref_id, text
from headwater.io.units import Conversion, unit_table
from headwater.model import Flow, FlowKind, InvalidDataError, Problem, Units
from headwater.release.schema import GEOGRAPHIES

FLOW_LIST = "olca"
"""The flow list of every JSON-LD package: flows by their openLCA UUID."""

KINDS = {
    "PRODUCT_FLOW": FlowKind.product,
    "WASTE_FLOW": FlowKind.waste,
    "ELEMENTARY_FLOW": FlowKind.elementary,
}


def flow_id(uuid: str) -> str:
    return f"olca:flow:{uuid}"


@dataclass(frozen=True)
class Flows:
    """The flows of a package and what is needed to read lines and factors of them."""

    flows: tuple[Flow, ...]
    """Sorted by id."""
    kinds: dict[str, FlowKind]
    """Each flow's kind, by UUID."""
    reference_property: dict[str, str]
    """The UUID of each flow's reference flow property, by flow UUID. An amount given in another
    property needs that property's conversion factor."""
    units: Units
    """Every unit of the package's unit groups."""
    unit_names: dict[str, str]
    """Each unit's name, by its UUID."""


def read_flows(package: Package) -> Flows:
    """The flows, with each one's unit: the reference unit of its reference flow property. Raises
    naming every flow and unit group that cannot be read."""
    problems: list[Problem] = []
    reference_units: dict[str, str] = {}
    names: dict[str, str] = {}
    all_names: set[str] = set()
    conversions: list[Conversion] = []
    for uuid, group in package.all("unit_groups"):
        units = objs(group, "units")
        reference = [text(u, "name") for u in units if field(u, "isRefUnit")]
        if len(reference) != 1 or reference[0] is None:
            message = f"has {len(reference)} reference units; it needs one, with a name"
            problems.append(Problem("bad-unit-group", f"unit group {uuid}", message))
            continue
        reference_units[uuid] = reference[0]
        for unit in units:
            name, factor = text(unit, "name"), field(unit, "conversionFactor")
            if name is None or not number(factor) or factor <= 0:
                message = f"has a unit {name!r} with the conversion factor {factor!r}"
                problems.append(Problem("bad-unit-group", f"unit group {uuid}", message))
                continue
            all_names.add(name)
            if (unit_id := text(unit, "@id")) is not None:
                names[unit_id] = name
            if name != reference[0]:
                quantity = text(group, "name") or uuid
                conversions.append(Conversion(name, reference[0], float(factor), quantity))
    property_units = {
        uuid: reference_units.get(ref_id(prop, "unitGroup") or "")
        for uuid, prop in package.all("flow_properties")
    }
    flows: list[Flow] = []
    kinds: dict[str, FlowKind] = {}
    properties: dict[str, str] = {}
    for uuid, data in package.all("flows"):
        item = f"flow {text(data, 'name')} ({uuid})"
        kind = KINDS.get(text(data, "flowType") or "")
        prop = reference_property(data)
        unit = property_units.get(prop or "")
        name = text(data, "name")
        if kind is None or name is None:
            message = f"has the flow type {field(data, 'flowType')!r} and the name {name!r}"
            problems.append(Problem("bad-flow", item, message))
        elif prop is None:
            message = "needs one reference flow property"
            problems.append(Problem("bad-flow", item, message))
        elif unit is None:
            message = f"its flow property {prop} is not in the package, or has no unit group"
            problems.append(Problem("bad-flow", item, message))
        else:
            kinds[uuid] = kind
            properties[uuid] = prop
            flows.append(
                Flow(
                    id=flow_id(uuid),
                    name=name,
                    kind=kind,
                    unit=unit,
                    compartment=text(data, "category") if kind is FlowKind.elementary else None,
                    cas=text(data, "cas"),
                )
            )
    if problems:
        raise InvalidDataError(problems)
    return Flows(
        flows=tuple(sorted(flows, key=lambda f: f.id)),
        kinds=kinds,
        reference_property=properties,
        units=unit_table(conversions, all_names, source="unit_groups/"),
        unit_names=names,
    )


def reference_property(flow: DataSet) -> str | None:
    """The UUID of a flow's one reference flow property, or None."""
    found = [f for f in objs(flow, "flowProperties") if field(f, "isRefFlowProperty")]
    return ref_id(found[0], "flowProperty") if len(found) == 1 else None


def unit_names(package: Package) -> dict[str, str]:
    """Each unit's name, by its UUID."""
    return {
        unit_id: name
        for _, group in package.all("unit_groups")
        for unit in objs(group, "units")
        if (unit_id := text(unit, "@id")) is not None and (name := text(unit, "name")) is not None
    }


def unit_name(ref: DataSet, names: dict[str, str]) -> str | None:
    """The name of the unit a reference names, from the reference or by its UUID."""
    return text(ref, "name") or names.get(text(ref, "@id") or "")


def in_other_property(flow: str, given: str | None, reference: str | None) -> str | None:
    """Why an amount or a factor of `flow` given in the flow property `given` cannot be read, or
    None when it is given in the flow's reference property. Example: 2 m3 of a flow counted in kg,
    which needs the conversion factor of the flow's volume property."""
    if given is None or reference is None or given == reference:
        return None
    return (
        f"is given in the flow property {given} of flow {flow}, not in its reference flow property "
        f"{reference}"
    )


def read_locations(package: Package) -> dict[str, tuple[str, str]]:
    """Each location's code and name, by its UUID. Raises for a location with no code, and for a
    code that names two places."""
    found: dict[str, tuple[str, str]] = {}
    names: dict[str, str] = {}
    problems: list[Problem] = []
    for uuid, data in package.all("locations"):
        code = text(data, "code")
        name = text(data, "name") or code
        item = f"location {name} ({uuid})"
        if code is None or name is None:
            problems.append(Problem("bad-location", item, "has no code"))
            continue
        if names.setdefault(code, name) != name:
            message = f"has the code {code}, which also names {names[code]}"
            problems.append(Problem("bad-location", item, message))
            continue
        found[uuid] = (code, name)
    if problems:
        raise InvalidDataError(problems)
    return found


def number(value: Any) -> bool:
    """True for a finite number that is not a bool."""
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def geographies(locations: dict[str, tuple[str, str]]) -> pa.Table:
    """The `GEOGRAPHIES` table of these locations, one row per code, sorted."""
    rows = sorted({code: name for code, name in locations.values()}.items())
    return pa.Table.from_pylist(
        [{"code": code, "name": name} for code, name in rows], schema=GEOGRAPHIES.schema
    )
