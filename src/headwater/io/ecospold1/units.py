"""ecoSpold1's unit names, as headwater names them.

Example: ecoinvent writes `p` for one piece and `kBq` for a thousand becquerels. `p` is headwater's
`unit`. A unit headwater does not know is kept as written, as a quantity of its own, unless a
conversion here links it: `m2a` (an area held for a year) is not `m2`, so it never converts to one.
"""

from collections.abc import Iterable
from functools import cache

from headwater.io import units as shared
from headwater.io.units import Conversion
from headwater.model import DEFAULT_UNITS, Units

ALIASES = {"p": "unit", "Nm3": "m3"}
"""ecoSpold1 unit names that headwater writes another way."""

APPROXIMATE = {
    "Nm3": "is written m3: this source writes the same flows in both Nm3 and m3, so for them to "
    "be one flow and to link, the two are one unit"
}
"""Aliases that rename a unit by a convention of the source, not by what it means, with what to
tell people when a source uses one."""

CONVERSIONS = (Conversion(unit_from="kBq", unit_to="Bq", factor=1000, quantity="radioactivity"),)
"""The units of ecoSpold1 that headwater's built-in table lacks, with how they convert."""

_KNOWN = shared.unit_table(CONVERSIONS, (), source="ecoSpold1")


def canonical(name: str) -> str:
    return ALIASES.get(name, name)


@cache
def quantity(name: str) -> str:
    """The quantity of a unit: its own when headwater knows it, else a quantity named like it."""
    unit = DEFAULT_UNITS.get(name) or _KNOWN.get(name)
    return unit.quantity if unit is not None else name


def unit_table(names: Iterable[str]) -> Units:
    """The units a source uses, by headwater's names."""
    used = set(names)
    known = (c for c in CONVERSIONS if {c.unit_from, c.unit_to} <= used)
    return shared.unit_table(known, used, source="ecoSpold1")
