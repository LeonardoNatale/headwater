"""A normalisation and weighting set: per indicator of one method, the number its score is divided
by and the weight it counts with in a single score.

Example, the toy set `toy-nw` on the method `toy-impacts`: climate change is divided by 10 and
weighs 0.6, ozone formation is divided by 0.01 and weighs 0.4, in Pt. The T-shirt's 1.19205 kg
CO2-eq and 0.00225 kg NMVOC-eq give 0.119205 and 0.225 normalised, 0.071523 and 0.09 Pt
weighted, and a single score of 0.161523 Pt. Each of these is a weighted sum of the method's own
indicators, so `set_indicators` gives it as one more indicator, with its terms.
"""

from collections.abc import Sequence
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from headwater.model.errors import InvalidDataError, Problem, RecordError
from headwater.model.method import Indicator, Method

SET_ID = r"^[A-Za-z0-9][A-Za-z0-9._-]*$"
"""A set's id names its folder, and starts the ids of its indicators: `toy-nw:single-score`."""


class PinnedMethod(BaseModel):
    """The method a set is for, by id, version and hash, as a derived release pins its parent."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class NwRow(BaseModel):
    """One indicator of a set. A normalised score is the score divided by `normalisation`, a
    weighted one the normalised score times `weight`. A set without normalisation weighs the
    scores as they are, and a set without weights gives normalised scores only."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    indicator: str = Field(min_length=1)
    normalisation: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    """What one score is divided by: for EF 3.1, one person's yearly impact."""
    weight: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    """What a normalised score is multiplied by in the single score. A weight given in percent,
    such as 20%, is 0.2."""

    @model_validator(mode="after")
    def _says_something(self) -> Self:
        if self.normalisation is None and self.weight is None:
            raise RecordError.one(
                "empty-row",
                f"set row {self.indicator}",
                "has neither a normalisation nor a weight",
            )
        return self


class NwSet(BaseModel):
    """A normalisation and weighting set for one method, pinned by the method's hash.

    Every row has a normalisation, or none has, and the same for weights, so the scores a single
    score adds up are all of one kind. `unit` is the unit of weighted scores and of the single
    score (`Pt` for EF 3.1), and is given exactly when the set has weights. A normalised score
    has no unit.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    id: str = Field(pattern=SET_ID)
    version: str = Field(min_length=1)
    method: PinnedMethod
    unit: str | None = Field(default=None, min_length=1)
    source: str = Field(min_length=1)
    """Where the numbers come from, for a person to check them."""
    rows: tuple[NwRow, ...]

    @property
    def normalises(self) -> bool:
        return bool(self.rows) and self.rows[0].normalisation is not None

    @property
    def weighs(self) -> bool:
        return bool(self.rows) and self.rows[0].weight is not None

    @model_validator(mode="after")
    def _rows_of_one_kind(self) -> Self:
        item = f"set {self.id}"
        problems: list[Problem] = []
        if not self.rows:
            problems.append(Problem("empty-set", item, "has no rows"))
        seen: set[str] = set()
        for row in self.rows:
            if row.indicator in seen:
                problems.append(
                    Problem("duplicate-indicator", f"set row {row.indicator}", "is given twice")
                )
            seen.add(row.indicator)
        for column in ("normalisation", "weight"):
            given = [r.indicator for r in self.rows if getattr(r, column) is not None]
            if given and len(given) < len(self.rows):
                blank = [r.indicator for r in self.rows if getattr(r, column) is None]
                problems.append(
                    Problem(
                        "mixed-set",
                        item,
                        f"gives a {column} to {', '.join(given)} and none to {', '.join(blank)}: "
                        "a single score would add scores of different kinds. Give every row one, "
                        "or none.",
                    )
                )
        if self.weighs and not self.unit:
            problems.append(
                Problem(
                    "missing-unit", item, "has weights, so it needs the unit of the single score"
                )
            )
        if self.unit and self.rows and not any(r.weight is not None for r in self.rows):
            problems.append(
                Problem(
                    "unit-not-used",
                    item,
                    f"has no weights, so its unit {self.unit!r} has nothing to apply to: a "
                    "normalised score has no unit",
                )
            )
        if problems:
            raise RecordError(problems)
        return self


class SetIndicator(Indicator):
    """An indicator a set works out from its method's: a weighted sum of their scores.

    Example: `toy-nw:single-score` is 0.06 × climate change + 40 × ozone formation, in Pt. A
    normalised score has no unit, so `unit` may be empty.
    """

    unit: str
    nw_set: str
    """The id of the set."""
    terms: tuple[tuple[str, float], ...]
    """Each indicator of the method it adds up, with the number its score is multiplied by."""
    left_out: tuple[str, ...]
    """The indicators of the method the set has no row for: they count in none of its scores."""


def set_indicators(nw_set: NwSet, method: Method) -> tuple[SetIndicator, ...]:
    """The indicators `nw_set` adds to `method`, its normalised scores first, then its weighted
    ones, then its single score, each in the order of the method's indicators.

    Raises InvalidDataError naming every row whose indicator the method does not have, and an id
    the method already has. The method's indicators the set has no row for are each indicator's
    `left_out`: they count in none of its scores, as EF 3.1's climate sub-indicators count in its
    total only.
    """
    known = {i.id: i for i in method.indicators}
    problems = [
        Problem(
            "unknown-indicator",
            f"set {nw_set.id} row {row.indicator}",
            f"is not an indicator of method {method.id} (it has: {', '.join(known)})",
        )
        for row in nw_set.rows
        if row.indicator not in known
    ]
    if problems:
        raise InvalidDataError(problems)
    by_id = {row.indicator: row for row in nw_set.rows}
    rows = [(known[i.id], by_id[i.id]) for i in method.indicators if i.id in by_id]
    left_out = tuple(i.id for i in method.indicators if i.id not in by_id)
    unit = nw_set.unit or ""
    found: list[SetIndicator] = []

    def add(id_: str, name: str, unit: str, terms: Sequence[tuple[str, float]]) -> None:
        found.append(
            SetIndicator(
                id=f"{nw_set.id}:{id_}",
                name=f"{name} ({nw_set.id})",
                unit=unit,
                nw_set=nw_set.id,
                terms=tuple(terms),
                left_out=left_out,
            )
        )

    if nw_set.normalises:
        for indicator, row in rows:
            assert row.normalisation is not None
            add(
                f"normalised:{indicator.id}",
                f"{indicator.name}, normalised",
                "",
                [(indicator.id, 1.0 / row.normalisation)],
            )
    if nw_set.weighs:
        weighted = [(indicator.id, _coefficient(row)) for indicator, row in rows]
        for (indicator, _), term in zip(rows, weighted, strict=True):
            add(f"weighted:{indicator.id}", f"{indicator.name}, weighted", unit, [term])
        add("single-score", "Single score", unit, weighted)
    clash = [i.id for i in found if i.id in known]
    if clash:
        raise InvalidDataError(
            Problem(
                "duplicate-indicator",
                f"indicator {i}",
                f"is an indicator of method {method.id} and of set {nw_set.id}",
            )
            for i in clash
        )
    return tuple(found)


def _coefficient(row: NwRow) -> float:
    """What a score is multiplied by in its weighted score: the weight, over the normalisation
    when there is one."""
    assert row.weight is not None
    return row.weight / row.normalisation if row.normalisation is not None else row.weight
