from datetime import date
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from headwater.model.errors import Problem, RecordError
from headwater.model.flows import FlowKind
from headwater.model.formula import Formula
from headwater.model.roles import ROLES_ALLOWING_NEGATIVE, Role
from headwater.model.uncertainty import ExchangeProperty, LinkStatus, Pedigree, Uncertainty

ROLES_WITH_SUPPLIER = frozenset(
    {Role.input, Role.own_use, Role.waste_to_treatment, Role.avoided_product}
)
"""Roles that buy from, or send to, another process. Every other role takes no supplier."""

ROLE_FLOW_KINDS: dict[Role, frozenset[FlowKind]] = {
    Role.reference: frozenset({FlowKind.product, FlowKind.waste}),
    Role.input: frozenset({FlowKind.product}),
    Role.own_use: frozenset({FlowKind.product}),
    Role.waste_to_treatment: frozenset({FlowKind.waste}),
    Role.recyclable: frozenset({FlowKind.product, FlowKind.waste}),
    Role.avoided_product: frozenset({FlowKind.product}),
    Role.emission: frozenset({FlowKind.elementary}),
    Role.resource: frozenset({FlowKind.elementary}),
}
"""The kinds of flow each role may carry. A treatment process has a waste as its reference."""


class ProcessKind(StrEnum):
    """The special activity types of ecoSpold2, as snake_case names.

    "Treatment" is not a kind. A process is a treatment when its reference flow is a waste.
    """

    ordinary_transforming_activity = "ordinary_transforming_activity"
    market_activity = "market_activity"
    io_activity = "io_activity"
    residual_activity = "residual_activity"
    production_mix = "production_mix"
    import_activity = "import_activity"
    supply_mix = "supply_mix"
    export_activity = "export_activity"
    re_export_activity = "re_export_activity"
    correction_activity = "correction_activity"
    market_group = "market_group"


class Exchange(BaseModel):
    """One line of a process. Its id is the process id plus the line id."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    process_id: str = Field(min_length=1)
    line_id: str = Field(min_length=1)
    role: Role
    flow_id: str = Field(min_length=1)
    flow_name: str | None = None
    """The flow's name as written next to its id in a file. Validation checks it still matches."""
    amount: float = Field(allow_inf_nan=False)
    formula: str | None = None
    """The formula the amount came from, written in a study file. `amount` is what it gives with
    the study's parameters, so everything that reads amounts sees a number."""
    unit: str = Field(min_length=1)
    supplier_id: str | None = None
    comment: str | None = None
    uncertainty: Uncertainty | None = None
    pedigree: Pedigree | None = None
    properties: tuple[ExchangeProperty, ...] = ()
    """Properties of the flow on this line, sorted by name. A name appears once."""
    link_status: LinkStatus | None = None
    """Only on roles that take a supplier. `linked` has a supplier, `cut_off` and `data_gap`
    have none. None says nothing, as on every line of a study file."""

    @property
    def label(self) -> str:
        """How messages name this line, for example `tshirt:tshirt line e02`."""
        return f"{self.process_id} line {self.line_id}"

    @field_validator("formula", mode="after")
    @classmethod
    def _formula_is_valid(cls, text: str | None) -> str | None:
        if text is not None:
            Formula.parse(text)
        return text

    @model_validator(mode="after")
    def _amount_fits_the_role(self) -> "Exchange":
        if self.amount < 0 and self.role not in ROLES_ALLOWING_NEGATIVE:
            origin = f" (the formula {self.formula!r} gives it)" if self.formula else ""
            raise RecordError.one(
                "negative-amount",
                self.label,
                f"amount {self.amount:g}{origin} is negative, but role {self.role.value!r} "
                "needs a positive amount. Only emission and resource may be negative.",
            )
        if self.role is Role.reference and self.amount == 0:
            raise RecordError.one(
                "reference-amount",
                self.label,
                "the amount of the reference line must be above zero",
            )
        return self

    @field_validator("properties", mode="after")
    @classmethod
    def _properties_by_name(
        cls, properties: tuple[ExchangeProperty, ...]
    ) -> tuple[ExchangeProperty, ...]:
        return tuple(sorted(properties, key=lambda p: p.name))

    @model_validator(mode="after")
    def _properties_and_link_status_fit(self) -> "Exchange":
        names = [p.name for p in self.properties]
        twice = sorted({n for n in names if names.count(n) > 1})
        if twice:
            raise RecordError.one(
                "duplicate-property",
                self.label,
                f"property {', '.join(map(repr, twice))} is given more than once",
            )
        status = self.link_status
        if status is None:
            return self
        if self.role not in ROLES_WITH_SUPPLIER:
            raise RecordError.one(
                "link-status",
                self.label,
                f"role {self.role.value!r} buys from no supplier, so it has no link status, "
                f"but {status.value!r} is given",
            )
        if status is LinkStatus.linked and self.supplier_id is None:
            raise RecordError.one(
                "link-status", self.label, "link status 'linked' needs a supplier"
            )
        if status is not LinkStatus.linked and self.supplier_id is not None:
            raise RecordError.one(
                "link-status",
                self.label,
                f"link status {status.value!r} takes no supplier, but {self.supplier_id!r} "
                "is given",
            )
        return self


