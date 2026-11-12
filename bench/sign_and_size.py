import os
import json, pickle, sqlite3, sys, time
from pathlib import Path
import duckdb, numpy as np, pyarrow as pa, pyarrow.parquet as pq
S = Path(sys.argv[1]); sys.path.insert(0, str(Path(__file__).parent))
from duck_bench import build, scores

# 1. Waste sign rule on real activities.
con = duckdb.connect(str(S / "duck" / "ei312.duckdb"), read_only=True)
A, B, c = build(con)
x = scores(A, B, c)
idx = dict(con.execute("select id, i from proc_idx").fetchall())
prod = {r[0]: r[1] for r in con.execute("select output_id, amount from edge where kind='production'").fetchall()}
burden = {k: x[i] * (1 if prod[k] > 0 else -1) for k, i in idx.items()}          # treatment scores made positive
h = B.T @ c
acts = [r[0] for r in con.execute("""
    select output_id from edge where kind='technosphere' and amount < 0 and input_id <> output_id
    group by output_id order by count(*) desc limit 50""").fetchall()]
worst = 0.0
for k in acts:
    rows = con.execute("select input_id, amount from edge where output_id=? and kind='technosphere'", [k]).fetchall()
    own_loss = sum(a for i, a in rows if i == k)
    total = h[idx[k]]
    for i, a in rows:
        if i == k:
            continue
        if prod[i] < 0:   # input is a waste treatment: record waste sent as positive kg, use positive burden
            sent = -a
            total += sent * burden[i]
        else:
            total += a * burden[i]
    mine = total / (prod[k] - own_loss)
    worst = max(worst, abs(mine - x[idx[k]]) / max(abs(x[idx[k]]), 1e-12))
print(f"sign rule on {len(acts)} activities with waste inputs: max relative diff {worst:.1e}")
con.close()

# 2. Like-for-like size: keep every field of every exchange and node as JSON.
bw = next(Path(os.environ["BRIGHTWAY2_DIR"]).glob("ei312-bench.*/lci/databases.db"))
src = sqlite3.connect(f"file:{bw}?mode=ro", uri=True)
t = time.perf_counter()
nodes = [{"id": f"{db}:{code}", "data": json.dumps(pickle.loads(d), default=str)} for db, code, d in src.execute("select database, code, data from activitydataset")]
edges = [{"input_id": f"{a}:{b}", "output_id": f"{c_}:{d_}", "kind": k, "data": json.dumps(pickle.loads(d), default=str)}
         for a, b, c_, d_, k, d in src.execute("select input_database, input_code, output_database, output_code, type, data from exchangedataset")]
print(f"full extract {time.perf_counter()-t:.1f}s")
out = S / "duck_full"; out.mkdir(exist_ok=True)
pq.write_table(pa.Table.from_pylist(nodes), out / "nodes_full.parquet", compression="zstd")
pq.write_table(pa.Table.from_pylist(edges), out / "edges_full.parquet", compression="zstd")
db = out / "full.duckdb"
if db.exists(): db.unlink()
d = duckdb.connect(str(db))
d.execute(f"create table node as select id, data::json as data from '{out/'nodes_full.parquet'}'")
d.execute(f"create table edge as select input_id, output_id, kind, data::json as data from '{out/'edges_full.parquet'}'")
d.execute("checkpoint"); d.close()
print(f"all fields: duckdb {db.stat().st_size/1e6:.1f} MB, parquet {sum(p.stat().st_size for p in out.glob('*.parquet'))/1e6:.1f} MB, brightway sqlite {bw.stat().st_size/1e6:.1f} MB")
print("sample exchange keys:", sorted(json.loads(edges[0]["data"]).keys()))
