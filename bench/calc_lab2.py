import sys, time
from pathlib import Path
import duckdb, numpy as np
from scipy.sparse.linalg import splu, spsolve
S = Path(sys.argv[1]); sys.path.insert(0, str(Path(__file__).parent))
from duck_bench import build
con = duckdb.connect(str(S / "duck" / "ei312.duckdb"), read_only=True)
A, B, c = build(con)
names = {i: (n, loc) for i, n, loc in con.execute("select p.i, n.name, n.location from proc_idx p join node n on n.id = p.id").fetchall()}
n = A.shape[0]; h = B.T @ c
lu = splu(A.tocsc(), permc_spec="MMD_AT_PLUS_A", diag_pivot_thresh=0.0)
x = lu.solve(h, trans="T")
x_piv = spsolve(A.T.tocsc(), h)
print(f"relative residual |A^T x - h| / |h|: {np.abs(A.T @ x - h).max() / np.abs(h).max():.1e}")
print(f"no-pivot vs pivoted solution, max relative diff over products with |x|>1e-9: {np.max(np.abs(x - x_piv)[np.abs(x_piv) > 1e-9] / np.abs(x_piv)[np.abs(x_piv) > 1e-9]):.1e}")
r = h - A.T @ x; x_ref = x + lu.solve(r, trans="T")
print(f"after one step of iterative refinement: {np.abs(A.T @ x_ref - h).max() / np.abs(h).max():.1e}")
k = next(i for i in range(n) if names[i] == ("finishing, textile, woven cotton", "GLO"))
Ac = A.tocsc()
def children(j):
    col = Ac.getcol(j); d = A[j, j]
    out = [("direct emissions", None, h[j] / d, 0.0)]
    for i, a in zip(col.indices, col.data):
        if i != j: out.append((f"{names[i][0]} | {names[i][1]}", i, -a * x[i] / d, -a / d))
    return out
def tree(j, scale, depth, total, pre=""):
    rows = sorted(children(j), key=lambda r: -abs(r[2]))
    for lab, i, v, amt in rows:
        if abs(v * scale) < 0.03 * abs(total): continue
        print(f"{pre}{v*scale:7.4f}  {100*v*scale/total:5.1f}%  {lab}")
        if i is not None and depth > 1: tree(i, scale * amt, depth - 1, total, pre + "   ")
t = time.perf_counter()
print(f"\nEXPLAIN {names[k]}: {x[k]:.4f} (3-flow climate subset)")
tree(k, 1.0, 3, x[k])
print(f"({time.perf_counter()-t:.4f} s)")
f = np.zeros(n); f[k] = 1; s = lu.solve(f)
Acoo = A.tocoo(); off = Acoo.row != Acoo.col
el = -x[Acoo.row] * s[Acoo.col] * Acoo.data / x[k]
idx = np.where(off)[0][np.argsort(-np.abs(el[off]))[:8]]
print("\nTOP OFF-DIAGONAL SENSITIVITIES (elasticity: % change in score per % change in that input)")
for o in idx:
    i, j = Acoo.row[o], Acoo.col[o]
    print(f"  {el[o]:7.4f}  {names[i][0]}|{names[i][1]}  used by  {names[j][0]}|{names[j][1]}")
o = idx[0]; i, j = Acoo.row[o], Acoo.col[o]
A2 = A.tolil(); A2[i, j] *= 1.01; A2 = A2.tocsc()
s2 = splu(A2, permc_spec="MMD_AT_PLUS_A", diag_pivot_thresh=0.0).solve(f)
print(f"check top one: predicted {el[o]:.4f}, finite difference (1% change) {((h @ s2) - x[k]) / x[k] / 0.01:.4f}")
# processes contributing to the total wherever they sit in the supply chain (needs s)
contrib = h * s
top = np.argsort(-contrib)[:5]
print("\nWHERE EMISSIONS HAPPEN (direct emissions x how much each process runs)")
for p in top: print(f"  {contrib[p]:.4f}  {100*contrib[p]/x[k]:5.1f}%  {names[p][0]} | {names[p][1]}")
