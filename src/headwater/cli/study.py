"""`headwater validate`, `calc` and `contributions`: work on a database or a study without writing
Python."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Annotated, Any

import typer

import headwater as hw
from headwater.cli._common import (
    IndicatorsOption,
    MethodFolderOption,
    MethodOption,
    NamespaceOption,
    OneIndicatorOption,
    ReleaseOption,
    SetOption,
    background_text,
    check_folder,
    database_header,
    fail,
    indicator_line,
    load_background,
    pick_indicators,
    pinned_record,
    print_validation,
    sensitivity_line,
    validation_data,
)
from headwater.model import (
    HeadwaterError,
    InvalidDataError,
    Problem,
    Report,
    did_you_mean,
)
from headwater.query._internals import internals

DatabaseArgument = Annotated[
    Path, typer.Argument(help="A database folder, which holds database.yaml.")
]

FolderArgument = Annotated[
    Path,
    typer.Argument(
        help="A database folder, which holds database.yaml, or a study folder, which holds "
        "study.yaml."
    ),
]

ProductOption = Annotated[
    str | None,
    typer.Option(
        "--product",
        help="The product of the database to analyse, by the id of the process that makes it. "
        "Needed when the database has more than one.",
    ),
]

_SHOWN = 20
"""Items of a list that `calc` prints one by one, as other commands do: the rest is a count."""


def validate_command(
    folder: FolderArgument,
    release: ReleaseOption = None,
    method: MethodOption = None,
    namespace: NamespaceOption = None,
    as_json: Annotated[
        bool, typer.Option("--json", help="Print JSON, for CI, instead of text.")
    ] = False,
    method_folder: MethodFolderOption = None,
) -> None:
    """Check a database or a study: files, units, roles, links, overrides, scenarios, cut-offs.

    Prints every error and warning. Exit code 1 when there is an error, so CI can stop on it.

    A database or a study that validates is one `calc` will not refuse.
    """
    problems: list[Problem] = []
    name = str(folder)
    kind = "Database"
    background: hw.Background | None = None
    opened: hw.Database | hw.Study | None = None
    try:
        opened = hw.open(folder)
    except InvalidDataError as error:
        problems.extend(error.problems)
    else:
        record = pinned_record(opened)
        name = record.name
        if isinstance(opened, hw.Study):
            kind = "Study"
        try:
            background = load_background(
                record,
                release=release,
                method=method,
                namespace=namespace,
                method_folders=method_folder,
                kind=kind.lower(),
            )
        except HeadwaterError as error:
            fail(error)
        problems.extend(check_folder(opened, background).problems)
    report = Report(tuple(problems))
    if as_json:
        data = validation_data(
            str(folder), background, report, None if opened is None else kind.lower()
        )
        typer.echo(json.dumps(data, indent=2, allow_nan=False, ensure_ascii=False))
    else:
        print_validation(name, background, report, kind)
    if report.errors:
        raise typer.Exit(code=1)


def calc_command(
    folder: FolderArgument,
    release: ReleaseOption = None,
    method: MethodOption = None,
    namespace: NamespaceOption = None,
    indicator: IndicatorsOption = None,
    scenario: Annotated[
        str | None,
        typer.Option("--scenario", help="Calculate in a scenario of the study, by name."),
    ] = None,
    nw_set: SetOption = None,
    iterations: Annotated[
        int | None,
        typer.Option(
            "--iterations",
            min=2,
            help="Also draw every uncertain amount of the background and of the database this "
            "many times, and print the range of each score.",
        ),
    ] = None,
    seed: Annotated[
        int, typer.Option("--seed", min=0, help="The seed of the draws of --iterations.")
    ] = 0,
    workers: Annotated[
        int,
        typer.Option("--workers", min=1, help="Run the iterations in this many processes."),
    ] = 1,
    method_folder: MethodFolderOption = None,
    product: Annotated[
        list[str] | None,
        typer.Option(
            "--product",
            help="A product of the database to score, by the id of the process that makes it. "
            "Give it once per product. Every product is scored without it.",
        ),
    ] = None,
    all_products: Annotated[
        bool,
        typer.Option(
            "--all-products",
            help="Score every product of the database, as without --product.",
        ),
    ] = False,
    as_json: Annotated[
        bool, typer.Option("--json", help="Print JSON, for scripts, instead of text.")
    ] = False,
    as_csv: Annotated[
        bool,
        typer.Option(
            "--csv",
            help="Print the scores as CSV instead of text: one row per product and indicator.",
        ),
    ] = False,
) -> None:
    """Calculate a database or a study: one score per indicator of the method for each product,
    with provenance.

    The background is the release the database pins, unless --release says another. With
    --iterations, the range of each score from a Monte Carlo run follows, in the scenario when
    --scenario names one.

    Every process of the database is a product, scored per unit of what it makes, or the
    products --product names by the ids of their processes. Each product is named by its process
    and what it makes. The first product is calculated in full, with its provenance; the others
    are scored on the same background, as `headwater diff` scores them, with each residual
    checked. --json prints the same as data, and --csv the scores, one row per product and
    indicator.

    A study scores each of its products, its amount of it, in the scenario --scenario names.
    --json and --csv print it as for a database, each product with its amount.
    """
    if as_json and as_csv:
        raise typer.BadParameter("give --csv or --json, not both.", param_hint="--csv")
    if (as_json or as_csv) and iterations is not None:
        raise typer.BadParameter(
            "--json and --csv print scores, not the ranges of a Monte Carlo run: leave out "
            "--iterations, or print text.",
            param_hint="--json",
        )
    if iterations is None and (seed != 0 or workers != 1):
        raise typer.BadParameter(
            "--seed and --workers are for a Monte Carlo run: add --iterations N.",
            param_hint="--iterations",
        )
    if product and all_products:
        raise typer.BadParameter(
            "give --product or --all-products, not both: --all-products scores every product.",
            param_hint="--product",
        )
    try:
        opened = hw.open(folder)
        if isinstance(opened, hw.Study):
            if product or all_products:
                raise typer.BadParameter(
                    "--product and --all-products are for a database: a study scores each of its "
                    "products, its amount of it.",
                    param_hint="--product",
                )
            text = _calc_study(
                opened,
                folder,
                load_background(
                    pinned_record(opened),
                    release=release,
                    method=method,
                    namespace=namespace,
                    sets=nw_set,
                    method_folders=method_folder,
                    kind="study",
                ),
                indicator,
                scenario,
                iterations,
                seed,
                workers,
                "json" if as_json else "csv" if as_csv else "text",
            )
            typer.echo(text)
            return
        _known_scenario(opened, scenario)
        chosen = _chosen_products(opened, product)
        if len(chosen) > 1 and (iterations is not None or scenario is not None):
            raise typer.BadParameter(
                f"--iterations and --scenario work on one product, and {len(chosen)} are asked "
                "for: give one --product.",
                param_hint="--product",
            )
        background = load_background(
            opened.folder.database,
            release=release,
            method=method,
            namespace=namespace,
            sets=nw_set,
            method_folders=method_folder,
        )
        indicators = pick_indicators(background, indicator, one=False)
        scored = score_products(opened, background, indicators, chosen)
        if as_json:
            data = calc_data(opened, str(folder), background, scored)
            text = json.dumps(data, indent=2, allow_nan=False, ensure_ascii=False)
        elif as_csv:
            text = calc_csv(calc_data(opened, str(folder), background, scored, hashes=False))
        else:
            text = calc_text(opened, folder, background, scored)
        if iterations is not None:
            mc = hw.monte_carlo(
                background,
                [(opened, chosen[0])],
                iterations=iterations,
                seed=seed,
                indicators=indicators,
                workers=workers,
            )
            text += "\n\n" + monte_carlo_text(mc, background)
    except HeadwaterError as error:
        fail(error)
    typer.echo(text)


def monte_carlo_text(mc: hw.MonteCarlo, background: hw.Background) -> str:
    """What `headwater calc --iterations` adds: the range of each score, and how it was drawn."""
    p = mc.provenance
    names = {i.id: i.name for i in background.method.indicators}
    ranges = [mc.range(mc.items[0], indicator) for indicator in mc.indicators]
    labels = [f"{names[r.indicator]}:" for r in ranges]
    width = max(len(label) for label in labels)
    lines = [f"Monte Carlo: {p.iterations} iterations, seed {p.seed}"]
    for label, r in zip(labels, ranges, strict=True):
        spread = ", ".join(
            [
                f"mean {r.mean:.6g}",
                f"median {r.median:.6g}",
                f"sd {r.sd:.3g}",
                *(f"{q:g}% {v:.6g}" for q, v in r.percentiles.items()),
            ]
        )
        lines.append(f"  {label:<{width}} {spread} {r.unit}")
    drawn = [
        ("lines drawn", p.uncertain_lines),
        ("database lines drawn", p.database_lines),
    ]
    lines += [
        f"  {name}: " + ", ".join(f"{n} {d}" for d, n in counts.items())
        for name, counts in drawn
        if counts
    ]
    if p.parameters:
        lines.append(f"  parameters drawn: {p.parameters}")
    lines += [
        f"  draws of the opposite sign: {p.opposite_signs}",
        f"  largest condition: {p.largest_condition:.3g}",
        f"  residual: {p.residual:.2e}",
        f"  generator: {p.generator}",
        f"  markets: {p.markets}",
    ]
    return "\n".join(lines)


@dataclass(frozen=True)
class Scored:
    """What `calc` found for the products of a database: the first in full, the others'
    scores."""

    products: list[str]
    """The ids of the processes scored, the first one first."""
    results: list[hw.Result]
    """The first product's result for each indicator, in order."""
    scores: dict[str, dict[str, float]]
    """Every product's scores, by process and indicator: the first's are those of `results`."""
    residuals: dict[str, float]
    """The largest residual of the solves behind each product's scores, by process."""


