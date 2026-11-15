"""`headwater sensitivity`: the lines of a database or a study, and of the background they reach,
that move its score most."""

from __future__ import annotations

from typing import Annotated, Any

import typer

import headwater as hw
from headwater.cli._analysis import (
    FolderArgument,
    ProductOption,
    ScenarioOption,
    analyse,
    document,
    header,
    result_data,
    sensitivity_data,
)
from headwater.cli._common import (
    JsonOption,
    MethodFolderOption,
    MethodOption,
    NamespaceOption,
    OneIndicatorOption,
    ReleaseOption,
    SetOption,
    sensitivity_line,
)
from headwater.cli._json import echo

TopOption = Annotated[
    int, typer.Option("--top", min=1, help="How many to list, the largest first.")
]

FORMAT = "headwater-sensitivity/1"
"""The `format` of `sensitivity --json`. Within version 1 keys may be added, none is renamed or
removed."""


def sensitivity_command(
    folder: FolderArgument,
    release: ReleaseOption = None,
    method: MethodOption = None,
    namespace: NamespaceOption = None,
    indicator: OneIndicatorOption = None,
    nw_set: SetOption = None,
    top: TopOption = 10,
    method_folder: MethodFolderOption = None,
    product: ProductOption = None,
    include_reference: Annotated[
        bool,
        typer.Option(
            "--include-reference",
            help="Also list each process's reference line, what it makes per run.",
        ),
    ] = False,
    scenario: ScenarioOption = None,
    as_json: JsonOption = False,
) -> None:
    """Sensitivity analysis: the lines a person can change, largest elasticity first.

    A line is an input, a waste, an emission or a resource, of the database or of a background
    process it reaches. Its elasticity is the % change of the score per 1% change of the line.
    A study has one list per product, its amount of it.
    """
    from headwater.calc import CalculationError

    analysed = analyse(
        folder,
        command="sensitivity",
        release=release,
        method=method,
        namespace=namespace,
        indicator=indicator,
        nw_set=nw_set,
        method_folder=method_folder,
        product=product,
        scenario=scenario,
    )
    lines = header(analysed)
    found: list[dict[str, Any]] = []
    for result in analysed.results:
        lines.append("")
        if len(analysed.results) > 1:
            lines.append(_product(result))
        top_lines: tuple[hw.Sensitivity, ...] | None = None
        refused = None
        try:
            top_lines = result.sensitivity(top=top, include_reference=include_reference)
        except CalculationError as error:
            refused = str(error)
            lines.append(f"No sensitivity: {error}")
        else:
            lines.append("Sensitivity, largest elasticity first:")
            lines += [sensitivity_line(line) for line in top_lines]
        found.append(
            result_data(
                result,
                sensitivity=None if top_lines is None else sensitivity_data(top_lines),
                no_sensitivity=refused,
            )
        )
    if as_json:
        echo(document(analysed, FORMAT, found))
    else:
        typer.echo("\n".join(lines))


def _product(result: hw.Result) -> str:
    """`1 × tshirt-dyed:tshirt: 1.24265 kg CO2-eq`, above each product's list in a study of
    several."""
    unit = f" {result.unit}" if result.unit else ""
    return f"{result.amount:g} × {result.process_id}: {result.score:.6g}{unit}"
