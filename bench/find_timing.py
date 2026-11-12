"""Finding a process by name on a release: what the search table and a query cost.

    export HEADWATER_HOME=/path/to/headwater-home           # holds the release
    uv run python bench/find_timing.py ecoinvent-3.12-cutoff@b9428cdd8045 \
        [path to a method folder, such as $HEADWATER_WORK/pilot/methods/EF-3.1]

It measures, in this order:

- `cli`: the whole `headwater find` command in a fresh process, best and worst of 5 runs. This is
  what a person waits for: the interpreter, the imports, reading the release, the table and one
  query.
- `start_up`: `headwater --help` in a fresh process, best and worst of 3 runs, which does no work, and
  the import time of `headwater.cli`, `pyarrow.dataset` and `pandas` from `python -X importtime`.
- `table`: `table_of_release` in this process, the first call and then the best of 5. It reads
  `processes` and `flows` and loads no method.
- `queries`: each query warm, 20 runs, the median and the largest in milliseconds, and how many
  processes it finds. Their text is generic: names of markets and the word `steel`.
- with a method folder, `background`: `hw.load` (factorizes: a bound on how long a person who
  already has a background waited for it), then the first `bg.find` (which builds the table from
  the records the background holds) and the best of 5 after it.

Standard output is the table of results. `bench/results/find_timing_results.json` gets counts and
times only, and the load average before and after, because other work on the machine slows every
row.
"""

import json
import os
import platform
import statistics
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import headwater as hw
from headwater.query.search import SearchTable, table_of_path

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results" / "find_timing_results.json"
RUNS = 20

QUERIES: tuple[tuple[str, dict[str, str | int | None]], ...] = (
    ("an exact name with its place", {"text": "market for steel, low-alloyed", "location": "GLO"}),
    ("an exact name, every place", {"text": "market for steel, low-alloyed"}),
    ("words of a name, in any order", {"text": "steel low-alloyed market"}),
    ("a short word that many names start with", {"text": "electricity"}),
    ("a product, with its place", {"text": "steel", "product": "steel", "location": "RER"}),
    ("one letter, which almost every name has, no limit", {"text": "a", "limit": None}),
    ("no match", {"text": "no process has this in its name"}),
)


def ms(seconds: float) -> float:
    return round(seconds * 1000, 3)


def timed[T](call: Callable[[], T]) -> tuple[float, T]:
    start = time.perf_counter()
    value = call()
    return time.perf_counter() - start, value


def cli(release: str) -> dict[str, float]:
    command = [
        str(Path(sys.executable).parent / "headwater"),
        "find",
        "market for steel, low-alloyed",
        "--release",
        release,
        "--location",
        "GLO",
    ]
    seconds: list[float] = []
    for _ in range(5):
        start = time.perf_counter()
        done = subprocess.run(command, capture_output=True, text=True, check=True)
        seconds.append(time.perf_counter() - start)
        assert done.stdout.strip(), "the command found nothing"
    return {"best_s": round(min(seconds), 3), "worst_s": round(max(seconds), 3)}


def start_up() -> dict[str, object]:
    python = sys.executable
    seconds: list[float] = []
    for _ in range(3):
        start = time.perf_counter()
        subprocess.run([str(Path(python).parent / "headwater"), "--help"], capture_output=True, check=True)
        seconds.append(time.perf_counter() - start)
    done = subprocess.run(
        [python, "-X", "importtime", "-c", "import headwater.cli"],
        capture_output=True,
        text=True,
        check=True,
    )
    cumulative: dict[str, float] = {}
    for line in done.stderr.splitlines():
        _, _, rest = line.partition("|")
        number, _, module = rest.partition("|")
        if module.strip() in ("headwater.cli", "pyarrow.dataset", "pandas"):
            cumulative[module.strip()] = round(int(number) / 1e6, 2)
    return {
        "help_best_s": round(min(seconds), 3),
        "help_worst_s": round(max(seconds), 3),
        "import_s": cumulative,
    }


def queries(table: SearchTable) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for label, arguments in QUERIES:
        text = str(arguments["text"])
        rest = {k: v for k, v in arguments.items() if k != "text"}
        runs: list[float] = []
        found = 0
        for _ in range(RUNS):
            start = time.perf_counter()
            found = len(table.find(text, **rest))  # pyright: ignore[reportArgumentType]
            runs.append(time.perf_counter() - start)
        rows.append(
            {
                "query": label,
                "found": found,
                "median_ms": ms(statistics.median(runs)),
                "max_ms": ms(max(runs)),
            }
        )
    return rows


def main(release: str, method: str | None) -> None:
    load_before = os.getloadavg()
    result: dict[str, object] = {
        "date": datetime.now(UTC).date().isoformat(),
        "machine": f"{platform.machine()} {platform.system()} {platform.release()}",
        "python": platform.python_version(),
        "release": release,
        "load_average_before": [round(v, 2) for v in load_before],
    }
    result["cli"] = cli(release)
    result["start_up"] = start_up()
    first, table = timed(lambda: table_of_path(release))
    best = min(timed(lambda: table_of_path(release))[0] for _ in range(5))
    result["table"] = {
        "processes": len(table.ids),
        "first_ms": ms(first),
        "best_of_5_ms": ms(best),
    }
    result["queries"] = queries(table)
    if method is not None:
        loaded, background = timed(lambda: hw.load(release, method=method))
        first_find, _ = timed(lambda: background.find("steel"))
        warm = min(timed(lambda: background.find("steel"))[0] for _ in range(5))
        result["background"] = {
            "load_s": round(loaded, 2),
            "first_find_ms": ms(first_find),
            "best_of_5_find_ms": ms(warm),
        }
    result["load_average_after"] = [round(v, 2) for v in os.getloadavg()]
    RESULTS.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2] if len(sys.argv) == 3 else None)
