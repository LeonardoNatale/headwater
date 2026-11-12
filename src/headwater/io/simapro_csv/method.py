"""A SimaPro CSV method export as a method source.

Example: the method `EF 3.1`, impact category `Climate change` in `kg CO2 eq`, gives `Methane,
fossil` in `Air`, `high. pop.` the factor 29.8 per kg. That is a factor of the indicator
`climate-change` for the flow named `Methane, fossil` in `air/high. pop.`, the compartment the
process reader writes. The shared stage, `headwater.io.method.map_method`, finds the flow by name
and compartment in a release of the same flow list, `simapro-<SimaPro's version>`. A factor for
an unspecified subcompartment covers the others, as in SimaPro: the stage also gives it to
`air/high. pop.` when the method has no factor of its own there.

The method's version defaults to the one its name holds (`EF 3.1` gives `3.1`), else to the
export's `Version` (`1;02` gives `1.02`); its id and its indicators' ids follow the rules every
method reader shares (`EF-3.1`, `climate-change`); its variant defaults to none. Damage
categories, normalisation and weighting are not kept.
"""

from pathlib import Path

from headwater.io.method import indicator_id, method_id, method_version, missing_version
from headwater.io.simapro_csv.export import read_export
from headwater.io.simapro_csv.records import MethodRecord
from headwater.io.simapro_csv.units import headwater_unit
from headwater.io.source import FactorRow, MethodOptions, MethodSource
from headwater.link import FlowKey
from headwater.model import Indicator, InvalidDataError, Problem, did_you_mean


def read_method(path: Path, options: MethodOptions) -> MethodSource:
    """Read the method `options.method` of the export at `path`, or its only method."""
    export = read_export(path)
    item = f"file {export.file}"
    if not export.methods:
        message = (
            "is a SimaPro process export: import it with headwater release import"
            if export.processes
            else "holds no Method block"
        )
        raise InvalidDataError([Problem("wrong-export-kind", item, message)])
    method = _choose(export.methods, options.method, item)
    categories = method.categories
    if options.categories is not None:
        names = {c.name for c in categories}
        unknown = [c for c in options.categories if c not in names]
        if unknown:
            raise InvalidDataError(
                [
                    Problem(
                        "unknown-category",
                        f"category {name}",
                        f"is not a category of {method.name}.{did_you_mean(name, names)} "
                        f"Categories: {', '.join(sorted(names))}",
                    )
                    for name in unknown
                ]
            )
        categories = tuple(c for c in categories if c.name in options.categories)
    problems: list[Problem] = []
    indicators: dict[str, Indicator] = {}
    for category in categories:
        id = indicator_id(category.name)
        if not id or id in indicators:
            reason = f"gives the same id as {indicators[id].name!r}" if id else "gives an empty id"
            problems.append(
                Problem("bad-indicator-id", f"category {category.name}", f"{reason}: {id!r}")
            )
            continue
        indicators[id] = Indicator(id=id, name=category.name, unit=category.unit)
    version = (options.version or "").strip() or method_version(method.name) or method.version
    if version is None:
        problems.append(missing_version(method.name))
    if problems or version is None:
        raise InvalidDataError(problems)
    ids = {i.name: i.id for i in indicators.values()}
    return MethodSource(
        name=method.name,
        id=(options.id or "").strip() or method_id(method.name, version),
        version=version,
        variant=(options.variant or "").strip() or None,
        indicators=tuple(sorted(indicators.values(), key=lambda i: i.id)),
        factors=tuple(
            FactorRow(
                indicator=ids[category.name],
                flow=FlowKey(name=f.name, compartment=f.compartment),
                factor=f.factor,
                unit=headwater_unit(f.unit),
                where=f.where,
                # A compartment with no `/` is one whose subcompartment is unspecified.
                covers_subcompartments="/" not in f.compartment,
            )
            for category in categories
            for f in category.factors
        ),
        flow_list=export.header.flow_list,
        origin=(
            f"Imported by headwater from {export.file}, method {method.name}, SimaPro "
            f"{export.header.simapro}."
        ),
        keyed_by="name and compartment",
        left_out=tuple(sorted(c.name for c in method.categories if c not in categories)),
    )


def _choose(methods: tuple[MethodRecord, ...], name: str | None, item: str) -> MethodRecord:
    names = ", ".join(m.name for m in methods)
    if name is None:
        if len(methods) == 1:
            return methods[0]
        raise InvalidDataError(
            [
                Problem(
                    "missing-method",
                    item,
                    f"holds several methods: name one with --method. Methods: {names}",
                )
            ]
        )
    found = [m for m in methods if m.name == name]
    if not found:
        raise InvalidDataError(
            [
                Problem(
                    "unknown-method",
                    item,
                    f"has no method {name!r}.{did_you_mean(name, [m.name for m in methods])} "
                    f"Methods: {names}",
                )
            ]
        )
    return found[0]
