"""`headwater schema`: write the JSON schemas of database.yaml, study.yaml and scenario files."""

from pathlib import Path
from typing import Annotated

import typer

from headwater.cli._common import fail


def schema_command(
    folder: Annotated[
        Path,
        typer.Argument(
            help="The folder that holds databases and studies side by side. Default: here."
        ),
    ] = Path("."),
) -> None:
    """Write JSON schemas for database.yaml, study.yaml and scenario files into FOLDER/.schemas.

    The products and processes of a study complete from the process ids of the databases in
    FOLDER. Point an editor at a schema with a first line such as `# yaml-language-server:
    $schema=../.schemas/study.schema.json`. A schema only suggests: `headwater validate` is the
    check.
    """
    from headwater.study.schema import process_ids_in, write_schemas

    if not folder.is_dir():
        fail(f"{folder} is not a folder", code=2)
    written = write_schemas(folder)
    ids = process_ids_in(folder)
    typer.echo(
        f"Wrote {', '.join(p.name for p in written)} into {folder / '.schemas'}, with "
        f"{len(ids)} process {'id' if len(ids) == 1 else 'ids'} of the databases in {folder}."
    )
