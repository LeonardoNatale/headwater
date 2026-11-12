"""Read one ecoSpold1 file into raw datasets: what each line says, nothing decided yet.

A file holds one dataset or several. A dataset's reference function gives the process its name,
location and unit, and its time period. Each `exchange` is a line, with its group, its amount as
written (signed) and its distribution. The roles, the flows and the ids come later, in `assemble`,
because they depend on the whole source: a flow is a waste when any dataset says so.

openLCA writes no XML namespace, the format's own tools write `EcoSpold01`: both are read. Any
other root, such as an ecoSpold1 impact method, is skipped with a warning.

A worker never raises: every failure comes back as a `Problem`, because an exception may not
survive the trip back from another process.
"""

import calendar
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from lxml import etree

from headwater.io.ecospold1.roles import Refused, Slot, slot_of
from headwater.io.ecospold1.uncertainty import (
    Refusal,
    read_pedigree,
    read_uncertainty,
    to_float,
)
from headwater.io.ecospold1.units import APPROXIMATE, canonical, quantity
from headwater.model import Problem, Severity
from headwater.model.uncertainty import Pedigree, Uncertainty

NAMESPACE = "http://www.EcoInvent.org/EcoSpold01"
ROOT = "ecoSpold"
AGGREGATED_TYPE = "2"
"""The `dataSetInformation` type of a dataset that holds cumulative results."""

type Element = etree._Element  # pyright: ignore[reportPrivateUsage]

_CAS = re.compile(r"(\d{1,6})-(\d{2})-(\d)")
_WHEN = re.compile(r"(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?")
_PERIOD = {
    "start": ("startYear", "startYearMonth", "startDate"),
    "end": ("endYear", "endYearMonth", "endDate"),
}


type FlowKey = tuple[str, str | None, str | None]
"""A flow by name, and by compartment for an elementary flow or by the quantity of its unit for a
product, the other being None."""


@dataclass(frozen=True, slots=True)
class Line:
    """One `exchange` as written. `amount` keeps its sign, and `uncertainty` describes it.

    `number` is the exchange's number in the file. Several exchanges of a dataset may share one, so
    `id` is the number for the first and `<number>-2`, `<number>-3` for the next."""

    id: str
    number: str
    name: str
    unit: str
    amount: float
    slot: Slot
    key: FlowKey
    """The flow the line names."""
    location: str | None
    cas: str | None
    comment: str | None
    uncertainty: Uncertainty | None
    pedigree: Pedigree | None


@dataclass(frozen=True, slots=True)
class Share:
    """One `allocation`: `fraction` percent for the co-product on line `product`, on these lines.
    All are line ids."""

    product: str
    fraction: float
    lines: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Dataset:
    number: str
    name: str
    location: str
    unit: str
    category: str
    sub_category: str
    infrastructure: bool
    time_start: date | None
    time_end: date | None
    aggregated: bool
    """Its `dataSetInformation` type is 2: it holds cumulative results, upstream included."""
    lines: tuple[Line, ...]
    shares: tuple[Share, ...]


@dataclass(frozen=True)
class ParsedFile:
    """What one file gave, with its path under the source folder."""

    file: str
    datasets: tuple[Dataset, ...] = ()
    problems: tuple[Problem, ...] = ()
    warnings: tuple[Problem, ...] = ()
    renamed: Counter[str] = field(default_factory=Counter[str])
    """Lines that use a unit renamed by a convention of the source (`APPROXIMATE`), by unit."""


def parse_files(paths: Sequence[Path], base: Path) -> list[ParsedFile]:
    """Read these files, named relative to `base`. Never raises."""
    return [parse_file(path, base) for path in paths]


def parse_file(path: Path, base: Path) -> ParsedFile:
    file = path.relative_to(base).as_posix() if path != base else path.name
    try:
        parser = etree.XMLParser(resolve_entities=False, no_network=True)
        root = etree.parse(str(path), parser).getroot()
    except etree.XMLSyntaxError as error:
        return _unreadable(file, f"is not valid XML: {error}")
    except OSError as error:
        return _unreadable(file, f"cannot be read: {error}")
    name = etree.QName(root)
    if name.localname != ROOT or name.namespace not in (None, NAMESPACE):
        why = f"is not an ecoSpold1 file: its root is {root.tag}"
        warning = Problem("skipped-file", file, why, severity=Severity.warning, location=file)
        return ParsedFile(file, warnings=(warning,))
    try:
        return _Parser(file, f"{{{name.namespace}}}" if name.namespace else "").read(root)
    except Exception as error:  # noqa: BLE001  every failure must come back as data
        return _unreadable(file, f"cannot be read: {type(error).__name__}: {error}")


