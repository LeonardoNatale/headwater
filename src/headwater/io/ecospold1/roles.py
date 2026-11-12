"""The group codes of an ecoSpold1 exchange and the roles they give.

A line says where it sits with `inputGroup` or `outputGroup`, from the format's documentation
(EcoSpold 1.3, data fields 3503 and 3504). The sign of the amount then decides between the roles a
group can have. This is the one place where ecoSpold1 turns groups and signs into roles.

| Group in the file                   | Role                                                  |
|-------------------------------------|-------------------------------------------------------|
| out0 reference product, positive    | `reference`                                           |
| out0 reference product, negative    | `reference`, its flow a waste: a treatment (1)        |
| out2 allocated by-product           | `reference`: the process makes several products       |
| out1 include avoided product system | `avoided_product`                                     |
| out3 waste to treatment             | `waste_to_treatment`, its flow a waste                |
| out4 to nature                      | `emission`                                            |
| in1 in2 in3 in5, zero or positive   | `input`, or `own_use` when it buys its own product (2)|
| in1 in2 in3 in5, negative           | `avoided_product` (3), or `waste_to_treatment` (4)    |
| in4 from nature                     | `resource`                                            |

(1) ecoSpold1 has no by-product classification, so a treatment is written as in ecoSpold2: a
negative reference. The amount is stored positive. A flow that comes out as a waste anywhere in the
source is a waste everywhere in it, so a line that uses it as a product is refused.

(2) The line gives the same product, location and unit as one of the process's own products.

(3) ecoSpold1 has no avoided-product marker: a negative input is how it writes a credit. It is the
same matrix entry as the negative input. The Excel import refuses a negative input instead, because
a person can mark it in the workbook; here background data cannot be fixed at the source.

(4) For an input of a waste flow: a waste sent to a treatment, stored positive.
"""

from dataclasses import dataclass
from enum import StrEnum

from headwater.model import Role

INPUT_GROUPS = {
    1: "Materials/fuels",
    2: "Electricity/heat",
    3: "Services",
    4: "From nature",
    5: "From technosphere",
}
OUTPUT_GROUPS = {
    0: "Reference product",
    1: "Include avoided product system",
    2: "Allocated by-product",
    3: "Waste to treatment",
    4: "To nature",
}
"""The codes, with the names the format's documentation gives them."""


class Slot(StrEnum):
    """Where a line sits: what its group says, before its sign is read."""

    reference = "reference"
    by_product = "by_product"
    avoided = "avoided"
    waste_out = "waste_out"
    emission = "emission"
    resource = "resource"
    input = "input"

    @property
    def elementary(self) -> bool:
        return self in (Slot.emission, Slot.resource)

    @property
    def makes(self) -> bool:
        """The line is a product of the process."""
        return self in (Slot.reference, Slot.by_product)


_INPUT = {1: Slot.input, 2: Slot.input, 3: Slot.input, 4: Slot.resource, 5: Slot.input}
_OUTPUT = {
    0: Slot.reference,
    1: Slot.avoided,
    2: Slot.by_product,
    3: Slot.waste_out,
    4: Slot.emission,
}


def slot_of(input_group: str | None, output_group: str | None) -> "Slot | Refused":
    """The slot of a line from the text of its two group elements, or why it has none."""
    if (input_group is None) == (output_group is None):
        both = "both an inputGroup and an outputGroup" if input_group else "no group"
        return Refused("unsupported-group", f"has {both}: a line is an input or an output")
    side, code, slots = (
        ("inputGroup", input_group, _INPUT)
        if input_group is not None
        else ("outputGroup", output_group, _OUTPUT)
    )
    assert code is not None
    found = slots.get(int(code)) if code.strip().isdecimal() else None
    if found is None:
        names = INPUT_GROUPS if input_group is not None else OUTPUT_GROUPS
        known = ", ".join(f"{n} {name}" for n, name in names.items())
        message = f"is in {side} {code.strip()!r}, which has no role. The groups are: {known}"
        return Refused("unsupported-group", message)
    return found


def is_waste_signal(slot: Slot, amount: float) -> bool:
    """The line says its flow is a waste: a treatment's negative reference, or a waste to treatment
    output."""
    return slot is Slot.waste_out or (slot is Slot.reference and amount < 0)


@dataclass(frozen=True)
class Assigned:
    role: Role
    negate: bool = False
    """The amount is stored as its absolute value, and its distribution turned the same way."""


@dataclass(frozen=True)
class Refused:
    code: str
    message: str


def assign(slot: Slot, amount: float, waste: bool) -> Assigned | Refused:
    """The role of a line in `slot` with this signed amount, `waste` saying its flow is a waste
    somewhere in the source. Raises nothing: a line with no role comes back as `Refused`."""
    if slot is Slot.emission:
        return Assigned(Role.emission)
    if slot is Slot.resource:
        return Assigned(Role.resource)
    if slot is Slot.input:
        if waste:
            if amount > 0:
                return _both("an input of a waste flow, with a positive amount")
            return Assigned(Role.waste_to_treatment, negate=True)
        return Assigned(Role.avoided_product, negate=True) if amount < 0 else Assigned(Role.input)
    if slot is Slot.reference and amount < 0:
        return Assigned(Role.reference, negate=True)  # the flow is a waste: see is_waste_signal
    if amount < 0:
        return Refused(
            "unexpected-sign",
            f"is a {_NAMES[slot]} with the amount {amount:g}, which has no role: only a "
            "reference product may be negative, as a treatment",
        )
    if slot is Slot.waste_out:
        return Assigned(Role.waste_to_treatment)
    if waste:
        return _both(f"a {_NAMES[slot]} with a positive amount")
    return Assigned(Role.avoided_product if slot is Slot.avoided else Role.reference)


_NAMES = {
    Slot.reference: "reference product",
    Slot.by_product: "by-product",
    Slot.avoided: "avoided product",
    Slot.waste_out: "waste to treatment",
}


def _both(what: str) -> Refused:
    return Refused(
        "waste-and-product",
        f"is {what}, but its flow is a waste elsewhere in the source: a waste is made by a "
        "treatment (a negative reference) and sent to it, never made or bought as a product",
    )
