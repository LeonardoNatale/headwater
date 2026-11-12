"""`headwater where`: which processes emit a database's score, and in which locations."""

import typer

from headwater.cli._common import (
    MethodFolderOption,
    MethodOption,
    NamespaceOption,
    OneIndicatorOption,
    ReleaseOption,
    SetOption,
    database_header,
    indicator_line,
)
from headwater.cli.sensitivity import TopOption, calc_one
from headwater.cli.study import DatabaseArgument, ProductOption


def where_command(
    database: DatabaseArgument,
    release: ReleaseOption = None,
    method: MethodOption = None,
    namespace: NamespaceOption = None,
    indicator: OneIndicatorOption = None,
    nw_set: SetOption = None,
    top: TopOption = 10,
    method_folder: MethodFolderOption = None,
    product: ProductOption = None,
) -> None:
    """Split a score by the process that emits it, anywhere upstream, and by location.

    Each process's part is its own direct emissions times how much it runs for the product. The
    largest come first, and the others are summed in a rest line.
    """
    opened, background, result = calc_one(
        database, release, method, namespace, indicator, nw_set, method_folder, product, "where"
    )
    lines = [*database_header(opened, database, background), indicator_line(background, result), ""]
    lines.append(str(result.where(top=top)))
    typer.echo("\n".join(lines))
