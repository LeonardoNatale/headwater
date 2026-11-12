"""What `bg.processes` costs on a release when it is called with ids it has not read yet.

    export HEADWATER_HOME=/path/to/headwater-home        # holds the release
    OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 uv run python bench/processes_read.py \
        ecoinvent-3.12-cutoff@b9428cdd8045 $HEADWATER_WORK/pilot/methods/EF-3.1 before

The release holds no method, so the second argument is a method folder: `hw.load` factorizes with
it once, before anything is timed. The third argument names the run (`before`, `after`) and the
results file: `bench/results/processes_read_<name>_results.json`.

It picks 300 processes at random (seed 0) from the processes table and measures, in this order:

- `single`: `bg.processes([id])` for 50 of them, one call each, every id new to the background.
  The first call, then the median and the largest in milliseconds, and the sum of the 50.
- `batch`: five calls of `bg.processes(ids)`, each with 50 other ids, all new to the background.
  The first, the median and the largest in milliseconds, and `per_id_median_ms`, the median call
  divided by 50.
- `repeat`: `bg.processes([id])` again for the 50 ids of `single`, which are now held.

For each of them, the parquet datasets the call opens are split by table: how long `ds.dataset`
took to open each one (finding the file and reading its footer), how long `to_table` took to scan
it with the filter, how many rows it returned, and how many times it was opened. The split is
made by wrapping `pyarrow.dataset.dataset`, so it sees what the code opens and nothing else:
the part of a call that is in no table is the rest of its time.

`processes_read.py layout <release>` reads no background and times nothing: it writes
`bench/results/processes_read_layout_results.json` with how the exchanges table is laid out (rows
per row group, whether the process ids of the groups are in order and apart, and how large each
group is once decoded), which is why a call for one id costs what it does.

Standard output and the results file hold counts and times only, with the load average before and
after, because other work on the machine slows every row.
"""

import json
import os
import platform
import random
import statistics
import subprocess
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pyarrow.dataset as ds
import pyarrow.parquet as pq

import headwater as hw
from headwater.query._internals import internals
from headwater.query._open import open_source
from headwater.release import Release

HERE = Path(__file__).resolve().parent
SINGLE = 50
BATCH = 50
BATCHES = 5


@dataclass
class Read:
    """One dataset a call opened: how long opening it took, and the scans made on it."""

    table: str
    open_s: float
    scan_s: float = 0.0
    rows: int = 0


class Watched:
    """A dataset that adds the time and rows of every `to_table` to its `Read`."""

    def __init__(self, real: Any, read: Read) -> None:
        self._real = real
        self._read = read

    def to_table(self, *args: Any, **kwargs: Any) -> Any:
        start = time.perf_counter()
        table = self._real.to_table(*args, **kwargs)
        self._read.scan_s += time.perf_counter() - start
        self._read.rows += table.num_rows
        return table

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


READS: list[Read] = []
_real_dataset: Any = ds.dataset  # pyright: ignore[reportUnknownMemberType]


def watching(source: Any, *args: Any, **kwargs: Any) -> Watched:
    start = time.perf_counter()
    real = _real_dataset(source, *args, **kwargs)
    read = Read(Path(source).name.removesuffix(".parquet"), time.perf_counter() - start)
    READS.append(read)
    return Watched(real, read)


def ms(seconds: float) -> float:
    return round(seconds * 1000, 3)


def call(background: hw.Background, ids: list[str]) -> dict[str, Any]:
    """`bg.processes(ids)` once: its time, and what it read from each table."""
    READS.clear()
    start = time.perf_counter()
    found = background.processes(ids)
    seconds = time.perf_counter() - start
    assert len(found) == len(set(ids))
    tables: dict[str, dict[str, float]] = defaultdict(
        lambda: {"opens": 0, "open_s": 0.0, "scan_s": 0.0, "rows": 0}
    )
    for read in READS:
        entry = tables[read.table]
        entry["opens"] += 1
        entry["open_s"] += read.open_s
        entry["scan_s"] += read.scan_s
        entry["rows"] += read.rows
    return {"s": seconds, "tables": dict(tables)}


