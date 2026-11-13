"""`headwater database convert`: rewrite a folder of the layout before 0.2.0 as two tables."""

from pathlib import Path
from typing import Annotated

import typer

from headwater.cli._common import count, fail
from headwater.model import HeadwaterError


def convert_command(
    folder: Annotated[
        Path,
        typer.Argument(
            help="A database, a background folder, or a folder that holds several of them, one "
            "level down.",
        ),
    ],
) -> None:
    """Convert processes/, one YAML and one CSV per process, into processes.csv and exchanges.csv
    (D48), once.

    Each folder is written into a copy first and read back: every process must read the same,
    ids, details, lines, formulas and distributions, and so must the rest of the folder. Only
    then are the tables put in place and processes/ removed, for every folder given. The comment
    lines of a process YAML go into the comment column of its row.

    Refused, and nothing written: a folder already converted, a comment after a value, and a
    folder that does not read. Exit code 0 when converted, 2 when nothing was written.
    """
    from headwater.study._convert import convert

    try:
        done = convert(folder)
    except (HeadwaterError, OSError) as error:
        fail(error, code=2)
    for one in done:
        typer.echo(
            f"converted {one.path}: {one.processes} "
            f"{'process' if one.processes == 1 else 'processes'} and {count(one.lines, 'line')}, "
            f"from {count(one.files, 'file')} in processes/ to processes.csv and exchanges.csv"
        )
