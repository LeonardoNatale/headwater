"""`headwater database import-brightway`: turn a database of a Brightway project into a database
folder."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from headwater.cli._common import fail, open_release
from headwater.model import HeadwaterError


def import_brightway_command(
    project: Annotated[str, typer.Argument(help="The Brightway project, by name.")],
    database: Annotated[str, typer.Argument(help="The database of the project to import.")],
    release: Annotated[
        str,
        typer.Option(
            "--release",
            help="The release to link to: name@hash of one in HEADWATER_HOME, or its folder.",
        ),
    ],
    out: Annotated[
        Path,
        typer.Option(
            "--out",
            help="The database folder to write: new, empty, or holding the unedited files of an "
            "earlier import, which are replaced. Other databases of the project go "
            "next to it, in folders named after them.",
        ),
    ],
    brightway_dir: Annotated[
        Path | None,
        typer.Option(
            "--brightway-dir",
            help="The folder of Brightway's projects. Default: BRIGHTWAY2_DIR, or where bw2data "
            "keeps them.",
        ),
    ] = None,
    mapping: Annotated[
        Path | None,
        typer.Option(
            "--mapping",
            help="A reviewed mapping file (CSV) for the lines that do not match by themselves.",
        ),
    ] = None,
    method: Annotated[
        list[str] | None,
        typer.Option(
            "--method",
            help="A method folder, or the id of a method the release holds: the database's method, "
            "and what it is validated against. Give it once per method to use several together. "
            "Default: the release's only method.",
        ),
    ] = None,
    name: Annotated[
        str | None,
        typer.Option("--name", help="The database's name. Default: the project database's."),
    ] = None,
) -> None:
    """Convert a database of a Brightway project into a database folder, and report what did
    not map. The project's files are read directly and never written: bw2data is not needed.

    Each line is linked to the node its exchange comes from: an activity of the database, the
    process of the release with the node's name, reference product and location, or a flow of
    nature by name and categories. A line that buys from another database of the project links
    to the database of that name next to --out, which the database then uses. Formulas and
    parameters are copied, and so are lognormal, normal, uniform and triangular distributions.

    Exit code 0 when every line mapped and the database validates and can be calculated, 1 when
    something needs a decision (the database is written), 2 when nothing was written: for example
    an exchange that comes from a node the project does not have.
    """
    from headwater.cli.excel import folders_not_held, print_import
    from headwater.io.brightway import import_brightway

    try:
        linked = open_release(release)
        done = import_brightway(
            project,
            database,
            linked,
            out,
            brightway_dir=brightway_dir,
            mapping=mapping,
            name=name,
            method=[Path(m) if Path(m).is_dir() else m for m in method] if method else None,
        )
        needed = folders_not_held(linked, method)
    except (HeadwaterError, OSError) as error:
        fail(error, code=2)
    print_import(done, needed)
    if not done.clean:
        raise typer.Exit(code=1)
