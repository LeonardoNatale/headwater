from enum import StrEnum


class Role(StrEnum):
    """The role of one exchange. Signs are derived from roles in `calc`, never here."""

    reference = "reference"
    """What the process makes. For a treatment process, the waste it treats."""

    input = "input"
    """A product bought from a supplier."""

    own_use = "own_use"
    """The process uses some of its own product, for losses."""

    waste_to_treatment = "waste_to_treatment"
    """Waste sent to a treatment process, in kg sent."""

    recyclable = "recyclable"
    """Material sent to recycling under the cut-off rule, no burden."""

    avoided_product = "avoided_product"
    """A product displaced by this process (substitution)."""

    emission = "emission"
    """Elementary flow to nature. Can be negative."""

    resource = "resource"
    """Elementary flow from nature. Can be negative."""


ROLES_ALLOWING_NEGATIVE = frozenset({Role.emission, Role.resource})
"""Only emission and resource amounts may be negative: a crop takes CO2 from the air.

This is what a person may write in a file. The sign a role gets in the matrix is decided in `calc`.
"""
