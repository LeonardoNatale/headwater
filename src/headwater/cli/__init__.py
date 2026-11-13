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

Every command takes `--json` but `skill show`, and every `--help` ends with the example `EXAMPLES`
holds for it (D49). With `--json`, a usage error is printed as a `headwater-error/1` document too.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Annotated, Any, NamedTuple

import typer
from typer._click.exceptions import UsageError
from typer.core import TyperCommand, TyperGroup

from headwater.cli import _json

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
        "Import databases from Excel or Brightway, list what reaches a database, and convert "
        "the layout before 0.2.0.",
    ),
    "demo": Entry(
        "headwater.cli.demo:demo_command",
        "Copy the demo into FOLDER: a toy release, four databases and a study of one of them.",
    ),
    "skill": Entry(
        "headwater.cli.skill:skill_app",
        "Install the skill that teaches an AI agent, such as Claude Code, to use headwater.",
    ),
}


class Example(NamedTuple):
    line: str
    """A command line that runs, from `headwater` on."""
    where: str
    """Where it runs: in the demo folder, or on a toy file of the repository's tests."""


DEMO = "in the folder of `headwater demo`"
"""Where most examples run."""

EXAMPLES: dict[str, Example] = {
    "validate": Example("headwater validate studies/tshirt-footprint --json", DEMO),
    "calc": Example("headwater calc studies/tshirt-footprint --json", DEMO),
    "contributions": Example("headwater contributions studies/tshirt-footprint --json", DEMO),
    "sensitivity": Example("headwater sensitivity studies/tshirt-footprint --top 3 --json", DEMO),
    "where": Example("headwater where studies/tshirt-footprint --json", DEMO),
    "schema": Example("headwater schema studies --json", DEMO),
    "find": Example(
        "headwater find electricity --release toy-background@6864b7dbb65d --json", DEMO
    ),
    "diff": Example(
        "headwater diff HEAD studies/tshirt-footprint --json",
        f"{DEMO}, committed to git, after an edit",
    ),
    "upgrade": Example(
        "headwater upgrade upgrade/tshirt --to toy-2@bd46d2b98059 --mapping "
        "upgrade/upgrade-mapping.csv --check --json",
        "on the toy releases toy-1 and toy-2 and the database of the repository's "
        "tests/fixtures/upgrade, copied to upgrade/",
    ),
    "release import": Example(
        "headwater release import olca-toy --source toy --version 1.0 --licence CC0-1.0 --json",
        "on the toy openLCA package of the repository's tests/fixtures/formats/olca-jsonld/toy, "
        "copied to olca-toy",
    ),
    "release pull": Example("headwater release pull releases/toy-background@6864b7dbb65d", DEMO),
    "method import": Example(
        'headwater method import olca-toy --method "toy climate v1" --flow-list '
        "toy-1.0@37b242df3416 --json",
        "on the same toy package, after the example of `headwater release import`",
    ),
    "method import-set": Example(
        "headwater method import-set toy-nw.csv --method toy-impacts --id toy-nw --version 1 "
        '--source "toy numbers" --unit Pt --json',
        "on the toy method folder and set of the repository's tests/fixtures/toy/methods, "
        "copied to toy-impacts and toy-nw.csv",
    ),
    "database import-excel": Example(
        "headwater database import-excel tshirt.xlsx --release toy-background@6864b7dbb65d "
        "--out studies/tshirt-excel --json",
        f"{DEMO}, with tshirt.xlsx the sheet of the repository's tests/fixtures/excel/tshirt.csv",
    ),
    "database import-brightway": Example(
        "headwater database import-brightway toy tshirt --brightway-dir brightway --release "
        "toy-background@6864b7dbb65d --out studies/tshirt-bw --json",
        f"{DEMO}, with the toy Brightway project of the repository's tests/fixtures/brightway "
        "copied to brightway",
    ),
    "database uses": Example("headwater database uses studies/dyes --json", DEMO),
    "database convert": Example(
        "headwater database convert old-tshirt-dyed --json",
        "on the folder of the repository's tests/fixtures/old-layout/tshirt-dyed, copied to "
        "old-tshirt-dyed",
    ),
    "demo": Example("headwater demo headwater-demo --json", "in any folder"),
    "skill install": Example("headwater skill install --json", "in the folder of a project"),
    "skill show": Example("headwater skill show", "anywhere"),
}
"""One example per command, by its name: the last line of its `--help`. A test runs each."""


def epilog(name: str) -> str:
    """The end of the `--help` of the command `name`: its example."""
    example = EXAMPLES[name]
    return f"Example, {example.where}: {example.line}"


def load(name: str) -> Command:
    """The command `name` of `COMMANDS`, with its module imported."""
    module, _, attribute = COMMANDS[name].target.partition(":")
    target = getattr(importlib.import_module(module), attribute)
    if isinstance(target, typer.Typer):
        group = typer.main.get_group(target)
        for word, command in getattr(group, "commands", {}).items():
            command.epilog = epilog(f"{name} {word}")
        return group
    app = typer.Typer(add_completion=False)
    app.command(name, epilog=epilog(name))(target)
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

    def parse_args(self, ctx: Context, args: list[str]) -> list[str]:
        _json.asked.set(_json.Asked.of(args))
        return super().parse_args(ctx, args)

    def invoke(self, ctx: Context) -> Any:
        """Run the command. With `--json`, a usage error is also printed as a document on
        standard output; its message still goes to standard error, with exit code 2."""
        try:
            return super().invoke(ctx)
        except UsageError as error:
            if _json.asked.get().json:
                hint = getattr(error, "param_hint", None)
                item = hint if isinstance(hint, str) else _json.asked.get().command
                _json.echo(
                    _json.error_document(
                        error.exit_code, [_json.problem("usage", item, error.message)]
                    )
                )
            raise


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