def score_products(
    database: hw.Database,
    background: hw.Background,
    indicators: Sequence[str],
    products: Sequence[str],
) -> Scored:
    """Calculate the first process of `products` for each of `indicators`, then score the
    others as `headwater diff` scores them, with `Database.scores`, keeping the residual of
    each. Each is the id of a process of `database`, a product, scored per unit of what it makes.

    Raises what `Database.calc` raises, `HeadwaterError`s that name what is wrong.
    """
    from headwater.query.database import scores_with_residual

    first, *others = products
    results = [database.calc(background, first, indicator=i) for i in indicators]
    scores = {first: {r.indicator: r.score for r in results}}
    residuals = {first: max(r.provenance.residual for r in results)}
    for process_id in others:
        scores[process_id], residuals[process_id] = scores_with_residual(
            database, background, process_id, indicators
        )
    return Scored([first, *others], results, scores, residuals)


def calc_text(
    database: hw.Database, path: Path | str, background: hw.Background, scored: Scored
) -> str:
    """What `headwater calc` prints: the scores of each product, then the provenance. With one
    product, the provenance is its
    result's. With several it is the database's, with the largest residual of them all, and each
    product's own input hash and residual are left to --json."""
    results = scored.results
    names = {i.id: i.name for i in background.indicators}
    units = {i.id: i.unit or "" for i in background.indicators}
    indicators = [r.indicator for r in results]
    lines = database_header(database, path, background)
    rows = [
        [(names[i], scored.scores[process_id][i], units[i]) for i in indicators]
        for process_id in scored.products
    ]
    for process_id, table in zip(scored.products, _score_tables(rows), strict=True):
        lines += ["", _product(database, background, process_id), *table]
    p = results[0].provenance
    one = len(scored.products) == 1
    residual = max(scored.residuals.values())
    lines += [
        "",
        "Provenance",
        *([f"  input hash: {p.input_hash}"] if one else []),
        f"  method hash: {p.method_hash}",
        f"  engine: headwater {p.engine_version}",
        f"  method: {p.method} {p.method_version}",
        f"  background: {_pinned(background)}",
        f"  pin: {p.pin}",
        *([f"  uses: {', '.join(p.used)}"] if p.used else []),
        *([f"  database: {_commit(p.database.commit, p.database.changed)}"] if p.database else []),
        *_sets(results),
        *([f"  scenario: {p.scenario}"] if p.scenario is not None else []),
        *(
            [
                "  cut-offs: "
                + ", ".join(f"{c.value} {share:.4g}" for c, share in p.cut_off_shares.items())
            ]
            if p.cut_off_shares and one
            else []
        ),
        f"  solver: {p.solver.solver}, ordering {p.solver.ordering}, "
        f"tolerance {p.solver.tolerance:g}, condition limit {p.solver.condition_limit:g}",
        f"  condition: background {p.condition:.3g}, database {p.database_condition:.3g}",
        f"  residual: {residual:.2e}"
        + ("" if one else f", the largest of the {len(scored.products)} products"),
        *(
            [
                "  scores that cancel: "
                + (
                    ", ".join(r.indicator for r in results if r.provenance.cancellation.cancels)
                    or "none"
                )
            ]
            if one
            else []
        ),
        *_not_characterised(results[0].not_characterised),
        *([] if one else ["  each product's input hash and residual: in --json"]),
    ]
    return "\n".join(lines)


