"""Numerics on the real release: solver settings (X5), condition, tolerance, exact zeros, copies.

    export HEADWATER_WORK=/path/to/scratch
    uv run python bench/parity_numerics.py <release folder>

Writes the 3-flow climate method, built from the release's flows by name, to
$HEADWATER_WORK/methods/climate-3-flows-<version>/, and results to
bench/results/parity_numerics_<release name>_results.json, for example
`parity_numerics_ecoinvent-3.12-cutoff_results.json`: counts, timings and statistics of errors
only. `parity_numerics_results.json` is the run on 3.10 of 11 October 2026.
"""

import json
import os
import platform
import sys
import time
from pathlib import Path

import numpy as np
import pyarrow
import scipy
from scipy import sparse
from scipy.sparse.linalg import LinearOperator, onenormest, splu

import headwater as hw
from headwater import calc
from headwater.calc.arrays import as_matrix
from headwater.calc.solve import _scale, backward_error, condition_estimate, structural_zeros
from headwater.query._internals import internals
from headwater.query._release import _TECHNOSPHERE, _Query
from headwater.release import Release

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results" / "parity_numerics_results.json"
"""Set by `main` from the release's name."""
FACTORS = {"Carbon dioxide, fossil": 1.0, "Methane, fossil": 29.8, "Dinitrogen monoxide": 273.0}

results: dict[str, object] = {}


def best(fn, repeat: int = 20) -> float:
    """Best time of `repeat` runs, in ms."""
    fastest = float("inf")
    for _ in range(repeat):
        start = time.perf_counter()
        fn()
        fastest = min(fastest, time.perf_counter() - start)
    return round(fastest * 1e3, 3)


def save() -> None:
    RESULTS.write_text(json.dumps(results, indent=2) + "\n")


def method_folder(release: Release, work: Path) -> Path:
    folder = work / "methods" / f"climate-3-flows-{release.info.version}"
    folder.mkdir(parents=True, exist_ok=True)
    flows = release.table("flows").to_pylist()
    rows = "".join(
        f"climate,{f['id']},,{FACTORS[f['name']]}\n"
        for f in flows
        if f["kind"] == "elementary" and f["name"] in FACTORS
    )
    (folder / "method.yaml").write_text(
        "id: climate-3-flows\nversion: \"1\"\n"
        f"flow_list: {release.info.flow_list}\n"
        "indicators:\n  - id: climate\n    name: Climate change, 3 flows\n    unit: kg CO2-eq\n"
    )
    (folder / "factors.csv").write_text("indicator,flow,location,factor\n" + rows)
    return folder


def errors(matrix, y, b) -> dict[str, float | int]:
    e = backward_error(matrix, y, b)
    return {
        "max": float(e.max()),
        "p99": float(np.quantile(e, 0.99)),
        "above_1e-14": int((e > 1e-14).sum()),
        "above_1e-12": int((e > 1e-12).sum()),
    }


