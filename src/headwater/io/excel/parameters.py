"""Parameters: project, database and activity parameters as one `ParameterSet`.

A parameter with a value keeps its row's distribution, by the mapping of `uncertainty`. One on a
parameter whose value is a formula, or that a study cannot hold, is reported and not copied.
"""

from collections import defaultdict

from pydantic import ValidationError

from headwater.io.excel.records import Head, number
from headwater.io.excel.report import Note, ParameterOutcome
from headwater.io.excel.uncertainty import distribution
from headwater.io.excel.units import resolve_unit
from headwater.io.excel.workbook import BrightwayWorkbook, ParameterRow
from headwater.model import (
    Formula,
    FormulaError,
    InvalidDataError,
    Parameter,
    ParameterSet,
    Uncertainty,
    Units,
)


class ParameterReader:
    def __init__(self, book: BrightwayWorkbook, heads: list[Head], units: Units) -> None:
        self.book = book
        self.heads = heads
        self.units = units
        self.notes: list[Note] = []
        self.parameter_outcomes: list[ParameterOutcome] = []
        self.parameters = ParameterSet()
        self.blocked: dict[str, dict[str, str]] = defaultdict(dict)
        """For each activity, the parameters of it that could not be copied, with why."""

    def read(self) -> None:
        """Project, database and activity parameters as one `ParameterSet`, letting headwater's
        own evaluator say which of them it can hold."""
        owner = {h.activity.source: h.id for h in self.heads}
        database_names = {p.name for p in self.book.database_parameters}
        rows: list[tuple[ParameterRow, str | None, bool]] = [
            *((p, None, p.name in database_names) for p in self.book.project_parameters),
            *((p, None, False) for p in self.book.database_parameters),
        ]
        for activity in self.book.activities:
            rows.extend((p, owner.get(activity.source), False) for p in activity.parameters)
        candidates: dict[str, tuple[ParameterRow, Parameter]] = {}
        for row, scope, shadowed in rows:
            if row.scope is not None and scope is None:
                self._parameter_failed(
                    row, "activity-not-converted", "its activity was not converted"
                )
            elif shadowed:
                self._parameter_failed(
                    row,
                    "parameter-shadowed",
                    "has the name of a database parameter, which takes its place",
                )
            else:
                parameter, why = self._parameter(row, scope)
                if parameter is None:
                    self._parameter_failed(row, "parameter-unsupported", why or "")
                elif parameter.id in candidates:
                    self._parameter_failed(
                        row,
                        "parameter-twice",
                        f"{parameter.id} is defined twice, and "
                        f"{candidates[parameter.id][0].source} already defines it",
                    )
                else:
                    candidates[parameter.id] = (row, parameter)
        chosen = {pid: p for pid, (_, p) in candidates.items()}
        while True:
            try:
                self.parameters = ParameterSet(chosen.values())
                break
            except InvalidDataError as error:
                bad = {
                    p.item.removeprefix("parameter "): p.message
                    for p in error.problems
                    if p.item.startswith("parameter ")
                }
                if not any(pid in chosen for pid in bad):
                    raise  # not about one parameter: a bug here, not a workbook to refuse
                for pid, message in bad.items():
                    if pid not in chosen:
                        continue
                    row, parameter = candidates[pid]
                    del chosen[pid]
                    self._parameter_failed(row, "parameter-unsupported", message)
                    if parameter.scope is not None:
                        self.blocked[parameter.scope][parameter.name] = message
        self.parameter_outcomes.extend(
            ParameterOutcome(row.source, row.name, row.scope, True, "copied", "copied")
            for pid, (row, _) in candidates.items()
            if pid in chosen
        )

    def _parameter(
        self, row: ParameterRow, scope: str | None
    ) -> tuple[Parameter | None, str | None]:
        fields: dict[str, object] = {"name": row.name, "scope": scope, "comment": row.comment}
        if row.formula is not None:
            try:
                Formula.parse(row.formula)
            except FormulaError as exc:
                return None, f"formula {row.formula!r}: {exc}"
            fields["formula"] = row.formula
        else:
            value, problem = number(row.amount)
            if value is None:
                return None, (
                    "has no value and no formula"
                    if problem == "no-amount"
                    else f"amount {row.amount!r} is not a number"
                )
            fields["value"] = value
        if row.uncertainty:
            fields["uncertainty"] = self._distribution(row, fields.get("value"))
        if row.unit is not None:
            unit = resolve_unit(row.unit, self.units)
            if unit is None:
                self.notes.append(
                    Note(
                        f"parameter {row.name}: unit {row.unit!r} is not a unit headwater knows, "
                        "so the parameter has no unit",
                        row.source,
                    )
                )
            else:
                fields["unit"] = unit
        try:
            return Parameter.model_validate(fields), None
        except ValidationError as error:
            return None, str(error.errors()[0]["msg"]).removeprefix("Value error, ")

    def _distribution(self, row: ParameterRow, value: object) -> Uncertainty | None:
        if isinstance(value, float):
            found, why = distribution(
                row.uncertainty,
                value,
                value,
                flip=False,
                what="value",
                item=f"parameter {row.name}",
            )
        else:
            found = None
            why = (
                f"is on a parameter whose value is the formula {row.formula!r}. A database gives "
                "a formula's spread to the parameters it uses"
            )
        if why is not None:
            self.notes.append(
                Note(f"parameter {row.name}: distribution not copied: it {why}.", row.source)
            )
        return found

    def _parameter_failed(self, row: ParameterRow, code: str, message: str) -> None:
        self.parameter_outcomes.append(
            ParameterOutcome(row.source, row.name, row.scope, False, code, message)
        )
