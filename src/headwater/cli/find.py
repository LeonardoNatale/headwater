"""`headwater find`: the id of a background process, from part of its name, product or place."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import TYPE_CHECKING, Annotated

import typer

from headwater.cli._common import NamespaceOption, fail, with_pull_hint
from headwater.model import HeadwaterError, InvalidDataError

if TYPE_CHECKING:
    from headwater.query import Match

DEFAULT_LIMIT = 20
FORMAT = "headwater-find/1"
"""The `format` of `find --json`. Within version 1 keys may be added, none is renamed or
removed."""


def match_line(match: Match) -> str:
    """One match: the id, then `name | product | location`, the unit and the rule that matched.
    Example: `toy:electricity-production  electricity production | electricity | GLO  kWh  (name
    starts with)`."""
    return (
        f"{match.process_id}  {match.name} | {match.reference_product} | {match.location}  "
        f"{match.unit}  ({match.why})"
    )


def find_command(
    text: Annotated[
        str,
        typer.Argument(help="Part of the name or the product, such as 'market for steel'."),
    ],
    release: Annotated[
        str,
        typer.Option(
            "--release",
            help="The background to search: a release (name@hash, with at least 12 characters "
            "of the hash, or its folder), or a folder of process files.",
        ),
    ],
    namespace: NamespaceOption = None,
    location: Annotated[
        str | None,
        typer.Option("--location", help="Only processes in this place, by its code: RER, GLO."),
    ] = None,
    product: Annotated[
        str | None,
        typer.Option(
            "--product", help="Only processes whose reference product has every word of this."
        ),
    ] = None,
    kind: Annotated[
        str | None,
        typer.Option("--kind", help="Only processes of this kind, such as market_activity."),
    ] = None,
    limit: Annotated[
        int, typer.Option("--limit", min=1, help="How many to list, the best first.")
    ] = DEFAULT_LIMIT,
    as_json: Annotated[
        bool, typer.Option("--json", help="Print the matches as one JSON object.")
    ] = False,
) -> None:
    """Find a background process by part of its name, product or place, and print its id.

    The best matches come first: the name is the text, starts with it, or has every word of it
    (in any order, each as part of a word), then the same three for the reference product. Case is
    ignored and nothing is fuzzy. Each line ends with the rule that matched. It loads no method
    and solves nothing.
    """
    from headwater.query.search import table_of_path

    try:
        table = table_of_path(release, namespace=namespace)
        # one more than asked for, to know whether there are more
        found = table.find(text, location=location, product=product, kind=kind, limit=limit + 1)
    except InvalidDataError as error:
        fail(with_pull_hint(error))
    except HeadwaterError as error:
        fail(error)
    except ValueError as error:
        fail(error, code=2)
    shown = found[:limit]
    if as_json:
        data = {"format": FORMAT, "matches": [asdict(m) for m in shown]}
        typer.echo(json.dumps(data, indent=2))
        return
    for match in shown:
        typer.echo(match_line(match))
    if not shown:
        typer.echo(f"No process matches {text!r}.", err=True)
    elif len(found) > limit:
        typer.echo("More processes match: raise --limit to see them.", err=True)
