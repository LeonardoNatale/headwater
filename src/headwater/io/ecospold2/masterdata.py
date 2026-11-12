"""ecoinvent MasterData: every flow, the flow properties, and the unit table.

Every product and elementary flow of MasterData is a flow of the release, also those no dataset
uses, so a method can name any of them.
"""

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from lxml import etree

from headwater.io.units import Conversion, unit_table
from headwater.model import (
    Flow,
    FlowKind,
    FlowProperty,
    FlowPropertyName,
    InvalidDataError,
    Problem,
    Units,
)

NS = "{http://www.EcoInvent.org/EcoSpold02}"


def parse_xml(path: Path) -> "Element":
    """The root of an XML file. Never fetches or expands anything the file points at. A parser
    is made per call: lxml parsers must not be shared between threads."""
    parser = etree.XMLParser(resolve_entities=False, no_network=True)
    return etree.parse(str(path), parser).getroot()


type Element = etree._Element  # pyright: ignore[reportPrivateUsage]

FILES = (
    "IntermediateExchanges.xml",
    "ElementaryExchanges.xml",
    "Properties.xml",
    "UnitConversions.xml",
)

WASTE, RECYCLABLE, ALLOCATABLE = "Waste", "Recyclable", "allocatable product"
CLASSES = (ALLOCATABLE, WASTE, RECYCLABLE)
"""ecoinvent's by-product classification. A Waste is a waste flow; the others are products,
except a flow an APOS dataset treats or sends to treatment (see `dataset.py`)."""

BY_PRODUCT_CLASSIFICATION = "By-product classification"

FLOW_PROPERTIES = {
    "heating value, net": FlowPropertyName.heating_value_net,
    "heating value, gross": FlowPropertyName.heating_value_gross,
    "price": FlowPropertyName.price,
    "dry mass": FlowPropertyName.dry_mass,
    "water content": FlowPropertyName.water_content,
    "carbon content, fossil": FlowPropertyName.carbon_content_fossil,
    "carbon content, non-fossil": FlowPropertyName.carbon_content_biogenic,
}
"""MasterData properties kept on the flow, by their ecoinvent name. Each is given per reference
unit of the flow. "wet mass" is handled apart: see `_wet_mass`."""

WET_MASS = "wet mass"


@dataclass(frozen=True)
class MasterData:
    flows: tuple[Flow, ...]
    flow_properties: tuple[FlowProperty, ...]
    classes: Mapping[str, str]
    """The by-product classification of each product flow, by flow id."""
    conversions: tuple[Conversion, ...]


def product_id(uuid: str) -> str:
    return f"ecoinvent:product:{uuid}"


def elementary_id(uuid: str) -> str:
    return f"ecoinvent:flow:{uuid}"


def text(element: Element, tag: str) -> str | None:
    """The stripped text of the first child `tag`, or None when it is missing or empty."""
    found = element.findtext(f"{NS}{tag}")
    return (found.strip() or None) if found is not None else None


def by_product_class(element: Element) -> str | None:
    for classification in element.iterfind(f"{NS}classification"):
        if text(classification, "classificationSystem") == BY_PRODUCT_CLASSIFICATION:
            return text(classification, "classificationValue")
    return None


def read_masterdata(folder: Path) -> MasterData:
    """Read the MasterData folder of an ecoSpold2 source. Raises naming every problem."""
    problems: list[Problem] = []
    roots: dict[str, Element] = {}
    for name in FILES:
        item = f"MasterData/{name}"
        path = folder / name
        if not path.is_file():
            problems.append(Problem("missing-file", item, f"is missing in {folder.parent}"))
            continue
        try:
            roots[name] = parse_xml(path)
        except etree.XMLSyntaxError as error:
            problems.append(Problem("unreadable-file", item, f"is not valid XML: {error}"))
    if problems:
        raise InvalidDataError(problems)
    properties = _properties(roots["Properties.xml"], problems)
    products, classes, raw = _products(roots["IntermediateExchanges.xml"], properties, problems)
    elementary = _elementary(roots["ElementaryExchanges.xml"], problems)
    conversions = _conversions(roots["UnitConversions.xml"], problems)
    if problems:
        raise InvalidDataError(problems)
    flows = (*products, *elementary)
    units = unit_table(conversions, [f.unit for f in flows], source="UnitConversions.xml")
    flow_properties = _flow_properties(products, raw, units, problems)
    if problems:
        raise InvalidDataError(problems)
    return MasterData(flows, flow_properties, classes, conversions)


def _properties(
    root: Element,
    problems: list[Problem],
) -> dict[str, tuple[str, str]]:
    """Name and unit of each property, by id."""
    found: dict[str, tuple[str, str]] = {}
    for element in root.iterfind(f"{NS}property"):
        uuid, name, unit = element.get("id"), text(element, "name"), text(element, "unitName")
        if uuid is None or name is None or unit is None:
            problems.append(
                Problem(
                    "invalid-masterdata",
                    f"MasterData/Properties.xml property {uuid}",
                    "needs an id, a name and a unitName",
                )
            )
            continue
        found[uuid] = (name, unit)
    return found


type RawProperty = tuple[str, float, str]
"""A property of a product in MasterData: name, amount, unit."""


