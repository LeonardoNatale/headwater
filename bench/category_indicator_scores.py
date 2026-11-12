"""EF v3.1 imported with its category indicators scores every product as the method before them.

    uv run python bench/category_indicator_scores.py <method folder> <indicators release> <results.json>

`<method folder>` is EF v3.1 imported from `LCIA Implementation 3.12.xlsx` by `headwater method
import`, which keeps each category's indicator name (D41). `<indicators release>` is an indicators
release of ecoinvent 3.12 cut-off solved with the method before them, `name@hash` in
`HEADWATER_HOME`, for example `ecoinvent-3.12-cutoff.EF-3.1@21c61d3ac200`. The script loads its
source release with the new method, solves it, and compares every product's score for every
indicator with the stored one.

The JSON holds hashes and counts only.
"""

import json
import platform
import sys
import time
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

import numpy as np

import headwater as hw
from headwater.query._internals import internals
from headwater.release import find, method_hash
from headwater.release.indicators import read_indicators
from headwater.study import read_method


def main(folder: Path, stored_name: str, out: Path) -> None:
    method = read_method(folder)
    stored = read_indicators(find(stored_name))
    parent = stored.release.parent
    assert parent is not None
    start = time.perf_counter()
    bg = hw.load(f"{parent.name}@{parent.hash[:12]}", method=folder)
    seconds = time.perf_counter() - start
    inner = internals(bg)
    rows = np.array([inner.index[p] for p in stored.product_ids])
    sign = inner.solved.block.reference_sign[rows]
    new = np.column_stack([inner.solved.x[i].values[rows] * sign for i in stored.indicators])
    old = stored.scores
    differ = new != old
    scale = np.maximum(np.abs(new), np.abs(old))
    relative = np.where(differ, np.abs(new - old) / np.where(scale > 0, scale, 1.0), 0.0)
    filled = [i.category_indicator for i in method.indicators if i.category_indicator]
    result = {
        "method": method.id,
        "method_hash": method_hash(method),
        "stored_method_hash": stored.method_hash,
        "release": f"{parent.name}@{parent.hash[:12]}",
        "indicators": len(method.indicators),
        "category_indicators_filled": len(filled),
        "category_indicators_distinct": len(set(filled)),
        "products": len(stored.product_ids),
        "scores_compared": int(new.size),
        "scores_equal": int((~differ).sum()),
        "largest_relative_difference": float(relative.max()),
        "load_seconds": round(seconds, 1),
        "machine": f"{platform.machine()}, {platform.system()} {platform.release()}",
        "date": datetime.now(UTC).date().isoformat(),
        "python": platform.python_version(),
        "versions": {"headwater": version("headwater"), "numpy": np.__version__},
    }
    out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main(Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]))
