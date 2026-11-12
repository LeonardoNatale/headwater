from enum import StrEnum
from typing import Annotated

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field


class CutOffCriterion(StrEnum):
    mass = "mass"
    energy = "energy"
    environmental_significance = "environmental_significance"


class Pin(BaseModel):
    """A release pinned by version, written `name@version`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    version: str = Field(min_length=1)

    @classmethod
    def parse(cls, text: str) -> "Pin":
        parts = text.strip().split("@")
        if len(parts) != 2 or not all(p and not any(c.isspace() for c in p) for p in parts):
            raise ValueError(f"must be written name@version, got {text!r}")
        return cls(name=parts[0], version=parts[1])

    def __str__(self) -> str:
        return f"{self.name}@{self.version}"


def _pin_from_text(value: object) -> object:
    return Pin.parse(value) if isinstance(value, str) else value


PinField = Annotated[Pin, BeforeValidator(_pin_from_text)]

DATABASE_NAME = r"^[A-Za-z0-9][A-Za-z0-9._-]*$"
"""A database's name, the start of its process ids, which is also the name of its folder when
another database uses it or a study names its products."""


def _used_database(value: object) -> object:
    if isinstance(value, str) and "@" in value:
        raise ValueError(
            f"{value!r} is a pinned version. An entry of uses is the name of a database folder "
            f"next to this one, such as {value.split('@')[0]!r}: git versions both databases"
        )
    return value


UsedDatabase = Annotated[str, BeforeValidator(_used_database), Field(pattern=DATABASE_NAME)]


def _no_repeats(value: tuple[str, ...]) -> tuple[str, ...]:
    twice = sorted({name for name in value if value.count(name) > 1})
    if twice:
        raise ValueError(f"names {', '.join(twice)} twice")
    return value


Uses = Annotated[tuple[UsedDatabase, ...], AfterValidator(_no_repeats)]
"""The `uses` of a database: folder names, each once."""

MethodId = Annotated[str, Field(min_length=1)]
MethodIds = Annotated[tuple[MethodId, ...], AfterValidator(_no_repeats)]
"""Method ids, each once: `[EF-3.1, IPCC-2021]`."""


class Allocation(BaseModel):
    """How a multi-output process of a database is split: text for people, which no calculation
    reads. Example: `{rule: physical, basis: mass}`."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    rule: str = Field(min_length=1)
    basis: str | None = None


class CutOff(BaseModel):
    """The criteria for leaving inputs out, and the largest share that may be left out in all."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    criteria: tuple[CutOffCriterion, ...]
    max_cumulative: float = Field(ge=0, le=1, allow_inf_nan=False)


class Database(BaseModel):
    """`database.yaml`: what a folder of processes is, and what it is scored on.

    Example, the toy T-shirt: `name: tshirt`, `background: toy-background@fixture`,
    `methods: [toy-climate]`. Every process of the database is a product, scored per unit of
    what it makes.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    name: str = Field(pattern=DATABASE_NAME)
    """The start of every process id of the database: `tshirt` gives `tshirt:tshirt`."""
    background: PinField
    """The release the database buys from, `name@hash`, or `toy-background@fixture` for the toy
    folder."""
    uses: Uses = ()
    """The databases whose processes this one buys from: folders next to this one, by name.
    `[dyes]` is the database in `../dyes`."""
    methods: MethodIds = ()
    """The methods the database is scored with when no other is given, such as by CI:
    `[toy-climate]`. A database with none is still checked."""
    description: str | None = None
    allocation: Allocation | None = None
    """How the database's multi-output processes are split, as text no calculation reads."""
    end_of_life: str | None = None
    """The end-of-life rule its processes follow, as text no calculation reads."""
    cut_off: CutOff | None = None
    """The criteria for leaving inputs out, and the largest share that may be left out in all.
    Needed only when `cut_offs.csv` has rows."""
