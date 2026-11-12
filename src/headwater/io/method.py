"""From a method as its source writes it to a method folder: the stage every method format shares.

Example: ecoinvent's workbook gives `Methane, fossil` to `air/urban air close to ground` a factor
of 29.8 for `climate change: fossil`. `map_method` finds that flow in the 3.12 release, so the
method holds the factor for `ecoinvent:flow:<uuid>`, and `write_method` writes `method.yaml` and
`factors.csv`. Everything after reads the folder by flow id.

A method maps onto a release of its own flow list. Names change between versions, so another
flow list needs a reviewed flow mapping, never a silent match. A factor given per another unit
than its flow's is converted within one quantity. Every factor that does not map is listed with
its reason, never guessed.
"""

import csv
import io
import re
import shutil
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import yaml

from headwater.io.files import yaml_comments
from headwater.io.source import FactorRow, MethodSource
from headwater.link import FlowKey, FlowMapping, map_flows
from headwater.model import (
    DEFAULT_UNITS,
    Factor,
    Flow,
    FlowKind,
    Indicator,
    InvalidDataError,
    Method,
    Problem,
    Severity,
    Units,
)
from headwater.release import Release, ReleaseKind, ReleaseRef
from headwater.release.cache import temporary_folder
from headwater.release.manifest import FOLDER_NAME

_VERSIONS = (
    re.compile(r"\bv(\d+(?:\.\d+)*)\b"),
    re.compile(r"\b((?:19|20)\d\d)\b"),
    re.compile(r"\b(\d+(?:\.\d+)+)\b"),
)
"""Where a method's name holds its version, in order: `v3.1`, a year, `2021`, a number with a
dot, `3.1`."""


def method_version(name: str) -> str | None:
    """The version a method's name holds, or None. `EF v3.1` gives `3.1`, `IPCC 2021` gives
    `2021`, and `EF 3.1` gives `3.1`."""
    for pattern in _VERSIONS:
        if (found := pattern.search(name)) is not None:
            return found.group(1)
    return None


def method_id(name: str, version: str) -> str:
    """The default id of a method. `EF v3.1` gives `EF-3.1`, `EF v3.1 no LT` gives
    `EF-3.1-no-LT`, and `IPCC 2021 (incl. biogenic CO2)` gives `IPCC-2021-incl-biogenic-CO2`. A
    name that does not hold its version gets it at the end: `IPCC 2021` and `AR6` give
    `IPCC-2021-AR6`."""
    name = _VERSIONS[0].sub(lambda m: m.group(1), name)
    if not re.search(rf"(?<![\w.]){re.escape(version)}(?![\w.])", name):
        name = f"{name} {version}"
    name = re.sub(r"(?<!\d)\.|\.(?!\d)", " ", name)  # a dot that is not inside a number
    return re.sub(r"[^A-Za-z0-9._]+", "-", name).strip("-")


def missing_version(name: str) -> Problem:
    """The problem of a method whose name holds no version."""
    return Problem(
        "missing-method-version",
        f"method {name}",
        "its name has no version such as 'v3.1' or '3.1' and no year such as '2021': give one",
    )


def indicator_id(name: str) -> str:
    """The id of an indicator, from its category's name: `climate change: fossil` gives
    `climate-change-fossil`. Empty for a name with no letter or digit."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


@dataclass(frozen=True)
class UnmappedFactor:
    """A factor of the source that does not map to a flow of the release, and why."""

    key: FlowKey
    where: str | None
    """Where it is in the source, for example `CFs!A12`."""
    reason: str


@dataclass(frozen=True)
class IndicatorReport:
    """What one indicator's factors mapped to."""

    indicator: Indicator
    mapped: int
    """Flows of the release this indicator's factors map to."""
    unmapped: tuple[UnmappedFactor, ...]
    """Factors whose flow is not in the release, in the order of the source."""
    flows_without_factor: tuple[str, ...]
    """Elementary flows of the release with no factor for this indicator, sorted."""
    copied: int = 0
    """Flows of the release given a factor that covers their subcompartment, and none of their
    own."""

    @property
    def factors(self) -> int:
        """Factors of this indicator in the source."""
        return self.mapped + len(self.unmapped)


@dataclass(frozen=True)
class MappedMethod:
    """A method mapped onto a release's flow list, and the report of its mapping."""

    method: Method
    source: MethodSource
    release: ReleaseRef
    """The release whose flow list the factors are mapped to."""
    flows: int
    """Elementary flows in that flow list."""
    indicators: tuple[IndicatorReport, ...]
    """One per indicator, sorted by indicator id like the method's."""
    not_characterised: tuple[Flow, ...]
    """Elementary flows of the flow list with no factor for any indicator, sorted by id. A study
    that reaches one lists it on its result."""
    mapping: str | None = None
    """The flow mapping file used, if any."""
    warnings: tuple[Problem, ...] = ()
    """Rows of the flow mapping no factor needed."""


