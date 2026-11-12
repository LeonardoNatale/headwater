"""The unit check, and the unit table of a source.

The unit check: every line's unit is known and converts to its flow's unit.

Example: electricity bought in MJ converts to kWh, since both are energy. Electricity bought in kg
does not, unless the flow has a property that converts mass to energy. Amounts keep the unit
written in the source: they are converted when the matrices are built.

The unit table: a source's conversions between units, made into one table that agrees with the
built-in units. Example: ecoSpold2's `1 kWh = 3.6 MJ` puts kWh in the quantity `energy`, with
factor 3.6 to MJ, its reference.
"""

from collections import Counter, defaultdict, deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc

from headwater.io.source import lookup
from headwater.model import (
    DEFAULT_UNITS,
    Catalog,
    Exchange,
    Flow,
    FlowProperty,
    InvalidDataError,
    Problem,
    Unit,
    Units,
)

compute: Any = pc

TOLERANCE = 1e-9
"""Relative difference above which two conversions of the same unit disagree."""


@dataclass(frozen=True)
class Conversion:
    """`1 unit_from = factor unit_to`, as a source gives it: ecoSpold2's UnitConversions.xml, or
    an openLCA unit group."""

    unit_from: str
    unit_to: str
    factor: float
    quantity: str


def check_units(
    lines: pa.Table, flows: Sequence[Flow], properties: Sequence[FlowProperty], units: Units
) -> tuple[Problem, ...]:
    """A problem per line whose unit is unknown or does not convert to its flow's unit. Only
    lines in another unit than their flow's become records, and they are not validated: a bad
    amount is refused when freezing, with every other row check."""
    expected = lookup(lines.column("flow_id"), {f.id: f.unit for f in flows})
    other = lines.filter(
        compute.fill_null(compute.not_equal(lines.column("unit"), expected), False)
    )
    if not other.num_rows:
        return ()
    catalog = Catalog(flows, (), properties, units)
    problems: list[Problem] = []
    for r in other.select(
        ["process_id", "line_id", "role", "flow_id", "amount", "unit"]
    ).to_pylist():
        line = Exchange.model_construct(
            process_id=r["process_id"],
            line_id=r["line_id"],
            role=r["role"],
            flow_id=r["flow_id"],
            amount=r["amount"],
            unit=r["unit"],
        )
        try:
            catalog.convert(line)
        except InvalidDataError as error:
            problems.extend(error.problems)
    return tuple(problems)


def unit_table(conversions: Iterable[Conversion], names: Iterable[str], *, source: str) -> Units:
    """Every unit of the conversions, plus `names`, with its quantity and factor.

    Units that convert into each other form one quantity. Where a group holds units of the
    built-in table (kg, MJ, m3, ...), it takes their quantity name and reference unit, and those
    units keep their built-in record, so the two tables never conflict. Other groups take
    the most common quantity of their conversions and the unit most converted to as reference. A
    unit nothing converts to or from is a quantity of its own. Raises naming every unit defined
    two ways.
    """
    conversions = tuple(conversions)
    neighbours: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for c in conversions:
        neighbours[c.unit_from].append((c.unit_to, c.factor))  # 1 from = factor to
        neighbours[c.unit_to].append((c.unit_from, 1 / c.factor))
    problems: list[Problem] = []
    units: dict[str, Unit] = {}
    quantities: dict[str, str] = {}  # quantity name -> the unit that first named it
    done: set[str] = set()
    for start in sorted(neighbours):
        if start in done:
            continue
        group = _group(start, neighbours)
        done.update(group)
        quantity, reference, scale = _anchor(group, conversions)
        values = _values(reference, neighbours)
        if quantity in quantities and quantities[quantity] not in group:
            problems.append(
                Problem(
                    "unit-conflict",
                    f"unit {reference!r}",
                    f"its quantity {quantity!r} is also the quantity of "
                    f"{quantities[quantity]!r}, but no conversion links the two",
                )
            )
        quantities.setdefault(quantity, reference)
        for name in sorted(group):
            factor = values[name] * scale
            default = DEFAULT_UNITS.get(name)
            if default is not None:
                if default.quantity != quantity or not _close(factor, default.factor_to_reference):
                    problems.append(
                        Problem(
                            "unit-conflict",
                            f"unit {name!r}",
                            f"{source} makes 1 {name} = {factor:.12g} of the reference "
                            f"of {quantity}; the built-in table has {default.quantity} with "
                            f"factor {default.factor_to_reference:.12g}",
                        )
                    )
                units[name] = default
            else:
                units[name] = Unit(name=name, quantity=quantity, factor_to_reference=factor)
    for c in conversions:
        a, b = units.get(c.unit_from), units.get(c.unit_to)
        if a is None or b is None or a.quantity != b.quantity:
            continue
        implied = a.factor_to_reference / b.factor_to_reference
        if not _close(implied, c.factor):
            item = f"unit {c.unit_from!r}"
            if not any(p.item == item for p in problems):
                problems.append(
                    Problem(
                        "unit-conflict",
                        item,
                        f"{source} gives 1 {c.unit_from} = {c.factor:.12g} "
                        f"{c.unit_to}, but other conversions give {implied:.12g} {c.unit_to}",
                    )
                )
    for name in sorted(set(names) - set(units)):
        units[name] = DEFAULT_UNITS.get(name) or Unit(
            name=name, quantity=name, factor_to_reference=1.0
        )
    if problems:
        raise InvalidDataError(problems)
    return Units(units.values())


def _group(start: str, neighbours: Mapping[str, list[tuple[str, float]]]) -> set[str]:
    group, queue = {start}, deque([start])
    while queue:
        for other, _ in neighbours[queue.popleft()]:
            if other not in group:
                group.add(other)
                queue.append(other)
    return group


def _anchor(group: set[str], conversions: tuple[Conversion, ...]) -> tuple[str, str, float]:
    """The quantity of a group, the unit the walk starts from, and that unit's factor."""
    built_in = [u for name in sorted(group) if (u := DEFAULT_UNITS.get(name)) is not None]
    if built_in:
        reference = next((u for u in built_in if u.factor_to_reference == 1), built_in[0])
        return reference.quantity, reference.name, reference.factor_to_reference
    inside = [c for c in conversions if c.unit_from in group]
    types = Counter(c.quantity for c in inside)
    targets = Counter(c.unit_to for c in inside)
    quantity = min(types, key=lambda t: (-types[t], t))
    reference = min(group, key=lambda u: (-targets[u], u))
    return quantity, reference, 1.0


def _values(reference: str, neighbours: Mapping[str, list[tuple[str, float]]]) -> dict[str, float]:
    """How many `reference` make one of each unit of its group, walking the conversions from it."""
    values, queue = {reference: 1.0}, deque([reference])
    while queue:
        unit = queue.popleft()
        for other, factor in sorted(neighbours[unit]):
            if other not in values:
                values[other] = values[unit] / factor  # 1 unit = factor other
                queue.append(other)
    return values


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= TOLERANCE * max(abs(a), abs(b))
