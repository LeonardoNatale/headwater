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
"""Brightway side of bench/monte_carlo.py: 200 Monte Carlo draws of ecoinvent 3.12 in Brightway.

Reads `$HEADWATER_WORK/mc-parity/products.json`, written by `monte_carlo.py prepare`, and the EF
3.1 method folder in `MC_METHOD` for the indicators, and writes to the same folder:

- `bw_samples.npy`: the score of each product for each EF 3.1 indicator in each draw
- `bw_scores.npy`: the same with no draw
- `bw_info.json`: products, indicators, counts, residuals and timings

Brightway draws its technosphere and biosphere matrices with bw2calc's `use_distributions` and
`next` on each matrix (stats_arrays, each line on its own). Its characterisation factors stay
fixed, as headwater's do: they are read from each method's data with no draw, and the factors
that carry a distribution are counted. Each draw is solved with an accurate scaled SuperLU
(rows then columns scaled to a largest entry of 1, minimum degree ordering on A + Aᵀ, no
pivoting, one refinement, pivoting if a row is still above 1e-12), not with bw2calc's own
solver: bw2calc took 86 s to factorize 3.12 (bench/results/bw_reference_3.12_results.json), and
parity means equal matrices and accurate solves (docs/decisions.md, D21).

    BRIGHTWAY2_DIR=$HEADWATER_WORK/mc-parity/brightway PILOT_BW_PROJECT=... MC_METHOD=... \
        uv run bench/bw_monte_carlo.py

BRIGHTWAY2_DIR must be a copy under HEADWATER_WORK, never a Brightway folder in use: bw2data
writes to the folder it is given.
"""

import hashlib
import json
import os
import platform
import sys
import time
from pathlib import Path

# The Brightway project in BRIGHTWAY2_DIR: ecoinvent 3.12 cut-off and the 3.12 workbook's methods.
PROJECT = os.environ.get("PILOT_BW_PROJECT", "")
DATABASE = "ecoinvent-3.12-cutoff"
ITERATIONS = 200
SEED = 42
TOLERANCE = 1e-12


def environment() -> tuple[Path, Path, Path]:
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
    return work / "mc-parity", brightway, Path(os.environ["MC_METHOD"]).expanduser()


def indicators(method_folder: Path) -> list[tuple[str, str]]:
    """(id, name) of each indicator of the method's method.yaml, in order."""
    found: list[tuple[str, str]] = []
    current: str | None = None
    for line in (method_folder / "method.yaml").read_text().splitlines():
        text = line.strip()
        if text.startswith("- id:"):
            current = text.removeprefix("- id:").strip()
        elif text.startswith("name:") and current is not None:
            found.append((current, text.removeprefix("name:").strip().strip("'\"")))
            current = None
    return found


