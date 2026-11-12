"""`headwater database import-excel`: turn a Brightway-format Excel inventory into a database."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from headwater.cli._common import count, fail, open_release
from headwater.cli.brightway import import_brightway_command
from headwater.cli.uses import uses_command
from headwater.model import HeadwaterError

if TYPE_CHECKING:
    from headwater.io.excel import ExcelImport
    from headwater.release import Release

database_app = typer.Typer(
    help="Import databases from Excel or Brightway, and list what reaches a database.",
    no_args_is_help=True,
)

_SHOWN = 20
"""Validation problems listed one by one."""


@database_app.command("import-excel")
def import_excel_command(
    workbook: Annotated[Path, typer.Argument(help="A Brightway-format Excel inventory (.xlsx).")],
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
            "earlier import, which are replaced.",
        ),
    ],
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
        typer.Option("--name", help="The database's name. Default: the workbook's database."),
    ] = None,
) -> None:
    """Convert a Brightway Excel inventory into a database folder, and report what did not map.

    Each line is linked to a process of the release by exact name, reference product and
    location, or by the mapping file. A line that cannot be linked is written with no supplier,
    so the database does not validate until a person decides, and is listed in the report.
    Formulas become parameters. database.yaml says the name, the release and `methods`.

    Exit code 0 when every line mapped and the database validates and can be calculated, 1 when
    something needs a decision (the database is written), 2 when nothing was written.
    """
    from headwater.io.excel import import_excel

    try:
        linked = open_release(release)
        done = import_excel(
            workbook,
            linked,
            out,
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


def folders_not_held(release: Release, method: Sequence[str] | None) -> list[Path]:
    """The method folders of `method` whose id `release` does not hold: the study names them by
    their id, so the commands that read it need them as --method-folder."""
    from headwater.study import read_method

    held = {m.id for m in release.methods()}
    folders = [Path(m) for m in method or [] if Path(m).is_dir()]
    return [f for f in folders if read_method(f).id not in held]


def print_import(done: ExcelImport, method_folders: Sequence[Path] = ()) -> None:
    """The report of an import, where it wrote the study, and what validation found. Then, for
    `method_folders`, the folders of methods the release does not hold, the option the commands
    that read the study need."""
    from headwater.io.excel.report import format_report

    typer.echo(format_report(done.report))
    typer.echo("")
    again = " (replacing the files of an earlier import)" if done.replaced else ""
    typer.echo(f"Wrote database {done.report.name} to {done.folder}{again}")
    _validation(done)
    if method_folders:
        option = " ".join(f"--method-folder {folder}" for folder in method_folders)
        typer.echo(
            "The release does not hold the database's method, so validate, calc and diff need its "
            f"folder: headwater validate {done.folder} {option}"
        )


def _validation(done: ExcelImport) -> None:
    if done.validation is None:
        typer.echo(f"Not validated: {done.validation_skipped}")
        return
    report = done.validation
    other = done.unexplained()
    above = len(report.problems) - len(other)
    typer.echo(
        f"Validation: {count(len(report.errors), 'error')}, "
        f"{count(len(report.warnings), 'warning')}"
        + (f". {above} of them {'is' if above == 1 else 'are'} listed above." if above else ".")
    )
    other = sorted(other, key=lambda p: p.severity.value != "error")  # errors first, as validate
    for problem in other[:_SHOWN]:
        typer.echo(f"  {problem.severity.value}: {problem}")
    if len(other) > _SHOWN:
        typer.echo(f"  and {len(other) - _SHOWN} more")
    uncovered = [p for p in done.blocking() if p.code == "no-factor"]
    if uncovered:
        typer.echo(
            f"{count(len(uncovered), 'flow')} of the database have no factor in the method. "
            "validate and calc refuse a database that emits one: add a factor, remove the line, "
            "or acknowledge the flow in no_factor.csv."
        )


database_app.command("import-brightway")(import_brightway_command)
database_app.command("uses")(uses_command)
