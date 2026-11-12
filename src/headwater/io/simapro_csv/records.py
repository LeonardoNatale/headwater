"""What each block, section and column of a SimaPro CSV export means, as typed records.

A `Process` block has metadata sections, one reference section (`Products`, or `Waste treatment`
for a waste treatment) and line sections. Each line section gives its lines one role and one
kind of flow, and the elementary ones a compartment:

| Section | Line ids | Role | Flow, compartment |
|---|---|---|---|
| Products | p1, p2, ... | reference | product |
| Waste treatment | w1 | reference | waste |
| Avoided products | a1 | avoided_product | product |
| Materials/fuels | m1 | input | product |
| Electricity/heat | e1 | input | product |
| Waste to treatment | wt1 | waste_to_treatment | waste |
| Resources | r1 | resource | elementary, `raw/<sub>` |
| Emissions to air | air1 | emission | elementary, `air/<sub>` |
| Emissions to water | water1 | emission | elementary, `water/<sub>` |
| Emissions to soil | soil1 | emission | elementary, `soil/<sub>` |
| Final waste flows | waste1 | emission | elementary, `waste/<sub>` |
| Non material emissions | nm1 | emission | elementary, `non material/<sub>` |
| Social issues | social1 | emission | elementary, `social/<sub>` |
| Economic issues | economic1 | emission | elementary, `economic/<sub>` |

SimaPro gives lines no ids, so a line's id is its section's code and its place in the section.
A blank subcompartment and `(unspecified)` are the same: `air`. Amounts must be numbers: SimaPro
writes the numbers it computed when the export is made with "Convert expressions to constants".
Parameters are read and not kept, since formulas are not. Metadata other than the identifier, the
category type and the process name is not kept.
"""

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from functools import cached_property

from pydantic import ValidationError

from headwater.io.simapro_csv.blocks import Row, Section
from headwater.model import (
    Distribution,
    FlowKind,
    Problem,
    RecordError,
    Role,
    Severity,
    Uncertainty,
)

UNSPECIFIED = "(unspecified)"
"""How a method writes a blank subcompartment."""


@dataclass(frozen=True)
class LineSection:
    name: str
    code: str
    """The start of its lines' ids: `m` for `Materials/fuels`."""
    role: Role
    kind: FlowKind
    compartment: str | None = None
    """For elementary flows: the compartment before the subcompartment."""

    @property
    def is_reference(self) -> bool:
        return self.role is Role.reference

    @property
    def is_elementary(self) -> bool:
        return self.kind is FlowKind.elementary


PRODUCTS = LineSection("Products", "p", Role.reference, FlowKind.product)
WASTE_TREATMENT = LineSection("Waste treatment", "w", Role.reference, FlowKind.waste)

LINE_SECTIONS: Mapping[str, LineSection] = {
    s.name: s
    for s in (
        PRODUCTS,
        WASTE_TREATMENT,
        LineSection("Avoided products", "a", Role.avoided_product, FlowKind.product),
        LineSection("Resources", "r", Role.resource, FlowKind.elementary, "raw"),
        LineSection("Materials/fuels", "m", Role.input, FlowKind.product),
        LineSection("Electricity/heat", "e", Role.input, FlowKind.product),
        LineSection("Emissions to air", "air", Role.emission, FlowKind.elementary, "air"),
        LineSection("Emissions to water", "water", Role.emission, FlowKind.elementary, "water"),
        LineSection("Emissions to soil", "soil", Role.emission, FlowKind.elementary, "soil"),
        LineSection("Final waste flows", "waste", Role.emission, FlowKind.elementary, "waste"),
        LineSection(
            "Non material emissions", "nm", Role.emission, FlowKind.elementary, "non material"
        ),
        LineSection("Social issues", "social", Role.emission, FlowKind.elementary, "social"),
        LineSection("Economic issues", "economic", Role.emission, FlowKind.elementary, "economic"),
        LineSection("Waste to treatment", "wt", Role.waste_to_treatment, FlowKind.waste),
    )
}

