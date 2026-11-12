"""`headwater method import`: import an impact method into a method folder. `headwater method
import-set`: write a normalisation and weighting set beside its method."""

from pathlib import Path
from typing import Annotated

import typer

from headwater.cli._common import fail, open_release
from headwater.io.formats import describe, method_from
from headwater.io.method import MappedMethod, write_method
from headwater.io.source import MethodOptions, SourceKind
from headwater.link import read_flow_mapping
from headwater.model import HeadwaterError

method_app = typer.Typer(
    help="Import impact methods, and normalisation and weighting sets.", no_args_is_help=True
)

_SHOWN = 20
"""Flows listed one by one before the rest are counted."""

_FORMATS = describe(SourceKind.method)


@method_app.command("import")
def import_command(
    path: Annotated[Path, typer.Argument(help="The method's source, in one of the formats below.")],
    flow_list: Annotated[
        str,
        typer.Option(
            help="The release whose elementary flows the factors map to: a release folder, or "
            "name@hash of a release in HEADWATER_HOME."
        ),
    ],
    method: Annotated[
        str | None,
        typer.Option(help="The method's name in the source, for example 'EF v3.1'."),
    ] = None,
    format: Annotated[
        str | None,
        typer.Option("--format", help=f"The source's format. Default: detected. {_FORMATS}."),
    ] = None,
    out: Annotated[
        Path | None,
        typer.Option(help="The method folder to write. Default: methods/<method id>."),
    ] = None,
    method_id: Annotated[
        str | None,
        typer.Option("--id", help="Method id. Default: from the name, 'EF v3.1' gives EF-3.1."),
    ] = None,
    version: Annotated[
        str | None,
        typer.Option(help="Method version. Default: from the name, 'EF v3.1' gives 3.1."),
    ] = None,
    variant: Annotated[
        str | None, typer.Option(help="Method variant. Default: the method's name.")
    ] = None,
    category: Annotated[
        list[str] | None,
        typer.Option(
            help="A category of the method to import, by name, for example 'climate change: "
            "direct land use change'. Give it once per category. Default: every category."
        ),
    ] = None,
    flow_mapping: Annotated[
        Path | None,
        typer.Option(
            help="CSV of reviewed decisions: the flow of the release each of the source's flow "
            "keys is, with who and when. Needed when the release has another flow list."
        ),
    ] = None,
) -> None:
    """Map every factor of a method to an elementary flow of a release, and write a method
    folder. Factors whose flow is not in the flow list are listed, not guessed."""
    try:
        options = MethodOptions(
            method=method,
            categories=tuple(category) if category else None,
            id=method_id,
            version=version,
            variant=variant,
        )
        mapping = read_flow_mapping(flow_mapping) if flow_mapping is not None else None
        mapped = method_from(
            path, open_release(flow_list, "flow list"), options, format=format, flows=mapping
        )
        folder = write_method(mapped, out or Path("methods") / mapped.method.id)
    except (HeadwaterError, OSError) as error:
        fail(error)
    _report(mapped, path)
    typer.echo(f"Wrote method {mapped.method.id} to {folder}")


