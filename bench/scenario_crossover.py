"""Update or refactorize: how many changed columns before a new factorization is better.

A scenario that changes k columns of A is solved either by a low-rank update of the stored
factorization (k transposed solves, then solves that carry a k-column correction) or by
factorizing the edited A again (one factorization, then plain solves). `query/_scenario.py`
updates up to `CROSSOVER` columns, and factorizes again above it, or when the update refuses the
edit (its residual check fails, or it finds A singular). This times both paths, end to end, for
k changed columns of the real release, and finds the largest k where the update works and is
faster. That k is `CROSSOVER` in `src/headwater/calc/update.py`.

    export HEADWATER_WORK=/path/to/scratch
    uv run python bench/scenario_crossover.py <release folder> [--repeat 3] [--sets 2] [--ks 1,10,100]
    uv run python bench/scenario_crossover.py --synthetic 23500        # no licensed data

What is timed, for each k and each set of k columns:

- update: `low_rank_update` (k solves, the singularity test, the residual check and the
  condition estimate), then the scores of every product for one indicator from the stored
  scores, then the supply of one demand (two solves with the update). If the update refuses
  the edit, the time until it refuses is recorded, and the cost of the scenario is that time
  plus the refactorization, which is what `calc` pays.
- refactorize: `refactorize` (a factorization with its residual check and condition estimate),
  then the scores, then the supply (one solve).

The changed columns are chosen at random with a fixed seed, `--sets` different sets for each k.
Each keeps its sparsity and has its purchases multiplied by 1.1, so the edited matrix stays
solvable. Each time is the best of `--repeat` runs.

Run it alone: both times move with the load of the machine. The load average is read before and
after each k and recorded, and a run that starts with a load average above 1 is marked
`"quiet": false`. Results go to bench/results/scenario_crossover_<release name>_results.json
after every k, for example `scenario_crossover_ecoinvent-3.12-cutoff_results.json`: counts and
timings only, nothing licensed. `scenario_crossover_results.json` is the run on 3.10 of
12 October 2026.
"""

import argparse
import json
import os
import platform
import sys
import time
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pyarrow
import scipy
from numpy.typing import NDArray
from scipy import sparse

import headwater as hw
from headwater.calc import (
    Factorization,
    ResidualError,
    SingularMatrixError,
    factorize,
    low_rank_update,
    scores,
    supply,
)
from headwater.calc.update import CROSSOVER, refactorize
from headwater.query._internals import internals
from headwater.release import Release

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results" / "scenario_crossover_results.json"
FACTORS = {"Carbon dioxide, fossil": 1.0, "Methane, fossil": 29.8, "Dinitrogen monoxide": 273.0}
SCALE = 1.1
"""The purchases of a changed column are multiplied by this."""
KS = (1, 10, 30, 100, 120, 150, 175, 200, 300, 400, 500, 700, 1000)
QUIET = 1.0
"""A 1-minute load average above this at the start marks the run as not quiet."""


def best(fn: Callable[[], object], repeat: int) -> float:
    """Best time of `repeat` runs, in seconds."""
    fastest = float("inf")
    for _ in range(repeat):
        start = time.perf_counter()
        fn()
        fastest = min(fastest, time.perf_counter() - start)
    return fastest


def method_folder(release: Release, work: Path) -> Path:
    """The 3-flow climate method (CO2, CH4, N2O), built from the release's flows by name."""
    folder = work / "methods" / f"climate-3-flows-{release.info.version}"
    folder.mkdir(parents=True, exist_ok=True)
    flows = release.table("flows").to_pylist()
    rows = "".join(
        f"climate,{f['id']},,{FACTORS[f['name']]}\n"
        for f in flows
        if f["kind"] == "elementary" and f["name"] in FACTORS
    )
    (folder / "method.yaml").write_text(
        'id: climate-3-flows\nversion: "1"\n'
        f"flow_list: {release.info.flow_list}\n"
        "indicators:\n  - id: climate\n    name: Climate change, 3 flows\n    unit: kg CO2-eq\n"
    )
    (folder / "factors.csv").write_text("indicator,flow,location,factor\n" + rows)
    return folder