def map_method(
    source: MethodSource, release: Release, *, flows: FlowMapping | None = None
) -> MappedMethod:
    """Find each factor's flow among the elementary flows of `release`, and report the rest.

    A factor that covers subcompartments (`air`, for a SimaPro method) also goes to every flow
    of the same name in a subcompartment of it (`air/high. pop.`) that the method gives no factor
    of its own for that indicator.

    Raises `InvalidDataError` for a release that is not a source release, a release of another
    flow list when no mapping is given, a method id that is not a folder name, and two factors
    of one indicator that map to the same flow.
    """
    problems = _check(source, release, flows)
    if problems:
        raise InvalidDataError(problems)
    elementary = tuple(f for f in release.flows() if f.kind is FlowKind.elementary)
    found = map_flows((f.flow for f in source.factors), elementary, mapping=flows)
    by_id = {f.id: f for f in elementary}
    units = DEFAULT_UNITS.merge(release.units())
    factors: dict[tuple[str, str], tuple[Factor, FactorRow]] = {}
    misses: list[tuple[FactorRow, str]] = []
    for row in source.factors:
        mapped = found.mapped.get(row.flow)
        if mapped is None:
            misses.append((row, found.unmapped[row.flow]))
            continue
        value, reason = _per_flow_unit(row, by_id[mapped.flow_id], units)
        if value is None:
            misses.append((row, reason))
            continue
        factor = Factor(
            indicator=row.indicator, flow_id=mapped.flow_id, location=row.location, factor=value
        )
        known = factors.setdefault((row.indicator, mapped.flow_id), (factor, row))
        if known[1] is not row:
            problems.append(
                Problem(
                    "duplicate-factor",
                    f"indicator {row.indicator} flow {mapped.flow_id}",
                    f"has two factors: {known[1].flow} [{known[1].where}] and {row.flow} "
                    f"[{row.where}] map to the same flow",
                )
            )
    copies = _copies(source.factors, elementary, factors, units, problems)
    if problems:
        raise InvalidDataError(problems)
    copied: dict[str, int] = defaultdict(int)
    by_row: dict[int, int] = defaultdict(int)
    for (indicator, _), (_, row) in copies.items():
        copied[indicator] += 1
        by_row[id(row)] += 1
    unmapped: dict[str, list[UnmappedFactor]] = defaultdict(list)
    for row, reason in misses:
        if by_row[id(row)]:
            reason += f"; copied to {by_row[id(row)]} flows of its subcompartments"
        unmapped[row.indicator].append(UnmappedFactor(row.flow, row.where, reason))
    direct = set(factors)
    factors |= copies
    method = Method(
        id=source.id,
        version=source.version,
        variant=source.variant,
        flow_list=release.info.flow_list,
        indicators=source.indicators,
        factors=tuple(
            sorted((f for f, _ in factors.values()), key=lambda f: (f.indicator, f.flow_id))
        ),
    )
    covered: dict[str, set[str]] = defaultdict(set)
    for indicator, flow_id in factors:
        covered[indicator].add(flow_id)
    mapped_directly: dict[str, int] = defaultdict(int)
    for indicator, _ in direct:
        mapped_directly[indicator] += 1
    ids = sorted(by_id)
    characterised = method.characterised_flow_ids
    return MappedMethod(
        method=method,
        source=source,
        release=release.ref,
        flows=len(elementary),
        indicators=tuple(
            IndicatorReport(
                indicator=i,
                mapped=mapped_directly[i.id],
                unmapped=tuple(unmapped[i.id]),
                flows_without_factor=tuple(f for f in ids if f not in covered[i.id]),
                copied=copied[i.id],
            )
            for i in method.indicators
        ),
        not_characterised=tuple(by_id[f] for f in ids if f not in characterised),
        mapping=flows.file if flows is not None else None,
        warnings=tuple(
            Problem(
                "row-not-needed",
                f"mapping {row.decision.source}",
                f"maps {row.key}, but no factor of {source.name} names it",
                severity=Severity.warning,
            )
            for row in found.unused_rows
        ),
    )


def _copies(
    rows: Sequence[FactorRow],
    elementary: Sequence[Flow],
    direct: Mapping[tuple[str, str], tuple[Factor, FactorRow]],
    units: Units,
    problems: list[Problem],
) -> dict[tuple[str, str], tuple[Factor, FactorRow]]:
    """The factors that rows covering subcompartments give to flows of the same name in a
    subcompartment of theirs, where the method gives that indicator no factor of its own. Adds a
    problem when two such rows of one indicator cover the same flow."""
    covering = [row for row in rows if row.covers_subcompartments]
    if not covering:
        return {}
    by_name: dict[str, list[Flow]] = defaultdict(list)
    for flow in elementary:
        by_name[flow.name].append(flow)
    copies: dict[tuple[str, str], tuple[Factor, FactorRow]] = {}
    for row in covering:
        prefix = f"{row.flow.compartment}/"
        for flow in by_name.get(row.flow.name or "", []):
            key = (row.indicator, flow.id)
            if key in direct or not (flow.compartment or "").startswith(prefix):
                continue
            value, _ = _per_flow_unit(row, flow, units)
            if value is None:
                continue  # a unit of another quantity: the flow stays without a factor
            factor = Factor(
                indicator=row.indicator, flow_id=flow.id, location=row.location, factor=value
            )
            known = copies.setdefault(key, (factor, row))
            if known[1] is not row:
                problems.append(
                    Problem(
                        "duplicate-factor",
                        f"indicator {row.indicator} flow {flow.id}",
                        f"has two factors that cover its subcompartment: {known[1].flow} "
                        f"[{known[1].where}] and {row.flow} [{row.where}]",
                    )
                )
    return copies


