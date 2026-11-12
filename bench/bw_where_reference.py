# /// script
# requires-python = "==3.12.*"
# dependencies = [
#     "bw2analyzer==0.11.8",
#     "bw2data==4.5.4",
#     "bw2calc==2.3",
#     "bw2io==0.9.14",
#     "peewee==3.19.0",
#     "bw2parameters==1.1.0",
#     "bw-processing==1.0",
#     "matrix-utils==0.6.2",
#     "numpy==1.26.4",
#     "scipy==1.17.0",
#     "pandas==2.3.3",
#     "openpyxl==3.1.5",
#     "stats-arrays==1.0",
#     "ecoinvent-interface==3.1",
#     "lxml==6.0.2",
#     "pyarrow==23.0.0",
#     "platformdirs==4.5.1",
#     "deepdiff==7.0.1",
#     "pydantic==2.12.5",
#     "randonneur==0.7.2",
#     "rapidfuzz==3.14.3",
#     "structlog==25.5.0",
#     "tqdm==4.67.1",
#     "xlsxwriter==3.2.9",
#     "xlrd==2.0.2",
#     "voluptuous==0.16.0",
#     "wrapt==2.0.1",
#     "fsspec==2026.1.0",
#     "morefs==0.2.2",
#     "fs==2.4.16",
#     "bw-migrations==0.2",
#     "asteval==1.0.8",
#     "pint==0.25.2",
#     "mrio-common-metadata==0.2.1",
#     "blinker==1.9.0",
#     "snowflake-id==1.0.2",
#     "tabulate==0.9.0",
#     "requests==2.32.5",
# ]
# ///
"""Brightway side of bench/where_parity.py: its contribution analysis on the 10 products.

Reads `$HEADWATER_WORK/where-parity/products.json`, written by `where_parity.py prepare`, and
writes to the same folder:

- `bw_top.parquet`: the 12 largest contributions of each product and indicator, keyed by
  headwater ids, from two solves of Brightway's matrices: bw2calc's own (`source` bw2calc), and
  an accurate one (`source` accurate)
- `bw_runs.json`: each product's score and the componentwise residual of each solve

bw2calc's contributions are bw2analyzer's `ContributionAnalysis.top_processes` of
`lca.characterized_inventory`: each process's direct emissions times how much it runs, sorted by
size. The accurate solve factorizes Brightway's technosphere matrix in float64 with SciPy's
SuperLU and refines once, so that a difference left after it is a difference of the matrices,
not of the solve (docs/decisions.md, D21).

The versions are T8's, from the Science team's lock file, pinned in the header above, so
`uv run` builds that environment:

    BRIGHTWAY2_DIR=$HEADWATER_WORK/where-parity/brightway PILOT_BW_PROJECT=... \
        uv run bench/bw_where_reference.py

BRIGHTWAY2_DIR must be a copy under HEADWATER_WORK, never a Brightway folder in use: bw2data
writes to the folder it is given.
"""

import hashlib
import json
import os
import platform
import sys
from pathlib import Path

# The Brightway project in BRIGHTWAY2_DIR: ecoinvent 3.12 cut-off and the 3.12 workbook's methods.
PROJECT = os.environ.get("PILOT_BW_PROJECT", "")
DATABASE = "ecoinvent-3.12-cutoff"
PREFIX = "ecoinvent:3.12:cutoff:"
METHODS = {
    "climate-change": (
        "ecoinvent-3.12",
        "EF v3.1",
        "climate change",
        "global warming potential (GWP100)",
    ),
    "land-use": ("ecoinvent-3.12", "EF v3.1", "land use", "soil quality index"),
}
LISTED = 12


def environment() -> tuple[Path, Path]:
    """Check the folders before bw2data is imported: it writes to whatever folder it is given."""
    work = Path(os.environ["HEADWATER_WORK"]).expanduser().resolve()
    bw_dir = os.environ.get("BRIGHTWAY2_DIR")
    if not bw_dir:
        sys.exit("set BRIGHTWAY2_DIR to a copy of a Brightway folder under HEADWATER_WORK")
    brightway = Path(bw_dir).expanduser().resolve()
    if not brightway.is_relative_to(work) or not brightway.is_dir():
        sys.exit(f"BRIGHTWAY2_DIR {brightway} is not a folder under HEADWATER_WORK {work}")
    if not PROJECT:
        sys.exit("set PILOT_BW_PROJECT to the name of the Brightway project in BRIGHTWAY2_DIR")
    return work / "where-parity", brightway


