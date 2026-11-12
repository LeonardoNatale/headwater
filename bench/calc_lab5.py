import sys, time
from pathlib import Path
import duckdb, numpy as np
from scipy.sparse.linalg import splu
S = Path(sys.argv[1]); sys.path.insert(0, str(Path(__file__).parent))
from duck_bench import build
con = duckdb.connect(str(S / "duck" / "ei312.duckdb"), read_only=True)
A, B, c = build(con); h = B.T @ c
names = {i: (n, loc) for i, n, loc in con.execute("select p.i, n.name, n.location from proc_idx p join node n on n.id = p.id").fetchall()}
n = A.shape[0]
lu = splu(A.tocsc(), permc_spec="MMD_AT_PLUS_A", diag_pivot_thresh=0.0)
x = lu.solve(h, trans="T")
edited = next(i for i in range(n) if names[i] == ("finishing, textile, woven cotton", "GLO"))
cands = [i for i in range(n) if names[i][0].startswith("polyester") or "yarn production" in names[i][0] or "fibre production, polyester" in names[i][0] or names[i][0].startswith("textile production")]
k = None
for i in cands:
    f = np.zeros(n); f[i] = 1; s = lu.solve(f)
    if abs(s[edited]) < 1e-15 and x[i] > 0.5: k, sk = i, s; break
print("chosen:", names[k], f"score {x[k]:.4f}", "supply of edited process:", sk[edited])
Ac = A.tocsc()
def children(j):
    col = Ac.getcol(j); d = A[j, j]
    out = [("direct emissions", None, h[j] / d, 0.0)]
    for i, a in zip(col.indices, col.data):
        if i != j: out.append((f"{names[i][0]} | {names[i][1]}", i, -a * x[i] / d, -a / d))
    return out
def tree(j, scale, depth, total, pre=""):
    for lab, i, v, amt in sorted(children(j), key=lambda r: -abs(r[2])):
        if abs(v * scale) < 0.04 * abs(total): continue
        print(f"{pre}{v*scale:7.4f}  {100*v*scale/total:5.1f}%  {lab}")
        if i is not None and depth > 1: tree(i, scale * amt, depth - 1, total, pre + "   ")
t = time.perf_counter(); tree(k, 1.0, 3, x[k]); print(f"(explain: {time.perf_counter()-t:.4f} s)")
Acoo = A.tocoo(); off = Acoo.row != Acoo.col
el = -x[Acoo.row] * sk[Acoo.col] * Acoo.data / x[k]
idx = np.where(off)[0][np.argsort(-np.abs(el[off]))[:6]]
print("TOP SENSITIVITIES")
for o in idx:
    i, j = Acoo.row[o], Acoo.col[o]; print(f"  {el[o]:7.4f}  {names[i][0]}|{names[i][1]}  used by  {names[j][0]}|{names[j][1]}")
o = idx[0]; i, j = Acoo.row[o], Acoo.col[o]
A2 = A.tolil(); A2[i, j] *= 1.01; A2 = A2.tocsc(); f = np.zeros(n); f[k] = 1
s2 = splu(A2, permc_spec="MMD_AT_PLUS_A", diag_pivot_thresh=0.0).solve(f)
print(f"check: predicted {el[o]:.4f}, finite difference {((h @ s2) - x[k]) / x[k] / 0.01:.4f}")
contrib = h * sk; top = np.argsort(-contrib)[:4]
print("WHERE EMISSIONS HAPPEN")
for p in top: print(f"  {contrib[p]:.4f}  {100*contrib[p]/x[k]:5.1f}%  {names[p][0]} | {names[p][1]}")
