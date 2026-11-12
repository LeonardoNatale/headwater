"""Read one ecoSpold2 dataset file into a process, with each line's role from ecoinvent's own
classification: the exchange group and the by-product classification, never the sign alone.

Each system model has its own role table for product lines (`ROLES`). Cut-off's:

| Line in the file                                     | Role                 | Amount     |
|------------------------------------------------------|----------------------|------------|
| output group 0, Waste, negative (a treatment)        | `reference`          | minus it   |
| output group 0, other class, positive                | `reference`          | as written |
| input group 1-3 or 5, Waste, zero or negative        | `waste_to_treatment` | minus it   |
| input group 1-3 or 5, Recyclable, negative           | `recyclable`         | minus it   |
| input group 1-3 or 5, other class, zero or positive  | `input` (1)          | as written |

(1) `own_use` when the line buys from the process itself.

APOS's is cut-off's, with these rows instead of the Recyclable sent away:

| Line in the file                                     | Role                 | Amount     |
|------------------------------------------------------|----------------------|------------|
| output group 0, Recyclable or allocatable, negative  | `reference` (2)      | minus it   |
| input group 1-3 or 5, Recyclable, negative           | `waste_to_treatment` | minus it   |
| input group 1-3 or 5, allocatable, negative          | `waste_to_treatment` | minus it   |

(2) A treatment: APOS splits a recycling activity into the product it makes and the treatment of
the material it takes, so "paper production, newsprint, recycled" treats "waste paper, sorted".
A flow that a dataset treats, or sends to treatment, is a waste in the release, whatever its
class, and a line that makes or buys it as a product is an error. The datasets that make a
recyclable send it to its treatment, which shares its burden, instead of cutting it off.

Elementary lines are the same in every system model: output group 4 is an `emission`, input
group 4 a `resource`, both as written.

Anything else is an error naming the line. A line whose amount is negated gets its distribution
negated too.

A worker never raises: every failure comes back as a `Problem`, because an exception may not
survive the trip back from another process. A worker reads a batch of files and sends back their
lines as Arrow tables, not as records: a table crosses between processes as a few buffers, and
the release is built from tables.
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from enum import StrEnum
from pathlib import Path

import pyarrow as pa
from lxml import etree
from pydantic import ValidationError

from headwater.io.ecospold2.masterdata import (
    ALLOCATABLE,
    CLASSES,
    NS,
    RECYCLABLE,
    WASTE,
    Element,
    by_product_class,
    elementary_id,
    number,
    parse_xml,
    product_id,
    text,
)
from headwater.model import (
    ROLES_WITH_SUPPLIER,
    Exchange,
    Problem,
    Process,
    ProcessKind,
    RecordError,
    Role,
    Severity,
)
from headwater.model.uncertainty import (
    Distribution,
    ExchangeProperty,
    LinkStatus,
    Pedigree,
    Uncertainty,
)
from headwater.release.schema import exchanges_table, processes_table

ACTIVITY_TYPES = {
    "0": ProcessKind.ordinary_transforming_activity,
    "1": ProcessKind.market_activity,
    "2": ProcessKind.io_activity,
    "3": ProcessKind.residual_activity,
    "4": ProcessKind.production_mix,
    "5": ProcessKind.import_activity,
    "6": ProcessKind.supply_mix,
    "7": ProcessKind.export_activity,
    "8": ProcessKind.re_export_activity,
    "9": ProcessKind.correction_activity,
    "10": ProcessKind.market_group,
}
"""ecoSpold2's specialActivityType codes."""

PRODUCT_INPUT_GROUPS = frozenset({"1", "2", "3", "5"})
"""Materials and fuels, electricity and heat, services, and from technosphere."""

SYSTEM_MODELS = {
    "290c1f85-4cc4-4fa1-b0c8-2cb7f4276dce": "cutoff",
    "06590a66-662a-4885-8494-ad0cf410f956": "apos",
}
"""ecoinvent system models by their id in SystemModels.xml. Each has its own role table in
`ROLES`."""


