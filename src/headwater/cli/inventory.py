"""`headwater inventory`: the total of each elementary flow over a product's whole life cycle,
with no method, for a database, a study or a background process.

Example, the T-shirt of the demo's study: 1.175 kg of carbon dioxide and 0.00227 kg of methane,
both to air. `--flow` and `--compartment` narrow the list, and each unit then ends with the sum
of the flows that match: `--flow water --compartment "natural resource"` sums the water taken.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

import typer

import headwater as hw
from headwater.cli._analysis import ProductOption, ScenarioOption, analyse, header
from headwater.cli._common import (
    JsonOption,
    MethodFolderOption,
    MethodOption,
    NamespaceOption,
    ReleaseOption,
    background_text,
    count,
    fail,
    open_release,
    with_pull_hint,
)
from headwater.cli._json import dumps
from headwater.model import HeadwaterError, InvalidDataError, Problem
from headwater.query._internals import internals

if TYPE_CHECKING:
    from collections.abc import Sequence

FORMAT = "headwater-inventory/1"
"""The `format` of `inventory --json`. Within version 1 keys may be added, none is renamed or
removed."""

CSV_COLUMNS = (
    "product",
    "product_amount",
    "flow_id",
    "flow_name",
    "compartment",
    "unit",
    "amount",
)
"""The columns of `inventory --csv`, one row per product and flow."""

UNCHANGED = "The method does not change the totals: a background only loads with one."

TargetArgument = Annotated[
    str,
    typer.Argument(
        metavar="FOLDER-OR-PROCESS",
        help="A database folder, which holds database.yaml, a study folder, which holds "
        "study.yaml, or the id of a background process, with --release.",
    ),
]

FlowOption = Annotated[
    str | None,
    typer.Option(
        "--flow", help="Only the flows whose name holds this text, case ignored, such as water."
    ),
]

CompartmentOption = Annotated[
    str | None,
    typer.Option(
        "--compartment",
        help="Only the flows of this compartment and of those under it, case ignored: air also "
        "keeps air/unspecified.",
    ),
]

TopOption = Annotated[
    int | None,
    typer.Option(
        "--top",
        min=1,
        help="How many flows to list per unit, the largest first. The others are summed in a "
        "rest line. Default: all of them.",
    ),
]

CsvOption = Annotated[
    bool,
    typer.Option("--csv", help="Print the flows as CSV, one row per product and flow."),
]


@dataclass(frozen=True)
class Filters:
    """What `--flow`, `--compartment` and `--top` ask for."""

    flow: str | None
    compartment: str | None
    top: int | None

    @property
    def narrowed(self) -> bool:
        """True when `--flow` or `--compartment` narrows the list: only then are there sums."""
        return self.flow is not None or self.compartment is not None

    def keeps(self, total: hw.FlowTotal) -> bool:
        if self.flow is not None and self.flow.casefold() not in total.name.casefold():
            return False
        if self.compartment is None:
            return True
        if total.compartment is None:
            return False
        wanted, held = self.compartment.casefold().rstrip("/"), total.compartment.casefold()
        return held == wanted or held.startswith(f"{wanted}/")

    def label(self, n: int) -> str:
        """`sum of 12 flows matching 'water' in natural resource`."""
        matching = f" matching {self.flow!r}" if self.flow is not None else ""
        within = f" in {self.compartment}" if self.compartment is not None else ""
        return f"sum of {count(n, 'flow')}{matching}{within}"


@dataclass(frozen=True)
class Inventory:
    """One product's inventory: the product, its amount, its flows and its provenance."""

    product: str
    amount: float
    totals: tuple[hw.FlowTotal, ...]
    provenance: dict[str, Any]


