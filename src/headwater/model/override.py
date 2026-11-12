from pydantic import BaseModel, ConfigDict, Field, model_validator

from headwater.model.errors import InvalidDataError, Problem, RecordError, did_you_mean
from headwater.model.process import Exchange, Process
from headwater.model.roles import Role


class Override(BaseModel):
    """Changed lines of one background process, for one study.

    Each line is the whole new line, as written in a process file: a blank supplier means no
    supplier. The study gets its own copy of the process with these lines in place of the
    release's, and only the study's own purchases use the copy. The background stays as it is.
    Example: line `e02` of the fabric production, now 2.5 kWh of electricity per kg.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    process_id: str = Field(min_length=1)
    lines: tuple[Exchange, ...]

    @model_validator(mode="after")
    def _lines_fit(self) -> "Override":
        item = f"override of {self.process_id}"
        problems: list[Problem] = []
        if not self.lines:
            problems.append(Problem("invalid-field", item, "needs at least one line"))
        seen: set[str] = set()
        for e in self.lines:
            if e.process_id != self.process_id:
                problems.append(
                    Problem(
                        "wrong-process",
                        e.label,
                        f"line belongs to process {e.process_id!r}, but sits in the override "
                        f"of {self.process_id!r}",
                    )
                )
            if e.line_id in seen:
                problems.append(
                    Problem("duplicate-line", e.label, f"line id {e.line_id!r} is used twice")
                )
            seen.add(e.line_id)
            if e.role is Role.reference:
                problems.append(
                    Problem(
                        "override-reference-line",
                        e.label,
                        "is the reference line, which is what the process makes. An override "
                        "changes the other lines.",
                    )
                )
        if problems:
            raise RecordError(problems)
        return self

    def apply_to(self, process: Process) -> Process:
        """`process` with the changed lines in place of its own.

        Raises `InvalidDataError` naming a line that `process` does not have. Whether a changed
        line still fits its flow, unit and supplier is checked by study validation.
        """
        known = {e.line_id for e in process.exchanges}
        problems = [
            Problem(
                "unknown-line",
                e.label,
                f"{process.id!r} has no line {e.line_id!r}.{did_you_mean(e.line_id, known)} "
                f"Its lines: {', '.join(sorted(known))}",
            )
            for e in self.lines
            if e.line_id not in known
        ]
        if process.id != self.process_id:
            problems.append(
                Problem(
                    "wrong-process",
                    f"override of {self.process_id}",
                    f"cannot be applied to process {process.id!r}",
                )
            )
        if problems:
            raise InvalidDataError(problems)
        new = {e.line_id: e for e in self.lines}
        return Process.model_validate(
            {
                **process.model_dump(exclude={"exchanges"}),
                "exchanges": [
                    _changed(e, new[e.line_id]) if e.line_id in new else e
                    for e in process.exchanges
                ],
            }
        )


def _changed(original: Exchange, row: Exchange) -> Exchange:
    """The row in place of the original line. A row that only changes the supplier keeps the
    original's distribution; any other row is the line as written, fixed unless it gives one."""
    if (
        row.uncertainty is None
        and row.formula is None
        and row.amount == original.amount
        and row.unit == original.unit
    ):
        return row.model_copy(update={"uncertainty": original.uncertainty})
    return row
