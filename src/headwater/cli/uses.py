"""`headwater database uses`: which databases and studies reach a database, directly or not."""

import json
import posixpath
from pathlib import Path
from typing import Annotated, Any

import typer

from headwater.cli._common import fail
from headwater.diff import uses_next_to
from headwater.model import HeadwaterError, InvalidDataError
from headwater.study import users_of

FORMAT = "headwater-uses/2"
"""The `format` of `database uses --json`. Within version 2 keys may be added, none is renamed or
removed."""


def uses_command(
    databases: Annotated[
        list[Path],
        typer.Argument(
            metavar="DATABASE...",
            help="Database folders. The folder need not exist: a database removed can still "
            "have users.",
        ),
    ],
    ref: Annotated[
        str | None,
        typer.Option(
            "--ref",
            help="Read the database.yaml and study.yaml files at this git ref instead of on disk.",
        ),
    ] = None,
    as_json: Annotated[
        bool, typer.Option("--json", help="Print JSON, for CI, instead of text.")
    ] = False,
) -> None:
    """List the databases that use each database and the studies whose products reach it,
    directly or not, and the way each goes.

    A database uses the folders next to it that its `uses:` names, and a study's products are
    in the databases next to it named by the prefix of their ids, so the users of `studies/dyes`
    are among the folders of `studies/`. Only their database.yaml and study.yaml files are read.
    Example: with `tshirt` using `dyes`, and the study `tshirt-footprint` scoring `tshirt:tshirt`,
    `dyes` is reached by `tshirt`, which uses it, and by `tshirt-footprint`, through `tshirt`.
    """
    found: list[dict[str, Any]] = []
    try:
        for database in databases:
            found.append(_users(database, ref))
    except HeadwaterError as error:
        fail(error, code=2)
    if as_json:
        data = {"format": FORMAT, "ref": ref, "databases": found}
        typer.echo(json.dumps(data, indent=2, ensure_ascii=False))
        return
    for database in found:
        users = database["users"]
        if not users:
            typer.echo(f"{database['path']}: no database or study reaches it.")
        else:
            typer.echo(f"{database['path']} is reached by:")
        for user in users:
            typer.echo(f"  {user['path']} ({user['kind']}, {_way(user['kind'], user['through'])})")
        for error in database["errors"]:
            typer.echo(f"  could not read {error['path']}/{error['file']}: {error['message']}")


def _users(database: Path, ref: str | None) -> dict[str, Any]:
    """The users of one database, as data: the folder and kind of each, with the databases it
    goes through, and the files next to it that did not read. Databases come first."""
    found = uses_next_to(database, ref)
    name = database.resolve().name

    def next_to(other: str) -> str:
        """The folder `other` beside `database`, written from where it was given: `../tshirt`
        for `.`."""
        return posixpath.normpath(posixpath.join(database.as_posix(), "..", other))

    reach = {**found.databases, **found.studies}
    users = [
        {
            "path": next_to(user),
            "kind": "study" if user in found.studies else "database",
            "through": list(chain),
        }
        for user, chain in users_of(name, reach)
    ]
    errors = [
        {"path": next_to(other), "file": file, "message": _message(error)}
        for file, read in (("database.yaml", found.databases), ("study.yaml", found.studies))
        for other, error in read.items()
        if isinstance(error, InvalidDataError)
    ]
    return {
        "path": database.as_posix(),
        "users": sorted(users, key=lambda u: (u["kind"] == "study", u["path"])),
        "errors": errors,
    }


def _message(error: InvalidDataError) -> str:
    return "; ".join(str(p) for p in error.problems)


def _way(kind: str, through: list[str]) -> str:
    """`uses dyes, which uses chemicals`, and for a study `products in tshirt, which uses dyes`."""
    first, *rest = through
    words = ("products in " if kind == "study" else "uses ") + first
    return ", which uses ".join([words, *rest])