CSV_COLUMNS = (
    "process_id", "process", "product", "per", "indicator_id", "indicator", "score_unit",
    "score",
)  # fmt: skip
"""The columns of `calc --csv`, one row per product and indicator."""


def calc_csv(data: dict[str, Any]) -> str:
    """The scores of `calc_data` as CSV, one row per product and indicator, each score written so
    that it reads back as the same float64. `process_id` is the product's id, `per` what a score
    is for, `1 kg`, and `score_unit` the unit of the score, empty for a normalised one."""
    import csv
    import io

    per = {p["id"]: p["per"] for p in data["products"]}
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    for r in data["results"]:
        product, indicator = r["product"], r["indicator"]
        writer.writerow(
            [
                product["id"],
                product["process"],
                product["name"],
                per[product["id"]],
                indicator["id"],
                indicator["name"],
                indicator["unit"] or "",
                repr(r["score"]),
            ]
        )
    return out.getvalue().rstrip("\n")


CALC_FORMAT = "headwater-calc/3"
"""The `format` of `calc --json`. Within version 3 keys may be added, none is renamed or
removed."""


def calc_data(
    database: hw.Database,
    path: str,
    background: hw.Background,
    scored: Scored,
    *,
    hashes: bool = True,
) -> dict[str, Any]:
    """`calc` as plain data: `json.dumps` of it is the output of `calc --json`. `path` is the
    database as given. `provenance` is the database's, with its commit; each product, named by
    the id of its process, has its own input hash and residual, and the scores that cancel for
    the first one, which is calculated in full (null for the others). `hashes` False leaves out
    the input hashes it would have to work out (null), for `--csv`, which does not print them."""
    from headwater.query.database import _input_hash  # pyright: ignore[reportPrivateUsage]

    results = scored.results
    inner = internals(background)
    info = {i.id: i for i in background.indicators}
    catalog = inner.study_catalog(database.folder)
    processes = {p.id: p for p in database.folder.processes}
    first = results[0].provenance

    def product(process_id: str) -> dict[str, Any]:
        process = processes[process_id]
        flow = catalog.flow(process.reference_exchange.flow_id)
        return {
            "id": process_id,
            "name": flow.name if flow else process.reference_exchange.flow_id,
            "process": process.name,
        }

    def about(process_id: str) -> dict[str, Any]:
        """What a product's scores are for and come from. The input hash is that of
        `Database.calc` for an indicator of the method."""
        flow_id = processes[process_id].reference_exchange.flow_id
        unit = flow.unit if (flow := catalog.flow(flow_id)) else None
        return {
            **product(process_id),
            "flow": flow_id,
            "unit": unit,
            "per": f"1 {unit}" if unit else None,
            "input_hash": (
                first.input_hash
                if process_id == scored.products[0] and first.nw_set is None
                else _input_hash(database.folder, background, process_id, None)
                if hashes
                else None
            ),
            "residual": scored.residuals[process_id],
            "cancels": (
                [r.indicator for r in results if r.provenance.cancellation.cancels]
                if process_id == scored.products[0]
                else None
            ),
        }

    return {
        **_calc_header(background, first, "database", database.name, path),
        "products": [about(process_id) for process_id in scored.products],
        "results": [
            {
                "product": product(process_id),
                "indicator": {"id": i, "name": info[i].name, "unit": info[i].unit},
                "score": scores[i],
            }
            for process_id, scores in scored.scores.items()
            for i in (r.indicator for r in results)
        ],
        "provenance": {
            "method_hash": first.method_hash,
            "pin": first.pin,
            "uses": list(first.used),
            "condition": first.condition,
            "database_condition": first.database_condition,
            "residual": max(scored.residuals.values()),
            "not_characterised": _not_characterised_data([results[0]]),
            "database": None if first.database is None else asdict(first.database),
        },
    }


