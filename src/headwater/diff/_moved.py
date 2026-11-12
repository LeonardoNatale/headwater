"""What a release change did to one line of a study: the record a diff across releases lists.

Kept apart from the bridge, which reads releases, so that rendering a diff loads no release.
"""

from dataclasses import dataclass

# How a line moved, besides the rules of an upgrade that need no person, `same-id` and
# `same-dataset`, which keep their names:
NEW_STUDY = "new-study"
"""The supplier has no successor by the rules, and takes the one the new study buys from at the
same line: decided by a mapping row, or by a person."""
NO_SUCCESSOR = "no-successor"
"""The supplier has no successor, and the new study no longer has the line."""
FACTOR = "factor"
"""An emission of the study, moved by the change of its factor."""
COPY = "override"
"""A copy of a background process, made by an override, moved as a whole."""


@dataclass(frozen=True)
class Moved:
    """What the release change did to one line of the study, alone.

    Example: `tshirt:tshirt` line `e02`, flow `toy:cotton-fabric` in `kg`, supplier
    `toy:1:fabric-a` then `toy:2:fabric-b`, 3.5 then 2.75 per kg, effect -0.1875, `same-dataset`.
    """

    process_id: str
    line_id: str | None
    """None for a copy that an override makes: its lines move as a whole."""
    flow_id: str
    unit: str
    old_supplier: str | None
    """None for an emission."""
    new_supplier: str | None
    """None for an emission, and for a line with no successor."""
    old_value: float
    """What one unit of the supplier scored, or the factor of the emission."""
    new_value: float
    effect: float
    how: str
    """`same-id` or `same-dataset` (the rule that moved the supplier), `new-study`,
    `no-successor`, `factor` or `override`."""
