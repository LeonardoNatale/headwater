"""Read an impact method of an openLCA JSON-LD package as a method source.

Example: the method `EF 3.1` of a package lists its impact categories, and the category `Climate
change` gives `Methane, fossil` the factor 29.8 per kg. That becomes a factor of the indicator
`climate-change` for the flow `olca:flow:<uuid>`. The shared stage,
`headwater.io.method.map_method`, finds that flow in the release by id.

The method's id, version and variant default to ones read from its name: `EF 3.1` gives
`EF-3.1`, `3.1` and `EF 3.1`. A factor given in another flow property than its flow's reference
one (per m3 of a flow counted in kg) is refused, naming it.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from headwater.io.method import indicator_id, method_id, method_version, missing_version
from headwater.io.olca_jsonld.flows import (
    FLOW_LIST,
    flow_id,
    in_other_property,
    number,
    read_locations,
    reference_property,
    unit_name,
    unit_names,
)
from headwater.io.olca_jsonld.package import DataSet, Package, field, obj, objs, ref_id, text
from headwater.io.source import FactorRow, MethodOptions, MethodSource
from headwater.link import FlowKey
from headwater.model import Indicator, InvalidDataError, Problem, did_you_mean

type _Category = tuple[str, DataSet]
"""A category's UUID and data set."""


@dataclass(frozen=True)
class _Flows:
    """What the factors of a method need from the package: the reference flow property of each
    flow they name, by UUID, the units' names and the locations."""

    properties: dict[str, str | None]
    units: dict[str, str]
    locations: dict[str, tuple[str, str]]


def read_method(path: Path, options: MethodOptions) -> MethodSource:
    """Read the method `options.method` of the package at `path`; with no name, the package's
    only method. `options.categories` chooses categories by name."""
    with Package(path) as package:
        method = _choose(package, options.method)
        every = _categories(package, method)
        chosen = _select(method, every, options.categories)
        named = {ref_id(f, "flow") or "" for _, c in chosen for f in objs(c, "impactFactors")}
        flows = _Flows(
            properties={
                uuid: reference_property(package.read("flows", uuid))
                for uuid in sorted(named)
                if package.has("flows", uuid)
            },
            units=unit_names(package),
            locations=read_locations(package),
        )
    problems: list[Problem] = []
    indicators = _indicators(chosen, problems)
    factors = [
        row
        for uuid, category in chosen
        if uuid in indicators
        for row in _factors(uuid, category, indicators[uuid], flows, problems)
    ]
    name = text(method, "name") or ""
    version = (options.version or "").strip() or method_version(name)
    if version is None:
        problems.append(missing_version(name))
    if problems or version is None:
        raise InvalidDataError(problems)
    kept = {uuid for uuid, _ in chosen}
    return MethodSource(
        name=name,
        id=(options.id or "").strip() or method_id(name, version),
        version=version,
        variant=(options.variant or "").strip() or name,
        indicators=tuple(sorted(indicators.values(), key=lambda i: i.id)),
        factors=tuple(factors),
        flow_list=FLOW_LIST,
        origin=f"Imported by headwater from {path.name}, method {name}, an openLCA JSON-LD "
        "package.",
        keyed_by="id",
        left_out=tuple(sorted(text(c, "name") or uuid for uuid, c in every if uuid not in kept)),
    )


def _choose(package: Package, name: str | None) -> DataSet:
    methods = [data for _, data in package.all("lcia_methods")]
    listed = ", ".join(sorted(text(m, "name") or "" for m in methods)) or "none"
    found = [m for m in methods if name is None or text(m, "name") == name]
    item = f"package {package.path.name}"
    if len(found) == 1:
        return found[0]
    if name is None and methods:
        message = f"holds {len(methods)} methods: name one with --method. Methods: {listed}"
        raise InvalidDataError([Problem("missing-method", item, message)])
    if not found:
        names = [text(m, "name") or "" for m in methods]
        what = f"has no method {name!r}.{did_you_mean(name, names)}" if name else "has no method."
        raise InvalidDataError([Problem("unknown-method", item, f"{what} Methods: {listed}")])
    message = f"has {len(found)} methods named {name!r}"
    raise InvalidDataError([Problem("ambiguous-method", item, message)])


def _categories(package: Package, method: DataSet) -> list[_Category]:
    """The method's categories, in its order. Raises for one not in the package."""
    found: list[_Category] = []
    missing: list[str] = []
    for ref in objs(method, "impactCategories"):
        uuid = text(ref, "@id") or ""
        if package.has("lcia_categories", uuid):
            found.append((uuid, package.read("lcia_categories", uuid)))
        else:
            missing.append(f"{text(ref, 'name')} ({uuid})")
    if missing:
        message = f"lists categories that are not in the package: {', '.join(missing)}"
        item = f"method {text(method, 'name')}"
        raise InvalidDataError([Problem("missing-category", item, message)])
    return found


def _select(
    method: DataSet, every: list[_Category], names: Sequence[str] | None
) -> list[_Category]:
    """The categories named, in the method's order; every one when no name is given."""
    if names is None:
        return every
    known = {n for _, c in every if (n := text(c, "name")) is not None}
    unknown = [n for n in names if n not in known]
    if unknown:
        hints = "".join(did_you_mean(n, known) for n in unknown)
        message = (
            f"has no categories {', '.join(map(repr, unknown))}.{hints} "
            f"Categories: {', '.join(sorted(known))}"
        )
        item = f"method {text(method, 'name')}"
        raise InvalidDataError([Problem("unknown-category", item, message)])
    return [(uuid, c) for uuid, c in every if text(c, "name") in names]


def _indicators(chosen: list[_Category], problems: list[Problem]) -> dict[str, Indicator]:
    """Each category's indicator, by its UUID. `Climate change - Fossil` gives the id
    `climate-change-fossil`."""
    found: dict[str, Indicator] = {}
    names: dict[str, str] = {}
    for uuid, category in chosen:
        name, unit = text(category, "name") or "", text(category, "refUnit")
        id = indicator_id(name)
        item = f"category {name}"
        if not id or id in names:
            reason = f"gives the same id as {names[id]!r}" if id else "gives an empty id"
            problems.append(Problem("bad-indicator-id", item, f"{reason}: {id!r}"))
        elif not unit:
            problems.append(Problem("bad-indicator", item, "has no reference unit"))
        else:
            names[id] = name
            found[uuid] = Indicator(id=id, name=name, unit=unit)
    return found


def _factors(
    uuid: str,
    category: DataSet,
    indicator: Indicator,
    flows: _Flows,
    problems: list[Problem],
) -> list[FactorRow]:
    found: list[FactorRow] = []
    for n, factor in enumerate(objs(category, "impactFactors"), start=1):
        where = f"lcia_categories/{uuid}.json factor {n}"
        flow = ref_id(factor, "flow") or ""
        location = ref_id(factor, "location")
        place = flows.locations.get(location or "")
        value = field(factor, "value")
        other = in_other_property(flow, ref_id(factor, "flowProperty"), flows.properties.get(flow))
        if other is not None:
            problems.append(Problem("other-flow-property", where, other))
        elif location is not None and place is None:
            message = f"is for the location {location}, which is not in the package"
            problems.append(Problem("bad-location", where, message))
        elif not number(value):
            problems.append(Problem("bad-factor", where, f"has the value {value!r}, not a number"))
        else:
            found.append(
                FactorRow(
                    indicator=indicator.id,
                    flow=FlowKey(id=flow_id(flow)),
                    factor=float(value),
                    unit=unit_name(obj(factor, "unit"), flows.units),
                    location=place[0] if place else None,
                    where=where,
                )
            )
    return found