def _unreadable(file: str, why: str) -> ParsedFile:
    return ParsedFile(file, problems=(Problem("unreadable-file", file, why, location=file),))


class _Parser:
    """Reads the datasets of one file, collecting every problem rather than stopping at the
    first."""

    def __init__(self, file: str, ns: str) -> None:
        self.file = file
        self.ns = ns
        self.problems: list[Problem] = []
        self.warnings: list[Problem] = []
        self.renamed: Counter[str] = Counter()

    def fail(self, code: str, item: str, message: str) -> None:
        self.problems.append(Problem(code, item, message, location=self.file))

    def read(self, root: Element) -> ParsedFile:
        datasets = [d for e in root.iterfind(f"{self.ns}dataset") if (d := self.dataset(e))]
        return ParsedFile(
            self.file, tuple(datasets), tuple(self.problems), tuple(self.warnings), self.renamed
        )

    def warn(self, code: str, item: str, message: str) -> None:
        self.warnings.append(
            Problem(code, item, message, severity=Severity.warning, location=self.file)
        )

    def text(self, element: Element, tag: str) -> str | None:
        return _clean(element.findtext(f"{self.ns}{tag}"))

    # --- datasets --------------------------------------------------------------------------

    def dataset(self, element: Element) -> Dataset | None:
        ns = self.ns
        number = element.get("number")
        item = f"dataset {number or '?'}"
        info = element.find(f"{ns}metaInformation/{ns}processInformation")
        flow_data = element.find(f"{ns}flowData")
        if info is None or flow_data is None:
            self.fail(
                "invalid-dataset",
                item,
                "has no metaInformation/processInformation or no flowData",
            )
            return None
        reference = info.find(f"{ns}referenceFunction")
        geography = info.find(f"{ns}geography")
        name = _clean(reference.get("name") if reference is not None else None)
        unit = _clean(reference.get("unit") if reference is not None else None)
        location = _clean(geography.get("location") if geography is not None else None)
        lacking = [
            what
            for found, what in (
                (number, "number"),
                (name, "name in its referenceFunction"),
                (unit, "unit in its referenceFunction"),
                (location, "location in its geography"),
            )
            if not found
        ]
        if lacking:
            self.fail("invalid-dataset", item, f"has no {', and no '.join(lacking)}")
            return None
        assert reference is not None and number and name and unit and location
        before = len(self.problems)
        seen: Counter[str] = Counter()
        found = (self.line(e, item, seen) for e in flow_data.iterfind(f"{ns}exchange"))
        lines = [line for line in found if line is not None]
        if len(self.problems) == before:  # a line that failed may have been the reference
            self.check(lines, item)
        shares = [
            s for e in flow_data.iterfind(f"{ns}allocation") if (s := self.share(e, item, lines))
        ]
        start, end = self.period(info.find(f"{ns}timePeriod"), item)
        return Dataset(
            number=number,
            name=name,
            location=location,
            unit=canonical(unit),
            category=reference.get("category", "").strip(),
            sub_category=reference.get("subCategory", "").strip(),
            infrastructure=reference.get("infrastructureProcess", "false").strip() == "true",
            time_start=start,
            time_end=end,
            aggregated=self.aggregated(info.find(f"{ns}dataSetInformation")),
            lines=tuple(lines),
            shares=tuple(shares),
        )

    def check(self, lines: list[Line], item: str) -> None:
        """One line is the reference product."""
        references = [line.id for line in lines if line.slot is Slot.reference]
        if len(references) != 1:
            self.fail(
                "reference-count",
                item,
                f"has {len(references)} reference products (lines in outputGroup 0); a dataset "
                "makes exactly one, and its co-products are in outputGroup 2",
            )

    def period(self, element: Element | None, item: str) -> tuple[date | None, date | None]:
        if element is None:
            return None, None
        found: list[date | None] = []
        for side, tags in _PERIOD.items():
            text = next((t for tag in tags if (t := self.text(element, tag))), None)
            try:
                found.append(_when(text, last=side == "end"))
            except ValueError:
                self.fail("invalid-dataset", item, f"has the time period {side} {text!r}")
                found.append(None)
        start, end = found
        if start and end and end < start:
            self.warn(
                "backwards-period",
                item,
                f"has a time period that ends ({end}) before it starts ({start}): kept with no "
                "time period, since the file does not say which date is wrong",
            )
            return None, None
        return start, end

    @staticmethod
    def aggregated(element: Element | None) -> bool:
        return element is not None and (element.get("type") or "").strip() == AGGREGATED_TYPE

    def share(self, element: Element, item: str, lines: list[Line]) -> Share | None:
        """An allocation, with the numbers it gives resolved to line ids. The co-product is the
        one product with that number. A number that only products have is no line to allocate:
        products are not allocated. A number no line has is refused."""
        number = _clean(element.get("referenceToCoProduct"))
        fraction = to_float(element.get("fraction"))
        if number is None or fraction is None:
            self.fail(
                "invalid-dataset",
                item,
                "has an allocation with no referenceToCoProduct, or no number in fraction",
            )
            return None
        products = [line.id for line in lines if line.slot.makes and line.number == number]
        if len(products) != 1:
            self.fail(
                "unknown-reference",
                item,
                f"gives an allocation to line {number}, which is "
                + (
                    "the number of several products"
                    if products
                    else "not a product of the dataset (outputGroup 0 or 2)"
                ),
            )
            return None
        referred = (
            r.text.strip() for r in element.iterfind(f"{self.ns}referenceToInputOutput") if r.text
        )
        allocated: list[str] = []
        for ref in referred:
            having = [line for line in lines if line.number == ref]
            if not having:
                self.fail(
                    "unknown-reference",
                    item,
                    f"allocates to line {ref}, which the dataset does not have",
                )
            allocated += [line.id for line in having if not line.slot.makes]
        return Share(products[0], fraction, tuple(dict.fromkeys(allocated)))

    # --- lines -----------------------------------------------------------------------------

    def line(self, element: Element, item: str, seen: Counter[str]) -> Line | None:
        number = _clean(element.get("number"))
        if number is None:
            self.fail("invalid-dataset", item, "has a line with no number")
            return None
        seen[number] += 1
        line_id = number if seen[number] == 1 else f"{number}-{seen[number]}"
        label = f"{item} line {line_id}"
        name, unit = _clean(element.get("name")), _clean(element.get("unit"))
        amount = to_float(element.get("meanValue"))
        if unit in APPROXIMATE:
            self.renamed[unit] += 1
        if name is None or unit is None:
            self.fail("invalid-dataset", label, "needs a name and a unit")
            return None
        if amount is None:
            self.fail(
                "invalid-dataset",
                label,
                f"needs a number in meanValue, got {element.get('meanValue')!r}",
            )
            return None
        slot = slot_of(self.text(element, "inputGroup"), self.text(element, "outputGroup"))
        if isinstance(slot, Refused):
            self.fail(slot.code, label, slot.message)
            return None
        category = _clean(element.get("category"))
        if slot.elementary and category is None:
            self.fail(
                "missing-category",
                label,
                f"is an elementary flow, {name!r}, with no category to name its compartment",
            )
            return None
        sub_category = _clean(element.get("subCategory"))
        key: FlowKey = (
            (name, "/".join(p for p in (category, sub_category) if p), None)
            if slot.elementary
            else (name, None, quantity(canonical(unit)))
        )
        comment = _clean(element.get("generalComment"))
        try:
            uncertainty, note = read_uncertainty(element.get, amount)
        except Refusal as error:
            self.fail(error.code, label, str(error))
            return None
        if note is not None:
            self.warn(note.code, label, note.message)
        return Line(
            id=line_id,
            number=number,
            name=name,
            unit=canonical(unit),
            amount=amount,
            slot=slot,
            key=key,
            location=_clean(element.get("location")),
            cas=_cas(element.get("CASNumber")),
            comment=comment,
            uncertainty=uncertainty,
            pedigree=read_pedigree(comment),
        )


def _clean(text: str | None) -> str | None:
    """The text without surrounding blanks, or None when nothing is left."""
    return (text.strip() or None) if text is not None else None


def _cas(text: str | None) -> str | None:
    """A CAS number without the zero padding the schema asks for: `000124-38-9` is `124-38-9`."""
    found = _clean(text)
    match = _CAS.fullmatch(found) if found else None
    return f"{int(match[1])}-{match[2]}-{match[3]}" if match else found


def _when(text: str | None, *, last: bool) -> date | None:
    """A date from a year, a year and month, or a date, with any time zone after it ignored. Of a
    year or a month, the first day, or the last when `last`."""
    if text is None:
        return None
    found = _WHEN.match(text)
    if found is None:
        raise ValueError(text)
    year = int(found[1])
    if found[2] is None:
        return date(year, 12, 31) if last else date(year, 1, 1)
    month = int(found[2])
    if found[3] is None:
        return date(year, month, calendar.monthrange(year, month)[1] if last else 1)
    return date(year, month, int(found[3]))
