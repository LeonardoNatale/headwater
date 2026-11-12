"""Time `headwater diff` on a real database: one product, then every product.

The database is in a git repository, with two lines changed on a branch: one in the process of
the product and one in another process. It runs the command as CI does (`diff BASE HEAD DATABASE
--json`, a new process each time) for the product alone (`--product <process id>`) and for every
product (the default). Then, on one loaded background, it times `diff_databases` alone for the
two, and renders the text and the JSON of the second. Only counts and times are written: no score
and no name of a dataset.

The first is the cost of scoring one product alone. `--product` is that product's process id,
and defaults to the first process of the database. Run it on a quiet machine, and keep BLAS to a
few threads:

    OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 VECLIB_MAXIMUM_THREADS=2 HEADWATER_HOME=... \\
    uv run python bench/diff_all_products.py --repo REPO --database studies/pilot \\
        --base main --head two-lines --method-folder methods/EF-3.1 [--product ID] \\
        [--rounds 3] [--out FILE]

The exit status is 1 when scoring every product takes more than 3 times as long as scoring one
alone, in the command or in `diff_databases`. Results go to
bench/results/diff_all_products_results.json, or `--out`.
"""

import argparse
import json
import os
import platform
import statistics
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
import scipy

from headwater.cli._common import load_background, methods_from_folders
from headwater.diff import StudyDiff, diff_databases, read_at, render_json, render_text

LIMIT = 3.0
"""Times as long that every product may take, against one product alone."""
COMMAND = [sys.executable, "-c", "from headwater.cli import main; main()"]
THREADS = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "VECLIB_MAXIMUM_THREADS")


def run_command(args: Sequence[str], repo: Path, rounds: int) -> dict[str, Any]:
    """`headwater diff ...` as a new process, `rounds` times: the seconds of each, and what the
    last one printed."""
    seconds: list[float] = []
    out = b""
    for _ in range(rounds):
        started = time.perf_counter()
        done = subprocess.run([*COMMAND, *args], cwd=repo, capture_output=True, check=False)
        seconds.append(time.perf_counter() - started)
        if done.returncode != 0:
            sys.exit(
                f"headwater {' '.join(args)} exited {done.returncode}:\n{done.stderr.decode()}"
            )
        out = done.stdout
    data = json.loads(out)
    return {
        "seconds": [round(s, 2) for s in seconds],
        "median": round(statistics.median(seconds), 2),
        "products": len({r["product"]["id"] for r in data["results"]}),
        "results": len(data["results"]),
        "differences": len(data["differences"]),
        "json_bytes": len(out),
    }


def best_of(rounds: int, call: Any) -> tuple[list[float], Any]:
    seconds: list[float] = []
    result = None
    for _ in range(rounds):
        started = time.perf_counter()
        result = call()
        seconds.append(time.perf_counter() - started)
    return seconds, result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument("--repo", type=Path, required=True, help="The git repository.")
    parser.add_argument(
        "--database", required=True, help="The database folder, from the repository."
    )
    parser.add_argument("--product", help="The process id of the one product: the first one.")
    parser.add_argument("--base", required=True, help="The ref without the changes.")
    parser.add_argument("--head", required=True, help="The ref with the changes.")
    parser.add_argument("--method-folder", type=Path, action="append", default=[])
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)
    repo: Path = args.repo.resolve()
    folders = [str(f.resolve()) for f in args.method_folder]
    method = [arg for f in folders for arg in ("--method-folder", f)]
    load_before = os.getloadavg()[0]

    at_base = read_at(repo / args.database, args.base)
    old = at_base.database
    at_head = read_at(repo / args.database, args.head)
    new = at_head.database
    one_id = args.product or min(p.id for p in new.processes)
    made = len(new.processes)
    print(f"{made} products")

    results: dict[str, Any] = {
        "machine": f"{platform.machine()} {platform.system()} {platform.release()}, "
        f"{os.cpu_count()} CPUs, Python {platform.python_version()}, NumPy {np.__version__}, "
        f"SciPy {scipy.__version__}",
        "blas_threads": {k: os.environ.get(k) for k in THREADS},
        "date": time.strftime("%Y-%m-%d"),
        "rounds": args.rounds,
        "database": {
            "processes": len(new.processes),
            "products": made,
            "lines": sum(len(p.exchanges) for p in new.processes),
        },
        "load_average_before": round(load_before, 2),
    }

    shared = ["diff", args.base, args.head, args.database, "--json", *method]
    print("headwater diff, one product")
    only = run_command([*shared, "--product", one_id], repo, args.rounds)
    print("headwater diff, every product")
    every = run_command(shared, repo, args.rounds)
    command_ratio = round(every["median"] / only["median"], 2)
    results["command"] = {"one_product": only, "every_product": every, "ratio": command_ratio}

    started = time.perf_counter()
    background = load_background(
        new.database,
        release=None,
        method=methods_from_folders(new.database, None, [Path(f) for f in folders]),
        namespace=None,
    )
    load = time.perf_counter() - started
    print(f"loaded the background in {load:.1f} s")

    def score(products: Any) -> StudyDiff:
        return diff_databases(old, new, background, products=products)

    one_seconds, one = best_of(args.rounds, lambda: score([one_id]))
    all_seconds, every_diff = best_of(args.rounds, lambda: score("all"))
    text = render_text(every_diff, path=args.database, old=at_base.source, new=at_head.source)
    data = render_json(every_diff, path=args.database, old=at_base.source, new=at_head.source)
    results["load_seconds"] = round(load, 2)
    call_ratio = round(statistics.median(all_seconds) / statistics.median(one_seconds), 2)
    results["diff_databases"] = {
        "one_product": {
            "seconds": [round(s, 2) for s in one_seconds],
            "median": round(statistics.median(one_seconds), 2),
            "rows": len(one.rows),
        },
        "every_product": {
            "seconds": [round(s, 2) for s in all_seconds],
            "median": round(statistics.median(all_seconds), 2),
            "rows": len(every_diff.rows),
        },
        "ratio": call_ratio,
    }
    results["text_every_product"] = {"lines": len(text.splitlines()), "bytes": len(text.encode())}
    results["json_every_product_bytes"] = len(json.dumps(data, allow_nan=False).encode())
    results["products_moved"] = sum(1 for p in every_diff.products if p.differs)
    results["load_average_after"] = round(os.getloadavg()[0], 2)

    out = args.out or Path(__file__).parent / "results" / "diff_all_products_results.json"
    out.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: results[k] for k in ("command", "diff_databases")}, indent=2))
    print(f"written to {out}")
    slow = max(command_ratio, call_ratio)
    if slow > LIMIT:
        print(f"every product takes {slow}x as long as one product alone: over {LIMIT}x")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
