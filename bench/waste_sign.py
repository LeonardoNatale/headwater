from pathlib import Path
import sys, duckdb, numpy as np
sys.path.insert(0, str(Path(__file__).parent))
from duck_bench import build, scores
con = duckdb.connect(str(Path(sys.argv[1]) / "duck" / "ei312.duckdb"), read_only=True)
A, B, c = build(con)
x = scores(A, B, c)
idx = dict(con.execute("select id, i from proc_idx").fetchall())
waste = [r[0] for r in con.execute("select output_id from edge where kind='production' and amount < 0").fetchall()]
xs = np.array([x[idx[w]] for w in waste])
other = np.delete(x, [idx[w] for w in waste])
print(f"waste treatment datasets: {len(xs)}, raw score < 0: {(xs < 0).sum()}, > 0: {(xs > 0).sum()}, == 0: {(xs == 0).sum()}")
print(f"other datasets: {len(other)}, raw score < 0: {(other < 0).sum()}")