def synthetic(n: int, seed: int = 1) -> sparse.csc_array:
    """A matrix with the size of ecoinvent 3.10 cut-off: six purchases per process, half of them
    from the 1% most popular products, amounts of 0.01 to 0.12. Its supply graph is made up, so
    its factorization time is not that of ecoinvent."""
    rng = np.random.default_rng(seed)
    hubs = max(2, n // 100)
    weights = 1.0 / np.arange(1, hubs + 1)
    weights /= weights.sum()
    rows = list(range(n))
    cols = list(range(n))
    data = [1.0] * n
    for j in range(n):
        found: set[int] = set()
        while len(found) < 6:
            popular = rng.random() < 0.5
            pick = int(rng.choice(hubs, p=weights)) if popular else int(rng.integers(n))
            if pick != j:
                found.add(pick)
        for i in sorted(found):
            rows.append(i)
            cols.append(j)
            data.append(-float(rng.uniform(0.01, 0.12)))
    return sparse.csc_array((data, (rows, cols)), shape=(n, n))


def changed_columns(A: sparse.csc_array, k: int, seed: int) -> tuple[list[int], sparse.csc_array]:
    """k random columns of A, and those columns with their purchases multiplied by SCALE."""
    rng = np.random.default_rng(seed)
    columns = np.sort(rng.choice(A.shape[0], k, replace=False))
    new = sparse.csc_array(A[:, columns]).copy()
    diagonal = np.repeat(columns, np.diff(new.indptr))
    new.data = np.where(new.indices == diagonal, new.data, new.data * SCALE)
    return columns.tolist(), new


def update_path(
    fact: Factorization,
    columns: list[int],
    new: sparse.csc_array,
    h: NDArray[np.float64],
    x: NDArray[np.float64],
    f: NDArray[np.float64],
) -> dict[str, float]:
    """The parts of the update path. Raises what the update raises when it refuses the edit."""
    start = time.perf_counter()
    update = low_rank_update(fact, columns, new)
    made = time.perf_counter()
    update.scores(h, start=x)
    scored = time.perf_counter()
    update.supply(f)
    done = time.perf_counter()
    return {"make": made - start, "scores": scored - made, "supply": done - scored}


def refactorize_path(
    fact: Factorization,
    columns: list[int],
    new: sparse.csc_array,
    h: NDArray[np.float64],
    f: NDArray[np.float64],
) -> dict[str, float]:
    start = time.perf_counter()
    fresh = refactorize(fact, columns, new)
    made = time.perf_counter()
    scores(fresh, h)
    scored = time.perf_counter()
    supply(fresh, f)
    done = time.perf_counter()
    return {"make": made - start, "scores": scored - made, "supply": done - scored}


def crossing(rows: list[dict[str, object]]) -> float | None:
    """The k where the update stops being faster, interpolated between two neighbouring k where
    the update worked in every set: the largest one where it is faster, and the next. None if
    there are no two such k, or the update is not slower at the second."""
    worked = [r for r in rows if r["update_worked"]]
    for first, second in zip(worked, worked[1:], strict=False):
        d1 = float(first["update_s"]) - float(first["refactorize_s"])  # pyright: ignore[reportArgumentType]
        d2 = float(second["update_s"]) - float(second["refactorize_s"])  # pyright: ignore[reportArgumentType]
        if d1 <= 0 < d2:
            k1, k2 = int(first["k"]), int(second["k"])  # pyright: ignore[reportArgumentType]
            return k1 + (k2 - k1) * (-d1) / (d2 - d1)
    return None


def usable(rows: list[dict[str, object]]) -> int:
    """The largest k such that at every measured k up to it, in every set, the update worked and
    was faster than refactorizing. 0 if it does not hold at the first k."""
    found = 0
    for row in rows:
        if not (row["update_worked"] and row["update_faster"]):
            break
        found = int(row["k"])  # pyright: ignore[reportArgumentType]
    return found


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("release", nargs="?", type=Path, help="a release folder")
    parser.add_argument("--synthetic", type=int, metavar="N", help="a made-up matrix of N processes")
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--sets", type=int, default=2, help="column sets tried for each k")
    parser.add_argument("--ks", default=",".join(str(k) for k in KS))
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    if (args.release is None) == (args.synthetic is None):
        parser.error("give a release folder, or --synthetic N")
    ks = sorted({int(k) for k in args.ks.split(",")})

    load_start = os.getloadavg()[0]
    scope: dict[str, object] = {
        "machine": f"{platform.machine()} {platform.system()} {platform.release()}",
        "cpus": os.cpu_count(),
        "python": platform.python_version(),
        "numpy": np.__version__,
        "scipy": scipy.__version__,
        "pyarrow": pyarrow.__version__,
        "headwater": hw.__version__,
        "blas": np.show_config(mode="dicts")["Build Dependencies"]["blas"]["name"],  # pyright: ignore[reportOptionalSubscript]
        "threads_env": {
            name: os.environ.get(name)
            for name in ("VECLIB_MAXIMUM_THREADS", "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS")
        },
        "date": time.strftime("%Y-%m-%d"),
        "crossover_in_code": CROSSOVER,
        "load_average_at_start": [round(v, 2) for v in os.getloadavg()],
        "quiet": load_start <= QUIET,
    }
    if not scope["quiet"]:
        print(f"WARNING: load average {load_start:.2f} is above {QUIET}: not a quiet run", flush=True)

    if args.release is not None:
        work = Path(os.environ["HEADWATER_WORK"]).expanduser()
        release = Release.open(args.release)
        bg = hw.load(args.release, method=method_folder(release, work))
        solved = internals(bg).solved
        fact = solved.factorization
        h = solved.h["climate"]
        x = solved.x["climate"].values
        scope["release"] = args.release.name
        scope["pivoted"] = fact.pivoted
        out = args.out or RESULTS.with_name(
            f"scenario_crossover_{args.release.name.split('@')[0]}_results.json"
        )
    else:
        A = synthetic(args.synthetic)
        fact = factorize(A)
        h = np.random.default_rng(3).lognormal(-3, 1.5, A.shape[0])
        x = scores(fact, h).values
        scope["release"] = f"synthetic, {args.synthetic} processes"
        out = args.out or RESULTS.with_name("scenario_crossover_synthetic_results.json")
    A = fact.matrix
    n = A.shape[0]
    demand = np.zeros(n)
    demand[int(np.random.default_rng(5).integers(n))] = 1.0
    print(f"{scope['release']}: {n} processes, {A.nnz} non-zeros in A", flush=True)

    stored = {
        "processes": n,
        "non_zeros": int(A.nnz),
        "factorize_s": round(best(lambda: factorize(A), 1), 3),
        "scores_ms": round(best(lambda: scores(fact, h), 20) * 1e3, 2),
        "supply_ms": round(best(lambda: supply(fact, demand), 20) * 1e3, 2),
    }
    print(stored, flush=True)

    rows: list[dict[str, object]] = []

    def save(final: bool) -> None:
        scope["load_average_at_end"] = [round(v, 2) for v in os.getloadavg()]
        out.write_text(
            json.dumps(
                {
                    "scope": scope,
                    "stored": stored,
                    "repeat": args.repeat,
                    "sets": args.sets,
                    "scale": SCALE,
                    "finished": final,
                    "rows": rows,
                    "usable_up_to": usable(rows),
                    "first_refused": next((r["k"] for r in rows if not r["update_worked"]), None),
                    "time_crossover": crossing(rows),
                },
                indent=2,
            )
            + "\n"
        )

    print(f"{'k':>6} {'set':>3} {'update s':>10} {'refactorize s':>14}  note   (load before/after)")
    for k in ks:
        before = os.getloadavg()[0]
        sets: list[dict[str, object]] = []
        for number in range(args.sets):
            columns, new = changed_columns(A, k, seed=1000 * number + k)
            record: dict[str, object] = {"seed": 1000 * number + k}
            refused: tuple[str, float] | None = None
            up: list[dict[str, float]] = []
            for _ in range(args.repeat):
                start = time.perf_counter()
                try:
                    up.append(update_path(fact, columns, new, h, x, demand))
                except (ResidualError, SingularMatrixError) as error:
                    refused = (type(error).__name__, time.perf_counter() - start)
                    break
            re = [refactorize_path(fact, columns, new, h, demand) for _ in range(args.repeat)]
            best_re = min(re, key=lambda r: sum(r.values()))
            record["refactorize_s"] = round(sum(best_re.values()), 4)
            record["refactorize_parts_s"] = {p: round(v, 4) for p, v in best_re.items()}
            if refused is None:
                best_up = min(up, key=lambda r: sum(r.values()))
                record["update_s"] = round(sum(best_up.values()), 4)
                record["update_parts_s"] = {p: round(v, 4) for p, v in best_up.items()}
                record["update_faster"] = bool(sum(best_up.values()) < sum(best_re.values()))
                note = "" if record["update_faster"] else "update slower"
                shown = record["update_s"]
            else:
                record["update_refused"] = refused[0]
                record["refused_after_s"] = round(refused[1], 4)
                record["update_faster"] = False
                note = f"update refused ({refused[0]}) after {refused[1]:.2f} s"
                shown = "refused"
            sets.append(record)
            print(
                f"{k:>6} {number:>3} {shown:>10} {record['refactorize_s']:>14}  {note}",
                flush=True,
            )
        after = os.getloadavg()[0]
        worked = [r for r in sets if "update_s" in r]
        row: dict[str, object] = {
            "k": k,
            "update_worked": len(worked) == len(sets),
            "update_faster": all(bool(r["update_faster"]) for r in sets),
            "update_s": max(float(r["update_s"]) for r in worked) if worked else None,  # pyright: ignore[reportArgumentType]
            "refactorize_s": round(sum(float(r["refactorize_s"]) for r in sets) / len(sets), 4),  # pyright: ignore[reportArgumentType]
            "sets": sets,
            "load_before": round(before, 2),
            "load_after": round(after, 2),
        }
        rows.append(row)
        print(f"       load {before:.2f} / {after:.2f}", flush=True)
        save(final=False)

    save(final=True)
    limit = usable(rows)
    refused_at = next((r["k"] for r in rows if not r["update_worked"]), None)
    cross = crossing(rows)
    print(f"the update works and is faster up to k = {limit} changed columns (CROSSOVER in code: {CROSSOVER})")
    print(f"first k where the update refuses the edit: {refused_at}")
    print(f"k where its time reaches the refactorization's, between two k where it works: {cross}")
    print(f"written to {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