def inventory_command(
    target: TargetArgument,
    release: ReleaseOption = None,
    method: MethodOption = None,
    namespace: NamespaceOption = None,
    method_folder: MethodFolderOption = None,
    product: ProductOption = None,
    scenario: ScenarioOption = None,
    flow: FlowOption = None,
    compartment: CompartmentOption = None,
    top: TopOption = None,
    as_csv: CsvOption = False,
    as_json: JsonOption = False,
) -> None:
    """The inventory: the total of each elementary flow over a product's whole life cycle.

    Each total is what every process that runs for the product emits or takes, times how much
    it runs: how much CO2, water or energy, with no method. Every flow is listed, with or
    without a factor, by unit, the largest first. A study has one inventory per product, its
    amount of it. With --flow or --compartment, each unit ends with the sum of the flows that
    match. The background still loads with a method, which does not change the totals.
    """
    if as_json and as_csv:
        raise typer.BadParameter("give --csv or --json, not both.", param_hint="--csv")
    filters = Filters(flow, compartment, top)
    if Path(target).is_dir():
        analysed = analyse(
            Path(target),
            command="inventory",
            release=release,
            method=method,
            namespace=namespace,
            indicator=None,
            nw_set=None,
            method_folder=method_folder,
            product=product,
            scenario=scenario,
            any_indicator=True,
        )
        background = analysed.background
        lines = header(analysed, indicator=False)
        found = [
            Inventory(r.process_id, r.amount, r.inventory(), _provenance(r))
            for r in analysed.results
        ]
        described: dict[str, Any] = {
            "path": target,
            "kind": "database" if isinstance(analysed.opened, hw.Database) else "study",
            "name": analysed.opened.name,
            "scenario": scenario,
        }
    else:
        if release is None:
            fail(
                f"{target!r} is not a folder. For a background process, give its id and the "
                "background with --release, such as: headwater inventory toy:fabric-production "
                "--release toy-background@6864b7dbb65d",
                code=2,
            )
        if product is not None or scenario is not None:
            raise typer.BadParameter(
                "--product and --scenario are for a database or a study folder, not a "
                "background process.",
                param_hint="--product" if product is not None else "--scenario",
            )
        background = _process_background(release, method, method_folder, namespace)
        try:
            totals = background.inventory(target)
        except ValueError as error:
            fail(error, code=2)
        lines = [
            f"Background: {background_text(background)}, method {background.method.id} "
            f"{background.method.version}"
        ]
        found = [Inventory(target, 1.0, totals, {"pin": background_text(background)})]
        described = {"path": None, "kind": "process", "name": target, "scenario": None}
    if as_json:
        typer.echo(dumps(_document(background, described, filters, found)))
    elif as_csv:
        typer.echo(_csv(found, filters))
    else:
        for inventory in found:
            lines += ["", *_text(inventory, filters)]
        typer.echo("\n".join(lines))


def _process_background(
    release: str,
    method: list[str] | None,
    method_folder: list[Path] | None,
    namespace: str | None,
) -> hw.Background:
    """The background for a process id: `release` with the methods given, else with the one
    method the release holds, or the first of several, said on standard error. Leaves with exit
    code 1 when there is no method to load."""
    chosen: list[str] = [*(method or []), *(str(f) for f in method_folder or [])]
    try:
        if not chosen:
            folder = Path(release)
            if folder.is_dir() and not (folder / "release.json").is_file():
                raise InvalidDataError(
                    [
                        Problem(
                            "method-needed",
                            f"background {release}",
                            "is a folder of process files, so --method must be a method "
                            f"folder. {UNCHANGED}",
                        )
                    ]
                )
            opened = open_release(release)
            held = [m.id for m in opened.method_entries]
            if not held:
                raise InvalidDataError(
                    [
                        Problem(
                            "method-needed",
                            f"release {opened.name}@{opened.hash[:12]}",
                            "holds no method, so give one with --method or --method-folder, "
                            f"a method folder. {UNCHANGED}",
                        )
                    ]
                )
            if len(held) > 1:
                typer.echo(
                    f"Release {opened.name} holds {len(held)} methods: loading the first, "
                    f"{held[0]}. It does not change the totals.",
                    err=True,
                )
            chosen = held[:1]
        methods: list[Path | str] = [
            Path(m) if (Path(m) / "method.yaml").is_file() else m for m in chosen
        ]
        return hw.load(release, method=methods, namespace=namespace)
    except InvalidDataError as error:
        fail(with_pull_hint(error))
    except HeadwaterError as error:
        fail(error)
    except ValueError as error:  # an argument the API refuses, such as an unknown method id
        fail(InvalidDataError([Problem("background", f"background {release}", str(error))]))


def _provenance(result: hw.Result) -> dict[str, Any]:
    """Where an inventory comes from: the hash of the inputs of the result it is read from, the
    release the folder pins, and the largest residual of the solves."""
    p = result.provenance
    return {"input_hash": p.input_hash, "pin": p.pin, "residual": p.residual}


