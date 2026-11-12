"""Brightway's unit names, and the unit of headwater's table that each one means.

Brightway writes `kilogram`, `kilowatt hour` and `cubic meter`. headwater (and ecoinvent) write
`kg`, `kWh` and `m3`. A name that is already a unit of the table is kept as it is. Anything else
is looked up here, so `Kilogram`, `kg` and `KG` all give `kg`, as bw2io's own normalisation does.
"""

from headwater.model import Units

_NAMES: dict[str, tuple[str, ...]] = {
    "kilogram": ("kg",),
    "kg": ("kg",),
    "gram": ("g",),
    "g": ("g",),
    "milligram": ("mg",),
    "mg": ("mg",),
    "ton": ("t",),
    "tonne": ("t",),
    "tonnes": ("t",),
    "t": ("t",),
    "kilowatt hour": ("kWh",),
    "kwh": ("kWh",),
    "megawatt hour": ("MWh",),
    "mwh": ("MWh",),
    "watt hour": ("Wh",),
    "wh": ("Wh",),
    "megajoule": ("MJ",),
    "mj": ("MJ",),
    "kilojoule": ("kJ",),
    "kj": ("kJ",),
    "gigajoule": ("GJ",),
    "gj": ("GJ",),
    "cubic meter": ("m3",),
    "cubic metre": ("m3",),
    "m3": ("m3",),
    "litre": ("l", "L"),
    "liter": ("l", "L"),
    "l": ("l", "L"),
    "square meter": ("m2",),
    "square metre": ("m2",),
    "m2": ("m2",),
    "hectare": ("ha",),
    "ha": ("ha",),
    "meter": ("m",),
    "metre": ("m",),
    "m": ("m",),
    "kilometer": ("km",),
    "kilometre": ("km",),
    "km": ("km",),
    "unit": ("unit",),
    "items": ("unit",),
    "item(s)": ("unit",),
    "p": ("unit",),
    "ton kilometer": ("tkm", "metric ton*km"),
    "t*km": ("tkm", "metric ton*km"),
    "tkm": ("tkm", "metric ton*km"),
    "metric ton*km": ("tkm", "metric ton*km"),
    "person kilometer": ("person*km",),
    "person*km": ("person*km",),
    "personkm": ("person*km",),
    "pkm": ("person*km",),
    "hour": ("hour",),
    "hr": ("hour",),
    "h": ("hour",),
    "kilogram day": ("kg*day",),
    "meter-year": ("m*year",),
    "square meter-year": ("m2*year",),
    "cubic meter-year": ("m3*year",),
    "kilometer-year": ("km*year",),
    "standard cubic meter": ("Sm3",),
    "kilo becquerel": ("kBq",),
    "guest night": ("guest night",),
}


def resolve_unit(text: str, units: Units) -> str | None:
    """The unit of `units` that Brightway's `text` means, or None when there is none.

    A name the table already has is returned as it is: `kg`, `metric ton*km`. Otherwise the
    Brightway name is looked up in lower case, and the first unit it can mean that the table has
    is returned: `ton kilometer` is `tkm`, or `metric ton*km` when the table has no `tkm`.
    """
    text = text.strip()
    if text in units:
        return text
    for candidate in _NAMES.get(text.lower(), ()):
        if candidate in units:
            return candidate
    return None