PROCESS_METADATA = frozenset(
    {
        "Category type",
        "Process identifier",
        "Type",
        "Process name",
        "Status",
        "Time period",
        "Geography",
        "Technology",
        "Representativeness",
        "Multiple output allocation",
        "Substitution allocation",
        "Cut off rules",
        "Capital goods",
        "Boundary with nature",
        "Infrastructure",
        "Date",
        "Record",
        "Generator",
        "External documents",
        "Literature references",
        "Collection method",
        "Data treatment",
        "Verification",
        "Comment",
        "Allocation rules",
        "System description",
        "Input parameters",
        "Calculated parameters",
    }
)
"""The sections of a `Process` block that are not lines."""

SUBSTANCE_BLOCKS: Mapping[str, str] = {
    "Raw materials": "raw",
    "Airborne emissions": "air",
    "Waterborne emissions": "water",
    "Emissions to soil": "soil",
    "Final waste flows": "waste",
    "Non material emissions": "non material",
    "Social issues": "social",
    "Economic issues": "economic",
}
"""Blocks that list elementary flows with their unit and CAS number, by compartment."""

METHOD_COMPARTMENTS: Mapping[str, str] = {
    "air": "air",
    "water": "water",
    "soil": "soil",
    "raw": "raw",
    "waste": "waste",
    "non mat.": "non material",
    "non material": "non material",
    "social": "social",
    "economic": "economic",
}
"""A method's compartments, lower-cased, as the process sections name them."""

METHOD_SECTIONS = frozenset(
    {
        "Name",
        "Version",
        "Comment",
        "Category",
        "Use Damage Assessment",
        "Use Normalization",
        "Use Weighting",
        "Use Addition",
        "Weighting unit",
        "Impact category",
        "Substances",
        "Damage category",
        "Impact categories",
        "Normalization-Weighting set",
        "Normalization",
        "Weighting",
    }
)
"""The sections of a `Method` block. Damage, normalisation and weighting are read and not kept."""

_NUMBER = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")


@dataclass(frozen=True, slots=True)
class Line:
    """One line of a process, as SimaPro writes it."""

    id: str
    section: LineSection
    where: str
    """`<file>:<line>`."""
    text: str
    """The name as written: a product text, or an elementary flow's name."""
    unit: str
    """SimaPro's name for the unit."""
    amount: float
    subcompartment: str | None = None
    uncertainty: Uncertainty | None = None
    comment: str | None = None
    allocation: float | None = None
    """For a product: its allocation factor, SimaPro's percentage divided by 100."""

    @property
    def compartment(self) -> str | None:
        return compartment(self.section.compartment, self.subcompartment)


@dataclass(frozen=True)
class ProcessRecord:
    identifier: str
    process_name: str | None
    where: str
    lines: tuple[Line, ...]
    """In the order of the file, reference lines first."""

    @cached_property
    def references(self) -> tuple[Line, ...]:
        return tuple(line for line in self.lines if line.section.is_reference)


@dataclass(frozen=True)
class Substance:
    """An elementary flow as a substance block lists it."""

    compartment: str
    name: str
    unit: str
    cas: str | None


@dataclass(frozen=True)
class UnitDefinition:
    """A row of the `Units` block: 1 `name` is `factor` of `reference`, a unit of `quantity`."""

    name: str
    quantity: str
    factor: float
    reference: str


@dataclass(frozen=True)
class FactorRecord:
    where: str
    name: str
    compartment: str
    factor: float
    unit: str


@dataclass(frozen=True)
class CategoryRecord:
    name: str
    unit: str
    factors: tuple[FactorRecord, ...]


@dataclass(frozen=True)
class MethodRecord:
    name: str
    version: str | None
    """The `Version` cells joined by dots: `1;02` gives `1.02`."""
    categories: tuple[CategoryRecord, ...]


