"""`headwater sensitivity`: the lines of a database and its background that move its score
most."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

import headwater as hw
from headwater.cli._common import (
    MethodFolderOption,
    MethodOption,
    NamespaceOption,
    OneIndicatorOption,
    ReleaseOption,
    SetOption,
    database_header,
    fail,
    indicator_line,
    load_background,
    pick_indicators,
    sensitivity_line,
)
from headwater.cli.study import DatabaseArgument, ProductOption, one_product, open_database
from headwater.model import HeadwaterError

TopOption = Annotated[
    int, typer.Option("--top", min=1, help="How many to list, the largest first.")
]


def sensitivity_command(
    database: DatabaseArgument,
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
) -> None:
    """Sensitivity analysis: the lines a person can change, largest elasticity first.

    A line is an input, a waste, an emission or a resource, of the database or of a background
    process it reaches. Its elasticity is the % change of the score per 1% change of the line.
    """
    from headwater.calc import CalculationError

    opened, background, result = calc_one(
        database, release, method, namespace, indicator, nw_set, method_folder, product
    )
    lines = [*database_header(opened, database, background), indicator_line(background, result), ""]
    try:
        found = result.sensitivity(top=top, include_reference=include_reference)
    except CalculationError as error:
        lines.append(f"No sensitivity: {error}")
    else:
        lines.append("Sensitivity, largest elasticity first:")
        lines += [sensitivity_line(line) for line in found]
    typer.echo("\n".join(lines))


def calc_one(
    database: Path,
    release: str | None,
    method: list[str] | None,
    namespace: str | None,
    indicator: str | None,
    nw_set: list[Path] | None = None,
    method_folders: list[Path] | None = None,
    product: str | None = None,
    command: str = "sensitivity",
) -> tuple[hw.Database, hw.Background, hw.Result]:
    """Open the database, load its background and calculate its product `product`, or its one
    product, for one indicator, as `contributions` does. Leaves with exit code 1 on an error of
    the database or the background, and 2 when the product is not one of the database's."""
    try:
        opened = open_database(database, command)
        process_id = one_product(opened, product)
        background = load_background(
            opened.folder.database,
            release=release,
            method=method,
            namespace=namespace,
            sets=nw_set,
            method_folders=method_folders,
        )
        [chosen] = pick_indicators(background, indicator, one=True)
        return opened, background, opened.calc(background, process_id, indicator=chosen)
    except HeadwaterError as error:
        fail(error)