def main() -> None:
    global RESULTS
    path = Path(sys.argv[1])
    work = Path(os.environ["HEADWATER_WORK"]).expanduser()
    release = Release.open(path)
    RESULTS = HERE / "results" / f"parity_numerics_{release.info.name}_results.json"
    results["scope"] = {
        "machine": f"{platform.machine()} {platform.system()} {platform.release()}",
        "python": platform.python_version(),
        "numpy": np.__version__,
        "scipy": scipy.__version__,
        "pyarrow": pyarrow.__version__,
        "release": path.name,
        "date": time.strftime("%Y-%m-%d"),
        "load_average_at_start": [round(v, 2) for v in os.getloadavg()],
    }

    # --- load --------------------------------------------------------------------------------
    folder = method_folder(release, work)
    start = time.perf_counter()
    bg = hw.load(path, method=folder)
    results["load_s"] = round(time.perf_counter() - start, 2)
    solved = internals(bg).solved
    A = solved.factorization.matrix
    h = solved.h["climate"]
    n = A.shape[0]
    fact = solved.factorization
    results["matrix"] = {"processes": n, "non_zeros": int(A.nnz), "method_flows": len(bg.method.factors)}
    results["headwater"] = {
        "path": [s.value for s in solved.x["climate"].path],
        "residual": solved.x["climate"].residual,
        "condition": fact.condition,
        "pivoted": fact.pivoted,
    }
    print(results["load_s"], results["headwater"], flush=True)
    save()

    # --- X5: solver settings -----------------------------------------------------------------
    row_scale, col_scale, scaled = _scale(A)
    settings = []
    zero = structural_zeros(fact.magnitude.T, h)
    for ordering in ("COLAMD", "MMD_AT_PLUS_A"):
        for thresh in (1.0, 0.0):
            start = time.perf_counter()
            lu = splu(scaled, permc_spec=ordering, diag_pivot_thresh=thresh)
            took = time.perf_counter() - start

            def solve(v, lu=lu):
                return row_scale * lu.solve(col_scale * v, trans="T")

            y = solve(h)
            raw = errors(A.T, y, h)
            y[zero] = 0.0
            y = y + solve(h - A.T @ y)
            y[zero] = 0.0
            settings.append(
                {
                    "ordering": ordering,
                    "diag_pivot_thresh": thresh,
                    "scaled": True,
                    "factorize_s": round(took, 2),
                    "lu_non_zeros": int(lu.L.nnz + lu.U.nnz),
                    "rows_swapped": bool(not np.array_equal(lu.perm_r, lu.perm_c)),
                    "solve_ms": best(lambda: solve(h)),
                    "raw": raw,
                    "zeros_exact_and_refined": errors(A.T, y, h),
                }
            )
            print(settings[-1], flush=True)
    start = time.perf_counter()
    lu = splu(A, permc_spec="COLAMD", diag_pivot_thresh=1.0)
    took = time.perf_counter() - start
    y = lu.solve(h, trans="T")
    settings.append(
        {
            "ordering": "COLAMD",
            "diag_pivot_thresh": 1.0,
            "scaled": False,
            "note": "SciPy defaults, as bw2calc's factorized solver",
            "factorize_s": round(took, 2),
            "lu_non_zeros": int(lu.L.nnz + lu.U.nnz),
            "raw": errors(A.T, y, h),
        }
    )
    print(settings[-1], flush=True)
    del lu
    start = time.perf_counter()
    calc.factorize(A)
    results["factorize_s"] = round(time.perf_counter() - start, 2)
    results["solver_settings"] = settings
    save()

    # --- condition ---------------------------------------------------------------------------
    unit = fact.solve(np.ones(n), transposed=True)
    results["condition"] = {
        "componentwise": fact.condition,
        "componentwise_ms": best(lambda: condition_estimate(fact, unit), 5),
        "loop_gain_check_min_burden": float(np.min(unit * solved.block.reference_sign)),
    }
    np.random.seed(0)
    for label, matrix, lu in (
        ("normwise_scaled", scaled, fact.lu),
        ("normwise_unscaled", A, splu(A, permc_spec="MMD_AT_PLUS_A", diag_pivot_thresh=1.0)),
    ):
        op = LinearOperator(
            (n, n),
            matvec=lambda v, lu=lu: lu.solve(np.asarray(v, dtype=float).ravel()),
            rmatvec=lambda v, lu=lu: lu.solve(np.asarray(v, dtype=float).ravel(), trans="T"),
            dtype=float,
        )
        results["condition"][label] = float(abs(matrix).sum(axis=0).max() * onenormest(op))
    print(results["condition"], flush=True)
    save()

    # --- tolerance: every forward solve, and the transposed one ------------------------------
    worst = np.empty(n)
    start = time.perf_counter()
    block = 500
    magnitude = fact.magnitude
    for first in range(0, n, block):
        cols = np.arange(first, min(first + block, n))
        F = np.zeros((n, cols.size))
        F[cols, np.arange(cols.size)] = 1.0
        S = fact.solve(F)
        residual = np.abs(F - A @ S)
        size = magnitude @ np.abs(S) + np.abs(F)
        with np.errstate(divide="ignore", invalid="ignore"):
            e = np.where(size > 0, residual / size, 0.0)
        worst[cols] = e.max(axis=0)
    results["forward_solves"] = {
        "products": n,
        "seconds": round(time.perf_counter() - start, 1),
        "largest_error": float(worst.max()),
        "p99_of_largest": float(np.quantile(worst, 0.99)),
        "solves_above_1e-14": int((worst > 1e-14).sum()),
        "solves_above_1e-12": int((worst > 1e-12).sum()),
    }
    results["transposed_solve"] = errors(A.T, fact.solve(h, transposed=True), h)
    print(results["forward_solves"], results["transposed_solve"], flush=True)
    save()

    # --- exact zeros and cancellation --------------------------------------------------------
    x = solved.x["climate"].values
    names = solved.block.names
    sign = solved.block.reference_sign
    gross = sign * fact.solve(np.abs(h), transposed=True)
    net = np.abs(x)
    nonzero = ~zero
    with np.errstate(divide="ignore", invalid="ignore"):
        share = net / gross
    results["zeros"] = {
        "structural_zeros": int(zero.sum()),
        "of_which_recycled_content": int(sum("Recycled Content cut-off" in names[i] for i in np.flatnonzero(zero))),
        "exactly_zero_in_x": int((x[zero] == 0).sum()),
        "structural_zeros_ms": best(lambda: structural_zeros(fact.magnitude.T, h), 5),
        "negative_direct_emissions": int((h < 0).sum()),
        "net_below_1e-3_of_gross": int((share[nonzero] < 1e-3).sum()),
        "net_below_1e-6_of_gross": int((share[nonzero] < 1e-6).sum()),
        "smallest_net_share": float(share[nonzero].min()),
    }
    print(results["zeros"], flush=True)
    save()

    # --- float32 -----------------------------------------------------------------------------
    from contextlib import closing

    with closing(_Query(release, check=False)) as query:
        query.index()
        t = query.arrays(_TECHNOSPHERE, ("row", "col", "value"))
    values32 = t["value"].astype(np.float32).astype(np.float64)
    A32 = sparse.csc_array((values32, (t["row"], t["col"])), shape=(n, n))
    x32 = calc.scores(calc.factorize(A32), h).values
    size = np.maximum(np.abs(x), np.abs(x32))
    with np.errstate(divide="ignore", invalid="ignore"):
        rel = np.where(size > 0, np.abs(x - x32) / size, 0.0)
    results["float32_lines"] = {
        "max_relative": float(rel.max()),
        "above_1e-9": int((rel > 1e-9).sum()),
        "above_1e-7": int((rel > 1e-7).sum()),
        "above_1e-6": int((rel > 1e-6).sum()),
    }
    print(results["float32_lines"], flush=True)
    save()

    # --- copies ------------------------------------------------------------------------------
    j = int(np.argmax(np.abs(x)))  # any product; the copies do not depend on it
    s = calc.supply(fact, np.eye(1, n, j).ravel()).values
    f = np.eye(1, n, j).ravel()
    study = sparse.csc_array(np.eye(1))
    bought = sparse.csc_array((n, 1))
    results["copies_ms"] = {
        "as_matrix_copy": best(lambda: as_matrix(A)),
        "as_matrix_shared": best(lambda: as_matrix(A, copy=False)),
        "abs_A": best(lambda: abs(A)),
        "residual_check_computing_abs": best(lambda: backward_error(A.T, x, h)),
        "residual_check_stored_abs": best(lambda: backward_error(A.T, x, h, fact.magnitude.T)),
        "stacked_matrix_one_study_process": best(
            lambda: sparse.block_array([[study, None], [bought, A]], format="csc")
        ),
        "explain_3_levels": best(lambda: calc.explain(A, h, x, j)),
        "levers": best(lambda: calc.levers(A, x, s, f), 5),
        "solve": best(lambda: fact.solve(h, transposed=True)),
        "checked_scores_with_exact_zeros": best(lambda: calc.scores(fact, h)),
        "memory_A_mb": round((A.data.nbytes + A.indices.nbytes + A.indptr.nbytes) / 1e6, 1),
    }
    print(results["copies_ms"], flush=True)
    results["scope"]["load_average_at_end"] = [round(v, 2) for v in os.getloadavg()]  # pyright: ignore[reportIndexIssue]
    save()


if __name__ == "__main__":
    main()