@dataclass
class Parse:
    """Where in the file the rows are, the decimal separator, and what went wrong so far."""

    file: str
    decimal: str
    constants: bool | None
    problems: list[Problem] = field(default_factory=list[Problem])
    warnings: list[Problem] = field(default_factory=list[Problem])

    def where(self, line: int) -> str:
        return f"{self.file}:{line}"

    def number(self, text: str) -> float | None:
        """A number written with the export's decimal separator, or None. With a decimal comma,
        a dot is refused: `1.500` could mean 1500."""
        if self.decimal != ".":
            if "." in text:
                return None
            text = text.replace(self.decimal, ".")
        return float(text) if _NUMBER.match(text) else None

    def fail(self, code: str, item: str, message: str, line: int) -> None:
        self.problems.append(Problem(code, item, message, location=self.where(line)))

    def warn(self, code: str, item: str, message: str, line: int) -> None:
        self.warnings.append(
            Problem(code, item, message, severity=Severity.warning, location=self.where(line))
        )


def compartment(top: str | None, sub: str | None) -> str | None:
    """`air` and `high. pop.` give `air/high. pop.`; a blank or `(unspecified)` sub gives `air`."""
    if top is None:
        return None
    return f"{top}/{sub}" if sub and sub != UNSPECIFIED else top


def to_uncertainty(
    kind: str, amount: float, spread: float, minimum: float, maximum: float
) -> Uncertainty | None:
    """SimaPro's distribution of `amount`, with the parameter names of stats_arrays.

    `spread` is SimaPro's first parameter: for a lognormal the squared geometric standard
    deviation (GSD²), so `scale` is ln(√GSD²); for a normal twice the standard deviation.
    `minimum` and `maximum` bound a uniform or a triangle, whose mode is the amount. None for
    `Undefined`, and for a distribution with no spread (GSD² of 1, or 0 as SimaPro writes for
    none) or a lognormal of 0, which are fixed amounts. Raises `ValueError` for the rest that
    makes no sense.
    """
    match kind:
        case "Undefined":
            return None
        case "Lognormal":
            if spread in (0.0, 1.0) or amount == 0.0:
                return None
            if spread < 1.0:
                raise ValueError(
                    f"a lognormal needs a squared geometric standard deviation of at least 1, "
                    f"not {spread:g}"
                )
            return Uncertainty(
                distribution=Distribution.lognormal,
                loc=math.log(abs(amount)),
                scale=math.log(math.sqrt(spread)),
            )
        case "Normal":
            if spread == 0.0:
                return None
            return Uncertainty(distribution=Distribution.normal, loc=amount, scale=spread / 2)
        case "Uniform":
            return Uncertainty(distribution=Distribution.uniform, minimum=minimum, maximum=maximum)
        case "Triangle":
            return Uncertainty(
                distribution=Distribution.triangular, loc=amount, minimum=minimum, maximum=maximum
            )
        case _:
            raise ValueError(
                f"{kind!r} is not a distribution SimaPro writes: Undefined, Lognormal, Normal, "
                "Uniform or Triangle"
            )


def read_process(start: int, found: Sequence[Section], parse: Parse) -> ProcessRecord | None:
    """The process of a `Process` block's sections, or None when it cannot be read."""
    metadata = {s.name: (s.rows[0][0] if s.rows else "") for s in found}
    identifier = metadata.get("Process identifier", "")
    category_type = metadata.get("Category type", "")
    item = f"process {identifier}"
    if not identifier:
        parse.fail("missing-identifier", "process", "has no Process identifier", start)
        return None
    if category_type.lower() == "waste scenario":
        parse.fail(
            "waste-scenario",
            item,
            "is a waste scenario, which splits waste between treatments by rules, not by lines. "
            "headwater reads processes with lines only: leave it out of the export.",
            start,
        )
        return None
    lines: list[Line] = []
    for section in found:
        kind = LINE_SECTIONS.get(section.name)
        if kind is None:
            continue
        for n, row in enumerate(section.rows, start=1):
            line = _line(row, kind, f"{kind.code}{n}", item, parse)
            if line is not None:
                lines.append(line)
    references = [line for line in lines if line.section.is_reference]
    if not references:
        parse.fail("no-reference-line", item, "has no Products and no Waste treatment line", start)
        return None
    if {line.section for line in references} == {PRODUCTS, WASTE_TREATMENT}:
        parse.fail("two-references", item, "has both Products and a Waste treatment", start)
        return None
    others = [line for line in lines if not line.section.is_reference]
    return ProcessRecord(
        identifier=identifier,
        process_name=metadata.get("Process name") or None,
        where=parse.where(start),
        lines=(*references, *others),
    )


