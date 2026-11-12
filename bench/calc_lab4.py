import sys
from pathlib import Path
import duckdb, numpy as np
from scipy.sparse import diags
from scipy.sparse.linalg import splu, spsolve
S = Path(sys.argv[1]); sys.path.insert(0, str(Path(__file__).parent))
from duck_bench import build
con = duckdb.connect(str(S / "duck" / "ei312.duckdb"), read_only=True)
A, B, c = build(con); h = B.T @ c
At = A.T.tocsc()
print(f"score range: max |x| / median |x| spans {np.abs(spsolve(At, h)).max():.1e} vs {np.median(np.abs(spsolve(At, h))):.1e}")
def solve_eq(M, b, pivot):
    # row/column equilibration: scale so every row and column has max |entry| = 1
    r = 1.0 / np.asarray(abs(M).max(axis=1).todense()).ravel()
    Mr = diags(r) @ M
    cc = 1.0 / np.asarray(abs(Mr).max(axis=0).todense()).ravel()
    Ms = (Mr @ diags(cc)).tocsc()
    y = splu(Ms, permc_spec="MMD_AT_PLUS_A", diag_pivot_thresh=1.0 if pivot else 0.0).solve(r * b)
    return cc * y
x0 = splu(At, permc_spec="MMD_AT_PLUS_A", diag_pivot_thresh=0.0).solve(h)
x1 = splu(At, permc_spec="MMD_AT_PLUS_A", diag_pivot_thresh=1.0).solve(h)
e0 = solve_eq(At, h, False); e1 = solve_eq(At, h, True)
def disagree(a, b):
    m = np.abs(b) > 1e-9
    return int(((np.abs(a - b) / np.maximum(np.abs(b), 1e-300)) > 1e-6)[m].sum())
print("products disagreeing >1e-6 between pivot/no-pivot: raw", disagree(x0, x1), "| equilibrated", disagree(e0, e1), "| equilibrated vs raw pivoted", disagree(e1, x1))
