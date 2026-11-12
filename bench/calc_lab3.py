import sys
from pathlib import Path
import duckdb, numpy as np
from scipy.sparse.linalg import splu, spsolve
S = Path(sys.argv[1]); sys.path.insert(0, str(Path(__file__).parent))
from duck_bench import build
con = duckdb.connect(str(S / "duck" / "ei312.duckdb"), read_only=True)
A, B, c = build(con); h = B.T @ c
x = splu(A.tocsc(), permc_spec="MMD_AT_PLUS_A", diag_pivot_thresh=0.0).solve(h, trans="T")
xp = spsolve(A.T.tocsc(), h)
print(f"residual no-pivot {np.abs(A.T@x-h).max()/np.abs(h).max():.1e}, pivoted spsolve {np.abs(A.T@xp-h).max()/np.abs(h).max():.1e}")
d = np.abs(x - xp)
rel = d / np.maximum(np.abs(xp), 1e-300)
bad = np.where((rel > 1e-6) & (np.abs(xp) > 1e-9))[0]
print(f"products differing by >1e-6 relative: {len(bad)}; their |score| range {np.abs(xp[bad]).min():.2e}..{np.abs(xp[bad]).max():.2e}; max abs diff {d.max():.2e}; median |score| {np.median(np.abs(xp)):.2e}")
names = {i: (n, loc, u) for i, n, loc, u in con.execute("select p.i, n.name, n.location, n.unit from proc_idx p join node n on n.id = p.id").fetchall()}
j = int(np.argmax(d)); print("max abs diff at:", names[j], "score", xp[j], "rel", d[j]/abs(xp[j]))
for b in bad[:9]:
    Ac = A.tocsc(); col = Ac.getcol(b)
    parts = np.array([h[b]] + [-a * x[i] for i, a in zip(col.indices, col.data) if i != b])
    print(f"  {names[b][0][:60]:60s} score {xp[b]: .2e}  sum|parts|/|score| = {np.abs(parts).sum()/abs(x[b]):.1e}")
