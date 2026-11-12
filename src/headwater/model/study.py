from enum import StrEnum
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from headwater.model._types import FiniteNumber
from headwater.model.database import DATABASE_NAME, CutOff, MethodIds, PinField


class Boundary(StrEnum):
    cradle_to_gate = "cradle-to-gate"
    cradle_to_grave = "cradle-to-grave"
    gate_to_gate = "gate-to-gate"


PROCESS_ID = r"^[^:\s]+:\S+$"
"""A process id: the name of its database or release, a colon, then the rest."""


class Product(BaseModel):
    """One product a study scores: a process of a database or of a release, and how much of
    what it makes. Example: `{process: tshirt:tshirt, amount: 2}` is 2 T-shirts."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    process: str = Field(pattern=PROCESS_ID)
    """The id of the process that makes the product: `tshirt:tshirt`."""
    amount: FiniteNumber = Field(gt=0)
    """How much of what the process makes, in its unit."""


def _each_process_once(products: tuple[Product, ...]) -> tuple[Product, ...]:
    ids = [p.process for p in products]
    twice = sorted({i for i in ids if ids.count(i) > 1})
    if twice:
        raise ValueError(f"names {', '.join(twice)} twice: give each product once, with its amount")
    return products


Products = Annotated[tuple[Product, ...], Field(min_length=1), AfterValidator(_each_process_once)]


class Study(BaseModel):
    """`study.yaml`: the question a study asks of databases, ISO 14044's goal and scope, and the
    products that answer it, with their amounts. A study holds no processes.

    Example: `tshirt-footprint` scores `{process: tshirt:tshirt, amount: 1}` for the functional
    unit `1 T-shirt`, with `toy-climate`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    name: str = Field(pattern=DATABASE_NAME)
    goal: str = Field(min_length=1)
    functional_unit: str = Field(min_length=1)
    products: Products
    """The products that meet the functional unit, each a process id and an amount."""
    boundary: Boundary
    methods: Annotated[MethodIds, Field(min_length=1)]
    cut_off: CutOff
    """The criteria for leaving inputs out, and the largest share that may be left out in all.
    A database a product reaches may not allow more."""
    background: PinField | None = None
    """The release the products are in, when every product is a process of a release. A product
    of a database is scored on the release that database pins."""
    audience: str | None = None
    public_comparison: bool | None = None
    data_quality: str | None = None
    pcr: str | None = None
    assumptions: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
