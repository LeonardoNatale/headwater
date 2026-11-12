"""The one place where an exchange's role becomes a sign in a matrix.

Inside the matrices headwater uses ecoinvent's convention. Example, the T-shirt process:
1 T-shirt made is +1 on the diagonal, 0.25 kg fabric bought is -0.25, and 0.05 kg scraps sent to
incineration is +0.05 in the row of textile waste, whose treatment has -1 on its diagonal.
"""

import math
from dataclasses import dataclass
from enum import StrEnum

from headwater.model.roles import ROLES_ALLOWING_NEGATIVE, Role


class Matrix(StrEnum):
    """Which matrix an entry belongs to."""

    technosphere = "technosphere"
    """A: one column per process, one row per product."""

    biosphere = "biosphere"
    """B: one column per process, one row per elementary flow."""


@dataclass(frozen=True, slots=True)
class Entry:
    """A signed value for one matrix. The caller places it at (product or flow, process)."""

    matrix: Matrix
    value: float


def matrix_entry(role: Role, amount: float, *, treatment: bool = False) -> Entry | None:
    """Turn one exchange into its matrix entry.

    `amount` is positive, as in files and the API. Only the roles in `ROLES_ALLOWING_NEGATIVE`
    (`emission` and `resource`) may be negative. `treatment` says the process treats a waste: its
    reference is entered as -amount.

    - `reference`: +amount on the diagonal, -amount for a treatment.
    - `input`: -amount in the row of the product bought.
    - `own_use`: -amount, added to the diagonal so it reduces the net output.
    - `waste_to_treatment`: +amount in the row of the waste. With the treatment's -1 reference,
      kg sent times burden per kg treated, both positive, gives the same result.
    - `avoided_product`: +amount in the row of the displaced product, which credits the process.
    - `recyclable`: no entry. Under the cut-off rule it carries no burden and no credit.
    - `emission`, `resource`: the amount as given, in the biosphere.
    """
    if not math.isfinite(amount):
        raise ValueError(f"amount for role {role.value} must be a finite number, got {amount}")
    amount = float(amount)
    if amount < 0 and role not in ROLES_ALLOWING_NEGATIVE:
        raise ValueError(f"amount for role {role.value} must be zero or positive, got {amount}")
    match role:
        case Role.reference:
            return Entry(Matrix.technosphere, -amount if treatment else amount)
        case Role.input:
            return Entry(Matrix.technosphere, -amount)
        case Role.own_use:
            if treatment:
                raise ValueError("role own_use has no defined sign in a treatment process")
            return Entry(Matrix.technosphere, -amount)
        case Role.waste_to_treatment | Role.avoided_product:
            return Entry(Matrix.technosphere, amount)
        case Role.recyclable:
            return None
        case Role.emission | Role.resource:
            return Entry(Matrix.biosphere, amount)


@dataclass(frozen=True, slots=True)
class RoleEntry:
    """What one unit of a role becomes in a matrix, for a process that treats waste or not."""

    role: Role
    treatment: bool
    matrix: Matrix | None
    """None for a role with no entry, such as `recyclable`."""
    sign: float
    """The entry of one unit of the role: amount × sign is the matrix entry. 0 if no entry."""


def role_entries() -> tuple[RoleEntry, ...]:
    """The sign of every role, from `matrix_entry`, as a table.

    A caller that holds exchanges in a table, such as a DuckDB query over a release, joins this
    table instead of deciding signs itself. A pair with no defined sign is left out: own use in
    a treatment.
    """
    entries: list[RoleEntry] = []
    for role in Role:
        for treatment in (False, True):
            try:
                entry = matrix_entry(role, 1.0, treatment=treatment)
            except ValueError:
                continue
            matrix, sign = (entry.matrix, entry.value) if entry is not None else (None, 0.0)
            entries.append(RoleEntry(role, treatment, matrix, sign))
    return tuple(entries)
