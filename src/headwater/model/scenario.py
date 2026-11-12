from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from headwater.model._types import FiniteNumber
from headwater.model.errors import RecordError
from headwater.model.formula import Formula
from headwater.model.process import Exchange


def _parses(text: str | None) -> str | None:
    if text is not None:
        Formula.parse(text)
    return text


class LineChange(BaseModel):
    """A new amount, unit or supplier for one line of a process.

    The process may be one of the study's, or one of the background. A change to the background
    is global: it changes that process for every process that buys from it, in this scenario.
    `amount` is the number the line gets. When it came from a `formula`, the amount is what the
    formula gives with the scenario's parameters.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    process_id: str = Field(min_length=1)
    line_id: str = Field(min_length=1)
    amount: FiniteNumber | None = None
    formula: str | None = None
    unit: str | None = Field(default=None, min_length=1)
    supplier_id: str | None = Field(default=None, min_length=1)

    @property
    def label(self) -> str:
        return f"{self.process_id} line {self.line_id}"

    _formula_parses = field_validator("formula")(_parses)

    @model_validator(mode="after")
    def _says_what_changes(self) -> "LineChange":
        if self.amount is None and self.unit is None and self.supplier_id is None:
            raise RecordError.one(
                "invalid-field",
                self.label,
                "changes nothing. Give an amount, a unit or a supplier.",
            )
        if self.formula is not None and self.amount is None:
            raise RecordError.one(
                "invalid-field",
                self.label,
                "has a formula but no amount. The amount is what the formula gives.",
            )
        return self

    def apply_to(self, exchange: Exchange) -> Exchange:
        """The line with this change made. A new amount replaces the line's own formula. A new
        amount or unit also drops the line's distribution: the scenario fixes the line. A new
        supplier alone keeps it, since the amount is the same data."""
        update: dict[str, Any] = {}
        if self.amount is not None:
            update.update(amount=self.amount, formula=self.formula, uncertainty=None)
        if self.unit is not None:
            update.update(unit=self.unit, uncertainty=None)
        if self.supplier_id is not None:
            update["supplier_id"] = self.supplier_id
        return Exchange.model_validate({**exchange.model_dump(), **update})


class ParameterChange(BaseModel):
    """A new value, or a new formula, for one named parameter.

    `parameter` is the parameter's id: its name, or `<process id>/<name>` for the parameter of
    a process. `database` is the database that holds it, as a study's scenario resolves it:
    `tshirt` for `fabric_kg` of the T-shirt.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    parameter: str = Field(min_length=1)
    database: str | None = None
    value: FiniteNumber | None = None
    formula: str | None = None

    _formula_parses = field_validator("formula")(_parses)

    @staticmethod
    def as_values(changes: Iterable["ParameterChange"]) -> dict[str, float | str]:
        """A value, or the text of a formula, for each parameter id that the changes name. This
        is what `ParameterSet.with_changes` takes."""
        values: dict[str, float | str] = {}
        for change in changes:
            if change.formula is not None:
                values[change.parameter] = change.formula
            elif change.value is not None:
                values[change.parameter] = change.value
        return values

    @model_validator(mode="after")
    def _a_value_or_a_formula(self) -> "ParameterChange":
        if (self.value is None) == (self.formula is None):
            raise RecordError.one(
                "invalid-field",
                f"parameter {self.parameter}",
                "needs a value or a formula, and only one of them",
            )
        return self


class Scenario(BaseModel):
    """A named set of changes of a study: line amounts, suppliers and parameters, of the
    databases its products reach and of the background.

    Example: `clean-grid`, where the electricity process emits 0.3 kg CO2 per kWh instead of
    0.5. A line change to a background process is the only way to change the background.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    description: str | None = None
    changes: tuple[LineChange | ParameterChange, ...]

    @model_validator(mode="after")
    def _has_changes(self) -> "Scenario":
        if not self.changes:
            raise RecordError.one(
                "invalid-field", f"scenario {self.name}", "needs at least one change"
            )
        return self

    @property
    def line_changes(self) -> tuple[LineChange, ...]:
        return tuple(c for c in self.changes if isinstance(c, LineChange))

    @property
    def parameter_changes(self) -> tuple[ParameterChange, ...]:
        return tuple(c for c in self.changes if isinstance(c, ParameterChange))

    def parameter_values(self, database: str) -> dict[str, float | str]:
        """The changes to the parameters of `database`, as `ParameterSet.with_changes` takes
        them."""
        return ParameterChange.as_values(
            c for c in self.parameter_changes if c.database == database
        )
