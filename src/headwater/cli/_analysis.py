"""What `contributions`, `where` and `sensitivity` share: open a database or a study, load its
background, and calculate each product asked for, for one indicator.

A database gives one product: its only one, or the one `--product` names. A study gives each of
its products, its amount of it, in the scenario `--scenario` names, or the one `--product` names.
Example: `headwater contributions project/tshirt-footprint` explains 1 T-shirt of `tshirt-dyed`,
1.242646, and with `--scenario cleaner-dye` the same T-shirt at 1.237646.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

import typer

import headwater as hw
from headwater.cli._common import (
    background_text,
    database_header,
    fail,
    indicator_line,
    load_background,
    pick_indicators,
    pinned_record,
)
from headwater.model import HeadwaterError, did_you_mean
from headwater.query._internals import internals

FolderArgument = Annotated[
    Path,
    typer.Argument(
        help="A database folder, which holds database.yaml, or a study folder, which holds "
        "study.yaml."
    ),
]

ProductOption = Annotated[
    str | None,
    typer.Option(
        "--product",
        help="The product to analyse, by the id of the process that makes it. Needed for a "
        "database with more than one; a study has each of its products analysed without it.",
    ),
]

ScenarioOption = Annotated[
    str | None,
    typer.Option("--scenario", help="For a study: analyse it in one of its scenarios, by name."),
]


@dataclass(frozen=True)
class Analysed:
    """A database or a study, its background, and one result per product analysed."""

    opened: hw.Database | hw.Study
    path: Path
    background: hw.Background
    results: tuple[hw.Result, ...]
    scenario: str | None


def analyse(
    folder: Path,
    *,
    command: str,
    release: str | None,
    method: list[str] | None,
    namespace: str | None,
    indicator: str | None,
    nw_set: list[Path] | None,
    method_folder: list[Path] | None,
    product: str | None,
    scenario: str | None,
) -> Analysed:
    """Open `folder`, load its background and calculate the products asked for, for one
    indicator. Leaves with exit code 1 on an error of the folder or the background, and 2 for a
    product or a scenario that the folder does not have."""
    from headwater.cli.study import known_scenario, one_product, study_scenario

    try:
        opened = hw.open(folder)
        if isinstance(opened, hw.Database):
            known_scenario(opened, scenario)
            wanted: str | None = one_product(opened, product)
            record = opened.folder.database
        else:
            study_scenario(opened, scenario)
            wanted = _study_product(opened, product, command)
            record = pinned_record(opened)
        background = load_background(
            record,
            release=release,
            method=method,
            namespace=namespace,
            sets=nw_set,
            method_folders=method_folder,
            kind="database" if isinstance(opened, hw.Database) else "study",
        )
        [chosen] = pick_indicators(background, indicator, one=True)
        if isinstance(opened, hw.Database):
            assert wanted is not None
            results = (opened.calc(background, wanted, indicator=chosen),)
        else:
            every = opened.calc(background, indicator=chosen, scenario=scenario)
            results = tuple(r for r in every if wanted is None or r.process_id == wanted)
    except HeadwaterError as error:
        fail(error)
    return Analysed(opened, folder, background, results, scenario)


def _study_product(study: hw.Study, product: str | None, command: str) -> str | None:
    """The product of `study` that `product` names, or None for all of them. Leaves with exit
    code 2 for an id that is not one of its products."""
    if product is None:
        return None
    ids = [p.process for p in study.folder.products]
    if product not in ids:
        fail(
            f"study {study.name} has no product {product!r}.{did_you_mean(product, ids)} Its "
            f"products: {', '.join(ids)}. `headwater {command}` takes the id of the process "
            "that makes one, as study.yaml names it.",
            code=2,
        )
    return product


def header(analysed: Analysed) -> list[str]:
    """The first lines of the text: the folder, the background and its method, the scenario of
    a study when one is named, and the indicator."""
    opened, background = analysed.opened, analysed.background
    if isinstance(opened, hw.Database):
        lines = database_header(opened, analysed.path, background)
    else:
        lines = [
            f"Study {opened.name} ({analysed.path})",
            f"Background: {background_text(background)}, method {background.method.id} "
            f"{background.method.version}",
        ]
        if analysed.scenario is not None:
            lines.append(f"Scenario: {analysed.scenario}")
    return [*lines, indicator_line(background, analysed.results[0])]


def document(analysed: Analysed, format: str, results: list[dict[str, Any]]) -> dict[str, Any]:
    """The JSON of an analysis: what was analysed, on which background and indicator, then
    `results`, one per product, each made by `result_data` with the command's own keys."""
    background = analysed.background
    inner = internals(background)
    release = str(inner.releases[0]) if inner.releases else None
    info = {i.id: i for i in background.indicators}[analysed.results[0].indicator]
    single = len(background.method.ids) == 1
    return {
        "format": format,
        "path": str(analysed.path),
        "kind": "database" if isinstance(analysed.opened, hw.Database) else "study",
        "name": analysed.opened.name,
        "background": {"release": release, "folder": None if release else str(inner.folder.path)},
        "method": (
            {"id": background.method.id, "version": background.method.version} if single else None
        ),
        "methods": list(background.method.ids),
        "indicator": {"id": info.id, "name": info.name, "unit": info.unit},
        "scenario": analysed.scenario,
        "engine_version": analysed.results[0].provenance.engine_version,
        "results": results,
    }


def result_data(result: hw.Result, **found: Any) -> dict[str, Any]:
    """One product's entry in `results`: the product and its amount, the score, what the
    command found, and where the score comes from."""
    p = result.provenance
    return {
        "product": {"id": result.process_id, "amount": result.amount},
        "score": result.score,
        "unit": result.unit,
        **found,
        "provenance": {
            "input_hash": p.input_hash,
            "method_hash": p.method_hash,
            "pin": p.pin,
            "residual": p.residual,
        },
    }


def sensitivity_data(lines: tuple[hw.Sensitivity, ...]) -> list[dict[str, Any]]:
    """The lines of a sensitivity analysis as data, largest elasticity first."""
    return [
        {
            "process_id": s.process_id,
            "name": s.name,
            "line_id": s.line_id,
            "role": s.role.value,
            "flow_id": s.flow_id,
            "supplier_id": s.supplier_id,
            "supplier_name": s.supplier_name,
            "amount": s.amount,
            "unit": s.unit,
            "elasticity": s.elasticity,
            "sensitivity": s.sensitivity,
            "copy_of": s.copy_of,
        }
        for s in lines
    ]