def study_calc_data(
    study: hw.Study,
    path: str,
    background: hw.Background,
    results: Sequence[Sequence[hw.Result]],
    scenario: str | None,
    *,
    hashes: bool = True,
) -> dict[str, Any]:
    """`calc` of a study as plain data, in the shape of `calc_data` with keys added. Each
    product has its `amount`, `per` is that amount of its unit, `0.5 kg`, and each score is for
    that amount. Every product is calculated in full, so each has the scores that cancel.
    `provenance` has as `uses` the databases the study reaches, the largest conditions and
    residual of all its products, the flows no indicator covers for any of them, and the study
    with its commit. `results` holds, for each indicator, one result per product, as
    `Study.calc` gives them. `hashes` False leaves the input hashes out (null), for `--csv`."""
    from headwater.query.study import study_input_hash
    from headwater.study._write import number

    inner = internals(background)
    info = {i.id: i for i in background.indicators}
    folder = study.folder
    chosen = next((s for s in folder.scenarios if s.name == scenario), None)
    first = results[0][0].provenance
    indicators = [rs[0].indicator for rs in results]
    by_product = list(zip(*results, strict=True))

    def made(process_id: str) -> tuple[str, str, str, str | None]:
        """What the process makes, the process's name, the id of the flow and its unit."""
        database = folder.database_of(process_id)
        if database is None:
            process = background.processes([process_id])[process_id]
            line = process.reference_exchange
            return line.flow_name or process.name, process.name, line.flow_id, line.unit
        process = next(p for p in database.processes if p.id == process_id)
        flow_id = process.reference_exchange.flow_id
        flow = inner.study_catalog(database).flow(flow_id)
        return (flow.name if flow else flow_id, process.name, flow_id, flow.unit if flow else None)

    products: list[dict[str, Any]] = []
    for product, scored in zip(folder.products, by_product, strict=True):
        name, process, flow_id, unit = made(product.process)
        products.append(
            {
                "id": product.process,
                "name": name,
                "process": process,
                "flow": flow_id,
                "unit": unit,
                "amount": product.amount,
                "per": f"{number(product.amount)} {unit}" if unit else None,
                "input_hash": (
                    study_input_hash(folder, background, product.process, chosen)
                    if hashes
                    else None
                ),
                "residual": max(r.provenance.residual for r in scored),
                "cancels": [r.indicator for r in scored if r.provenance.cancellation.cancels],
            }
        )
    every = [r for rs in results for r in rs]
    ref = first.study
    return {
        **_calc_header(background, first, "study", study.name, path),
        "products": products,
        "results": [
            {
                "product": {k: p[k] for k in ("id", "name", "process")},
                "indicator": {"id": i, "name": info[i].name, "unit": info[i].unit},
                "score": r.score,
            }
            for p, scored in zip(products, by_product, strict=True)
            for i, r in zip(indicators, scored, strict=True)
        ],
        "provenance": {
            "method_hash": first.method_hash,
            "pin": first.pin,
            "uses": sorted(folder.reached),
            "condition": max(r.provenance.condition for r in every),
            "database_condition": max(r.provenance.database_condition for r in every),
            "residual": max(p["residual"] for p in products),
            "not_characterised": _not_characterised_data(results[0]),
            "study": None if ref is None else asdict(ref),
        },
    }


