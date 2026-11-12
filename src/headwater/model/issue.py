from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from headwater.model.database import CutOffCriterion
from headwater.model.errors import RecordError


class IssueStatus(StrEnum):
    open = "open"
    linked = "linked"
    cut_off = "cut_off"
    data_gap = "data_gap"


_RESOLUTION_FIELDS = (
    "supplier_id",
    "reason",
    "criterion",
    "share",
    "cumulative_share",
    "resolved_by",
    "resolved_at",
)

_REQUIRED: dict[IssueStatus, tuple[str, ...]] = {
    IssueStatus.open: (),
    IssueStatus.linked: ("supplier_id", "resolved_by", "resolved_at"),
    IssueStatus.cut_off: (
        "reason",
        "criterion",
        "share",
        "cumulative_share",
        "resolved_by",
        "resolved_at",
    ),
    IssueStatus.data_gap: ("reason", "resolved_by", "resolved_at"),
}


class Issue(BaseModel):
    """An input that was not linked, and how it was resolved.

    Example: `tshirt:tshirt` line `e04`, glue, cut off: 0.02% of the mass, who, when.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    process_id: str = Field(min_length=1)
    line_id: str = Field(min_length=1)
    flow_id: str = Field(min_length=1)
    status: IssueStatus
    supplier_id: str | None = None
    reason: str | None = None
    criterion: CutOffCriterion | None = None
    share: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    cumulative_share: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    resolved_by: str | None = None
    resolved_at: datetime | None = None

    @model_validator(mode="after")
    def _resolution_matches_the_status(self) -> "Issue":
        item = f"issue {self.process_id} line {self.line_id}"
        required = _REQUIRED[self.status]
        missing = [f for f in required if getattr(self, f) is None]
        if missing:
            raise RecordError.one(
                "incomplete-issue",
                item,
                f"status {self.status.value!r} needs {', '.join(missing)}",
            )
        if self.status is IssueStatus.open:
            extra = [f for f in _RESOLUTION_FIELDS if getattr(self, f) is not None]
            if extra:
                raise RecordError.one(
                    "incomplete-issue",
                    item,
                    f"status 'open' cannot carry a resolution, but has {', '.join(extra)}",
                )
        return self
