"""Import of a folder of ecoSpold1 files: the time to read it and to run the shared stages, best
of a few runs.

    uv run python bench/import_ecospold1.py <results.json> [runs]

Reads `$ECOSPOLD1_DIR` (a folder of ecoSpold1 `.xml` files, such as BAFU:2026's `datasets/`) as
source `bafu`, version `2026`, with the parse using every CPU. Nothing is written but the JSON,
which holds timings and counts only, not the data.

The runs are added to those already in the JSON. The load average before each run is recorded:
run it on a quiet machine.
"""

import json
import os
import platform
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

from headwater.io.ecospold1 import read
from headwater.io.inventory import link_inventory
from headwater.io.source import InventoryOptions

OPTIONS = InventoryOptions(version="2026", source="bafu", licence="BAFU, cite")


def run(folder: Path) -> dict[str, object]:
    load = os.getloadavg()[0]
    start = time.perf_counter()
    source = read(folder, OPTIONS)
    middle = time.perf_counter()
    inventory = link_inventory(source)
    end = time.perf_counter()
    return {
        "load_before": round(load, 2),
        "read_s": round(middle - start, 2),
        "stages_s": round(end - middle, 2),
        "total_s": round(end - start, 2),
        "processes": source.processes.num_rows,
        "lines": source.lines.num_rows,
        "flows": len(source.flows),
        "open_issues": dict(Counter(p.code for p in inventory.problems)),
    }


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    out = Path(sys.argv[1])
    runs = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    folder = Path(os.environ["ECOSPOLD1_DIR"])
    found = []
    for i in range(runs):
        result = run(folder)
        found.append(result)
        print(f"run {i + 1}: {result['read_s']} s read, {result['stages_s']} s stages", flush=True)
    results = json.loads(out.read_text()) if out.exists() else {}
    found = [*results.get("runs", []), *found]
    best = min(found, key=lambda r: r["total_s"])  # pyright: ignore[reportArgumentType, reportUnknownLambdaType]
    results = {
        "machine": f"{platform.machine()}, {platform.system()} {platform.release()}",
        "cpus": os.cpu_count(),
        "python": platform.python_version(),
        "pyarrow": version("pyarrow"),
        "lxml": version("lxml"),
        "date": datetime.now(UTC).date().isoformat(),
        "runs": found,
        "best": best,
    }
    out.write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps(best))


if __name__ == "__main__":
    main()