def _line(row: Row, section: LineSection, id: str, process: str, parse: Parse) -> Line | None:
    """One line. The columns are, by section:

    - Products: name, unit, amount, allocation %, waste type, category, comment
    - Waste treatment: name, unit, amount, waste type, category, comment
    - lines that buy: name, unit, amount, distribution, 3 parameters, comment
    - elementary lines: name, subcompartment, unit, amount, distribution, 3 parameters, comment
    """
    item = f"{process} line {id}"
    shift = 1 if section.is_elementary else 0
    text, unit, amount_text = row[0], row[1 + shift], row[2 + shift]
    if not text or not unit:
        parse.fail("bad-line", item, "needs a name and a unit", row.line)
        return None
    amount = _amount(amount_text, item, row, parse)
    if amount is None:
        return None
    if section is PRODUCTS:
        # A blank allocation gives no factor: a process that makes several products is then an
        # open issue of the allocation stage.
        allocation = _amount(row[3], f"{item} allocation", row, parse) if row[3] else None
        if row[3] and allocation is None:
            return None
        return Line(
            id, section, parse.where(row.line), text, unit, amount, comment=row[6] or None,
            allocation=None if allocation is None else allocation / 100,
        )  # fmt: skip
    if section is WASTE_TREATMENT:
        return Line(id, section, parse.where(row.line), text, unit, amount, comment=row[5] or None)
    kind = row[3 + shift] or "Undefined"
    comment = row[7 + shift] or None
    subcompartment = (row[1] or None) if section.is_elementary else None
    if kind == "Undefined":
        return Line(
            id, section, parse.where(row.line), text, unit, amount, subcompartment, None, comment
        )
    parameters = [row[i + shift] for i in (4, 5, 6)]
    if kind in ("Lognormal", "Normal"):
        parameters[0] = parameters[0] or "0"  # SimaPro's way to write no spread
    numbers = [parse.number(p) for p in parameters]
    try:
        needed = numbers[:1] if kind in ("Lognormal", "Normal") else numbers[1:]
        if any(n is None for n in needed):
            raise ValueError(f"its parameters {', '.join(parameters)} are not all numbers")
        spread, minimum, maximum = (n or 0.0 for n in numbers)
        uncertainty = to_uncertainty(kind, amount, spread, minimum, maximum)
    except ValueError as error:
        parse.fail(
            "bad-uncertainty", item, f"has a {kind} distribution: {_reason(error)}", row.line
        )
        return None
    if uncertainty is None and kind in ("Lognormal", "Normal"):
        if kind == "Lognormal" and amount == 0.0 and numbers[0] not in (0.0, 1.0):
            parse.warn(
                "lognormal-of-zero",
                item,
                "has a lognormal distribution of an amount of 0: kept as a fixed amount",
                row.line,
            )
        else:
            parse.warn(
                "zero-variance",
                item,
                f"has a {kind.lower()} distribution with no spread: kept as a fixed amount",
                row.line,
            )
    return Line(
        id=id,
        section=section,
        where=parse.where(row.line),
        text=text,
        unit=unit,
        amount=amount,
        subcompartment=subcompartment,
        uncertainty=uncertainty,
        comment=comment,
    )