def summary(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """The first, median and largest time of `runs`, and the median of each table's work."""
    totals = [r["s"] for r in runs]
    names = sorted({t for r in runs for t in r["tables"]})
    per_table: dict[str, dict[str, float]] = {}
    for name in names:
        rows = [r["tables"].get(name, {}) for r in runs]
        per_table[name] = {
            "opens_per_call": statistics.median(r.get("opens", 0) for r in rows),
            "open_median_ms": ms(statistics.median(r.get("open_s", 0.0) for r in rows)),
            "scan_median_ms": ms(statistics.median(r.get("scan_s", 0.0) for r in rows)),
            "rows_median": statistics.median(r.get("rows", 0) for r in rows),
        }
    in_tables = [
        sum(t["open_s"] + t["scan_s"] for t in r["tables"].values()) for r in runs
    ]
    return {
        "calls": len(runs),
        "first_ms": ms(totals[0]),
        "median_ms": ms(statistics.median(totals)),
        "max_ms": ms(max(totals)),
        "sum_ms": ms(sum(totals)),
        "in_tables_median_ms": ms(statistics.median(in_tables)),
        "tables": per_table,
    }


def machine() -> str:
    brand = subprocess.run(
        ["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True
    ).stdout.strip()
    return f"{brand or platform.machine()}, {os.cpu_count()} cores, {platform.system()} {platform.release()}"


def main(release: str, method: str, label: str) -> None:
    result: dict[str, Any] = {
        "label": label,
        "date": datetime.now(UTC).date().isoformat(),
        "machine": machine(),
        "python": platform.python_version(),
        "release": release,
        "load_average_before": [round(v, 2) for v in os.getloadavg()],
    }
    start = time.perf_counter()
    background = hw.load(release, method=method)
    result["load_s"] = round(time.perf_counter() - start, 2)
    held = background.release
    assert held is not None
    ids = sorted(held.table("processes").column("id").to_pylist())
    sample = random.Random(0).sample(ids, min(SINGLE + BATCHES * BATCH, len(ids)))
    split = min(SINGLE, len(sample) // 2)  # a toy release is small
    singles, rest = sample[:split], sample[split:]
    batches = [rest[i : i + BATCH] for i in range(0, len(rest), BATCH)]
    sizes = {"processes": len(ids), "flows": held.table("flows").num_rows}
    sizes["exchanges"] = held.table("exchanges").num_rows
    sizes["exchanges_row_groups"] = pq.ParquetFile(held.path / "exchanges.parquet").num_row_groups
    result["rows"] = sizes

    ds.dataset = watching  # pyright: ignore[reportAttributeAccessIssue]
    try:
        result["single"] = summary([call(background, [i]) for i in singles])
        result["batch"] = summary([call(background, batch) for batch in batches])
        result["batch"]["ids_per_call"] = BATCH
        result["batch"]["per_id_median_ms"] = round(result["batch"]["median_ms"] / BATCH, 3)
        result["repeat"] = summary([call(background, [i]) for i in singles])
    finally:
        ds.dataset = _real_dataset  # pyright: ignore[reportAttributeAccessIssue]

    flows = getattr(internals(background), "_flows", {})
    result["flow_mapping"] = {"entries": len(flows), "dict_bytes": sys.getsizeof(flows)}
    result["load_average_after"] = [round(v, 2) for v in os.getloadavg()]
    out = HERE / "results" / f"processes_read_{label}_results.json"
    out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


def layout(release: str) -> None:
    """How the exchanges table is stored: row groups, the order of their process ids, and the
    size of each decoded."""
    source = open_source(release)
    assert isinstance(source, Release)
    file = pq.ParquetFile(source.path / "exchanges.parquet")
    meta = file.metadata
    names = [meta.schema.column(i).name for i in range(meta.num_columns)]
    key = names.index("process_id")
    groups = range(meta.num_row_groups)
    ranges = [
        (meta.row_group(i).column(key).statistics.min, meta.row_group(i).column(key).statistics.max)
        for i in groups
    ]
    decoded: list[float] = []
    largest_columns: dict[str, float] = {}
    for i in groups:
        table = file.read_row_group(i)
        decoded.append(round(table.nbytes / 1e6, 1))
        if i == 0:
            sizes = sorted(((table.column(n).nbytes, n) for n in table.column_names), reverse=True)
            largest_columns = {n: round(b / 1e6, 1) for b, n in sizes[:3]}
    result = {
        "rows": meta.num_rows,
        "columns": meta.num_columns,
        "row_groups": meta.num_row_groups,
        "rows_per_group": [meta.row_group(i).num_rows for i in groups],
        "process_ids_in_order_and_apart": all(
            ranges[i][1] <= ranges[i + 1][0] for i in range(len(ranges) - 1)
        ),
        "decoded_mb_per_group": decoded,
        "decoded_mb_total": round(sum(decoded), 1),
        "decoded_mb_largest_columns_of_group_0": largest_columns,
    }
    out = HERE / "results" / "processes_read_layout_results.json"
    out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "layout":
        layout(sys.argv[2])
    elif len(sys.argv) == 4:
        main(sys.argv[1], sys.argv[2], sys.argv[3])
    else:
        sys.exit(__doc__)
