"""Time a database's calc, explain and levers on a synthetic release of ecoinvent size.

Builds a release with no licensed data (the one of `release_calc.py`: 23,500 processes, 4,000
elementary flows, about 620,000 lines), then times, on a fresh `hw.load` each round:

- `hw.load` from the release, the assembly of A and B alone, and the memory of the columns of
  lines that the loaded background keeps,
- the first `Result.sensitivity(top=10)` of a product that buys 20 products,
- the same call again,
- `Database.calc` and, inside it, `validate_database`, and `Result.explain` on the loaded
  background.

Run it on a quiet machine: a second process or a build in the background moves the times by a
third.

    uv run python bench/levers.py [processes] [--release FOLDER] [--rounds N] [--out FILE]

`--release` reuses a release built earlier (the folder that holds `release.json`). The exit
status is 1 when the first `levers(top=10)` takes 0.3 s or more, the limit of the M2 plan.
Results go to bench/results/levers_results.json, or `--out`: timings and counts only. To time
the code before a change, run the script with that code's `src` first on PYTHONPATH and
`uv run --no-sync`.
"""

import argparse
import json
import platform
import sys
import tempfile
import time
from pathlib import Path

import duckdb
import numpy as np
import pyarrow as pa
import scipy
from release_calc import INFO, synthetic  # type: ignore[import-not-found]  # a sibling script

import headwater as hw
from headwater.model import Exchange, Flow, FlowKind, Process, ProcessKind, Role
from headwater.model.database import Database
from headwater.query._internals import internals
from headwater.query._release import assemble_release
from headwater.release import Release, build
from headwater.study import DatabaseFolder
from headwater.study.validate import validate_database

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results" / "levers_results.json"
SKU = "st:sku"
LIMIT = 0.3
"""Seconds: the largest accepted time of the first `levers(top=10)`."""


def study_of(release: Release, products: list[str], suppliers: list[str]) -> hw.Database:
    """A database of one process that buys 0.1 kg of each of 20 products."""
    rng = np.random.default_rng(7)
    lines = [
        Exchange(
            process_id="st:sku",
            line_id="e000",
            role=Role.reference,
            flow_id="st:sku",
            amount=1.0,
            unit="unit",
        )
    ]
    for j, i in enumerate(rng.choice(len(products), 20, replace=False)):
        lines.append(
            Exchange(
                process_id="st:sku",
                line_id=f"e{j + 1:03d}",
                role=Role.input,
                flow_id=products[i],
                amount=0.1,
                unit="kg",
                supplier_id=suppliers[i],
            )
        )
    process = Process(
        id="st:sku",
        name="SKU",
        location="GLO",
        kind=ProcessKind.ordinary_transforming_activity,
        exchanges=tuple(lines),
    )
    return hw.Database(
        DatabaseFolder(
            path=Path("."),
            namespace="st",
            processes=(process,),
            flows=(Flow(id="st:sku", name="SKU", kind=FlowKind.product, unit="unit"),),
            flow_properties=(),
            units=(),
            database=Database(
                name="st",
                background=f"{release.name}@{release.hash[:12]}",  # pyright: ignore[reportArgumentType]
                methods=("synth-method",),
            ),
        )
    )


def line_columns_mb(background: hw.Background) -> float | None:
    """Megabytes the loaded background keeps for its lines, or None for code that keeps none."""
    # Read the instance's own attributes: older code loads `lines` on first access, which would
    # move the time of the first `levers` call.
    held = vars(internals(background).solved.block)
    lines, emissions = held.get("lines"), held.get("emissions")
    if not hasattr(lines, "row") or not hasattr(emissions, "row"):
        return None
    arrays = [getattr(x, name) for x in (lines, emissions) for name in vars(x) if name != "line_id"]
    size = sum(a.nbytes for a in arrays) + lines.line_id.nbytes + emissions.line_id.nbytes  # type: ignore[union-attr]
    return round(size / 1e6, 1)


def seconds(fn):
    start = time.perf_counter()
    out = fn()
    return time.perf_counter() - start, out


def main(release: Release, rounds: int) -> dict[str, object]:
    products = [f.id for f in release.flows() if f.id.startswith("synth:p")]
    suppliers = [
        f"synth:proc{f.id.removeprefix('synth:p')}"
        for f in release.flows()
        if f.id.startswith("synth:p")
    ]
    study = study_of(release, products, suppliers)
    out: dict[str, object] = {}
    columns: dict[str, list[float]] = {
        "load": [],
        "assemble": [],
        "calc": [],
        "validate": [],
        "explain": [],
        "levers_first": [],
        "levers_second": [],
    }
    for _ in range(rounds):
        took, background = seconds(lambda: hw.load(release.path, method="synth-method"))
        columns["load"].append(took)
        out["line_columns_mb"] = line_columns_mb(background)
        columns["assemble"].append(
            seconds(lambda: assemble_release(release, background.method))[0]
        )
        took, result = seconds(lambda: study.calc(background, SKU, indicator="ind-a"))
        calc_times = [took] + [
            seconds(lambda: study.calc(background, SKU, indicator="ind-a"))[0] for _ in range(2)
        ]
        columns["calc"].append(min(calc_times))
        columns["validate"].append(
            min(
                seconds(
                    lambda: validate_database(
                        study.folder, internals(background).folder, method=background.method
                    )
                )[0]
                for _ in range(3)
            )
        )
        columns["explain"].append(seconds(lambda: result.contributions())[0])
        columns["levers_first"].append(seconds(lambda: result.sensitivity(top=10))[0])
        columns["levers_second"].append(seconds(lambda: result.sensitivity(top=10))[0])
        out["score"] = result.score
    for name, values in columns.items():
        out[name] = round(min(values), 4)
        print(f"{name:<16} best of {rounds}: {min(values) * 1e3:>10.1f} ms")
    every, levers = seconds(lambda: result.sensitivity())
    out["levers_all"] = round(every, 4)
    out["levers_all_count"] = len(levers)
    print(f"{'levers_all':<16} {every * 1e3:>10.1f} ms ({len(levers)} levers)")
    return out


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("processes", nargs="?", type=int, default=23_500)
    parser.add_argument("--release", type=Path, help="a release built earlier")
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--out", type=Path, default=RESULTS)
    args = parser.parse_args()
    results: dict[str, object] = {
        "machine": f"{platform.machine()} {platform.system()} {platform.release()}",
        "software": (
            f"Python {platform.python_version()}, NumPy {np.__version__}, "
            f"SciPy {scipy.__version__}, DuckDB {duckdb.__version__}, PyArrow {pa.__version__}"
        ),
        "date": time.strftime("%Y-%m-%d"),
        "rounds": args.rounds,
    }
    with tempfile.TemporaryDirectory() as tmp:
        if args.release is None:
            processes, flows, method, _, _ = synthetic(args.processes)
            release = build(INFO, processes=processes, flows=flows, methods=[method], into=tmp)
        else:
            release = Release.open(args.release)
        results.update(main(release, args.rounds))
    args.out.write_text(json.dumps(results, indent=2) + "\n")
    print(f"wrote {args.out}")
    sys.exit(1 if float(results["levers_first"]) >= LIMIT else 0)  # type: ignore[arg-type]
