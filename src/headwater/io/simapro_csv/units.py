"""SimaPro's units as headwater's: by name, from the export's `Units` block and the built-in table.

Example: SimaPro writes a litre `l`, a piece `p` and a tonne `ton`. They are headwater's `L`,
`unit` and `t`, and lines and flows carry those names. A unit the export defines in its `Units`
block (`kBq;Radioactivity;1;kBq`) keeps its definition, which must agree with the built-in table
where both have the unit. A unit that neither defines is refused, naming a line that uses it.
"""

from collections.abc import Mapping, Sequence

from headwater.io.simapro_csv.records import UnitDefinition
from headwater.io.units import Conversion, unit_table
from headwater.model import DEFAULT_UNITS, InvalidDataError, Units

ALIASES: Mapping[str, str] = {"l": "L", "p": "unit", "ton": "t"}
"""SimaPro's names for units headwater names otherwise."""


def headwater_unit(name: str) -> str:
    return ALIASES.get(name, name)


def read_units(defined: Sequence[UnitDefinition], used: Mapping[str, str]) -> Units:
    """The units of an export, by headwater's names. `used` gives, for each SimaPro unit name a
    line uses, the item of one such line. Raises `InvalidDataError` for a unit used and defined
    nowhere, and for a definition that disagrees with the built-in table."""
    conversions = [
        Conversion(headwater_unit(d.name), headwater_unit(d.reference), d.factor, d.quantity)
        for d in defined
    ]
    known = {c.unit_from for c in conversions} | {c.unit_to for c in conversions}
    names = {name: headwater_unit(name) for name in used}
    problems = [
        DEFAULT_UNITS.unknown(name, used[name], " (not in the export's Units block)")
        for name, unit in names.items()
        if unit not in known and unit not in DEFAULT_UNITS
    ]
    if problems:
        raise InvalidDataError(problems)
    return unit_table(conversions, sorted(set(names.values())), source="the Units block")