def main() -> None:
    out, brightway = environment()
    products = json.loads((out / "products.json").read_text())

    import bw2analyzer as ba
    import bw2calc as bc
    import bw2data as bd
    import numpy as np
    import pyarrow as pa
    import pyarrow.parquet as pq
    import scipy
    from scipy.sparse.linalg import splu

    bd.projects.set_current(PROJECT)
    if not Path(bd.projects.dir).resolve().is_relative_to(brightway):
        sys.exit(f"Brightway project folder {bd.projects.dir} is not under {brightway}")
    for indicator, method in METHODS.items():
        if method not in bd.methods:
            sys.exit(f"no method {method} for {indicator} in project {PROJECT}")

    # Brightway's code for an ecoinvent dataset is md5(activity uuid + product uuid).
    headwater_id: dict[int, str] = {}
    for node in bd.Database(DATABASE):
        code = hashlib.md5((node["activity"] + node["flow"]).encode("utf-8")).hexdigest()
        if code != node["code"]:
            sys.exit(f"code of node {node.id} is not md5(activity + product)")
        headwater_id[node.id] = f"{PREFIX}{node['activity']}_{node['flow']}"
    nodes = [
        bd.get_node(
            database=DATABASE,
            code=hashlib.md5((p["activity"] + p["product"]).encode("utf-8")).hexdigest(),
        )
        for p in products
    ]

    lca = bc.LCA({nodes[0].id: 1}, METHODS["climate-change"])
    lca.lci(factorize=True)
    lca.lcia()
    A = lca.technosphere_matrix.tocsc()
    A64 = A.astype(np.float64)
    lu = splu(A64)
    column_of = {node_id: col for node_id, col in lca.dicts.activity.items()}
    row_of = {node_id: row for node_id, row in lca.dicts.product.items()}
    process_of_col = {col: headwater_id[node_id] for node_id, col in lca.dicts.activity.items()}

    def residual(s: np.ndarray, f: np.ndarray) -> float:
        """Largest backward error of A s = f, row by row: |f - A s| / (|A| |s| + |f|). A row
        smaller than 1e-12 of the largest is measured against 1e-12 of the largest: rows of
        processes that hardly run hold rounding noise of about 1e-17, and noise against noise
        is 1 whatever the solve."""
        size = abs(A64) @ np.abs(s) + np.abs(f)
        return float(np.max(np.abs(f - A64 @ s) / np.maximum(size, 1e-12 * size.max())))

    analysis = ba.ContributionAnalysis()
    rows: dict[str, list[object]] = {
        "product_id": [],
        "indicator": [],
        "source": [],
        "rank": [],
        "process_id": [],
        "value": [],
    }
    runs: dict[str, dict[str, dict[str, float]]] = {}

    def keep(product_id: str, indicator: str, source: str, top: np.ndarray) -> None:
        for rank, (value, col) in enumerate(top):
            rows["product_id"].append(product_id)
            rows["indicator"].append(indicator)
            rows["source"].append(source)
            rows["rank"].append(rank)
            rows["process_id"].append(process_of_col[int(col)])
            rows["value"].append(float(value))

    for indicator, method in METHODS.items():
        lca.switch_method(method)
        # Each process's direct emissions per run, characterised: the column sums of C B.
        h = np.asarray((lca.characterization_matrix @ lca.biosphere_matrix).sum(axis=0)).ravel()
        for product, node in zip(products, nodes, strict=True):
            product_id = product["process_id"]
            row, col = row_of[node.id], column_of[node.id]
            if abs(abs(A[row, col]) - 1) > 1e-6:
                sys.exit(f"{product_id}: a reference amount that is not 1")
            # One unit of the product, or 1 kg treated: a treatment's reference is -1.
            sign = float(np.sign(A[row, col]))
            lca.redo_lcia({node.id: sign})
            keep(
                product_id,
                indicator,
                "bw2calc",
                analysis.top_processes(lca.characterized_inventory, limit=LISTED),
            )
            f = np.asarray(lca.demand_array, dtype=np.float64)
            s = lu.solve(f)
            s += lu.solve(f - A64 @ s)
            parts = h * s
            order = np.argsort(-np.abs(parts), kind="stable")[:LISTED]
            keep(product_id, indicator, "accurate", np.column_stack([parts[order], order]))
            runs.setdefault(product_id, {})[indicator] = {
                "score_bw2calc": float(lca.score),
                "score_accurate": float(parts.sum()),
                "residual_bw2calc": residual(np.asarray(lca.supply_array, dtype=np.float64), f),
                "residual_accurate": residual(s, f),
            }

    pq.write_table(pa.table(rows), out / "bw_top.parquet")
    info = {
        "products": len(products),
        "technosphere_dtype": str(A.dtype),
        "versions": {
            "python": platform.python_version(),
            "bw2data": _version(bd.__version__),
            "bw2calc": _version(bc.__version__),
            "bw2analyzer": _version(ba.__version__),
            "numpy": np.__version__,
            "scipy": scipy.__version__,
            "machine": f"{platform.machine()} {platform.system()} {platform.release()}",
        },
    }
    (out / "bw_runs.json").write_text(json.dumps(runs, indent=1) + "\n")
    (out / "bw_info.json").write_text(json.dumps(info, indent=1) + "\n")
    print(json.dumps(info, indent=1))


def _version(found: object) -> str:
    """`4.5.4` for a version that a package gives as a tuple, such as (4, 5, 4)."""
    return ".".join(map(str, found)) if isinstance(found, tuple) else str(found)


if __name__ == "__main__":
    main()