def main() -> None:
    out, brightway, method_folder = environment()
    products = json.loads((out / "products.json").read_text())
    wanted = indicators(method_folder)

    import bw2calc as bc
    import bw2data as bd
    import numpy as np
    import scipy
    from scipy import sparse
    from scipy.sparse.linalg import splu

    bd.projects.set_current(PROJECT)
    if not Path(bd.projects.dir).resolve().is_relative_to(brightway):
        sys.exit(f"Brightway project folder {bd.projects.dir} is not under {brightway}")
    methods = []
    for indicator, name in wanted:
        found = [m for m in bd.methods if m[:3] == ("ecoinvent-3.12", "EF v3.1", name)]
        if len(found) != 1:
            sys.exit(f"{indicator}: {len(found)} Brightway methods named {name!r}")
        methods.append(found[0])
    nodes = [
        bd.get_node(
            database=DATABASE,
            code=hashlib.md5((p["activity"] + p["product"]).encode("utf-8")).hexdigest(),
        )
        for p in products
    ]

    started = time.perf_counter()
    lca = bc.LCA({nodes[0].id: 1}, methods[0], use_distributions=True, seed_override=SEED)
    lca.load_lci_data()
    static = bc.LCA({nodes[0].id: 1}, methods[0])
    static.load_lci_data()
    if dict(static.dicts.product) != dict(lca.dicts.product) or dict(
        static.dicts.biosphere
    ) != dict(lca.dicts.biosphere):
        sys.exit("the static and the drawn LCA number their rows differently")
    load_s = time.perf_counter() - started

    # Characterisation factors, fixed: each method's own amounts, by row of the biosphere.
    rows_of = dict(lca.dicts.biosphere)
    C = np.zeros((len(rows_of), len(methods)))
    with_distribution = 0
    for j, method in enumerate(methods):
        for flow, cf in bd.Method(method).load():
            flow_id = flow if isinstance(flow, int) else bd.get_id(flow)
            amount = cf["amount"] if isinstance(cf, dict) else cf
            if isinstance(cf, dict) and cf.get("uncertainty type", 0) > 1:
                with_distribution += 1
            if flow_id in rows_of:
                C[rows_of[flow_id], j] += amount

    product_rows = []
    signs = []
    A0 = static.technosphere_matrix.tocsc()
    for product, node in zip(products, nodes, strict=True):
        row, col = lca.dicts.product[node.id], lca.dicts.activity[node.id]
        if abs(abs(A0[row, col]) - 1) > 1e-6:
            sys.exit(f"{product['process_id']}: a reference amount that is not 1")
        product_rows.append(row)
        signs.append(float(np.sign(A0[row, col])))  # 1 kg treated for a treatment: -1
    product_rows = np.array(product_rows)
    signs = np.array(signs)

    def scale(A: sparse.csc_array) -> tuple[np.ndarray, np.ndarray, sparse.csc_array]:
        r = 1.0 / abs(A).max(axis=1).toarray()
        S = sparse.csc_array(sparse.diags_array(r) @ A)
        c = 1.0 / abs(S).max(axis=0).toarray()
        return r, c, sparse.csc_array(S @ sparse.diags_array(c))

    def solve(A: sparse.csc_array, H: np.ndarray) -> tuple[np.ndarray, float, bool]:
        """x with Aᵀ x = H, column by column, and the largest componentwise residual."""
        A = sparse.csc_array(A, dtype=np.float64)
        A.sum_duplicates()
        r, c, S = scale(A)
        At = A.T.tocsr()
        size_of = abs(At)
        for pivot in (0.0, 1.0):
            lu = splu(S, permc_spec="MMD_AT_PLUS_A", diag_pivot_thresh=pivot)

            def apply(v: np.ndarray) -> np.ndarray:
                return r[:, None] * lu.solve(c[:, None] * v, trans="T")

            X = apply(H)

            def error(X: np.ndarray) -> np.ndarray:
                size = size_of @ np.abs(X) + np.abs(H)
                gap = np.abs(H - At @ X)
                with np.errstate(divide="ignore", invalid="ignore"):
                    e = np.where(size > 0, gap / size, np.where(gap == 0, 0.0, np.inf))
                return e

            e = error(X)
            if e.max() > TOLERANCE:
                X = X + apply(H - At @ X)
                e = error(X)
            if e.max() <= TOLERANCE:
                return X, float(e.max()), pivot > 0
        return X, float(e.max()), True

    def score(A: sparse.csc_array, B: sparse.csr_array) -> tuple[np.ndarray, float, bool]:
        H = np.asarray(B.T @ C)
        X, residual, pivoted = solve(A, H)
        return X[product_rows, :] * signs[:, None], residual, pivoted

    scores, residual0, _ = score(A0, static.biosphere_matrix.tocsr())
    samples = np.empty((len(products), len(methods), ITERATIONS))
    residuals, pivoted, seconds = [], 0, []
    previous = None
    for k in range(ITERATIONS):
        t = time.perf_counter()
        next(lca.technosphere_mm)
        next(lca.biosphere_mm)
        A = lca.technosphere_mm.matrix.tocsc()
        B = lca.biosphere_mm.matrix.tocsr()
        if previous is not None and np.array_equal(previous, A.data):
            sys.exit("next() did not draw a new technosphere matrix")
        previous = A.data.copy()
        samples[:, :, k], residual, was_pivoted = score(A, B)
        residuals.append(residual)
        pivoted += int(was_pivoted)
        seconds.append(time.perf_counter() - t)
        print(k, round(seconds[-1], 2), f"{residual:.2e}", flush=True)

    np.save(out / "bw_samples.npy", samples)
    np.save(out / "bw_scores.npy", scores)
    info = {
        "items": [p["process_id"] for p in products],
        "indicators": [i for i, _ in wanted],
        "methods": [list(m) for m in methods],
        "iterations": ITERATIONS,
        "seed_override": SEED,
        "factors_with_a_distribution": with_distribution,
        "residual_static": residual0,
        "residual_max": max(residuals),
        "pivoted": pivoted,
        "load_seconds": round(load_s, 1),
        "median_seconds_per_draw": round(float(np.median(seconds)), 2),
        "versions": {
            "python": platform.python_version(),
            "bw2data": _version(bd.__version__),
            "bw2calc": _version(bc.__version__),
            "numpy": np.__version__,
            "scipy": scipy.__version__,
            "machine": f"{platform.machine()} {platform.system()} {platform.release()}",
        },
    }
    (out / "bw_info.json").write_text(json.dumps(info, indent=1) + "\n")
    print(json.dumps(info, indent=1))


def _version(found: object) -> str:
    """`4.5.4` for a version that a package gives as a tuple, such as (4, 5, 4)."""
    return ".".join(map(str, found)) if isinstance(found, tuple) else str(found)


if __name__ == "__main__":
    main()
