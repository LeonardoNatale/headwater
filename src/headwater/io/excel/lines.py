"""Lines: each row of an activity as a line of its process, or as a line with no supplier.

A role comes from what the supplier makes, never from the sign of the amount. A negative amount
to a treatment (a supplier that makes a waste) is waste sent for treatment, in positive kg. A
negative amount to any other product is a credit that the workbook does not classify, so it is
reported. Brightway's `substitution` type is an avoided product.

A line's distribution comes from its row's uncertainty fields, by the mapping of `uncertainty`,
once its role, and so its sign, is known. One that a study cannot hold is reported, and not
copied: on a line whose amount is a formula, or that is not linked, too.
"""

import math
from collections import Counter

from headwater.io.excel.linking import Linker
from headwater.io.excel.parameters import ParameterReader
from headwater.io.excel.records import (
    EMISSION_COMPARTMENTS,
    NATURAL_RESOURCE,
    RELATIVE,
    Amount,
    Draft,
    Head,
    Linked,
    Refusal,
    Target,
    join,
    number,
    process_stem,
)
from headwater.io.excel.report import Note, Outcome, Status
from headwater.io.excel.uncertainty import cells, distribution
from headwater.io.excel.units import resolve_unit
from headwater.io.excel.workbook import ExchangeRow
from headwater.io.files import one_line
from headwater.model import (
    Flow,
    FlowKind,
    Formula,
    FormulaError,
    ParameterSet,
    Role,
    Uncertainty,
    Units,
)