def _products(
    root: Element,
    properties: Mapping[str, tuple[str, str]],
    problems: list[Problem],
) -> tuple[list[Flow], dict[str, str], dict[str, list[RawProperty]]]:
    flows: list[Flow] = []
    classes: dict[str, str] = {}
    raw: dict[str, list[RawProperty]] = {}
    for element in root.iterfind(f"{NS}intermediateExchange"):
        uuid, name, unit = element.get("id"), text(element, "name"), text(element, "unitName")
        item = f"MasterData/IntermediateExchanges.xml product {uuid}"
        if uuid is None or name is None or unit is None:
            problems.append(
                Problem("invalid-masterdata", item, "needs an id, a name and a unitName")
            )
            continue
        flow_class = by_product_class(element)
        if flow_class not in CLASSES:
            code = "missing-classification" if flow_class is None else "unknown-classification"
            problems.append(
                Problem(
                    code,
                    item,
                    f"has the by-product classification {flow_class!r}; "
                    f"expected one of {', '.join(CLASSES)}",
                )
            )
            continue
        flow = Flow(
            id=product_id(uuid),
            name=name,
            kind=FlowKind.waste if flow_class == WASTE else FlowKind.product,
            unit=unit,
            cas=element.get("casNumber") or None,
        )
        flows.append(flow)
        classes[flow.id] = flow_class
        for prop in element.iterfind(f"{NS}property"):
            known = properties.get(prop.get("propertyId", ""))
            amount = prop.get("amount")
            if known is None or amount is None:
                problems.append(
                    Problem(
                        "invalid-masterdata",
                        item,
                        f"property {prop.get('propertyId')} is not in Properties.xml, or has "
                        "no amount",
                    )
                )
                continue
            value = number(amount)
            if value is None:
                problems.append(
                    Problem(
                        "invalid-masterdata", item, f"property amount {amount!r} is not a number"
                    )
                )
                continue
            raw.setdefault(flow.id, []).append((known[0], value, known[1]))
    return flows, classes, raw


def _elementary(
    root: Element,
    problems: list[Problem],
) -> list[Flow]:
    flows: list[Flow] = []
    for element in root.iterfind(f"{NS}elementaryExchange"):
        uuid, name, unit = element.get("id"), text(element, "name"), text(element, "unitName")
        compartment = element.find(f"{NS}compartment")
        if uuid is None or name is None or unit is None or compartment is None:
            problems.append(
                Problem(
                    "invalid-masterdata",
                    f"MasterData/ElementaryExchanges.xml flow {uuid}",
                    "needs an id, a name, a unitName and a compartment",
                )
            )
            continue
        parts = [text(compartment, "compartment"), text(compartment, "subcompartment")]
        flows.append(
            Flow(
                id=elementary_id(uuid),
                name=name,
                kind=FlowKind.elementary,
                unit=unit,
                compartment="/".join(p for p in parts if p),
                cas=element.get("casNumber") or None,
            )
        )
    return flows


def _conversions(
    root: Element,
    problems: list[Problem],
) -> tuple[Conversion, ...]:
    found: list[Conversion] = []
    for element in root.iterfind(f"{NS}unitConversion"):
        unit_from, unit_to = text(element, "unitFromName"), text(element, "unitToName")
        quantity, factor = text(element, "unitType"), element.get("factor")
        if unit_from is None or unit_to is None or quantity is None or factor is None:
            problems.append(
                Problem(
                    "invalid-masterdata",
                    f"MasterData/UnitConversions.xml conversion {element.get('id')}",
                    "needs unitFromName, unitToName, unitType and factor",
                )
            )
            continue
        value = number(factor)
        if value is None or value <= 0:
            problems.append(
                Problem(
                    "invalid-masterdata",
                    f"MasterData/UnitConversions.xml conversion {element.get('id')}",
                    f"factor {factor} from {unit_from} to {unit_to} must be above 0",
                )
            )
            continue
        found.append(Conversion(unit_from, unit_to, value, quantity))
    return tuple(found)


def number(value: str | None) -> float | None:
    """A finite float from an attribute, or None."""
    if value is None:
        return None
    try:
        found = float(value)
    except ValueError:
        return None
    return found if math.isfinite(found) else None


def _flow_properties(
    products: Iterable[Flow],
    raw: Mapping[str, list[RawProperty]],
    units: Units,
    problems: list[Problem],
) -> tuple[FlowProperty, ...]:
    """The MasterData properties a flow keeps. A flow property is above zero, so a zero (a price
    of 0, no fossil carbon) is not one; the lines that carry it keep it as written."""
    found: list[FlowProperty] = []
    for flow in products:
        for name, value, unit in raw.get(flow.id, []):
            prop = FLOW_PROPERTIES.get(name) or (
                _wet_mass(flow, units) if name == WET_MASS else None
            )
            if prop is None or value == 0:
                continue
            if value < 0:
                problems.append(
                    Problem(
                        "bad-property",
                        f"flow {flow.id} property {name}",
                        f"is {value}; a property of a flow is above 0",
                    )
                )
                continue
            found.append(FlowProperty(flow_id=flow.id, property=prop, value=value, unit=unit))
    return tuple(found)


def _wet_mass(flow: Flow, units: Units) -> FlowPropertyName | None:
    """Wet mass is the mass of one reference unit of the flow. For a flow counted in volume that
    is its density (1 m3 holds 0.8 kg), for one counted in items its mass per item. For a flow
    counted in mass it says nothing, and for other quantities there is no property to hold it."""
    quantity = units.quantity_of(flow.unit)
    if quantity == "volume":
        return FlowPropertyName.density
    if quantity == "item":
        return FlowPropertyName.mass_per_item
    return None