class ProcessDetails(BaseModel):
    """What `processes/<stem>.yaml` says about a process: not its exchanges.

    `location` is an ecoinvent geography short code such as `GLO`, `RER` or `TR`. It is not
    checked against a list yet.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1)
    location: str = Field(min_length=1)
    time_start: date | None = None
    time_end: date | None = None
    kind: ProcessKind

    @field_validator("name", "location", mode="before")
    @classmethod
    def _yaml_read_a_yes_no_word(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError(
                "YAML read an unquoted word as a yes/no value. Put quotes around it, "
                "for example location: 'NO' (the code of Norway)"
            )
        return value

    @field_validator("time_start", "time_end", mode="before")
    @classmethod
    def _a_year_is_not_a_date(cls, value: object) -> object:
        if isinstance(value, int) and not isinstance(value, bool):
            raise ValueError(
                f"{value} is not a date. Write a full ISO date, for example 2024-01-01"
            )
        return value

    @model_validator(mode="after")
    def _time_runs_forward(self) -> "ProcessDetails":
        if self.time_start and self.time_end and self.time_end < self.time_start:
            raise ValueError(f"time_end {self.time_end} is before time_start {self.time_start}")
        return self


class Process(ProcessDetails):
    """An activity making one reference flow, at a place and a time.

    Its id is the namespace plus the file name, for example `tshirt:tshirt`. The name is for people.
    """

    id: str = Field(min_length=1)
    exchanges: tuple[Exchange, ...]
    source_ref: str | None = None

    @property
    def reference_exchange(self) -> Exchange:
        return next(e for e in self.exchanges if e.role is Role.reference)

    @model_validator(mode="after")
    def _lines_are_consistent(self) -> "Process":
        problems: list[Problem] = []
        seen: set[str] = set()
        for e in self.exchanges:
            if e.process_id != self.id:
                problems.append(
                    Problem(
                        "wrong-process",
                        e.label,
                        f"line belongs to process {e.process_id!r}, "
                        f"but sits in process {self.id!r}",
                    )
                )
            if e.line_id in seen:
                problems.append(
                    Problem("duplicate-line", e.label, f"line id {e.line_id!r} is used twice")
                )
            seen.add(e.line_id)
        references = [e.line_id for e in self.exchanges if e.role is Role.reference]
        if len(references) != 1:
            found = ", ".join(references) if references else "none"
            problems.append(
                Problem(
                    "reference-count",
                    f"process {self.id}",
                    f"needs exactly one reference line, found {found}",
                )
            )
        if problems:
            raise RecordError(problems)
        return self
