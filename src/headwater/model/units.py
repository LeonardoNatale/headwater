from collections.abc import Iterable, Iterator

from pydantic import BaseModel, ConfigDict, Field

from headwater.model.errors import InvalidDataError, Problem, did_you_mean


class Unit(BaseModel):
    """A unit of one quantity. `factor_to_reference` is how many reference units make one of it.

    Example: `g` is mass with factor 0.001, because 1 g = 0.001 kg.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1)
    quantity: str = Field(min_length=1)
    factor_to_reference: float = Field(gt=0, allow_inf_nan=False)


class Units:
    """A table of units. Converts amounts between units of the same quantity."""

    def __init__(self, units: Iterable[Unit] = ()) -> None:
        by_name: dict[str, Unit] = {}
        problems: list[Problem] = []
        for unit in units:
            known = by_name.setdefault(unit.name, unit)
            if known != unit:
                problems.append(_conflict(known, unit))
        if problems:
            raise InvalidDataError(problems)
        self._by_name = by_name

    def get(self, name: str) -> Unit | None:
        return self._by_name.get(name)

    def __contains__(self, name: object) -> bool:
        return name in self._by_name

    def __iter__(self) -> Iterator[Unit]:
        return iter(self._by_name.values())

    def __len__(self) -> int:
        return len(self._by_name)

    @property
    def names(self) -> list[str]:
        return sorted(self._by_name)

    def quantity_of(self, name: str) -> str:
        return self._by_name[name].quantity

    def merge(self, other: "Units") -> "Units":
        """The units of both tables. A unit defined two different ways is an error."""
        return Units([*self, *other])

    def convert(self, amount: float, unit: str, to: str) -> float:
        """`amount` of `unit`, expressed in `to`. Both must be units of the same quantity."""
        source = self._lookup(unit)
        target = self._lookup(to)
        if source.quantity != target.quantity:
            raise InvalidDataError(
                [
                    Problem(
                        code="unit-quantity-mismatch",
                        item=f"unit {unit!r}",
                        message=(
                            f"{unit!r} is {source.quantity} and {to!r} is {target.quantity}, "
                            "so one cannot be converted into the other"
                        ),
                    )
                ]
            )
        if unit == to:
            return amount  # no arithmetic, so an amount in its own unit is never rounded
        return amount * source.factor_to_reference / target.factor_to_reference

    def unknown(self, name: str, item: str, where: str = "") -> Problem:
        """The problem for a unit that is not in this table. `where` adds context: ` (flow x)`."""
        return Problem(
            code="unknown-unit",
            item=item,
            message=f"unknown unit {name!r}{where}.{did_you_mean(name, self._by_name)}",
        )

    def _lookup(self, name: str) -> Unit:
        unit = self._by_name.get(name)
        if unit is None:
            raise InvalidDataError([self.unknown(name, f"unit {name!r}")])
        return unit


def _conflict(first: Unit, second: Unit) -> Problem:
    return Problem(
        code="unit-conflict",
        item=f"unit {first.name!r}",
        message=(
            f"defined two ways: {first.quantity} with factor_to_reference "
            f"{first.factor_to_reference:g}, and {second.quantity} with factor_to_reference "
            f"{second.factor_to_reference:g}"
        ),
    )


def _unit(name: str, quantity: str, factor: float) -> Unit:
    return Unit(name=name, quantity=quantity, factor_to_reference=factor)


# One reference unit per quantity (factor 1): kg, MJ, m3, m2, m, unit, tkm.
# `tkm` is a tonne-kilometre of freight. Its quantity has the name ecoinvent gives to the same
# work (`freight-transport-work`), so `tkm` converts to ecoinvent's `metric ton*km` at 1 to 1.
DEFAULT_UNITS = Units(
    [
        _unit("kg", "mass", 1),
        _unit("g", "mass", 0.001),
        _unit("mg", "mass", 1e-6),
        _unit("t", "mass", 1000),
        _unit("MJ", "energy", 1),
        _unit("kJ", "energy", 0.001),
        _unit("GJ", "energy", 1000),
        _unit("Wh", "energy", 0.0036),
        _unit("kWh", "energy", 3.6),
        _unit("MWh", "energy", 3600),
        _unit("m3", "volume", 1),
        _unit("L", "volume", 0.001),
        _unit("m2", "area", 1),
        _unit("ha", "area", 10000),
        _unit("m", "length", 1),
        _unit("km", "length", 1000),
        _unit("unit", "item", 1),
        _unit("tkm", "freight-transport-work", 1),
    ]
)