def _calc_header(
    background: hw.Background, first: hw.Provenance, kind: str, name: str | None, path: str
) -> dict[str, Any]:
    """The keys `calc --json` starts with, for a database or a study."""

    inner = internals(background)
    release = str(inner.releases[0]) if inner.releases else None
    return {
        "format": CALC_FORMAT,
        "path": path,
        "kind": kind,
        "name": name,
        "background": {"release": release, "folder": None if release else str(inner.folder.path)},
        "method": (
            {"id": first.method, "version": first.method_version, "hash": first.method_hash}
            if len(background.method.ids) == 1
            else None
        ),
        "methods": list(background.method.ids),
        "engine_version": first.engine_version,
        "solver": asdict(first.solver),
        "scenario": first.scenario,
    }


def _not_characterised_data(results: Sequence[hw.Result]) -> list[dict[str, Any]]:
    """The flows no indicator covers for any of `results`, each once, in the order they come,
    with every process that uses it, and the reason of the row of a database's own."""
    found: dict[str, tuple[hw.NotCharacterised, list[str]]] = {}
    for result in results:
        for flow in result.not_characterised:
            if flow.flow_id not in found:
                found[flow.flow_id] = (flow, list(flow.processes))
                continue
            kept, processes = found[flow.flow_id]
            processes += [p for p in flow.processes if p not in processes]
            if flow.in_database and not kept.in_database:  # the row that gives a reason
                found[flow.flow_id] = (flow, processes)
    return [
        {
            "id": flow.flow_id,
            "name": flow.name,
            "processes": processes,
            "reason": flow.reason if flow.in_database else None,
        }
        for flow, processes in found.values()
    ]