def _amount(text: str, item: str, row: Row, parse: Parse) -> float | None:
    value = parse.number(text)
    if value is not None:
        return value
    said = (
        " The export says 'Convert expressions to constants: No'."
        if parse.constants is False
        else ""
    )
    parse.fail(
        "formula-amount",
        item,
        f"has the amount {text!r}, which is not a number. headwater reads numbers, not formulas: "
        f"export again from SimaPro with 'Convert expressions to constants'.{said}",
        row.line,
    )
    return None


def _reason(error: ValueError) -> str:
    """The message of a record's own validator, which pydantic wraps, or the error's."""
    if isinstance(error, ValidationError):
        inner = [
            p.message
            for e in error.errors()
            if isinstance((found := (e.get("ctx") or {}).get("error")), RecordError)
            for p in found.problems
        ]
        if inner:
            return "; ".join(inner)
    return str(error)


def read_substance(row: Row, compartment: str) -> Substance | None:
    """A row of a substance block: name, unit, CAS number, comment."""
    if not row[0] or not row[1]:
        return None
    return Substance(compartment, row[0], row[1], row[2] or None)


def read_unit(row: Row, parse: Parse) -> UnitDefinition | None:
    """A row of the `Units` block: name, quantity, factor to the reference, reference."""
    factor = parse.number(row[2])
    if not row[0] or not row[1] or not row[3] or factor is None or factor <= 0:
        parse.fail(
            "bad-unit",
            f"unit {row[0]!r}",
            "a Units row is a name, a quantity, a factor above 0 and a reference unit",
            row.line,
        )
        return None
    return UnitDefinition(row[0], row[1], factor, row[3])


def read_method(start: int, found: Sequence[Section], parse: Parse) -> MethodRecord | None:
    """The method of a `Method` block's sections: each `Impact category` is followed by its
    `Substances`."""
    metadata = {s.name: s.rows[0] for s in found if s.rows}
    name = metadata["Name"][0] if "Name" in metadata else ""
    if not name:
        parse.fail("missing-method-name", "method", "has no Name", start)
        return None
    version = ".".join(metadata["Version"].cells) if "Version" in metadata else None
    item = f"method {name}"
    categories: list[CategoryRecord] = []
    for before, section, after in zip([None, *found], found, [*found[1:], None], strict=False):
        if section.name == "Impact category":
            heading = section.rows[0] if section.rows else None
            if heading is None or not heading[0]:
                parse.fail("bad-category", item, "has an unnamed category", start)
            elif after is None or after.name != "Substances":
                parse.fail(
                    "bad-category", item, f"category {heading[0]} has no Substances", heading.line
                )
            else:
                factors = [_factor(row, item, parse) for row in after.rows]
                categories.append(
                    CategoryRecord(heading[0], heading[1], tuple(f for f in factors if f))
                )
        elif section.name == "Substances" and (before is None or before.name != "Impact category"):
            line = section.rows[0].line if section.rows else start
            parse.fail("bad-category", item, "has Substances outside an Impact category", line)
    return MethodRecord(name, version or None, tuple(categories))


def _factor(row: Row, item: str, parse: Parse) -> FactorRecord | None:
    """A row of `Substances`: compartment, subcompartment, name, CAS number, factor, unit."""
    top = METHOD_COMPARTMENTS.get(row[0].lower())
    factor = parse.number(row[4])
    if top is None:
        parse.fail(
            "unknown-compartment",
            item,
            f"names the compartment {row[0]!r}. Compartments: "
            f"{', '.join(sorted(set(METHOD_COMPARTMENTS.values())))}",
            row.line,
        )
        return None
    if not row[2] or not row[5] or factor is None:
        parse.fail(
            "bad-factor", item, "a Substances row needs a name, a factor and a unit", row.line
        )
        return None
    found = compartment(top, row[1] or None)
    assert found is not None
    return FactorRecord(parse.where(row.line), row[2], found, factor, row[5])