class Side(StrEnum):
    """Where a product line sits in its dataset."""

    reference = "reference"
    """Output group 0."""

    input = "input"
    """Input group 1, 2, 3 or 5."""


class Sign(StrEnum):
    negative = "negative"
    zero = "zero"
    positive = "positive"


type RoleKey = tuple[Side, str, Sign]
"""A product line's side, by-product class and sign: `(Side.input, "Waste", Sign.negative)`."""

type RoleTable = Mapping[RoleKey, tuple[Role, bool]]
"""The role of each kind of product line, and whether its amount is negated."""

CUTOFF: RoleTable = {
    (Side.reference, ALLOCATABLE, Sign.positive): (Role.reference, False),
    (Side.reference, RECYCLABLE, Sign.positive): (Role.reference, False),
    (Side.reference, WASTE, Sign.negative): (Role.reference, True),
    (Side.input, ALLOCATABLE, Sign.zero): (Role.input, False),
    (Side.input, ALLOCATABLE, Sign.positive): (Role.input, False),
    (Side.input, RECYCLABLE, Sign.zero): (Role.input, False),
    (Side.input, RECYCLABLE, Sign.positive): (Role.input, False),
    (Side.input, RECYCLABLE, Sign.negative): (Role.recyclable, True),
    (Side.input, WASTE, Sign.zero): (Role.waste_to_treatment, True),
    (Side.input, WASTE, Sign.negative): (Role.waste_to_treatment, True),
}
"""The cut-off system model's rules, the table at the top of this module."""

APOS: RoleTable = {
    (Side.reference, ALLOCATABLE, Sign.positive): (Role.reference, False),
    (Side.reference, RECYCLABLE, Sign.positive): (Role.reference, False),
    (Side.reference, WASTE, Sign.negative): (Role.reference, True),
    (Side.reference, ALLOCATABLE, Sign.negative): (Role.reference, True),
    (Side.reference, RECYCLABLE, Sign.negative): (Role.reference, True),
    (Side.input, ALLOCATABLE, Sign.zero): (Role.input, False),
    (Side.input, ALLOCATABLE, Sign.positive): (Role.input, False),
    (Side.input, RECYCLABLE, Sign.zero): (Role.input, False),
    (Side.input, RECYCLABLE, Sign.positive): (Role.input, False),
    (Side.input, WASTE, Sign.zero): (Role.waste_to_treatment, True),
    (Side.input, WASTE, Sign.negative): (Role.waste_to_treatment, True),
    (Side.input, ALLOCATABLE, Sign.negative): (Role.waste_to_treatment, True),
    (Side.input, RECYCLABLE, Sign.negative): (Role.waste_to_treatment, True),
}
"""The APOS system model's rules, cut-off's with the second table at the top of this module."""

ROLES: Mapping[str, RoleTable] = {"cutoff": CUTOFF, "apos": APOS}
"""The role table of each system model, by its name in `SYSTEM_MODELS`."""


def side_of(output: str | None, input_: str | None) -> Side | None:
    """The side of a product line from its exchange groups, or None if it has none."""
    if output == "0" and input_ is None:
        return Side.reference
    if input_ in PRODUCT_INPUT_GROUPS and output is None:
        return Side.input
    return None


def sign_of(amount: float) -> Sign:
    if amount < 0:
        return Sign.negative
    return Sign.zero if amount == 0 else Sign.positive


_SHARED_PROPERTIES: dict[tuple[str, str, str], ExchangeProperty] = {}
_SHARED_PEDIGREES: dict[tuple[str, ...], Pedigree] = {}
"""Identical properties and pedigrees, made once per process and shared. The records are frozen,
so sharing is safe, and a worker keeps one record per distinct value: ecoinvent 3.10 has 4.5
million properties and half a million pedigrees, mostly repeats. Keys are the text in the file,
so 0 and -0 stay apart. `forget_shared` empties them."""


