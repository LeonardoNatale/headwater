"""Time `headwater diff`'s attribution on a study of about 100 lines, 25 indicators, ecoinvent size.

Builds a release with no licensed data (the one of `release_calc.py`: 23,500 processes, 4,000
elementary flows, about 620,000 lines) and a method of 25 indicators, the size of EF 3.1 (the
sub-indicators included). Then times, on one loaded background:

- `hw.load` of the release for that method: assembly, factorization and the solves of all 25
  indicators. It is paid once per diff, whatever the study.
- `Study.calc` of the study for one indicator, as the reference for a full result.
- `Study.scores` of the study for all 25 indicators at once: what attribution does for each
  difference.
- `diff_databases` between the study and a copy with 1, 10 and every one of its 105 lines changed,
  for all 25 indicators. The cost is the old and the new study in full (2 x 25 `Study.calc`),
  and each difference made alone to the old study and scored for all indicators (`Study.scores`).
- For the 10 changed lines: every effect of `diff_databases` against `Study.calc` of the old study
  with that one difference made, relative to the old score. This is accuracy, not time.

The study is 5 processes: a product that buys 4 parts and 16 background products, and 4 parts that
buy 20 background products each.

Run it on a quiet machine: a second process or a build in the background moves the times by a
third. The load average before and after is written to the results.

    uv run python bench/diff_cost.py [processes] [--rounds N] [--out FILE]
    uv run python bench/diff_cost.py --into FOLDER   # build the release there and keep it
    uv run python bench/diff_cost.py --release FOLDER  # reuse a release kept earlier

The exit status is 1 when the diff that changes every line takes 30 s or more, the limit of the M2
plan. Results go to bench/results/diff_cost_results.json, or `--out`: timings and counts only.
"""

import argparse
import json
import os
import platform
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path

import duckdb
import numpy as np
import pyarrow as pa
import scipy

import headwater as hw
from headwater.diff import Row, diff_databases, differences
from headwater.model import (
    Exchange,
    Factor,
    Flow,
    FlowKind,
    Indicator,
    Method,
    Process,
    ProcessKind,
    Role,
)
from headwater.model.database import Database
from headwater.release import Release, build
from headwater.study import DatabaseFolder

from release_calc import INFO, synthetic  # type: ignore[import-not-found]  # a sibling script

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results" / "diff_cost_results.json"
LIMIT = 30.0
"""Seconds: the largest accepted time of the diff that changes every line."""
INDICATORS = 25
SKU = "st:sku"
"""The product diffed and calculated: the one the other 4 processes make parts for."""
METHOD = "synth-ef25"


def seconds(fn):  # type: ignore[no-untyped-def]
    start = time.perf_counter()
    out = fn()
    return time.perf_counter() - start, out


def method_of(elementary: list[str], seed: int = 3) -> Method:
    """25 indicators, each with a factor for 3,500 of the elementary flows."""
    rng = np.random.default_rng(seed)
    ids = [f"ind-{k:02d}" for k in range(INDICATORS)]
    covered = rng.choice(len(elementary), 3_500, replace=False)
    return Method(
        id=METHOD,
        version="1",
        flow_list="synthetic",
        indicators=tuple(Indicator(id=i, name=f"Indicator {i}", unit="u") for i in ids),
        factors=tuple(
            Factor(indicator=i, flow_id=elementary[k], factor=float(rng.uniform(0.1, 30)))
            for i in ids
            for k in covered
        ),
    )


