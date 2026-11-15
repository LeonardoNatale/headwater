"""`headwater release`: import a source into a release, pull a release into the cache, and list
the cache."""

import time
from collections import Counter
from pathlib import Path
from typing import Annotated, Any

import pyarrow.compute as pc
import typer

from headwater.cli._common import JsonOption, fail
from headwater.cli._json import echo
from headwater.io.exclude import read_exclude
from headwater.io.formats import describe, inventory_from
from headwater.io.inventory import Inventory
from headwater.io.source import InventoryOptions, SourceKind
from headwater.link import read_supplier_mapping
from headwater.model import HeadwaterError, IssueStatus, Role
from headwater.release import Release, pull, releases_dir

release_app = typer.Typer(help="Import, pull and cache releases.", no_args_is_help=True)

compute: Any = pc

_SHOWN = 20
"""Warnings listed one by one before the rest are counted."""

_FORMATS = describe(SourceKind.inventory)

IMPORT_FORMAT = "headwater-release-import/1"
"""The `format` of `release import --json`. Within version 1 keys may be added, none is renamed
or removed."""

PULL_FORMAT = "headwater-release-pull/1"
"""The `format` of `release pull --json`. Within version 1 keys may be added, none is renamed or
removed."""

LIST_FORMAT = "headwater-release-list/1"
"""The `format` of `release list --json`. Within version 1 keys may be added, none is renamed or
removed."""


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
    as_json: JsonOption = False,
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
        found = _summary(inventory, path, time.perf_counter() - start)
        if not as_json:
            _report(found)
        release = inventory.freeze(name=name)
    except HeadwaterError as error:
        fail(error)
    if as_json:
        written = {"release": f"{release.name}@{release.hash}", "path": str(release.path)}
        echo({"format": IMPORT_FORMAT, **written, **found})
        return
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
    as_json: JsonOption = False,
) -> None:
    """Check a release folder or URL and copy it into the cache in HEADWATER_HOME."""
    try:
        release = pull(source)
    except HeadwaterError as error:
        fail(error)
    if as_json:
        pulled = {"release": f"{release.name}@{release.hash}", "path": str(release.path)}
        echo({"format": PULL_FORMAT, **pulled})
        return
    typer.echo(f"Pulled release {release.path.name} into {release.path.parent}")


@release_app.command("list")
def list_command(as_json: JsonOption = False) -> None:
    """List the releases in the cache: `releases/` in HEADWATER_HOME, ~/.headwater by default.

    Each release is given by the name@hash a database pins, with its kind and its size on disk.
    A folder that does not open as a release is listed with the reason.
    """
    folder = releases_dir()
    entries = (
        sorted(p for p in folder.iterdir() if not p.name.startswith(".")) if folder.is_dir() else []
    )
    found = [_listed(path) for path in entries if path.is_dir()]
    if as_json:
        echo({"format": LIST_FORMAT, "folder": str(folder), "releases": found})
        return
    if not found:
        typer.echo(f"No releases in {folder}. Pull one with: headwater release pull <folder>")
        return
    typer.echo(f"Releases in {folder}:")
    for entry in found:
        if "error" in entry:
            typer.echo(f"  {entry['folder']}  cannot be read: {entry['error']}")
        else:
            typer.echo(f"  {entry['release']}  {entry['kind']}  {_size(entry['bytes'])}")


def _listed(path: Path) -> dict[str, Any]:
    """One folder of the cache: the release it holds, its kind and its size in bytes."""
    size = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    try:
        release = Release.open(path)
    except HeadwaterError as error:
        return {"folder": path.name, "path": str(path), "bytes": size, "error": str(error)}
    return {
        "folder": path.name,
        "release": f"{release.name}@{release.hash[:12]}",
        "hash": release.hash,
        "kind": release.manifest.kind.value,
        "path": str(path),
        "bytes": size,
    }


def _size(size: int) -> str:
    """A size for people: 1.2 MB, 340 kB."""
    for unit, scale in (("GB", 1e9), ("MB", 1e6), ("kB", 1e3)):
        if size >= scale:
            return f"{size / scale:.1f} {unit}"
    return f"{size} B"


def _summary(inventory: Inventory, path: Path, took: float) -> dict[str, Any]:
    """What was read, as data: the counts, the lines by role, each data gap and each warning."""
    lines = inventory.exchange_table
    info = inventory.info
    values: Any = compute.value_counts(lines.column("role"))
    counts: list[dict[str, Any]] = values.to_pylist()
    roles = Counter({Role(c["values"]): c["counts"] for c in counts})
    gaps = [i for i in inventory.issues if i.status is IssueStatus.data_gap]
    processes = {
        r["id"]: r for r in inventory.process_table.select(["id", "name", "location"]).to_pylist()
    }
    flows = {f.id: f for f in inventory.flows}
    return {
        "source_path": str(path),
        "source": info.source,
        "version": info.version,
        "system_model": info.system_model,
        "seconds": took,
        "processes": inventory.process_table.num_rows,
        "flows": len(inventory.flows),
        "lines": lines.num_rows,
        "roles": {r.value: roles[r] for r in Role if roles[r]},
        "data_gaps": [
            {
                "process_id": issue.process_id,
                "name": processes[issue.process_id]["name"],
                "location": processes[issue.process_id]["location"],
                "line_id": issue.line_id,
                "flow_id": issue.flow_id,
                "flow_name": flows[issue.flow_id].name,
                "reason": issue.reason,
            }
            for issue in gaps
        ],
        "warnings": [str(w) for w in inventory.warnings],
    }


def _report(found: dict[str, Any]) -> None:
    what = " ".join(p for p in (found["source"], found["version"], found["system_model"]) if p)
    typer.echo(
        f"Read {found['processes']:,} processes of {what} from {found['source_path']} in "
        f"{found['seconds']:.1f} s: {found['flows']:,} flows, {found['lines']:,} lines."
    )
    typer.echo("Lines by role: " + ", ".join(f"{r} {n:,}" for r, n in found["roles"].items()) + ".")
    gaps = found["data_gaps"]
    if gaps:
        typer.echo(f"{len(gaps)} data gap{'s' if len(gaps) > 1 else ''}:")
        for gap in gaps:
            typer.echo(
                f"- {gap['name']}, {gap['location']} ({gap['process_id']}) line "
                f"{gap['line_id']}, {gap['flow_name']}: {gap['reason']}"
            )
    warnings = found["warnings"]
    if warnings:
        count = len(warnings)
        typer.echo(f"{count} warning{'s' if count > 1 else ''}:")
        for warning in warnings[:_SHOWN]:
            typer.echo(f"- {warning}")
        if count > _SHOWN:
            typer.echo(f"- and {count - _SHOWN} more")
