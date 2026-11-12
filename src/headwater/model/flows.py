from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class FlowKind(StrEnum):
    product = "product"
    waste = "waste"
    elementary = "elementary"


class Flow(BaseModel):
    """A product, a waste or an elementary flow, with the unit its amounts are counted in."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    kind: FlowKind
    unit: str = Field(min_length=1)
    compartment: str | None = None
    cas: str | None = None


class FlowPropertyName(StrEnum):
    heating_value_net = "heating_value_net"
    heating_value_gross = "heating_value_gross"
    density = "density"
    mass_per_item = "mass_per_item"
    dry_mass = "dry_mass"
    water_content = "water_content"
    carbon_content_fossil = "carbon_content_fossil"
    carbon_content_biogenic = "carbon_content_biogenic"
    price = "price"


CONVERTING_PROPERTIES = frozenset(
    {
        FlowPropertyName.heating_value_net,
        FlowPropertyName.density,
        FlowPropertyName.mass_per_item,
    }
)
"""The properties that may turn an amount of one quantity into another.

Gross heating value is kept as data but never used for conversion, so a number cannot change
between net and gross by accident.
"""


class FlowProperty(BaseModel):
    """One reference unit of the flow holds `value` of `unit`.

    Example: natural gas, reference unit m3, `heating_value_net` 36 MJ. So 1 m3 = 36 MJ, and
    72 MJ of natural gas is 2 m3.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    flow_id: str = Field(min_length=1)
    property: FlowPropertyName
    value: float = Field(gt=0, allow_inf_nan=False)
    unit: str = Field(min_length=1)
