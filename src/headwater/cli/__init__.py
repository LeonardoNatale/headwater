"""The `headwater` command, a thin layer over the Python API.

Importing this module loads Typer and nothing else. A command's module, and the libraries it
reads and solves with, are imported when that command is chosen, so `headwater --help` loads no
command and none of those libraries (times: docs/appendix/benchmarks.md). `COMMANDS` is the list,
in the order `--help` shows it: each entry names where the command is and holds the line `--help`
prints for it, which a test compares with the command's own help.

A module under cli/ imports numpy, scipy, pyarrow, DuckDB and the file readers only inside the
function that uses them, or under `TYPE_CHECKING`, so that `<command> --help` loads none of them;
tests/cli/test_startup.py fails when one is imported at the top. The `release` and `method`
modules are the exception: their help lists the formats that can be read, so they import the
readers.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Annotated, Any, NamedTuple

import typer
from typer.core import TyperCommand, TyperGroup

if TYPE_CHECKING:
    from typer._click import Command, Context


class Entry(NamedTuple):
    target: str
    """`module:name` of a command function, or of a `typer.Typer` that holds a group."""
    help: str
    """The first paragraph of the command's help, on one line."""


COMMANDS = {
    "validate": Entry(
        "headwater.cli.study:validate_command",
        "Check a database or a study: files, units, roles, links, overrides, scenarios, cut-offs.",
    ),
    "calc": Entry(
        "headwater.cli.study:calc_command",
        "Calculate a database or a study: one score per indicator of the method for each "
        "product, with provenance.",
    ),
    "contributions": Entry(
        "headwater.cli.study:contributions_command",
        "Contribution analysis of a score: what it buys, level by level, and the lines that move "
        "it most.",
    ),
    "sensitivity": Entry(
        "headwater.cli.sensitivity:sensitivity_command",
        "Sensitivity analysis: the lines a person can change, largest elasticity first.",
    ),
    "where": Entry(
        "headwater.cli.where:where_command",
        "Split a score by the process that emits it, anywhere upstream, and by location.",
    ),
    "schema": Entry(
        "headwater.cli.schema:schema_command",
        "Write JSON schemas for database.yaml, study.yaml and scenario files into FOLDER/.schemas.",
    ),
    "find": Entry(
        "headwater.cli.find:find_command",
        "Find a background process by part of its name, product or place, and print its id.",
    ),
    "diff": Entry(
        "headwater.cli.diff:diff_command",
        "Compare a database or a study at two git refs: each score before and after, and why it "
        "changed.",
    ),
    "upgrade": Entry(
        "headwater.cli.upgrade:upgrade_command",
        "Move databases and studies from the release they pin to another, and show what moved.",
    ),
    "release": Entry("headwater.cli.release:release_app", "Import, pull and cache releases."),
    "method": Entry(
        "headwater.cli.method:method_app",
        "Import impact methods, and normalisation and weighting sets.",
    ),
    "database": Entry(
        "headwater.cli.excel:database_app",
        "Import databases from Excel or Brightway, and list what reaches a database.",
    ),
    "demo": Entry(
        "headwater.cli.demo:demo_command",
        "Copy the demo into FOLDER: a toy release, four databases and a study of one of them.",
    ),
}


def load(name: str) -> Command:
    """The command `name` of `COMMANDS`, with its module imported."""
    module, _, attribute = COMMANDS[name].target.partition(":")
    target = getattr(importlib.import_module(module), attribute)
    if isinstance(target, typer.Typer):
        return typer.main.get_group(target)
    app = typer.Typer(add_completion=False)
    app.command(name)(target)
    return typer.main.get_command(app)


class LazyGroup(TyperGroup):
    """The group of `headwater`. It holds a stub for each command, enough to list them with their
    help, and swaps in the real command, importing its module, when one is run."""

    def __init__(self, **attrs: Any) -> None:
        super().__init__(**attrs)
        for name, entry in COMMANDS.items():
            self.add_command(TyperCommand(name=name, help=entry.help))

    def resolve_command(
        self, ctx: Context, args: list[str]
    ) -> tuple[str | None, Command | None, list[str]]:
        name, command, rest = super().resolve_command(ctx, args)
        return name, load(command.name) if command and command.name else command, rest


app = typer.Typer(
    name="headwater",
    help="Life cycle assessment from frozen releases.",
    cls=LazyGroup,
    no_args_is_help=True,
    add_completion=False,
)


def _print_version(asked: bool) -> None:
    if asked:
        from importlib.metadata import version

        typer.echo(f"headwater {version('headwater')}")
        raise typer.Exit()


@app.callback()
def _group(
    version: Annotated[
        bool,
        typer.Option(
            "--version", help="Print the version and exit.", callback=_print_version, is_eager=True
        ),
    ] = False,
) -> None:
    pass


def main() -> None:
    app()


__all__ = ["app", "main"]