@method_app.command("import-set")
def import_set_command(
    path: Annotated[
        Path,
        typer.Argument(
            help="A CSV with the columns indicator, normalisation and weight, one row per "
            "indicator. A score is divided by its normalisation and multiplied by its weight. "
            "Leave a column blank in every row to leave that step out."
        ),
    ],
    method: Annotated[
        Path, typer.Option(help="The method folder the set is for, such as methods/EF-3.1.")
    ],
    set_id: Annotated[str, typer.Option("--id", help="The set's id, such as EF-3.1-NW.")],
    version: Annotated[str, typer.Option(help="The set's version.")],
    source: Annotated[
        str, typer.Option(help="Where the numbers come from, for a person to check them.")
    ],
    unit: Annotated[
        str | None,
        typer.Option(help="The unit of weighted scores and the single score, such as Pt."),
    ] = None,
    out: Annotated[
        Path | None,
        typer.Option(help="The set folder to write. Default: beside the method, named by --id."),
    ] = None,
) -> None:
    """Write a normalisation and weighting set beside its method, pinned to it by its hash.

    Each row must name an indicator of the method. The method's indicators with no row are
    listed: they count in none of the set's scores. The method folder is not changed.
    """
    from headwater.io.nwset import make_set, write_set
    from headwater.study import read_method, read_set_rows

    try:
        found = read_method(method)
        nw_set, indicators = make_set(
            read_set_rows(path), found, id=set_id, version=version, unit=unit, source=source
        )
        folder = write_set(nw_set, out or method.parent / set_id)
    except (HeadwaterError, OSError) as error:
        fail(error)
    steps = [
        s for s, given in (("normalised", nw_set.normalises), ("weighted", nw_set.weighs)) if given
    ]
    unit_text = f", in {nw_set.unit}" if nw_set.unit else ""
    typer.echo(
        f"Read {_count(len(nw_set.rows), 'row')} from {path}: {' and '.join(steps)}{unit_text}."
    )
    typer.echo(
        f"Set {nw_set.id} {nw_set.version} adds {_count(len(indicators), 'indicator')} to method "
        f"{found.id} {found.version}, pinned by its hash {nw_set.method.hash[:12]}."
    )
    left_out = indicators[0].left_out if indicators else ()
    if left_out:
        typer.echo(f"Indicators of {found.id} with no row, left out: {', '.join(left_out)}")
    typer.echo(f"Wrote {folder}")


def _report(imported: MappedMethod, path: Path) -> None:
    source = imported.source
    rows = sum(r.factors for r in imported.indicators)
    typer.echo(f"Read {rows:,} factors of {source.name} from {path} ({source.flow_list}).")
    typer.echo(
        f"Mapped to flow list {imported.method.flow_list} of release {imported.release.name}@"
        f"{imported.release.hash[:12]}, which has {imported.flows:,} elementary flows."
    )
    header = (
        "indicator",
        "factors",
        "mapped",
        "copied",
        "not in flow list",
        "flows with no factor",
    )
    table = [header] + [
        (
            r.indicator.id,
            f"{r.factors:,}",
            f"{r.mapped:,}",
            f"{r.copied:,}",
            f"{len(r.unmapped):,}",
            f"{len(r.flows_without_factor):,}",
        )
        for r in imported.indicators
    ]
    widths = [max(len(row[i]) for row in table) for i in range(len(header))]
    for row in table:
        cells = [row[0].ljust(widths[0])] + [
            c.rjust(w) for c, w in zip(row[1:], widths[1:], strict=True)
        ]
        typer.echo("  ".join(cells).rstrip())
    unmapped = [(r.indicator.id, u) for r in imported.indicators for u in r.unmapped]
    if unmapped:
        typer.echo(f"{_count(len(unmapped), 'factor')} whose flow is not in the flow list:")
        for indicator, u in unmapped:
            where = f" [{u.where}]" if u.where else ""
            typer.echo(f"- {indicator}: {u.key}{where}: {u.reason}")
    flows = imported.not_characterised
    if flows:
        typer.echo(f"{_count(len(flows), 'flow')} with no factor for any indicator:")
        for flow in flows[:_SHOWN]:
            typer.echo(f"- {flow.name} ({flow.compartment}) {flow.id}")
        if len(flows) > _SHOWN:
            typer.echo(f"- and {len(flows) - _SHOWN:,} more")
    if source.left_out:
        typer.echo(
            f"{_count(len(source.left_out), 'category', 'categories')} of {source.name} left "
            f"out: {', '.join(source.left_out)}"
        )
    for warning in imported.warnings:
        typer.echo(f"- {warning}")


def _count(n: int, noun: str, plural: str | None = None) -> str:
    return f"{n:,} {noun if n == 1 else plural or noun + 's'}"