class LineReader:
    def __init__(
        self,
        units: Units,
        study: str,
        linker: Linker,
        parameters: ParameterReader,
        flows: dict[str, Flow],
    ) -> None:
        self.units = units
        self.study = study
        self.linker = linker
        self._parameters = parameters
        self.flows = flows
        """The flow each process makes, to which the flows made up for unmapped lines are added."""
        self.notes: list[Note] = []
        self.mapped_by: Counter[str] = Counter()
        self.database_rows: Counter[str] = Counter()
        self.placeholders: dict[tuple[object, ...], str] = {}

    @property
    def parameters(self) -> ParameterSet:
        return self._parameters.parameters

    @property
    def blocked(self) -> dict[str, dict[str, str]]:
        return self._parameters.blocked

    def lines(self, head: Head) -> list[tuple[Draft, Outcome | None]]:
        """The lines of one process: its reference first, then the others in workbook order."""
        lines = [self._reference(head)]
        for row in head.activity.exchanges:
            if row is not head.production:
                lines.append(self._line(head, row))
        return lines

    def _reference(self, head: Head) -> tuple[Draft, Outcome | None]:
        row = head.production
        if row is None:  # made from the activity's metadata: there is no row to report on
            return Draft(
                Role.reference, head.id, head.label, head.amount, None, head.unit, None, None
            ), None
        amount = self._amount(row, head.amount, head.kind is FlowKind.waste, head.id)
        written = -head.amount if head.kind is FlowKind.waste else head.amount
        draft = Draft(
            Role.reference,
            head.id,
            head.label,
            amount.value,
            amount.formula,
            head.unit,
            None,
            join(row.comment, amount.note),
            self._distribution(row, written, Role.reference, amount),
        )
        return draft, self._linked(head, row, "reference", amount.refusal, None)

    def _line(self, head: Head, row: ExchangeRow) -> tuple[Draft, Outcome | None]:
        if row.type == "biosphere":
            result = self._biosphere(head, row)
        elif row.type in ("technosphere", "substitution"):
            self.database_rows[row.database or ""] += 1
            result = self._technosphere(head, row)
        else:
            result = Refusal(
                Status.unmapped,
                "no-type" if row.type is None else "unknown-type",
                "has no type. A line is `technosphere`, `biosphere`, `substitution` or "
                "`production`."
                if row.type is None
                else f"has type {row.type!r}, which headwater does not know. A line is "
                "`technosphere`, `biosphere`, `substitution` or `production`.",
            )
        if isinstance(result, Refusal):
            return self._unmapped(head, row, result)
        draft, how, formula, note = result
        by_name = f"{draft.supplier_id}: {note}" if note else None
        return draft, self._linked(head, row, how, formula, by_name)

    def _linked(
        self,
        head: Head,
        row: ExchangeRow,
        how: str,
        formula: Refusal | None,
        by_name: str | None,
    ) -> Outcome:
        """The outcome of a line that linked: mapped, or unexpressible when its formula is."""
        name = head.activity.name
        if formula is not None:
            return Outcome(
                row.source,
                name,
                row.description,
                formula.status,
                formula.code,
                formula.message,
                by_name=by_name,
            )
        self.mapped_by[how] += 1
        return Outcome(
            row.source,
            name,
            row.description,
            Status.mapped,
            how,
            f"linked ({how})",
            by_name=by_name,
        )

    def _technosphere(self, head: Head, row: ExchangeRow) -> Linked | Refusal:
        value, problem = self._number(head, row)
        if value is None:
            return Refusal(
                Status.unmapped,
                problem or "no-amount",
                "has no amount. If the cell holds an Excel formula, open the workbook in Excel "
                "and save it, so its value is stored."
                if problem == "no-amount"
                else f"amount {row.amount!r} is not a number",
            )
        unit = self._unit(row)
        if isinstance(unit, Refusal):
            return unit
        target = self.linker.target(head, row)
        if isinstance(target, Refusal):
            return target
        role = self._role(head, row, value, target)
        if isinstance(role, Refusal):
            return role
        mismatch = self._mismatch(unit, target)
        if mismatch is not None:
            return mismatch
        flips = role is Role.waste_to_treatment
        amount = self._amount(row, (-value if flips else value) + 0.0, flips, head.id)
        draft = Draft(
            role,
            target.flow_id,
            target.flow_name,
            amount.value,
            amount.formula,
            unit,
            target.supplier_id,
            join(row.comment, amount.note),
            self._distribution(row, value, role, amount),
        )
        return draft, target.how, amount.refusal, target.note

    def _number(self, head: Head, row: ExchangeRow) -> tuple[float | None, str | None]:
        """The number of a line, as Brightway writes it. A line with a formula and no amount next
        to it has the formula's value, when headwater can work it out."""
        value, problem = number(row.amount)
        if value is None and problem == "no-amount" and row.formula is not None:
            if self._formula_failure(row.formula, head.id) is None:
                return self.parameters.evaluate(row.formula, head.id), None
        return value, problem

    def _unit(self, row: ExchangeRow) -> str | Refusal:
        if row.unit is None:
            return Refusal(
                Status.unmapped, "no-unit", "has no unit. Write the unit the amount is counted in."
            )
        unit = resolve_unit(row.unit, self.units)
        if unit is None:
            return Refusal(
                Status.unmapped,
                "unknown-unit",
                f"unit {row.unit!r} is not a unit headwater knows. Units: "
                f"{', '.join(self.units.names)}",
            )
        return unit

    def _mismatch(self, unit: str, target: Target) -> Refusal | None:
        have, need = self.units.get(unit), self.units.get(target.unit)
        if have is None or need is None or have.quantity == need.quantity:
            return None
        return Refusal(
            Status.unmapped,
            "unit-mismatch",
            f"is in {unit!r} ({have.quantity}), but {target.flow_id} is counted in "
            f"{target.unit!r} ({need.quantity}). The import converts units of one quantity, and "
            "does not turn mass into energy.",
        )

    def _role(self, head: Head, row: ExchangeRow, value: float, target: Target) -> Role | Refusal:
        if row.type == "substitution":
            if target.kind is FlowKind.waste:
                return Refusal(
                    Status.unmapped,
                    "role-flow-kind",
                    f"is a substitution, but {target.flow_id} is a waste, and a product displaces "
                    "a product",
                )
            if value < 0:
                return Refusal(
                    Status.unmapped,
                    "negative-substitution",
                    "is a substitution with a negative amount. Write the amount displaced as a "
                    "positive number.",
                )
            return Role.avoided_product
        if target.kind is FlowKind.waste:
            if value > 0:
                return Refusal(
                    Status.unmapped,
                    "positive-to-treatment",
                    f"has a positive amount to a treatment ({target.flow_id} is a waste). In "
                    "Brightway, waste sent for treatment is a negative amount.",
                )
            return Role.waste_to_treatment
        if value < 0:
            return Refusal(
                Status.unmapped,
                "negative-to-product",
                "has a negative amount to a product, which is a credit. The sign never decides a "
                "role. If it is a credit, set the line's type to `substitution` in the workbook "
                "and import again, or change its role to avoided_product in the CSV by hand.",
            )
        if target.own and target.supplier_id == head.id:
            return Role.own_use
        return Role.input

    def _biosphere(self, head: Head, row: ExchangeRow) -> Linked | Refusal:
        value, problem = self._number(head, row)
        if value is None:
            return Refusal(
                Status.unmapped,
                problem or "no-amount",
                "has no amount"
                if problem == "no-amount"
                else f"amount {row.amount!r} is not a number",
            )
        unit = self._unit(row)
        if isinstance(unit, Refusal):
            return unit
        if not row.categories:
            return Refusal(
                Status.unmapped,
                "no-categories",
                "has no categories. A flow of nature is found by its name and its compartment, "
                "for example `air::urban air close to ground` or `water`.",
            )
        flow = self.linker.flow_of(row)
        if isinstance(flow, Refusal):
            return flow
        found, how = flow
        top = found.compartment.split("/")[0]
        if top == NATURAL_RESOURCE:
            role = Role.resource
        elif top in EMISSION_COMPARTMENTS:
            role = Role.emission
        else:
            return Refusal(
                Status.unmapped,
                "biosphere-role",
                f"{found.id} is in compartment {found.compartment!r}, which is neither a "
                "resource nor an emission to air, water or soil",
            )
        mismatch = self._mismatch(
            unit, Target(None, found.id, found.name, FlowKind.elementary, found.unit, how)
        )
        if mismatch is not None:
            return mismatch
        amount = self._amount(row, value, False, head.id)
        draft = Draft(
            role,
            found.id,
            found.name,
            amount.value,
            amount.formula,
            unit,
            None,
            join(row.comment, amount.note),
            self._distribution(row, value, role, amount),
        )
        return draft, how, amount.refusal, None

    def _distribution(
        self, row: ExchangeRow, written: float, role: Role, amount: Amount
    ) -> Uncertainty | None:
        """The distribution of a line, from its row's uncertainty fields. `written` is its amount
        as Brightway writes it: a waste sent for treatment, or what a treatment takes, is
        negative there, so its bounds turn with its sign."""
        if not row.uncertainty:
            return None
        if amount.formula is not None:
            why = (
                f"is on a line whose amount is the formula {amount.formula!r}. A database gives a "
                "formula's spread to the parameters it uses"
            )
            found = None
        else:
            flip = role is Role.waste_to_treatment or (role is Role.reference and written < 0)
            found, why = distribution(
                row.uncertainty, written, amount.value, flip=flip, what="amount", item=row.source
            )
        if why is not None:
            self.notes.append(Note(f"distribution not copied: it {why}.", row.source))
        return found

    def _amount(self, row: ExchangeRow, expected: float, flip: bool, scope: str) -> Amount:
        """The amount of a line. `expected` is the number headwater should hold, from the
        number Excel stored. When the line has a formula that headwater can use, the amount is
        that formula's, and `flip` turns it into the sign headwater holds: waste sent for
        treatment is positive, where Brightway writes it negative."""
        if row.formula is None:
            return Amount(expected)
        failure = self._formula_failure(row.formula, scope)
        if failure is not None:
            code, message = failure
            return Amount(
                expected,
                refusal=Refusal(
                    Status.unexpressible,
                    code,
                    f"{message.rstrip('.')}. The amount is kept as the number Excel stored, "
                    f"{expected:g}.",
                ),
                note=f"formula not copied: {row.formula}",
            )
        text = f"-({row.formula})" if flip else row.formula
        value = self.parameters.evaluate(text, scope)
        if not math.isclose(value, expected, rel_tol=RELATIVE, abs_tol=1e-12):
            self.notes.append(
                Note(
                    f"the formula {row.formula!r} gives {value:g}, but the amount next to it "
                    f"says {expected:g}. headwater uses the formula's.",
                    row.source,
                )
            )
        return Amount(value, text)

    def _formula_failure(self, text: str, scope: str) -> tuple[str, str] | None:
        """Why headwater cannot use a formula, as a code and a message, or None."""
        try:
            formula = Formula.parse(text)
        except FormulaError as exc:
            return "formula-unsupported", f"formula {text!r} is not in headwater's syntax: {exc}"
        for name in formula.names:
            lost = self.blocked.get(scope, {}).get(name)
            if lost is not None:
                return (
                    "formula-unknown-parameter",
                    f"formula {text!r} uses {name!r}, a parameter of this activity that could "
                    f"not be copied ({lost})",
                )
            hint = self.parameters.unknown_name(name, scope)
            if hint is not None:
                return (
                    "formula-unknown-parameter",
                    f"formula {text!r} uses {name!r}, which is not a parameter it can see.{hint}",
                )
        try:
            self.parameters.evaluate(text, scope)
        except FormulaError as exc:
            return "formula-unsupported", f"formula {text!r} cannot be worked out: {exc}"
        return None

    def _unmapped(self, head: Head, row: ExchangeRow, refusal: Refusal) -> tuple[Draft, Outcome]:
        """A line that did not link is still written: with no supplier, and the workbook's own
        words in its comment, so the study stops at validation until a person decides and the
        mapping row can be written without opening the workbook again."""
        value, _ = self._number(head, row)
        unit = resolve_unit(row.unit, self.units) if row.unit else None
        unit = unit or row.unit or "UNKNOWN"
        if row.type == "biosphere":
            top = row.categories[0] if row.categories else ""
            role = Role.resource if top == NATURAL_RESOURCE else Role.emission
            kind = FlowKind.elementary
            amount = value or 0.0
            compartment = "/".join(row.categories) or None
        else:
            negative = value is not None and value < 0
            role = (
                Role.avoided_product
                if row.type == "substitution"
                else Role.waste_to_treatment
                if negative
                else Role.input
            )
            kind = FlowKind.waste if role is Role.waste_to_treatment else FlowKind.product
            amount = abs(value) if value is not None else 0.0
            compartment = None
        flow = self._placeholder(kind, row, unit, compartment)
        if row.uncertainty and cells(row.uncertainty, value or 0.0, flip=False) is not None:
            self.notes.append(
                Note(
                    "distribution not copied: it is on a line that is not linked yet. The import "
                    "that links it copies it.",
                    row.source,
                )
            )
        written = f"{row.amount if row.amount is not None else 'no amount'} {row.unit or 'no unit'}"
        said = f"UNMAPPED ({refusal.code}) {row.description}, {written}: {refusal.message}"
        formula = f"formula not copied: {row.formula}" if row.formula else None
        draft = Draft(
            role, flow.id, flow.name, amount, None, unit, None, join(said, formula, row.comment)
        )
        outcome = Outcome(
            row.source,
            head.activity.name,
            row.description,
            refusal.status,
            refusal.code,
            refusal.message,
            refusal.suggestions,
            database=refusal.study,
        )
        return draft, outcome

    def _placeholder(
        self, kind: FlowKind, row: ExchangeRow, unit: str, compartment: str | None
    ) -> Flow:
        key = (kind, row.name, row.reference_product, row.location, unit, compartment)
        known = self.placeholders.get(key)
        if known is not None:
            return self.flows[known]
        base = f"{self.study}:unmapped-{process_stem(row.name, row.location)}"
        flow_id, n = base, 1
        while flow_id in self.flows:
            n += 1
            flow_id = f"{base}-{n}"
        flow = Flow(
            id=flow_id,
            name=one_line(row.reference_product or row.name),
            kind=kind,
            unit=unit,
            compartment=compartment,
        )
        self.placeholders[key] = flow_id
        self.flows[flow.id] = flow
        return flow