def _commit(commit: str | None, changed: bool) -> str:
    """`commit 1a2b3c...`, with `, with changes` when the files differ from it, or `not in git`."""
    if commit is None:
        return "not in git"
    return f"commit {commit}" + (", with changes" if changed else "")


def contributions_command(
    database: DatabaseArgument,
    release: ReleaseOption = None,
    method: MethodOption = None,
    namespace: NamespaceOption = None,
    indicator: OneIndicatorOption = None,
    depth: Annotated[int, typer.Option("--depth", min=0, help="Levels of purchases to show.")] = 3,
    min_share: Annotated[
        float,
        typer.Option(
            "--min-share",
            min=0.0,
            max=1.0,
            help="Leave out purchases below this share of the score. They are summed in a rest "
            "line.",
        ),
    ] = 0.03,
    sensitivity: Annotated[
        int,
        typer.Option(
            "--sensitivity",
            min=0,
            help="How many lines of the sensitivity analysis to list, the largest first. 0: none.",
        ),
    ] = 5,
    nw_set: SetOption = None,
    method_folder: MethodFolderOption = None,
    product: ProductOption = None,
) -> None:
    """Contribution analysis of a score: what it buys, level by level, and the lines that move
    it most.

    The tree adds up at every node: score = direct + purchases + rest.

    The lines that move it most are the top of `headwater sensitivity`: the elasticity of a line
    is the % change of the score per 1% change of the line.
    """
    from headwater.calc import CalculationError

    try:
        opened = open_database(database, "contributions")
        process_id = one_product(opened, product)
        background = load_background(
            opened.folder.database,
            release=release,
            method=method,
            namespace=namespace,
            sets=nw_set,
            method_folders=method_folder,
        )
        [chosen] = pick_indicators(background, indicator, one=True)
        result = opened.calc(background, process_id, indicator=chosen)
    except HeadwaterError as error:
        fail(error)
    lines = database_header(opened, database, background)
    lines += [indicator_line(background, result), ""]
    lines.append(str(result.contributions(min_share=min_share, max_depth=depth)))
    if sensitivity > 0:
        lines.append("")
        try:
            found = result.sensitivity(top=sensitivity)
        except CalculationError as error:
            lines.append(f"No sensitivity: {error}")
        else:
            lines.append("Sensitivity, largest elasticity first:")
            lines += [sensitivity_line(line) for line in found]
    typer.echo("\n".join(lines))


