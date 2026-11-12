"""ecoinvent's LCIA implementation workbook as a method source.

Example: `EF v3.1`, category `climate change: fossil`, `Methane, fossil`, `air`, `urban air close
to ground` becomes a factor of indicator `climate-change-fossil` for the flow named `Methane,
fossil` in `air/urban air close to ground`. The shared stage, `headwater.io.method.map_method`,
finds that flow in the release by name and compartment.

The method's id, version and variant default to ones read from the workbook's method name, when
not given or blank: `EF v3.1` gives `EF-3.1`, `3.1` and `EF v3.1`. A name with no `v` version
takes a year in it: `IPCC 2021 (incl. biogenic CO2)` gives `IPCC-2021-incl-biogenic-CO2` and
`2021`. Indicator ids are made from the category names: `climate change: fossil` gives
`climate-change-fossil`.
"""

from dataclasses import dataclass, field
from pathlib import Path

from headwater.io.lcia.workbook import LciaWorkbook, read_workbook
from headwater.io.method import (
    MappedMethod,
    indicator_id,
    map_method,
    method_id,
    method_version,
    missing_version,
)
from headwater.io.source import FactorRow, MethodOptions, MethodSource
from headwater.link import FlowKey
from headwater.model import Indicator, InvalidDataError, Problem
from headwater.release import Release

ECOINVENT = "ecoinvent"


def read(path: Path, options: MethodOptions) -> MethodSource:
    """Read the method `options.method` of the workbook at `path`."""
    if not options.method:
        raise InvalidDataError(
            [
                Problem(
                    "missing-method",
                    f"workbook {path.name}",
                    "holds many methods: name one, for example --method 'EF v3.1'",
                )
            ]
        )
    return method_source(
        read_workbook(path, options.method, categories=options.categories), options
    )


def method_source(workbook: LciaWorkbook, options: MethodOptions | None = None) -> MethodSource:
    """The workbook's factors, keyed by name and compartment, with the method's id, version and
    variant from `options` or from the method's name."""
    options = options or MethodOptions()
    problems: list[Problem] = []
    version = (options.version or "").strip() or _version_in_name(workbook.method, problems)
    indicators = _indicators(workbook, problems)
    if problems or version is None:
        raise InvalidDataError(problems)
    return MethodSource(
        name=workbook.method,
        id=(options.id or "").strip() or method_id(workbook.method, version),
        version=version,
        variant=(options.variant or "").strip() or workbook.method,
        indicators=tuple(sorted(indicators.values(), key=lambda i: i.id)),
        factors=tuple(
            FactorRow(
                indicator=indicators[row.category].id,
                flow=FlowKey(name=row.name, compartment=row.compartment),
                factor=row.factor,
                where=row.location,
            )
            for row in workbook.rows
        ),
        flow_list=f"{ECOINVENT}-{workbook.version}",
        origin=(
            f"Imported by headwater from {workbook.path.name}, method {workbook.method}, "
            f"ecoinvent {workbook.version}."
        ),
        keyed_by="name, compartment and subcompartment",
        left_out=workbook.left_out,
    )


@dataclass(frozen=True)
class MethodImport(MappedMethod):
    """A method mapped from a workbook, with the workbook it came from."""

    workbook: LciaWorkbook = field(kw_only=True)


def import_method(
    workbook: LciaWorkbook,
    release: Release,
    *,
    method_id: str | None = None,
    version: str | None = None,
    variant: str | None = None,
) -> MethodImport:
    """Map each factor of `workbook` to an elementary flow of `release`, and report the rest.

    The release must be an ecoinvent source release of the workbook's version: a row matches the
    one elementary flow with exactly its name and compartment, no case folding, synonyms or CAS
    numbers, and names change between versions.
    """
    options = MethodOptions(id=method_id, version=version, variant=variant)
    mapped = map_method(method_source(workbook, options), release)
    return MethodImport(**vars(mapped), workbook=workbook)


def _version_in_name(name: str, problems: list[Problem]) -> str | None:
    """The version in a method's name (see `method_version`), or a problem."""
    found = method_version(name)
    if found is None:
        problems.append(missing_version(name))
    return found


def _indicators(workbook: LciaWorkbook, problems: list[Problem]) -> dict[str, Indicator]:
    """Each category's indicator, by category name. `climate change: fossil` gives the id
    `climate-change-fossil`, and keeps its category indicator, `global warming potential
    (GWP100)`."""
    indicators: dict[str, Indicator] = {}
    categories: dict[str, str] = {}
    for category in workbook.categories:
        id = indicator_id(category.name)
        if not id or id in categories:
            reason = f"gives the same id as {categories[id]!r}" if id else "gives an empty id"
            problems.append(
                Problem("bad-indicator-id", f"category {category.name}", f"{reason}: {id!r}")
            )
            continue
        categories[id] = category.name
        indicators[category.name] = Indicator(
            id=id, name=category.name, category_indicator=category.indicator, unit=category.unit
        )
    return indicators
