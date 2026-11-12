"""`headwater release`: import a source into a release, and pull a release into the cache."""

import time
from collections import Counter
from pathlib import Path
from typing import Annotated, Any, NoReturn

import pyarrow.compute as pc
import typer

from headwater.io.exclude import read_exclude
from headwater.io.formats import describe, inventory_from
from headwater.io.inventory import Inventory
from headwater.io.source import InventoryOptions, SourceKind
from headwater.link import read_supplier_mapping
from headwater.model import HeadwaterError, IssueStatus, Role
from headwater.release import pull

release_app = typer.Typer(help="Import, pull and cache releases.", no_args_is_help=True)

compute: Any = pc

_SHOWN = 20
"""Warnings listed one by one before the rest are counted."""

_FORMATS = describe(SourceKind.inventory)


@release_app.command("import")
def import_command(
    path: Annotated[
        Path, typer.Argument(help="The source: a folder or a file, in one of the formats below.")
    ],
    format: Annotated[
        str | None,
        typer.Option("--format", help=f"The source's format. Default: detected. {_FORMATS}."),
    ] = None,
    version: Annotated[
        str | None,
        typer.Option(
            help="The source's version, which goes into every id. ecospold2 needs it, for "
            "example 3.10: it must be the newest release the datasets name. Files that name no "
            "release after 3.0, as in 3.12, cannot check it: it is taken as given, with a warning."
        ),
    ] = None,
    source: Annotated[
        str | None,
        typer.Option(
            help="The source's name, which goes into every id, for a format whose files do not "
            "give it, for example bafu."
        ),
    ] = None,
    licence: Annotated[
        str | None,
        typer.Option(help="The source's licence, for a format whose files do not give it."),
    ] = None,
    name: Annotated[
        str | None,
        typer.Option(help="Release name. Default: <source>-<version>-<system model>."),
    ] = None,
    exclude: Annotated[
        Path | None,
        typer.Option(
            help="CSV of files to leave out, with columns file,reason,resolved_by,resolved_at. "
            "Lines that buy from them become data gaps."
        ),
    ] = None,
    workers: Annotated[
        int | None, typer.Option(min=1, help="Processes that parse files. Default: one per CPU.")
    ] = None,
    supplier_mapping: Annotated[
        Path | None,
        typer.Option(
            help="CSV of reviewed decisions about suppliers: which process a line buys from, or "
            "why it is a data gap, with who and when."
        ),
    ] = None,
) -> None:
    """Import a source into a release in HEADWATER_HOME."""
    start = time.perf_counter()
    try:
        options = InventoryOptions(
            version=version,
            source=source,
            licence=licence,
            exclude=read_exclude(exclude) if exclude is not None else (),
            workers=workers,
        )
        mapping = read_supplier_mapping(supplier_mapping) if supplier_mapping else None
        inventory = inventory_from(path, options, format=format, suppliers=mapping)
        _report(inventory, path, time.perf_counter() - start)
        release = inventory.freeze(name=name)
    except HeadwaterError as error:
        _fail(error)
    typer.echo(f"Wrote release {release.path.name} to {release.path.parent}")


@release_app.command("pull")
def pull_command(
    source: Annotated[
        str,
        typer.Argument(
            help="A release folder, or the URL of one (https://, gs://, s3://, file://), named "
            "<name>@<hash>."
        ),
    ],
) -> None:
    """Check a release folder or URL and copy it into the cache in HEADWATER_HOME."""
    try:
        release = pull(source)
    except HeadwaterError as error:
        _fail(error)
    typer.echo(f"Pulled release {release.path.name} into {release.path.parent}")


def _report(inventory: Inventory, path: Path, took: float) -> None:
    lines = inventory.exchange_table
    info = inventory.info
    values: Any = compute.value_counts(lines.column("role"))
    counts: list[dict[str, Any]] = values.to_pylist()
    roles = Counter({Role(c["values"]): c["counts"] for c in counts})
    what = " ".join(p for p in (info.source, info.version, info.system_model) if p)
    typer.echo(
        f"Read {inventory.process_table.num_rows:,} processes of {what} from {path} in "
        f"{took:.1f} s: {len(inventory.flows):,} flows, {lines.num_rows:,} lines."
    )
    typer.echo(
        "Lines by role: " + ", ".join(f"{r.value} {roles[r]:,}" for r in Role if roles[r]) + "."
    )
    gaps = [i for i in inventory.issues if i.status is IssueStatus.data_gap]
    if gaps:
        processes = {
            r["id"]: r
            for r in inventory.process_table.select(["id", "name", "location"]).to_pylist()
        }
        flows = {f.id: f for f in inventory.flows}
        typer.echo(f"{len(gaps)} data gap{'s' if len(gaps) > 1 else ''}:")
        for issue in gaps:
            process = processes[issue.process_id]
            typer.echo(
                f"- {process['name']}, {process['location']} ({issue.process_id}) line "
                f"{issue.line_id}, {flows[issue.flow_id].name}: {issue.reason}"
            )
    if inventory.warnings:
        count = len(inventory.warnings)
        typer.echo(f"{count} warning{'s' if count > 1 else ''}:")
        for warning in inventory.warnings[:_SHOWN]:
            typer.echo(f"- {warning}")
        if count > _SHOWN:
            typer.echo(f"- and {count - _SHOWN} more")


def _fail(error: HeadwaterError) -> NoReturn:
    typer.echo(str(error), err=True)
    raise typer.Exit(code=1)
