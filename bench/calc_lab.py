"""Experiments for the calculation core, on ecoinvent 3.12 cut-off loaded in DuckDB (calc fields)."""

import json
import sys
import time
from pathlib import Path

import duckdb
import numpy as np
from scipy.sparse import csc_matrix, csr_matrix
from scipy.sparse.csgraph import connected_components
from scipy.sparse.linalg import splu

S = Path(sys.argv[1])
sys.path.insert(0, str(Path(__file__).parent))
from duck_bench import build  # noqa: E402

res = {}


def timed(label, fn, *a, **k):
    t = time.perf_counter()
    out = fn(*a, **k)
    res[label] = round(time.perf_counter() - t, 4)
    print(f"{label}: {res[label]} s", flush=True)
    return out


con = duckdb.connect(str(S / "duck" / "ei312.duckdb"), read_only=True)
A, B, c = build(con)
names = {i: (n, loc) for i, n, loc in con.execute(
    "select p.i, n.name, n.location from proc_idx p join node n on n.id = p.id").fetchall()}
n = A.shape[0]
h = B.T @ c  # direct climate impact per unit of each process

# 1. Structure: strongly connected components of the supply graph.
ncomp, labels = connected_components(A, directed=True, connection="strong")
sizes = np.bincount(labels)
big = np.argsort(sizes)[::-1][:5]
res["scc_count"] = int(ncomp)
res["scc_largest_sizes"] = [int(sizes[b]) for b in big]
res["scc_singletons"] = int((sizes == 1).sum())
print("SCCs:", ncomp, "largest:", res["scc_largest_sizes"], "singletons:", res["scc_singletons"], flush=True)

# 2. Factorization options. LCA matrices rarely need pivoting.
Acsc = A.tocsc()
for spec in ["COLAMD", "MMD_AT_PLUS_A"]:
    for thresh in [1.0, 0.0]:
        lab = f"splu_{spec}_pivot{thresh}"
        try:
            lu = timed(lab, splu, Acsc, permc_spec=spec, diag_pivot_thresh=thresh)
            res[lab + "_nnz_LU"] = int(lu.L.nnz + lu.U.nnz)
        except Exception as e:
            res[lab] = f"failed: {type(e).__name__}"
lu = splu(Acsc, permc_spec="MMD_AT_PLUS_A", diag_pivot_thresh=0.0)
x = timed("transposed_solve_with_factor", lu.solve, h, trans="T")
f = np.zeros(n)
k = int(np.argmax([1 if "textile, woven cotton" in names[i][0] else 0 for i in range(n)]))
f[k] = 1.0
s = timed("forward_solve_with_factor", lu.solve, f)
res["residual_transposed"] = float(np.abs(A.T @ x - h).max())
res["residual_forward"] = float(np.abs(A @ s - f).max())
print("residuals", res["residual_transposed"], res["residual_forward"], flush=True)
res["check_forward_equals_adjoint"] = float(abs(h @ s - x[k]) / abs(x[k]))
print("product", names[k], "score", x[k], "h.s", h @ s, flush=True)

# 3. Explain from stored scores: score_k = (direct_k + sum_i -A_ik * x_i) / A_kk, recursively.
Ac = A.tocsc()


def explain(j, depth, cutoff, total, prefix=""):
    col = Ac.getcol(j)
    diag = A[j, j]
    parts = [("direct emissions", h[j] / diag, None)]
    for i, a in zip(col.indices, col.data):
        if i == j:
            continue
        parts.append((names[i][0] + " | " + str(names[i][1]), -a * x[i] / diag, i))
    parts.sort(key=lambda p: -abs(p[1]))
    lines = []
    for label, v, i in parts:
        if abs(v) < cutoff * abs(total):
            continue
        lines.append(f"{prefix}{v:9.4f}  {label}")
        if i is not None and depth > 1:
            lines += explain(i, depth - 1, cutoff, total, prefix + "    ")
    return lines