def forget_shared() -> None:
    """Empty the shared records, so a process that parsed in place does not keep them."""
    _SHARED_PROPERTIES.clear()
    _SHARED_PEDIGREES.clear()


_PEDIGREE = {
    "reliability": "reliability",
    "completeness": "completeness",
    "temporal_correlation": "temporalCorrelation",
    "geographical_correlation": "geographicalCorrelation",
    "further_technological_correlation": "furtherTechnologyCorrelation",
}


@dataclass(frozen=True)
class ParsedFile:
    """What one file gave: a process, or the problems that stop it."""

    file: str
    process: Process | None = None
    release: tuple[int, int] | None = None
    """The ecoinvent release the dataset last changed in, from its file attributes."""
    system_model: str | None = None
    classes: tuple[tuple[str, str, str], ...] = ()
    """(line id, flow id, by-product class) of every product line, to check against MasterData."""
    materials: tuple[tuple[str, bool], ...] = ()
    """(flow id, treated) of each line that treats (a negative reference) or sends to treatment
    a flow of another class than Waste: a waste in the release."""
    problems: tuple[Problem, ...] = ()
    warnings: tuple[Problem, ...] = ()


@dataclass(frozen=True)
class FileInfo:
    """What a file says about the source: the ecoinvent release it last changed in, and its
    system model. None when the file could not be read that far."""

    file: str
    release: tuple[int, int] | None
    system_model: str | None


CLASSES_SCHEMA = pa.schema(
    [
        pa.field("process_id", pa.string(), nullable=False),
        pa.field("line_id", pa.string(), nullable=False),
        pa.field("flow_id", pa.string(), nullable=False),
        pa.field("class", pa.string(), nullable=False),
    ]
)
"""The by-product classification of every product line, to check against MasterData."""


@dataclass(frozen=True)
class ParsedBatch:
    """What a batch of files gave: its processes and lines as Arrow tables (the lines with the
    flow name written next to them), each product line's class, the flows its datasets treat or
    send to treatment that MasterData does not class as Waste, and every problem."""

    processes: pa.Table
    exchanges: pa.Table
    classes: pa.Table
    materials: tuple[tuple[str, str, bool], ...]
    """(process id, flow id, treated) of each dataset that treats or sends to treatment a flow of
    another class than Waste."""
    files: tuple[FileInfo, ...]
    problems: tuple[Problem, ...]
    warnings: tuple[Problem, ...]


def parse_files(paths: Sequence[Path], version: str) -> ParsedBatch:
    """Read a batch of dataset files of ecoinvent `version`. Never raises."""
    parsed = [parse_file(path, version) for path in paths]
    processes = [f.process for f in parsed if f.process is not None]
    classes = [
        (f.process.id, line_id, flow_id, flow_class)
        for f in parsed
        if f.process is not None
        for line_id, flow_id, flow_class in f.classes
    ]
    return ParsedBatch(
        processes=processes_table(processes),
        exchanges=exchanges_table(processes, flow_names=True),
        classes=pa.Table.from_pylist(
            [dict(zip(CLASSES_SCHEMA.names, row, strict=True)) for row in classes],
            schema=CLASSES_SCHEMA,
        ),
        materials=tuple(
            (f.process.id, flow_id, treated)
            for f in parsed
            if f.process is not None
            for flow_id, treated in f.materials
        ),
        files=tuple(FileInfo(f.file, f.release, f.system_model) for f in parsed),
        problems=tuple(p for f in parsed for p in f.problems),
        warnings=tuple(w for f in parsed for w in f.warnings),
    )