def _not_characterised(flows: Sequence[hw.NotCharacterised]) -> list[str]:
    """The flows no indicator covers: how many, then the first `_SHOWN` of them, each with its
    first `_SHOWN` processes. A flow can be used by very many processes."""
    lines = [f"  not characterised flows: {len(flows)}"]
    # The database's own come first, so they are never in the "more" a long list hides.
    for flow in sorted(flows, key=lambda f: not f.in_database)[:_SHOWN]:
        used = ", ".join(flow.processes[:_SHOWN])
        if len(flow.processes) > _SHOWN:
            used += f" and {len(flow.processes) - _SHOWN} more"
        own = f"; the database's own: {flow.reason}" if flow.in_database else ""
        lines.append(f"    {flow.name} ({flow.flow_id}), used by {used}{own}")
    if len(flows) > _SHOWN:
        lines.append(f"    and {len(flows) - _SHOWN} more")
    return lines


def _score_tables(blocks: Sequence[Sequence[tuple[str, float, str]]]) -> list[list[str]]:
    """For each block of (indicator name, score, unit), one line per indicator, with names and
    scores aligned across every block. A normalised score has no unit."""
    labels = [[f"{name}:" for name, _, _ in rows] for rows in blocks]
    scores = [[f"{score:.6g}" for _, score, _ in rows] for rows in blocks]
    label_width = max(len(label) for block in labels for label in block)
    score_width = max(len(score) for block in scores for score in block)
    return [
        [
            f"  {label:<{label_width}} {score:>{score_width}} {unit}".rstrip()
            for label, score, (_, _, unit) in zip(block_labels, block_scores, rows, strict=True)
        ]
        for block_labels, block_scores, rows in zip(labels, scores, blocks, strict=True)
    ]


def _sets(results: Sequence[hw.Result]) -> list[str]:
    """One line per normalisation and weighting set the scores come from: its id, version and
    hash, its method, and the method's indicators it leaves out."""
    lines: list[str] = []
    seen: set[str] = set()
    for r in results:
        s = r.provenance.nw_set
        if s is None or s.id in seen:
            continue
        seen.add(s.id)
        left_out = f"; left out of the set: {', '.join(s.left_out)}" if s.left_out else ""
        lines.append(
            f"  set: {s.id} {s.version}, hash {s.hash}, for method {s.method.id} "
            f"{s.method.version}{left_out}"
        )
    return lines


def _known_scenario(database: hw.Database, scenario: str | None) -> None:
    """A usage error for a scenario asked of a database: scenarios are a study's."""
    if scenario is not None:
        raise typer.BadParameter(
            f"database {database.name} holds no scenarios: a scenario is a study's (D46). Give the "
            "folder of a study whose products are in this database.",
            param_hint="--scenario",
        )


def one_product(database: hw.Database, product: str | None) -> str:
    """The id of the process `product` names, or of the database's one process when `product`
    is None. Leaves with exit code 2 for an id that is no process of the database, saying which
    process makes it when it is a flow, and for no product named when the database has several."""
    if product is not None:
        return _chosen_products(database, [product])[0]
    processes = database.folder.processes
    if len(processes) != 1:
        ids = [p.id for p in processes]
        fail(
            f"Database {database.name} has {len(ids)} products: give one with --product, by the id "
            f"of its process. Its products: {', '.join(ids)}",
            code=2,
        )
    return processes[0].id


def _chosen_products(database: hw.Database, product: Sequence[str] | None) -> list[str]:
    """The processes to score, in the order of the database: those `product` names, else all.
    Leaves with exit code 2 for an id that is no process of the database, saying which process
    makes it when it is the id of a flow."""
    from headwater.diff import unknown_product

    processes = database.folder.processes
    for wanted in product or []:
        message = unknown_product(wanted, processes)
        if message is not None:
            fail(message, code=2)
    named = set(product or ())
    return [p.id for p in processes if not product or p.id in named]


def _product(database: hw.Database, background: hw.Background, process_id: str) -> str:
    """`bag sewing | bag (tshirt:bag-sewing), per kg`: the name of the process, what it makes
    and its id, and the unit a score is for. The name of a process that has its product's name
    is not repeated: `T-shirt (tshirt:tshirt), per unit`."""
    from headwater.diff import product_label

    process = next(p for p in database.folder.processes if p.id == process_id)
    flow_id = process.reference_exchange.flow_id
    flow = internals(background).study_catalog(database.folder).flow(flow_id)
    label = product_label(process.name, flow.name if flow else flow_id, process_id)
    if flow is None:
        return label
    return f"{label}, per {flow.unit}"


