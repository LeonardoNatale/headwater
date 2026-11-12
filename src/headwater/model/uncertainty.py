"""What an exchange may carry beyond its amount: uncertainty, pedigree, properties, link status.

All of it is optional. A study file can set a distribution, centred on the line's amount
(docs/design.md, Uncertainty); the ecoinvent importer sets all of it.
"""

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from headwater.model.errors import RecordError


class Distribution(StrEnum):
    lognormal = "lognormal"
    normal = "normal"
    uniform = "uniform"
    triangular = "triangular"


_NEEDS: dict[Distribution, tuple[str, ...]] = {
    Distribution.lognormal: ("loc", "scale"),
    Distribution.normal: ("loc", "scale"),
    Distribution.uniform: ("minimum", "maximum"),
    Distribution.triangular: ("loc", "minimum", "maximum"),
}


class Uncertainty(BaseModel):
    """The distribution of an exchange amount, with the parameter names of stats_arrays.

    - lognormal: `loc` is the mean of ln|x| (it may be negative), `scale` its standard deviation.
      Example: 3 kWh with a geometric standard deviation of 1.1 is loc ln(3), scale ln(1.1).
    - normal: `loc` is the mean, `scale` the standard deviation.
    - uniform: `minimum` and `maximum`.
    - triangular: `loc` is the mode, between `minimum` and `maximum`.

    `shape` is kept for distributions that need one. `hw.monte_carlo` draws from the four
    distributions (docs/calculation.md, Uncertainty).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    distribution: Distribution
    loc: float | None = Field(default=None, allow_inf_nan=False)
    scale: float | None = Field(default=None, allow_inf_nan=False)
    shape: float | None = Field(default=None, allow_inf_nan=False)
    minimum: float | None = Field(default=None, allow_inf_nan=False)
    maximum: float | None = Field(default=None, allow_inf_nan=False)

    @model_validator(mode="after")
    def _parameters_fit_the_distribution(self) -> "Uncertainty":
        item = f"{self.distribution.value} uncertainty"
        missing = [f for f in _NEEDS[self.distribution] if getattr(self, f) is None]
        if missing:
            raise RecordError.one("incomplete-uncertainty", item, f"needs {', '.join(missing)}")
        if self.scale is not None and self.scale <= 0:
            raise RecordError.one("bad-uncertainty", item, f"scale {self.scale:g} must be above 0")
        if self.minimum is not None and self.maximum is not None:
            if self.minimum > self.maximum:
                raise RecordError.one(
                    "bad-uncertainty",
                    item,
                    f"minimum {self.minimum:g} is above maximum {self.maximum:g}",
                )
            if self.distribution is Distribution.triangular and self.loc is not None:
                if not self.minimum <= self.loc <= self.maximum:
                    raise RecordError.one(
                        "bad-uncertainty",
                        item,
                        f"loc {self.loc:g} (the mode) is outside minimum {self.minimum:g} "
                        f"and maximum {self.maximum:g}",
                    )
        return self


_Score = Annotated[int, Field(ge=1, le=5)]


class Pedigree(BaseModel):
    """The five data quality scores of the ecoinvent pedigree matrix, 1 (best) to 5 (worst)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reliability: _Score
    completeness: _Score
    temporal_correlation: _Score
    geographical_correlation: _Score
    further_technological_correlation: _Score


class ExchangeProperty(BaseModel):
    """A property of the flow on one line: here, 1 kWh holds 3.6 MJ, or 1 kg costs 0.11 EUR2005.

    Names and units are kept as the source writes them, so nothing is dropped. They do not
    convert units: only a flow's own properties do.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1)
    value: float = Field(allow_inf_nan=False)
    unit: str = Field(min_length=1)


class LinkStatus(StrEnum):
    """How a line that buys from a supplier was resolved. Lines that buy nothing have none."""

    linked = "linked"
    cut_off = "cut_off"
    data_gap = "data_gap"