t = time.perf_counter()
tree = explain(k, 3, 0.03, x[k])
res["explain_3_levels_s"] = round(time.perf_counter() - t, 4)
col = Ac.getcol(k)
recon = (h[k] + sum(-a * x[i] for i, a in zip(col.indices, col.data) if i != k)) / A[k, k]
res["explain_level1_sums_to_score_rel_err"] = float(abs(recon - x[k]) / abs(x[k]))
print(f"\nEXPLAIN {names[k]} total {x[k]:.4f} (3 levels, >3% shown, {res['explain_3_levels_s']} s)")
print("\n".join(tree[:25]), flush=True)

# 4. Sensitivity of this product's score to every technosphere entry: d score / d A_ij = -x_i * s_j.
t = time.perf_counter()
Acoo = A.tocoo()
elastic = -x[Acoo.row] * s[Acoo.col] * Acoo.data / x[k]  # % change of score per % change of entry
order = np.argsort(-np.abs(elastic))[:8]
res["sensitivity_all_entries_s"] = round(time.perf_counter() - t, 4)
print(f"\nTOP SENSITIVITIES (elasticity), all {A.nnz} entries in {res['sensitivity_all_entries_s']} s")
for o in order:
    i, j = Acoo.row[o], Acoo.col[o]
    print(f"  {elastic[o]:8.4f}  {names[i][0]}|{names[i][1]}  ->  {names[j][0]}|{names[j][1]}")
# finite-difference check on the top entry
o = order[1] if Acoo.row[order[0]] == Acoo.col[order[0]] else order[0]
i, j = Acoo.row[o], Acoo.col[o]
A2 = A.tolil(); A2[i, j] = A2[i, j] * 1.01; A2 = A2.tocsc()
s2 = splu(A2, permc_spec="MMD_AT_PLUS_A", diag_pivot_thresh=0.0).solve(f)
fd = ((h @ s2) - x[k]) / x[k] / 0.01
res["sensitivity_vs_finite_difference"] = [float(elastic[o]), float(fd)]
print("elasticity predicted vs finite difference:", res["sensitivity_vs_finite_difference"], flush=True)

# 5. One change, all scores updated without refactorizing (Sherman-Morrison on the transposed system).
delta = A[i, j] * 0.01
t = time.perf_counter()
# A' = A + delta e_i e_j^T  ->  A'^T = A^T + delta e_j e_i^T
ei = np.zeros(n); ei[i] = 1.0
z = lu.solve(np.eye(1, n, j).ravel(), trans="T")  # A^-T e_j
x_new = x - z * (delta * x[i]) / (1.0 + delta * z[i])
res["sherman_morrison_update_s"] = round(time.perf_counter() - t, 4)
x_ref = timed("full_refactor_and_solve_s", lambda: splu(A2.T.tocsc() if False else A2, permc_spec="MMD_AT_PLUS_A", diag_pivot_thresh=0.0).solve(h, trans="T"))
res["sherman_morrison_max_rel_err"] = float(np.max(np.abs(x_new - x_ref) / np.maximum(np.abs(x_ref), 1e-12)))
res["products_changed_by_edit"] = int((np.abs(x_new - x) > 1e-12 * np.abs(x)).sum())
print("SM update", res["sherman_morrison_update_s"], "s, max rel err", res["sherman_morrison_max_rel_err"],
      "products whose score moved:", res["products_changed_by_edit"], flush=True)

# 6. Many SKUs: 50,000 synthetic products, 20 background inputs each, scored from stored x.
rng = np.random.default_rng(0)
rows = np.repeat(np.arange(50_000), 20)
cols = rng.integers(0, n, size=rows.size)
vals = rng.random(rows.size)
F = csr_matrix((vals, (rows, cols)), shape=(50_000, n))
X = np.column_stack([x] * 25)  # pretend 25 indicators
timed("score_50k_skus_25_indicators_numpy", lambda: F @ X)
d = duckdb.connect()
d.execute("create table bom as select * from (select unnest(?) as sku, unnest(?) as product, unnest(?) as amount)",
          [rows.tolist(), cols.tolist(), vals.tolist()])
d.execute("create table score as select unnest(?) as product, unnest(?) as climate", [list(range(n)), x.tolist()])
timed("score_50k_skus_1_indicator_duckdb_sql",
      lambda: d.execute("select sku, sum(amount * climate) from bom join score using (product) group by sku").fetchnumpy())

(S / "calc_lab_results.json").write_text(json.dumps(res, indent=2))