def _check(source: MethodSource, release: Release, flows: FlowMapping | None) -> list[Problem]:
    item = f"release {release.name}"
    problems: list[Problem] = []
    if release.kind is not ReleaseKind.source:
        problems.append(
            Problem(
                "indicators-release",
                item,
                "holds scores only, with no flow list. Map to its source release "
                f"{release.parent}.",
            )
        )
    elif release.info.flow_list != source.flow_list and flows is None:
        problems.append(
            Problem(
                "flow-list-mismatch",
                item,
                f"has the flow list {release.info.flow_list}, but {source.name} names the flows "
                f"of {source.flow_list}. Names and ids change between flow lists, so the method "
                "maps only onto a release of its own flow list, or with a reviewed flow mapping "
                "file.",
            )
        )
    if not re.fullmatch(FOLDER_NAME, source.id):
        problems.append(
            Problem(
                "bad-method-id",
                f"method {source.id}",
                "a method id names a folder: use letters, digits, '.', '_' and '-' only",
            )
        )
    return problems


def _per_flow_unit(row: FactorRow, flow: Flow, units: Units) -> tuple[float | None, str]:
    """The factor per the flow's own unit, or None and the reason it does not convert. A factor
    per g is 1000 times the factor per kg."""
    if row.unit is None or row.unit == flow.unit:
        return row.factor, ""
    if row.unit not in units or flow.unit not in units:
        unknown = row.unit if row.unit not in units else flow.unit
        return None, f"its unit {unknown!r} is unknown"
    if units.quantity_of(row.unit) != units.quantity_of(flow.unit):
        return None, (
            f"it is per {row.unit}, which does not convert to {flow.unit}, the unit of {flow.id}"
        )
    return row.factor * units.convert(1.0, flow.unit, row.unit), ""


# --- the method folder ----------------------------------------------------------------------


def write_method(mapped: MappedMethod, folder: Path | str) -> Path:
    """Write the method into `folder`, which must not exist or must be empty.

    `method.yaml` starts with comments that say where the factors came from, the release they
    were mapped to, and the categories of the source's method that were left out, if any.
    `factors.csv` is sorted by indicator and flow. The files are written into a hidden folder
    beside `folder`, then renamed, so a failed write leaves no half-written method.
    """
    target = Path(folder)
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise InvalidDataError(
            [
                Problem(
                    "folder-not-empty",
                    f"folder {target}",
                    "already holds files. An import never overwrites a method: choose another "
                    "folder, or remove this one first.",
                )
            ]
        )
    temp = temporary_folder(target.parent, target.name)
    try:
        (temp / "method.yaml").write_text(_method_yaml(mapped), encoding="utf-8")
        (temp / "factors.csv").write_text(_factors_csv(mapped), encoding="utf-8")
        if target.exists():
            target.rmdir()
        temp.rename(target)
    except BaseException:
        shutil.rmtree(temp, ignore_errors=True)
        raise
    return target


def _method_yaml(mapped: MappedMethod) -> str:
    method, source = mapped.method, mapped.source
    release = f"{mapped.release.name}@{mapped.release.hash[:12]}"
    comments = [
        source.origin,
        f"Each factor's flow was found by {source.keyed_by} in release",
        f"{release}, and is written by id.",
    ]
    if mapped.mapping is not None:
        comments.append(f"The rows of {mapped.mapping} decided the flows they name.")
    copied = sum(r.copied for r in mapped.indicators)
    if copied:
        were = "factor was" if copied == 1 else "factors were"
        comments.append(
            f"{copied} {were} copied to subcompartments: a factor for a compartment's "
            "unspecified subcompartment also counts in the others that have none of their own."
        )
    if source.left_out:
        comments.append(f"Categories of {source.name} left out: {', '.join(source.left_out)}.")
    data = {
        "id": method.id,
        "version": method.version,
        "variant": method.variant,
        "flow_list": method.flow_list,
        "indicators": [i.model_dump() for i in method.indicators],
    }
    body = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100)
    return yaml_comments(comments) + body


def _factors_csv(mapped: MappedMethod) -> str:
    text = io.StringIO()
    writer = csv.writer(text, lineterminator="\n")
    writer.writerow(["indicator", "flow", "location", "factor"])
    for factor in mapped.method.factors:
        writer.writerow(
            [factor.indicator, factor.flow_id, factor.location or "", repr(factor.factor)]
        )
    return text.getvalue()