@dataclass(frozen=True)
class Group:
    """The flows of one unit that the filters keep, those `--top` lists, and the rest."""

    unit: str
    kept: tuple[hw.FlowTotal, ...]
    listed: tuple[hw.FlowTotal, ...]

    @property
    def rest(self) -> tuple[hw.FlowTotal, ...]:
        return self.kept[len(self.listed) :]


def _groups(totals: Sequence[hw.FlowTotal], filters: Filters) -> list[Group]:
    """The flows `filters` keeps, by unit, in the order of `totals`: by unit, then largest
    first."""
    by_unit: dict[str, list[hw.FlowTotal]] = {}
    for total in totals:
        if filters.keeps(total):
            by_unit.setdefault(total.unit, []).append(total)
    return [
        Group(unit, tuple(kept), tuple(kept if filters.top is None else kept[: filters.top]))
        for unit, kept in by_unit.items()
    ]


def _text(inventory: Inventory, filters: Filters) -> list[str]:
    """One product's inventory as text. Example, the T-shirt: `1 × tshirt:tshirt: 2 elementary
    flows`, then `kg` and a line per flow, such as `1.125  Carbon dioxide, fossil (air)  toy:co2`,
    the largest first. A rest line ends a unit that `--top` cuts, and a sum line one that the
    filters narrow."""
    groups = _groups(inventory.totals, filters)
    kept = sum(len(g.kept) for g in groups)
    shown = f", {kept} kept by the filters" if filters.narrowed else ""
    lines = [
        f"{inventory.amount:g} × {inventory.product}: "
        f"{count(len(inventory.totals), 'elementary flow')}{shown}"
    ]
    for group in groups:
        lines.append(f"  {group.unit}")
        for total in group.listed:
            where = f" ({total.compartment})" if total.compartment else ""
            lines.append(f"    {total.amount:.6g}  {total.name}{where}  {total.flow_id}")
        if group.rest:
            rest = sum(t.amount for t in group.rest)
            lines.append(f"    {rest:.6g}  rest, {count(len(group.rest), 'flow')}")
        if filters.narrowed:
            total = sum(t.amount for t in group.kept)
            lines.append(f"    {total:.6g}  {filters.label(len(group.kept))}")
    return lines


def _document(
    background: hw.Background,
    described: dict[str, Any],
    filters: Filters,
    found: Sequence[Inventory],
) -> dict[str, Any]:
    """The JSON of `inventory --json`. `sums` is there only when `--flow` or `--compartment`
    narrows the list."""
    inner = internals(background)
    release = str(inner.releases[0]) if inner.releases else None
    results: list[dict[str, Any]] = []
    for inventory in found:
        groups = _groups(inventory.totals, filters)
        entry: dict[str, Any] = {
            "product": {"id": inventory.product, "amount": inventory.amount},
            "flow_count": len(inventory.totals),
            "flows": [
                {
                    "flow_id": t.flow_id,
                    "name": t.name,
                    "compartment": t.compartment,
                    "unit": t.unit,
                    "amount": t.amount,
                }
                for g in groups
                for t in g.listed
            ],
            "rest": [
                {
                    "unit": g.unit,
                    "amount": sum(t.amount for t in g.rest),
                    "count": len(g.rest),
                }
                for g in groups
                if g.rest
            ],
        }
        if filters.narrowed:
            entry["sums"] = [
                {
                    "unit": g.unit,
                    "amount": sum(t.amount for t in g.kept),
                    "count": len(g.kept),
                    "label": filters.label(len(g.kept)),
                }
                for g in groups
            ]
        entry["provenance"] = inventory.provenance
        results.append(entry)
    return {
        "format": FORMAT,
        **described,
        "background": {"release": release, "folder": None if release else str(inner.folder.path)},
        "methods": list(background.method.ids),
        "filters": {"flow": filters.flow, "compartment": filters.compartment, "top": filters.top},
        "engine_version": hw.__version__,
        "results": results,
    }


def _csv(found: Sequence[Inventory], filters: Filters) -> str:
    """The flows `--top` lists, one row per product and flow, each amount written so that it
    reads back as the same float64."""
    import csv
    import io

    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    for inventory in found:
        for group in _groups(inventory.totals, filters):
            for t in group.listed:
                writer.writerow(
                    [
                        inventory.product,
                        repr(inventory.amount),
                        t.flow_id,
                        t.name,
                        t.compartment or "",
                        t.unit,
                        repr(t.amount),
                    ]
                )
    return out.getvalue().rstrip("\n")
