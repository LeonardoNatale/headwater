from collections.abc import Mapping, Sequence
from functools import cached_property
from typing import Any, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    model_serializer,
    model_validator,
)

from headwater.model.errors import InvalidDataError, Problem, RecordError

JOIN = "+"
"""Joins the ids, and the versions, of methods used together. A method id cannot hold it."""


class Indicator(BaseModel):
    """One impact category or sub-indicator, with the unit its score is in.

    Example: `climate-change-fossil`, the category `climate change: fossil`, its category
    indicator `global warming potential (GWP100)`, in `kg CO2-Eq`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    category_indicator: str | None = Field(default=None, min_length=1)
    """What the category's score measures, as ISO 14044 names it, and the last part of
    Brightway's method tuple. ecoinvent's LCIA workbook gives it; other sources leave it None.
    None is written nowhere: not in `method.yaml`, `release.json` or the method's hash."""
    unit: str = Field(min_length=1)

    @model_serializer(mode="wrap")
    def _category_indicator_only_when_given(
        self, handler: SerializerFunctionWrapHandler
    ) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        if self.category_indicator is None:
            data.pop("category_indicator", None)
        return data


class Factor(BaseModel):
    """The characterisation factor of one flow for one indicator, optionally for one location."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    indicator: str = Field(min_length=1)
    flow_id: str = Field(min_length=1)
    location: str | None = None
    factor: float = Field(allow_inf_nan=False)

    @property
    def label(self) -> str:
        where = f" at {self.location}" if self.location else ""
        return f"factor {self.indicator}/{self.flow_id}{where}"


_WORKED_OUT = ("characterised_flow_ids", "factors_by_indicator")
"""The cached properties of `Method`, which a copy with other factors must not keep."""


class Method(BaseModel):
    """Indicators and factors, mapped to one flow list."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    variant: str | None = None
    flow_list: str = Field(min_length=1)
    indicators: tuple[Indicator, ...]
    factors: tuple[Factor, ...] = ()
    parts: tuple[str, ...] = ()
    """For several methods used together (`combine`), the id of each, in order. Empty for one
    method."""

    @property
    def ids(self) -> tuple[str, ...]:
        """The id of each method this holds: its own, or those of several used together."""
        return self.parts or (self.id,)

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> Self:
        """A copy with `update` made. Tables worked out from the factors and the indicators are
        not carried over to a copy that changes either: it works out its own."""
        copy = super().model_copy(update=update, deep=deep)
        if update and ("factors" in update or "indicators" in update):
            for name in _WORKED_OUT:
                vars(copy).pop(name, None)
        return copy

    @cached_property
    def characterised_flow_ids(self) -> frozenset[str]:
        """Flows that have a factor for at least one indicator. Worked out once, not on every
        access."""
        return frozenset(f.flow_id for f in self.factors)

    @cached_property
    def factors_by_indicator(self) -> Mapping[str, Mapping[str, float]]:
        """The factor of each flow, for each indicator, worked out once. A flow with no factor
        for an indicator is not in its table: it counts 0 for it. Factors for one location
        are read as global ones; `require_global_factors` refuses them first."""
        table: dict[str, dict[str, float]] = {i.id: {} for i in self.indicators}
        for factor in self.factors:
            table.setdefault(factor.indicator, {})[factor.flow_id] = factor.factor
        return table

    @model_validator(mode="after")
    def _factors_match_the_indicators(self) -> "Method":
        problems: list[Problem] = []
        if self.parts and self.id != JOIN.join(self.parts):
            problems.append(
                Problem(
                    "bad-method-id",
                    f"method {self.id}",
                    f"methods used together are named by their ids joined by {JOIN!r}: "
                    f"{JOIN.join(self.parts)}",
                )
            )
        if not self.parts and JOIN in self.id:
            problems.append(
                Problem(
                    "bad-method-id",
                    f"method {self.id}",
                    f"a method id cannot hold {JOIN!r}: it joins the ids of methods used together",
                )
            )
        indicators: set[str] = set()
        for indicator in self.indicators:
            if indicator.id in indicators:
                problems.append(
                    Problem(
                        "duplicate-indicator",
                        f"indicator {indicator.id}",
                        f"listed twice in method {self.id}",
                    )
                )
            indicators.add(indicator.id)
        seen: set[tuple[str, str, str | None]] = set()
        for factor in self.factors:
            if factor.indicator not in indicators:
                problems.append(
                    Problem(
                        "unknown-indicator",
                        factor.label,
                        f"{factor.indicator!r} is not an indicator of method {self.id} "
                        f"(it has: {', '.join(sorted(indicators))})",
                    )
                )
            key = (factor.indicator, factor.flow_id, factor.location)
            if key in seen:
                problems.append(
                    Problem(
                        "duplicate-factor", factor.label, f"has more than one value in {self.id}"
                    )
                )
            seen.add(key)
        if problems:
            raise RecordError(problems)
        return self


def combine(methods: Sequence[Method]) -> Method:
    """Several methods as one, so a background scores all their indicators at once.

    Example: EF-3.1 and IPCC-2021, which holds `climate-change-direct-land-use-change`, give the
    method `EF-3.1+IPCC-2021`, version `3.1+2021`, with the indicators and factors of both, in
    that order, and `parts` EF-3.1 and IPCC-2021. Each indicator keeps its id, so it is found in
    the method it came from. The joined id names the methods in results; files list the ids.
    One method is returned as it is.

    Raises InvalidDataError naming both methods when two of them have an indicator with the same
    id, and naming the flow lists when they differ: their factors would not be for the same
    flows.
    """
    if not methods:
        raise ValueError("combine needs at least one method")
    if len(methods) == 1:
        return methods[0]
    problems: list[Problem] = []
    first = methods[0]
    for method in methods[1:]:
        if method.flow_list != first.flow_list:
            problems.append(
                Problem(
                    "flow-list-mismatch",
                    f"method {method.id}",
                    f"is mapped to flow list {method.flow_list!r}, and method {first.id} to "
                    f"{first.flow_list!r}: methods used together need one flow list",
                )
            )
    owner: dict[str, str] = {}
    seen: set[str] = set()
    for method in methods:
        if method.id in seen:
            problems.append(Problem("duplicate-method", f"method {method.id}", "is given twice"))
            continue
        seen.add(method.id)
        for indicator in method.indicators:
            if indicator.id in owner:
                problems.append(
                    Problem(
                        "duplicate-indicator",
                        f"indicator {indicator.id}",
                        f"is in method {owner[indicator.id]} and in method {method.id}. Import "
                        "one of them without its category, with --category",
                    )
                )
            owner.setdefault(indicator.id, method.id)
    if problems:
        raise InvalidDataError(problems)
    return Method(
        id=JOIN.join(m.id for m in methods),
        version=JOIN.join(m.version for m in methods),
        variant=None,
        flow_list=first.flow_list,
        indicators=tuple(i for m in methods for i in m.indicators),
        factors=tuple(f for m in methods for f in m.factors),
        parts=tuple(m.id for m in methods),
    )
