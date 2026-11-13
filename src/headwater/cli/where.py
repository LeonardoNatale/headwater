"""`headwater where`: which processes emit the score of a database or a study, and in which
locations."""

from typing import Any

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
)
from headwater.cli._common import (
    JsonOption,
    MethodFolderOption,
    MethodOption,
    NamespaceOption,
    OneIndicatorOption,
    ReleaseOption,
    SetOption,
)
from headwater.cli._json import echo
from headwater.cli.sensitivity import TopOption

FORMAT = "headwater-where/1"
"""The `format` of `where --json`. Within version 1 keys may be added, none is renamed or
removed."""


def where_command(
    folder: FolderArgument,
    release: ReleaseOption = None,
    method: MethodOption = None,
    namespace: NamespaceOption = None,
    indicator: OneIndicatorOption = None,
    nw_set: SetOption = None,
    top: TopOption = 10,
    method_folder: MethodFolderOption = None,
    product: ProductOption = None,
    scenario: ScenarioOption = None,
    as_json: JsonOption = False,
) -> None:
    """Split a score by the process that emits it, anywhere upstream, and by location.

    Each process's part is its own direct emissions times how much it runs for the product. The
    largest come first, and the others are summed in a rest line. A study has one split per
    product, its amount of it.
    """
    analysed = analyse(
        folder,
        command="where",
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
        split = result.where(top=top)
        lines += ["", str(split)]
        found.append(result_data(result, where=where_data(split)))
    if as_json:
        echo(document(analysed, FORMAT, found))
    else:
        typer.echo("\n".join(lines))


def where_data(split: hw.Where) -> dict[str, Any]:
    """Where a score is emitted, as data: each process kept and each location, largest first,
    with its share of the score (null for a score of exactly 0), and what `--top` left out."""
    total = split.score

    def share(value: float) -> float | None:
        return value / total if total != 0 else None

    return {
        "processes": [
            {
                "process_id": e.process_id,
                "name": e.name,
                "location": e.location,
                "amount": e.amount,
                "unit": e.unit,
                "score": e.score,
                "share": share(e.score),
                "copy_of": e.copy_of,
                "database": e.database,
            }
            for e in split.processes
        ],
        "rest": split.rest,
        "rest_count": split.rest_count,
        "locations": [
            {
                "location": p.location,
                "score": p.score,
                "share": share(p.score),
                "processes": p.processes,
            }
            for p in split.locations
        ],
        "locations_rest": split.locations_rest,
        "locations_rest_count": split.locations_rest_count,
    }