def _pinned(background: hw.Background) -> str:
    inner = internals(background)
    if inner.releases:
        return str(inner.releases[0])
    return f"folder {inner.folder.path}"


def open_database(path: Path, command: str) -> hw.Database:
    """The database in the folder `path`. Leaves with exit code 2 for a study: `command` takes
    a database."""
    opened = hw.open(path)
    if isinstance(opened, hw.Study):
        fail(
            f"{path} holds a study (study.yaml). `headwater {command}` takes a database folder, "
            "one of those its products are in; `headwater calc` and `headwater validate` take "
            "a study.",
            code=2,
        )
    return opened


def _calc_study(
    study: hw.Study,
    path: Path,
    background: hw.Background,
    indicator: list[str] | None,
    scenario: str | None,
    iterations: int | None,
    seed: int,
    workers: int,
    output: str = "text",
) -> str:
    """What `headwater calc` prints for a study: each product, its amount, and its scores, then
    the provenance of the first. `output` `json` or `csv` prints `study_calc_data` instead."""
    held = [s.name for s in study.folder.scenarios]
    if scenario is not None and scenario not in held:
        have = f"Its scenarios: {', '.join(held)}" if held else "It has no scenarios."
        raise typer.BadParameter(
            f"study {study.name} has no scenario {scenario!r}.{did_you_mean(scenario, held)} "
            f"{have}",
            param_hint="--scenario",
        )
    indicators = pick_indicators(background, indicator, one=False)
    names = {i.id: i.name for i in background.indicators}
    units = {i.id: i.unit or "" for i in background.indicators}
    by_indicator = [study.calc(background, indicator=i, scenario=scenario) for i in indicators]
    if output == "json":
        data = study_calc_data(study, str(path), background, by_indicator, scenario)
        return json.dumps(data, indent=2, allow_nan=False, ensure_ascii=False)
    if output == "csv":
        return calc_csv(
            study_calc_data(study, str(path), background, by_indicator, scenario, hashes=False)
        )
    products = study.products
    blocks = [
        [(names[i], by_indicator[k][n].score, units[i]) for k, i in enumerate(indicators)]
        for n in range(len(products))
    ]
    lines = [
        f"Study {study.name} ({path})",
        f"Background: {background_text(background)}, method {background.method.id} "
        f"{background.method.version}",
    ]
    for product, table in zip(products, _score_tables(blocks), strict=True):
        lines += ["", f"{product.amount:g} × {product.process}", *table]
    first = by_indicator[0][0].provenance
    ref = first.study
    lines += [
        "",
        "Provenance",
        f"  input hash: {first.input_hash}",
        f"  method hash: {first.method_hash}",
        f"  engine: headwater {first.engine_version}",
        f"  background: {_pinned(background)}",
        *([f"  scenario: {first.scenario}"] if first.scenario is not None else []),
        "  study: "
        + ("not saved" if ref is None or not ref.saved else _commit(ref.commit, ref.changed)),
        f"  residual: {max(r.provenance.residual for rs in by_indicator for r in rs):.2e}",
        "  scores that cancel: "
        + (
            ", ".join(
                f"{r.process_id} {r.indicator}"
                for rs in by_indicator
                for r in rs
                if r.provenance.cancellation.cancels
            )
            or "none"
        ),
        *_not_characterised(by_indicator[0][0].not_characterised),
    ]
    if iterations is not None:
        if len(products) != 1:
            raise typer.BadParameter(
                f"--iterations works on one product, and study {study.name} has {len(products)}.",
                param_hint="--iterations",
            )
        item = (
            (study, products[0].process)
            if scenario is None
            else (
                study,
                products[0].process,
                scenario,
            )
        )
        mc = hw.monte_carlo(
            background,
            [item],
            iterations=iterations,
            seed=seed,
            indicators=indicators,
            workers=workers,
        )
        lines += ["", monte_carlo_text(mc, background)]
    return "\n".join(lines)
