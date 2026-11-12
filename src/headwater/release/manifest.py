"""release.json: what a release is, what it holds, and its hash."""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from headwater.model import Indicator

FORMAT = "headwater-release/2"

FOLDER_NAME = r"^[A-Za-z0-9][A-Za-z0-9._-]*$"
"""A name that is safe as a folder name on every system, with no `@` and no `/`."""

HASH = r"^[0-9a-f]{64}$"


class ReleaseKind(StrEnum):
    source = "source"
    """Imported from a source such as ecoinvent: processes, flows, exchanges, issues, methods."""

    indicators = "indicators"
    """Solved indicator vectors for one method, pinned to the source release they came from."""


class ReleaseInfo(BaseModel):
    """What a release is a version of. Every field is part of its hash.

    Example: `ecoinvent-3.12-cutoff`, source ecoinvent, version 3.12, system model cutoff,
    licence ecoinvent EULA, flow list ecoinvent-3.12.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    name: str = Field(pattern=FOLDER_NAME)
    source: str = Field(min_length=1)
    version: str = Field(min_length=1)
    system_model: str | None = None
    licence: str = Field(min_length=1)
    flow_list: str = Field(min_length=1)


class ReleaseRef(BaseModel):
    """A release named by its full hash, written `name@hash`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(pattern=FOLDER_NAME)
    hash: str = Field(pattern=HASH)

    def __str__(self) -> str:
        return f"{self.name}@{self.hash}"


class MethodEntry(BaseModel):
    """A method held by a release: its metadata and indicators here, its factors in
    `methods/<id>/factors.parquet`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(pattern=FOLDER_NAME)
    version: str
    variant: str | None
    flow_list: str
    indicators: tuple[Indicator, ...]
    """Sorted by id."""
    hash: str = Field(pattern=HASH)


class TableEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    rows: int = Field(ge=0)
    content_sha256: str = Field(pattern=HASH)


class Manifest(ReleaseInfo):
    """The content of release.json."""

    format: Literal["headwater-release/2"]
    kind: ReleaseKind
    methods: tuple[MethodEntry, ...]
    """Sorted by id. An indicators release has exactly one: the method it was solved for."""
    parent: ReleaseRef | None
    """The source release an indicators release was solved from. None for a source release."""
    recipe: dict[str, JsonValue] | None
    """How an indicators release's scores are made, besides its parent and method: the engine
    version and the solver settings asked for. With them, it is the release's identity. None for
    a source release."""
    provenance: dict[str, JsonValue] | None
    """What the solve of an indicators release gave: the steps taken, the largest residual, the
    exact zeros. Recorded, not part of the hash: it differs between machines."""
    tables: dict[str, TableEntry]
    """Row count and digest of every table. Part of a source release's hash, not of an
    indicators release's, whose rows `verify()` still checks against them."""
    hash: str = Field(pattern=HASH)
    engine_version: str
    files: dict[str, str]
    """sha256 of every file's bytes, an integrity check. Not part of the hash."""

    @property
    def info(self) -> ReleaseInfo:
        return ReleaseInfo.model_validate(self.model_dump(include=set(ReleaseInfo.model_fields)))