def parse_file(path: Path, version: str) -> ParsedFile:
    """Read one dataset file of ecoinvent `version`. Never raises."""
    item = f"datasets/{path.name}"
    try:
        if path.stat().st_size == 0:
            message = (
                "is empty, so its dataset cannot be read. To import without it, list it in an "
                "exclude file with a reason: lines that buy from it then become data gaps."
            )
            return ParsedFile(path.name, problems=(Problem("empty-file", item, message),))
        root = parse_xml(path)
        return _Reader(path.name, version).read(root)
    except etree.XMLSyntaxError as error:
        return ParsedFile(
            path.name, problems=(Problem("unreadable-file", item, f"is not valid XML: {error}"),)
        )
    except Exception as error:  # noqa: BLE001  every failure must come back as data
        message = f"cannot be read: {type(error).__name__}: {error}"
        return ParsedFile(path.name, problems=(Problem("unreadable-file", item, message),))


class _Reader:
    """Reads one dataset, collecting every problem rather than stopping at the first."""

    def __init__(self, file: str, version: str) -> None:
        self.file = file
        self.version = version
        self.where = f"datasets/{file}"
        self.problems: list[Problem] = []
        self.warnings: list[Problem] = []
        self.classes: list[tuple[str, str, str]] = []
        self.materials: list[tuple[str, bool]] = []

    def fail(self, code: str, message: str, item: str | None = None) -> None:
        located = item is not None
        self.problems.append(
            Problem(code, item or self.where, message, location=self.where if located else None)
        )

    def read(self, root: Element) -> ParsedFile:
        dataset = root.find(f"{NS}activityDataset")
        if dataset is None:
            dataset = root.find(f"{NS}childActivityDataset")
        activity = (
            None if dataset is None else dataset.find(f"{NS}activityDescription/{NS}activity")
        )
        flow_data = None if dataset is None else dataset.find(f"{NS}flowData")
        if dataset is None or activity is None or flow_data is None:
            self.fail("invalid-dataset", "has no activityDataset with an activity and flowData")
            return self.result()
        activity_id = activity.get("id")
        kind = ACTIVITY_TYPES.get(activity.get("specialActivityType", "0"))
        if kind is None:
            self.fail(
                "unknown-activity-type",
                f"has specialActivityType {activity.get('specialActivityType')!r}; "
                f"known codes are 0 to 10",
            )
        release = self._release(dataset)
        system_model = self._system_model(dataset)
        references = [
            e
            for e in flow_data.iterfind(f"{NS}intermediateExchange")
            if text(e, "outputGroup") == "0"
        ]
        reference_product = references[0].get("intermediateExchangeId") if references else None
        if len(references) != 1:
            self.fail(
                "reference-count",
                f"has {len(references)} reference products (lines in output group 0); a dataset "
                "makes exactly one",
            )
        elif self.file != f"{activity_id}_{reference_product}.spold":
            self.fail(
                "file-name-mismatch",
                f"holds activity {activity_id} making product {reference_product}, so its name "
                f"should be {activity_id}_{reference_product}.spold",
            )
        if self.problems or release is None or system_model is None or kind is None:
            return self.result(release, system_model)
        prefix = f"ecoinvent:{self.version}:{system_model}"
        process_id = f"{prefix}:{activity_id}_{reference_product}"
        description = dataset.find(f"{NS}activityDescription")
        assert description is not None
        roles = ROLES[system_model]
        exchanges = [
            line
            for element in flow_data
            if (line := self._line(element, process_id, prefix, roles)) is not None
        ]
        period = description.find(f"{NS}timePeriod")
        record = {
            "id": process_id,
            "name": text(activity, "activityName"),
            "location": description.findtext(f"{NS}geography/{NS}shortname"),
            "time_start": self._date(period, "startDate"),
            "time_end": self._date(period, "endDate"),
            "kind": kind,
            "exchanges": exchanges,
            "source_ref": self.file,
        }
        if self.problems:
            return self.result(release, system_model)
        try:
            process = Process.model_validate(record)
        except ValidationError as error:
            self.problems.extend(self._from_validation(error, f"process {process_id}"))
            return self.result(release, system_model)
        return self.result(release, system_model, process)

    def result(
        self,
        release: tuple[int, int] | None = None,
        system_model: str | None = None,
        process: Process | None = None,
    ) -> ParsedFile:
        return ParsedFile(
            self.file,
            process=process,
            release=release,
            system_model=system_model,
            classes=tuple(self.classes),
            materials=tuple(self.materials),
            problems=tuple(self.problems),
            warnings=tuple(self.warnings),
        )

    def _release(self, dataset: Element) -> tuple[int, int] | None:
        attributes = dataset.find(f"{NS}administrativeInformation/{NS}fileAttributes")
        major = None if attributes is None else attributes.get("majorRelease", "")
        minor = None if attributes is None else attributes.get("minorRelease", "")
        if major is None or minor is None or not (major.isdigit() and minor.isdigit()):
            self.fail("invalid-dataset", "has no fileAttributes with majorRelease and minorRelease")
            return None
        return int(major), int(minor)

    def _system_model(self, dataset: Element) -> str | None:
        representativeness = dataset.find(f"{NS}modellingAndValidation/{NS}representativeness")
        if representativeness is None:
            self.fail("invalid-dataset", "has no representativeness, so no system model")
            return None
        found = SYSTEM_MODELS.get(representativeness.get("systemModelId", ""))
        if found is None:
            name = text(representativeness, "systemModelName")
            supported = ", ".join(f"{n} ({i})" for i, n in SYSTEM_MODELS.items())
            self.fail(
                "unsupported-system-model",
                f"uses the system model {name!r} ({representativeness.get('systemModelId')}). "
                f"The roles are derived with the rules of a system model, and only these have "
                f"rules: {supported}.",
            )
        return found

    def _date(self, period: Element | None, attribute: str) -> date | None:
        value = None if period is None else period.get(attribute)
        if value is None:
            return None
        try:
            return date.fromisoformat(value)
        except ValueError:
            self.fail("invalid-dataset", f"timePeriod {attribute} {value!r} is not an ISO date")
            return None

    # --- lines -----------------------------------------------------------------------------

    def _line(
        self, element: Element, process_id: str, prefix: str, roles: RoleTable
    ) -> Exchange | None:
        tag = element.tag
        if tag == f"{NS}intermediateExchange":
            elementary = False
        elif tag == f"{NS}elementaryExchange":
            elementary = True
        else:
            return None  # parameters and other elements of flowData are not lines
        line_id = element.get("id")
        if not line_id:
            flow = element.get("intermediateExchangeId") or element.get("elementaryExchangeId")
            self.fail("invalid-dataset", f"a line of flow {flow} has no id")
            return None
        label = f"{process_id} line {line_id}"
        amount = number(element.get("amount"))
        if amount is None:
            self.fail("invalid-dataset", f"amount {element.get('amount')!r} is not a number", label)
            return None
        output, input_ = text(element, "outputGroup"), text(element, "inputGroup")
        supplier: str | None = None
        if elementary:
            flow_id = elementary_id(element.get("elementaryExchangeId", ""))
            if output == "4" and input_ is None:
                role, negated = Role.emission, False
            elif input_ == "4" and output is None:
                role, negated = Role.resource, False
            else:
                self._unsupported_group(output, input_, label)
                return None
        else:
            uuid = element.get("intermediateExchangeId", "")
            flow_id = product_id(uuid)
            flow_class = by_product_class(element)
            found = self._role(output, input_, flow_class, amount, label, roles)
            if found is None:
                return None
            role, negated = found
            assert flow_class is not None
            self.classes.append((line_id, flow_id, flow_class))
            if negated and flow_class != WASTE and role is not Role.recyclable:
                self.materials.append((flow_id, role is Role.reference))
            link = element.get("activityLinkId")
            if role in ROLES_WITH_SUPPLIER and link:
                supplier = f"{prefix}:{link}_{uuid}"
                if role is Role.input and supplier == process_id:
                    role = Role.own_use
        uncertainty, pedigree = self._uncertainty(element, amount, negated, label)
        record = {
            "process_id": process_id,
            "line_id": line_id,
            "role": role,
            "flow_id": flow_id,
            "flow_name": text(element, "name"),
            "amount": abs(amount) if negated else amount,
            "unit": text(element, "unitName"),
            "supplier_id": supplier,
            "link_status": LinkStatus.linked if supplier else None,
            "comment": text(element, "comment"),
            "uncertainty": uncertainty,
            "pedigree": pedigree,
            "properties": self._properties(element, label),
        }
        try:
            return Exchange.model_validate(record)
        except ValidationError as error:
            self.problems.extend(self._from_validation(error, label))
            return None

    def _role(
        self,
        output: str | None,
        input_: str | None,
        flow_class: str | None,
        amount: float,
        label: str,
        roles: RoleTable,
    ) -> tuple[Role, bool] | None:
        """The role of a product line, and whether its amount is negated."""
        if flow_class is None:
            self.fail("missing-classification", "has no by-product classification", label)
            return None
        if flow_class not in CLASSES:
            self.fail(
                "unknown-classification",
                f"has the by-product classification {flow_class!r}; "
                f"expected one of {', '.join(CLASSES)}",
                label,
            )
            return None
        side = side_of(output, input_)
        if side is None:
            self._unsupported_group(output, input_, label)
            return None
        found = roles.get((side, flow_class, sign_of(amount)))
        if found is None:
            signs = [s.value for s in Sign if (side, flow_class, s) in roles]
            a = "an" if side is Side.input else "a"
            allowed = f"is {' or '.join(signs)}" if signs else "has no role"
            self.fail(
                "unexpected-sign",
                f"is {a} {side} of class {flow_class!r} with amount {amount:g}, which has no "
                f"role. In this system model {a} {side} of this class {allowed}.",
                label,
            )
        return found

    def _unsupported_group(self, output: str | None, input_: str | None, label: str) -> None:
        group = f"output group {output}" if output is not None else f"input group {input_}"
        if output is not None and input_ is not None:
            group = f"output group {output} and input group {input_}"
        self.fail("unsupported-group", f"is in {group}, which has no role", label)

    def _properties(self, element: Element, label: str) -> list[ExchangeProperty]:
        found: list[ExchangeProperty] = []
        for prop in element.iterfind(f"{NS}property"):
            name, unit, amount = text(prop, "name"), text(prop, "unitName"), prop.get("amount")
            key = (name or "", amount or "", unit or "")
            shared = _SHARED_PROPERTIES.get(key)
            if shared is not None:
                found.append(shared)
                continue
            value = number(amount)
            if name is None or unit is None or value is None:
                self.fail(
                    "invalid-property",
                    f"property {prop.get('propertyId')} needs a name, a unitName and an amount",
                    label,
                )
                continue
            found.append(
                _SHARED_PROPERTIES.setdefault(
                    key, ExchangeProperty(name=name, value=value, unit=unit)
                )
            )
        return found

    def _uncertainty(
        self, element: Element, amount: float, negated: bool, label: str
    ) -> tuple[Uncertainty | None, Pedigree | None]:
        """Lognormal: `loc` is ln|amount| and `scale` the square root of the variance with
        pedigree, as Brightway does. Normal: the mean and that same square root. Uniform and
        triangular: their bounds and mode. A negated amount negates the distribution."""
        block = element.find(f"{NS}uncertainty")
        if block is None:
            return None, None
        pedigree = self._pedigree(block, label)
        distributions = [
            child
            for child in block.iterchildren(f"{NS}*")
            if etree.QName(child).localname not in ("pedigreeMatrix", "comment")
        ]
        if len(distributions) != 1:
            self.fail("bad-uncertainty", "needs exactly one distribution", label)
            return None, pedigree
        child = distributions[0]
        name = etree.QName(child).localname
        sign = -1.0 if negated else 1.0

        def value(attribute: str) -> float:
            found = number(child.get(attribute))
            if found is None:
                raise ValueError(
                    f"{name} needs a number in {attribute}, got {child.get(attribute)!r}"
                )
            return found

        try:
            match name:
                case "lognormal" | "normal":
                    variance = value("varianceWithPedigreeUncertainty")
                    if variance < 0:
                        raise ValueError(f"{name} variance {variance:g} is negative")
                    if variance == 0:
                        self.warnings.append(
                            Problem(
                                "zero-variance",
                                label,
                                f"has a {name} distribution with zero variance: kept as a fixed "
                                "amount, with its pedigree",
                                severity=Severity.warning,
                                location=self.where,
                            )
                        )
                        return None, pedigree
                    if name == "lognormal":
                        if amount == 0:
                            raise ValueError(
                                "a lognormal distribution needs an amount other than 0"
                            )
                        loc = math.log(abs(amount))
                    else:
                        loc = sign * value("meanValue")
                    distribution = Distribution(name)
                    fields = {"loc": loc, "scale": math.sqrt(variance)}
                case "uniform":
                    low, high = value("minValue"), value("maxValue")
                    distribution = Distribution.uniform
                    fields = {"minimum": low, "maximum": high}
                    if negated:
                        fields = {"minimum": -high, "maximum": -low}
                case "triangular":
                    low, mode, high = value("minValue"), value("mostLikelyValue"), value("maxValue")
                    distribution = Distribution.triangular
                    fields = {"minimum": low, "loc": mode, "maximum": high}
                    if negated:
                        fields = {"minimum": -high, "loc": -mode, "maximum": -low}
                case _:
                    self.fail(
                        "unsupported-uncertainty",
                        f"has a {name} distribution; supported are lognormal, normal, uniform "
                        "and triangular",
                        label,
                    )
                    return None, pedigree
            return Uncertainty(distribution=distribution, **fields), pedigree
        except ValueError as error:  # pydantic's ValidationError is a ValueError too
            problems = _record_problems(error) if isinstance(error, ValidationError) else []
            message = "; ".join(f"{p.item}: {p.message}" for p in problems) or str(error)
            self.fail("bad-uncertainty", message, label)
            return None, pedigree

    def _pedigree(self, block: Element, label: str) -> Pedigree | None:
        matrix = block.find(f"{NS}pedigreeMatrix")
        if matrix is None:
            return None
        scores = {field: matrix.get(attribute, "") for field, attribute in _PEDIGREE.items()}
        key = tuple(scores.values())
        if (shared := _SHARED_PEDIGREES.get(key)) is not None:
            return shared
        try:
            pedigree = Pedigree.model_validate({k: int(v) for k, v in scores.items()})
            return _SHARED_PEDIGREES.setdefault(key, pedigree)
        except ValueError as error:
            self.fail(
                "bad-pedigree", f"pedigree matrix is not five scores from 1 to 5: {error}", label
            )
            return None

    def _from_validation(self, error: ValidationError, item: str) -> list[Problem]:
        found = [replace(p, location=self.where) for p in _record_problems(error)]
        for err in error.errors():
            if not isinstance((err.get("ctx") or {}).get("error"), RecordError):
                field = ".".join(str(part) for part in err["loc"])
                message = err["msg"].removeprefix("Value error, ")
                text_ = f"{field}: {message}" if field else message
                found.append(Problem("invalid-dataset", item, text_, location=self.where))
        return found


def _record_problems(error: ValidationError) -> list[Problem]:
    """The problems a record's own validator raised, which pydantic wraps."""
    found: list[Problem] = []
    for err in error.errors():
        inner = (err.get("ctx") or {}).get("error")
        if isinstance(inner, RecordError):
            found.extend(inner.problems)
    return found