def study_of(release: Release, products: np.ndarray, seed: int = 7) -> DatabaseFolder:
    """The study of about 100 lines. A product `st:sku` buys 4 parts and 16 background products;
    each part buys 20 background products."""
    rng = np.random.default_rng(seed)
    picks = iter(rng.choice(products, 4 * 20 + 16, replace=False))

    def buys(process: str, first: int, count: int) -> list[Exchange]:
        out = []
        for j in range(count):
            i = int(next(picks))
            out.append(
                Exchange(
                    process_id=process,
                    line_id=f"b{first + j:03d}",
                    role=Role.input,
                    flow_id=f"synth:p{i}",
                    amount=float(rng.uniform(0.05, 0.5)),
                    unit="kg",
                    supplier_id=f"synth:proc{i}",
                )
            )
        return out

    def reference(process: str, unit: str) -> Exchange:
        return Exchange(
            process_id=process,
            line_id="e000",
            role=Role.reference,
            flow_id=process,
            amount=1.0,
            unit=unit,
        )

    parts = [f"st:part{k}" for k in range(1, 5)]
    sku_lines = [reference("st:sku", "unit")]
    for k, part in enumerate(parts, start=1):
        sku_lines.append(
            Exchange(
                process_id="st:sku",
                line_id=f"p{k:03d}",
                role=Role.input,
                flow_id=part,
                amount=0.25,
                unit="kg",
                supplier_id=part,
            )
        )
    sku_lines += buys("st:sku", 0, 16)
    processes = [("st:sku", sku_lines)]
    processes += [(part, [reference(part, "kg"), *buys(part, 0, 20)]) for part in parts]
    return DatabaseFolder(
        path=Path("."),
        namespace="st",
        processes=tuple(
            Process(
                id=pid,
                name=pid,
                location="GLO",
                kind=ProcessKind.ordinary_transforming_activity,
                exchanges=tuple(lines),
            )
            for pid, lines in processes
        ),
        flows=(
            Flow(id="st:sku", name="SKU", kind=FlowKind.product, unit="unit"),
            *(Flow(id=p, name=p, kind=FlowKind.product, unit="kg") for p in parts),
        ),
        flow_properties=(),
        units=(),
        database=Database(
            name="st",
            background=f"{release.name}@{release.hash[:12]}",  # pyright: ignore[reportArgumentType]
            methods=(METHOD,),
        ),
    )


