"""Structure of the supply graph of a release: its strongly connected groups.

    uv run python bench/supply_graph.py <release folder>

A group is a set of processes that are all, directly or indirectly, suppliers of each other.
Reads the technosphere of the release (A of headwater, without the recyclable lines, which have
no supplier), and writes to
bench/results/supply_graph_<release name>_results.json, for example
`supply_graph_ecoinvent-3.12-cutoff_results.json`: counts only. `calc_lab.py` made the same
count on 3.10, on a DuckDB copy (docs/appendix/benchmarks.md, "Calculation core").
"""

import json
import os
import platform
import sys
import time
from pathlib import Path

import numpy as np
import scipy
from scipy import sparse
from scipy.sparse.csgraph import connected_components

from headwater.query._release import _TECHNOSPHERE, _Query
from headwater.release import Release

HERE = Path(__file__).resolve().parent


def main() -> None:
    path = Path(sys.argv[1])
    release = Release.open(path)
    query = _Query(release, check=False)
    try:
        index = query.index()
        a = query.arrays(_TECHNOSPHERE, ("row", "col", "value"))
    finally:
        query.close()
    n = len(index.ids)
    graph = sparse.csr_array(
        (np.ones(len(a["row"])), (a["row"].astype(np.int64), a["col"].astype(np.int64))),
        shape=(n, n),
    )
    groups, label = connected_components(graph, directed=True, connection="strong")
    sizes = np.sort(np.bincount(label))[::-1]
    alone = int((sizes == 1).sum())
    result = {
        "scope": {
            "machine": f"{platform.machine()} {platform.system()} {platform.release()}",
            "python": platform.python_version(),
            "scipy": scipy.__version__,
            "release": path.name,
            "date": time.strftime("%Y-%m-%d"),
            "load_average": [round(v, 2) for v in os.getloadavg()],
        },
        "processes": n,
        "groups": int(groups),
        "largest": [int(s) for s in sizes[:5]],
        "largest_share": round(float(sizes[0]) / n, 4),
        "processes_in_no_loop": alone,
    }
    out = HERE / "results" / f"supply_graph_{release.info.name}_results.json"
    out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
