import keyword
import math
import re
from collections.abc import Iterable, Iterator, Mapping
from graphlib import CycleError, TopologicalSorter
from types import MappingProxyType

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from headwater.model._types import FiniteNumber
from headwater.model.errors import InvalidDataError, Problem, RecordError, did_you_mean
from headwater.model.formula import FORMULA_CONSTANTS, FORMULA_FUNCTIONS, Formula, FormulaError
from headwater.model.uncertainty import Uncertainty

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_MISSING = "\0missing"
"""Stands for a name that is not a parameter, so its user is left out without more noise."""


class Parameter(BaseModel):
    """A named number of a study: a value, or a formula of other parameters.

    `scope` is None for a parameter of the whole study, or the id of the one process that may
    use it. Example: `fabric_kg`, value 0.25, unit `kg`. Its id is `fabric_kg`, or
    `tshirt:tshirt/cut_ratio` when scoped to a process.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    name: str
    scope: str | None = Field(default=None, min_length=1)
    value: FiniteNumber | None = None
    formula: str | None = None
    unit: str | None = Field(default=None, min_length=1)
    comment: str | None = None
    uncertainty: Uncertainty | None = None
    """The distribution of a value, centred on it: `hw.monte_carlo` draws it. A formula has
    none: its spread comes from the parameters it uses."""

    @property
    def id(self) -> str:
        return self.name if self.scope is None else f"{self.scope}/{self.name}"

    @field_validator("name")
    @classmethod
    def _name_is_free(cls, name: str) -> str:
        if not _NAME.fullmatch(name):
            raise ValueError(
                f"name {name!r} is not a name. Use letters, digits and underscores, starting "
                "with a letter or an underscore, for example fabric_kg"
            )
        if name in FORMULA_FUNCTIONS:
            raise ValueError(
                f"name {name!r} is a function of formulas, so a parameter cannot use it"
            )
        if name in FORMULA_CONSTANTS:
            raise ValueError(
                f"name {name!r} is a constant of formulas, so a parameter cannot use it"
            )
        if name.lower() in ("nan", "inf", "infinity"):
            raise ValueError(
                f"name {name!r} reads as a number in an amount, so a parameter cannot use it"
            )
        if keyword.iskeyword(name):
            hint = (
                " YAML reads on, off, yes and no as true or false: put quotes around the name."
                if name in ("True", "False")
                else ""
            )
            raise ValueError(
                f"name {name!r} is a Python keyword, so a parameter cannot use it.{hint}"
            )
        return name

    @field_validator("formula")
    @classmethod
    def _formula_is_valid(cls, text: str | None) -> str | None:
        if text is not None:
            Formula.parse(text)
        return text

    @model_validator(mode="after")
    def _a_value_or_a_formula(self) -> "Parameter":
        if (self.value is None) == (self.formula is None):
            raise RecordError.one(
                "invalid-field",
                f"parameter {self.id}",
                "needs a value or a formula, and only one of them",
            )
        if self.formula is not None and self.uncertainty is not None:
            raise RecordError.one(
                "formula-with-distribution",
                f"parameter {self.id}",
                "is a formula, so its spread comes from the parameters it uses",
            )
        return self


class ParameterSet:
    """The parameters of a study, checked and worked out in dependency order.

    A parameter of the study may be used by every formula. A parameter of a process may be used
    by the formulas of that process, and may not hide a parameter of the study. Building a set
    raises one `InvalidDataError` naming every loop, unknown name and formula that fails.
    """

    def __init__(self, parameters: Iterable[Parameter] = ()) -> None:
        problems: list[Problem] = []
        by_id: dict[str, Parameter] = {}
        for parameter in parameters:
            if parameter.id in by_id:
                problems.append(
                    Problem("duplicate-parameter", f"parameter {parameter.id}", "is defined twice")
                )
            else:
                by_id[parameter.id] = parameter
        self._by_id = dict(sorted(by_id.items()))
        problems.extend(self._shadowing())
        graph = self._dependencies(problems)
        order = self._order(graph, problems)
        self._values = self._evaluate(order, graph, problems)
        if problems:
            raise InvalidDataError(problems)
        self.parameters = tuple(self._by_id[i] for i in order)
        self.values: Mapping[str, float] = MappingProxyType(self._values)

    def __len__(self) -> int:
        return len(self._by_id)

    def __iter__(self) -> Iterator[Parameter]:
        return iter(self.parameters)

    def __contains__(self, parameter_id: object) -> bool:
        return parameter_id in self._by_id

    def __eq__(self, other: object) -> bool:
        return isinstance(other, ParameterSet) and other._by_id == self._by_id

    def __hash__(self) -> int:
        return hash(tuple(self._by_id))

    def __repr__(self) -> str:
        return f"ParameterSet({', '.join(self._by_id)})"

    def get(self, parameter_id: str) -> Parameter | None:
        return self._by_id.get(parameter_id)

    def visible(self, scope: str | None) -> dict[str, str]:
        """The parameters a formula can use, as name to id: those of the study, and those of
        `scope` when it is a process."""
        found = {p.name: p.id for p in self._by_id.values() if p.scope is None}
        if scope is not None:
            found.update({p.name: p.id for p in self._by_id.values() if p.scope == scope})
        return found

    def unknown_name(self, name: str, scope: str | None = None) -> str | None:
        """None when a formula in `scope` can use `name`. Otherwise a hint to add to the message:
        a suggestion, or the process that has a parameter of that name."""
        if name in self.visible(scope):
            return None
        elsewhere = sorted(
            p.id for p in self._by_id.values() if p.name == name and p.scope is not None
        )
        if elsewhere:
            return f" {', '.join(elsewhere)} has that name, but only its own process can use it."
        return did_you_mean(name, self.visible(scope))

    def evaluate(self, text: str, scope: str | None = None) -> float:
        """The value of a formula written in a process (or in the study, when `scope` is None).

        Raises `FormulaError` for a formula that cannot be read or computed, or a name that no
        parameter visible from `scope` has.
        """
        formula = Formula.parse(text)
        visible = self.visible(scope)
        return formula.evaluate(
            {name: self._values[visible[name]] for name in formula.names if name in visible}
        )

    def with_changes(self, changes: Mapping[str, float | str]) -> "ParameterSet":
        """A new set where some parameters have a new value, or a new formula when given as text
        that is not a number. Everything that depends on them is worked out again.

        The keys are parameter ids. Raises `InvalidDataError` naming an unknown id, or the loop a
        new formula would make.
        """
        unknown = [
            Problem(
                "unknown-parameter",
                f"parameter {parameter_id}",
                f"is not a parameter of the study.{did_you_mean(parameter_id, self._by_id)}",
            )
            for parameter_id in changes
            if parameter_id not in self._by_id
        ]
        if unknown:
            raise InvalidDataError(unknown)
        changed: list[Parameter] = []
        problems: list[Problem] = []
        for parameter in self._by_id.values():
            if parameter.id not in changes:
                changed.append(parameter)
                continue
            new = changes[parameter.id]
            as_number = _as_number(new)
            try:
                changed.append(
                    Parameter.model_validate(
                        {
                            **parameter.model_dump(),
                            "value": as_number,
                            "formula": None if as_number is not None else new,
                            "uncertainty": None,  # a value set by a scenario is fixed
                        }
                    )
                )
            except ValueError as error:
                problems.append(Problem("invalid-field", f"parameter {parameter.id}", str(error)))
        if problems:
            raise InvalidDataError(problems)
        return ParameterSet(changed)

    # --- building ----------------------------------------------------------------------------

    def _shadowing(self) -> list[Problem]:
        study = {p.name for p in self._by_id.values() if p.scope is None}
        return [
            Problem(
                "parameter-shadow",
                f"parameter {p.id}",
                f"has the name of a parameter of the study, {p.name!r}. Rename one of them, so a "
                "formula never means two things.",
            )
            for p in self._by_id.values()
            if p.scope is not None and p.name in study
        ]

    def _dependencies(self, problems: list[Problem]) -> dict[str, set[str]]:
        """Which parameters each parameter uses. A name that is not a parameter is a problem."""
        graph: dict[str, set[str]] = {}
        for parameter in self._by_id.values():
            graph[parameter.id] = set()
            if parameter.formula is None:
                continue
            visible = self.visible(parameter.scope)
            for name in Formula.parse(parameter.formula).names:
                if name in visible:
                    graph[parameter.id].add(visible[name])
                    continue
                hint = self.unknown_name(name, parameter.scope)
                problems.append(
                    Problem(
                        "unknown-parameter",
                        f"parameter {parameter.id}",
                        f"formula {parameter.formula!r} uses {name!r}, which is not a "
                        f"parameter it can see.{hint}",
                    )
                )
                graph[parameter.id].add(_MISSING)
        return graph

    def _order(self, graph: dict[str, set[str]], problems: list[Problem]) -> list[str]:
        """Parameters in an order where each comes after those it uses. Loops are problems, and
        the parameters in them are left out."""
        remaining = {k: {d for d in v if d != _MISSING} for k, v in graph.items()}
        while True:
            try:
                return list(TopologicalSorter(remaining).static_order())
            except CycleError as error:
                found = list(error.args[1])[:-1][::-1]  # each one uses the next
                start = found.index(min(found))
                loop = [*found[start:], *found[:start]]
                problems.append(
                    Problem(
                        "parameter-cycle",
                        f"parameter {loop[0]}",
                        f"parameters use each other in a loop: {' → '.join([*loop, loop[0]])}. "
                        "Each one uses the next, so none can be worked out first.",
                    )
                )
                remaining = {
                    k: {d for d in v if d not in loop}
                    for k, v in remaining.items()
                    if k not in loop
                }

    def _evaluate(
        self, order: list[str], graph: dict[str, set[str]], problems: list[Problem]
    ) -> dict[str, float]:
        values: dict[str, float] = {}
        failed = {i for i in self._by_id if i not in order}
        for parameter_id in order:
            parameter = self._by_id[parameter_id]
            if graph[parameter_id] & failed or _MISSING in graph[parameter_id]:
                failed.add(parameter_id)  # its cause is already a problem
                continue
            if parameter.value is not None:
                values[parameter_id] = parameter.value
                continue
            assert parameter.formula is not None
            visible = self.visible(parameter.scope)
            try:
                values[parameter_id] = Formula.parse(parameter.formula).evaluate(
                    {n: values[i] for n, i in visible.items() if i in values}
                )
            except FormulaError as error:
                failed.add(parameter_id)
                problems.append(Problem("bad-formula", f"parameter {parameter_id}", str(error)))
        return values


def _as_number(change: float | str) -> float | None:
    """The number in a change, or None when it is a formula."""
    if isinstance(change, str):
        try:
            number = float(change)
        except ValueError:
            return None
        return number if math.isfinite(number) else None
    return change