def changed(folder: DatabaseFolder, count: int | None) -> DatabaseFolder:
    """A copy with the amount of `count` input lines raised by 10%, spread over the processes in
    turn. `None` changes every input line."""
    inputs = [
        (p.id, x.line_id) for p in folder.processes for x in p.exchanges if x.role is Role.input
    ]
    chosen = set(inputs if count is None else inputs[:: max(1, len(inputs) // count)][:count])
    processes = tuple(
        p.model_copy(
            update={
                "exchanges": tuple(
                    x.model_copy(update={"amount": x.amount * 1.1})
                    if (p.id, x.line_id) in chosen
                    else x
                    for x in p.exchanges
                )
            }
        )
        for p in folder.processes
    )
    return replace(folder, processes=processes)


def worst_effect_error(
    old: DatabaseFolder, rows: tuple[Row, ...], background: hw.Background
) -> float:
    """The largest gap between an effect that `diff_databases` found and `Database.calc` of the
    old SKU with only that difference made, relative to the old score."""
    worst = 0.0
    for row in rows:
        before = hw.Database(old).calc(background, SKU, indicator=row.indicator).score
        for driver in row.drivers:
            assert driver.difference.apply is not None and driver.contribution is not None
            alone = hw.Database(driver.difference.apply(old)).calc(
                background, SKU, indicator=row.indicator
            )
            worst = max(worst, abs((alone.score - before) - driver.contribution) / max(abs(before), 1e-300))
    return worst


def run(release: Release, products: np.ndarray, rounds: int) -> dict[str, object]:
    out: dict[str, object] = {"indicators": INDICATORS}
    old = study_of(release, products)
    out["database_lines"] = sum(len(p.exchanges) for p in old.processes)
    out["database_processes"] = len(old.processes)
    print(f"database: {out['database_processes']} processes, {out['database_lines']} lines")

    best_load, background = float("inf"), None
    for _ in range(rounds):
        took, background = seconds(lambda: hw.load(release.path, method=METHOD))
        best_load = min(best_load, took)
    assert isinstance(background, hw.Background)
    out["load"] = round(best_load, 3)
    print(f"{'hw.load (all 25 indicators)':<44} best of {rounds}: {best_load:>8.2f} s")

    one = hw.Database(old)
    best_calc = min(
        seconds(lambda: one.calc(background, SKU, indicator="ind-00"))[0] for _ in range(5)
    )
    out["calc_one_indicator"] = round(best_calc, 4)
    print(f"{'Database.calc, one indicator':<44} best of 5: {best_calc * 1e3:>8.1f} ms")

    best_scores = min(seconds(lambda: one.scores(background, SKU))[0] for _ in range(5))
    out["scores_all_indicators"] = round(best_scores, 4)
    print(f"{'Database.scores, 25 indicators':<44} best of 5: {best_scores * 1e3:>8.1f} ms")

    ids = [i.id for i in background.method.indicators]
    for label, count in (("1", 1), ("10", 10), ("all", None)):
        new = changed(old, count)
        found = differences(old, new)
        scored = sum(1 for d in found if d.scored)
        best = float("inf")
        for _ in range(rounds):
            took, diff = seconds(
                lambda: diff_databases(
                    old, new, background, indicators=ids, products=[SKU], differences=found
                )
            )
            best = min(best, took)
        out[f"diff_{label}"] = round(best, 3)
        out[f"diff_{label}_differences"] = scored
        out[f"diff_{label}_full_calcs"] = 2 * INDICATORS
        out[f"diff_{label}_scorings"] = scored
        print(
            f"diff, {scored:>3} lines changed, 25 indicators{'':<5} best of {rounds}: "
            f"{best:>8.2f} s  ({2 * INDICATORS} full calculations, {scored} scorings of 25 "
            "indicators)"
        )
        out[f"diff_{label}_rows"] = len(diff.rows)
        if label == "10":
            worst = worst_effect_error(old, diff.rows, background)
            out["diff_10_effects_vs_calc_worst_error"] = worst
            print(f"{'effects against Database.calc of each difference alone':<44} worst error: {worst:.1e}")
    return out


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("processes", nargs="?", type=int, default=23_500)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--out", type=Path, default=RESULTS)
    parser.add_argument("--into", type=Path, help="build the release here and keep it")
    parser.add_argument("--release", type=Path, help="a release kept earlier with --into")
    args = parser.parse_args()
    load_average = os.getloadavg()[0]
    results: dict[str, object] = {
        "machine": f"{platform.machine()} {platform.system()} {platform.release()}, "
        f"{os.cpu_count()} CPUs",
        "software": (
            f"Python {platform.python_version()}, NumPy {np.__version__}, "
            f"SciPy {scipy.__version__}, DuckDB {duckdb.__version__}, PyArrow {pa.__version__}"
        ),
        "date": time.strftime("%Y-%m-%d"),
        "load_average_before": round(load_average, 2),
        "rounds": args.rounds,
    }
    print(f"load average before: {load_average:.2f}")
    with tempfile.TemporaryDirectory() as tmp:
        processes, flows, _, _, product_index = synthetic(args.processes)
        if args.release is None:
            elementary = [f.id for f in flows if f.kind is FlowKind.elementary]
            took, release = seconds(
                lambda: build(
                    INFO,
                    processes=processes,
                    flows=flows,
                    methods=[method_of(elementary)],
                    into=args.into or tmp,
                )
            )
            print(f"built the release in {took:.1f} s")
        else:
            release = Release.open(args.release)
        results["processes"] = args.processes
        results["lines"] = int(release.manifest.tables["exchanges.parquet"].rows)
        results.update(run(release, product_index, args.rounds))
    results["load_average_after"] = round(os.getloadavg()[0], 2)
    args.out.write_text(json.dumps(results, indent=2) + "\n")
    print(f"wrote {args.out}")
    sys.exit(1 if float(results["diff_all"]) >= LIMIT else 0)  # type: ignore[arg-type]
