import os
"""Put the ecoinvent 3.12 cut-off data from the scratch Brightway project into DuckDB and time
what an engine built on DuckDB would do. Run with BRIGHTWAY2_DIR pointing at the scratch dir."""

import json
import pickle
import random
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import duckdb
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from scipy.sparse import csc_matrix
from scipy.sparse.linalg import spsolve

S = Path(sys.argv[1])
OUT = S / "duck"
OUT.mkdir(exist_ok=True)
DB = OUT / "ei312.duckdb"
res = {}


def mark(k, t0, **extra):
    res[k] = {"s": round(time.perf_counter() - t0, 3), **extra}
    print(k, res[k], flush=True)


def extract():
    BW = next(Path(os.environ["BRIGHTWAY2_DIR"]).glob("ei312-bench.*/lci/databases.db"))
    t = time.perf_counter()
    con = sqlite3.connect(f"file:{BW}?mode=ro", uri=True)
    nodes = []
    for db, code, name, loc, prod, typ, data in con.execute(
        "select database, code, name, location, product, type, data from activitydataset"
    ):
        d = pickle.loads(data)
        cats = d.get("categories") or ()
        nodes.append({
            "id": f"{db}:{code}", "database": db, "name": name, "location": loc, "product": prod,
            "kind": typ, "unit": d.get("unit"), "compartment": "/".join(c for c in cats if c),
        })
    edges = []
    for idb, icode, odb, ocode, typ, data in con.execute(
        "select input_database, input_code, output_database, output_code, type, data from exchangedataset"
    ):
        d = pickle.loads(data)
        edges.append({
            "input_id": f"{idb}:{icode}", "output_id": f"{odb}:{ocode}", "kind": typ,
            "amount": float(d["amount"]), "unit": d.get("unit"),
            "uncertainty_type": int(d.get("uncertainty type") or 0),
            "loc": float(d["loc"]) if d.get("loc") is not None else None,
            "scale": float(d["scale"]) if d.get("scale") is not None else None,
        })
    con.close()
    mark("extract_from_brightway_pickles", t, nodes=len(nodes), edges=len(edges))
    pq.write_table(pa.Table.from_pylist(nodes), OUT / "nodes.parquet", compression="zstd")
    pq.write_table(pa.Table.from_pylist(edges), OUT / "edges.parquet", compression="zstd")
    res["parquet_mb"] = round(sum((OUT / f).stat().st_size for f in ["nodes.parquet", "edges.parquet"]) / 1e6, 1)


def load():
    if DB.exists():
        DB.unlink()
    t = time.perf_counter()
    con = duckdb.connect(str(DB))
    con.execute("""
        create table node (id varchar primary key, database varchar, name varchar, location varchar,
                           product varchar, kind varchar, unit varchar, compartment varchar)""")
    con.execute("""
        create table edge (input_id varchar references node(id), output_id varchar references node(id),
                           kind varchar, amount double, unit varchar, uncertainty_type integer,
                           loc double, scale double)""")
    con.execute(f"insert into node select * from '{OUT / 'nodes.parquet'}'")
    con.execute(f"insert into edge select * from '{OUT / 'edges.parquet'}'")
    con.execute("checkpoint")
    con.close()
    mark("load_into_duckdb_with_pk_fk", t, file_mb=round(DB.stat().st_size / 1e6, 1))


def build(con):
    """Matrix indices assigned in SQL; arrays fetched straight into NumPy."""
    con.execute("""
        create or replace temp table proc_idx as
        select id, row_number() over (order by id) - 1 as i from node where database = 'ei312-cutoff'""")
    con.execute("""
        create or replace temp table flow_idx as
        select id, row_number() over (order by id) - 1 as i from node where database = 'ei312-biosphere'""")
    t = con.execute("""
        select pi.i as row, po.i as col,
               case when e.kind = 'production' then e.amount else -e.amount end as v
        from edge e join proc_idx pi on pi.id = e.input_id join proc_idx po on po.id = e.output_id
        where e.kind in ('production', 'technosphere')""").fetchnumpy()
    b = con.execute("""
        select fi.i as row, po.i as col, e.amount as v
        from edge e join flow_idx fi on fi.id = e.input_id join proc_idx po on po.id = e.output_id
        where e.kind = 'biosphere'""").fetchnumpy()
    n = con.execute("select count(*) from proc_idx").fetchone()[0]
    m = con.execute("select count(*) from flow_idx").fetchone()[0]
    A = csc_matrix((t["v"], (t["row"], t["col"])), shape=(n, n))
    B = csc_matrix((b["v"], (b["row"], b["col"])), shape=(m, n))
    cf = con.execute("""
        select fi.i, case n.name when 'Carbon dioxide, fossil' then 1.0 when 'Methane, fossil' then 29.8
                                 when 'Dinitrogen monoxide' then 273.0 end
        from node n join flow_idx fi on fi.id = n.id
        where n.name in ('Carbon dioxide, fossil', 'Methane, fossil', 'Dinitrogen monoxide')""").fetchall()
    c = np.zeros(m)
    for i, v in cf:
        c[i] = v
    return A, B, c


def scores(A, B, c):
    return spsolve(A.T.tocsc(), B.T @ c)


def main():
    extract()
    load()

    con = duckdb.connect(str(DB))
    t = time.perf_counter()
    A, B, c = build(con)
    mark("query_and_build_matrices", t, A=list(A.shape), A_nnz=int(A.nnz), B=list(B.shape))
    t = time.perf_counter()
    x = scores(A, B, c)
    mark("adjoint_solve_all_scores", t)

    # Compare with bw2calc on random products.
    import bw2calc as bc
    import bw2data as bd
    bd.projects.set_current("ei312-bench")
    ids = [r[0] for r in con.execute("select id from proc_idx order by i").fetchall()]
    pos = {k: i for i, k in enumerate(ids)}
    random.seed(3)
    sample = random.sample(list(bd.Database("ei312-cutoff")), 10)
    lca = bc.LCA({sample[0]: 1}, method=("bench", "GWP100 subset"))
    lca.lci(); lca.lcia()
    diffs = []
    for a in sample:
        lca.lci({a.id: 1}); lca.lcia_calculation()
        mine = x[pos[f"ei312-cutoff:{a['code']}"]]
        diffs.append(abs(mine - lca.score) / max(abs(lca.score), 1e-12))
    res["max_rel_diff_vs_bw2calc_10_products"] = float(max(diffs))
    print("max rel diff", max(diffs), flush=True)

    # Sign facts straight from the data.
    res["negative_production_amounts"] = con.execute(
        "select count(*) from edge where kind = 'production' and amount < 0").fetchone()[0]
    res["negative_technosphere_inputs"] = con.execute(
        "select count(*) from edge where kind = 'technosphere' and amount < 0").fetchone()[0]
    res["units_distinct"] = con.execute("select count(distinct unit) from edge").fetchone()[0]

    # One edit, then rebuild everything from the database and solve again.
    target = con.execute("select input_id, output_id from edge where kind = 'technosphere' limit 1").fetchone()
    t = time.perf_counter()
    con.execute("update edge set amount = amount * 1.1 where input_id = ? and output_id = ? and kind = 'technosphere'", target)
    mark("update_one_edge", t)
    t = time.perf_counter()
    A2, B2, c2 = build(con)
    scores(A2, B2, c2)
    mark("rebuild_from_db_and_solve_after_edit", t)

    # Foreign keys reject a dangling link instead of storing it.
    try:
        con.execute("insert into edge values ('ei312-cutoff:does-not-exist', ?, 'technosphere', 1, 'kg', 0, null, null)", [target[1]])
        res["fk_dangling_insert"] = "accepted"
    except Exception as e:
        res["fk_dangling_insert"] = f"rejected: {type(e).__name__}"
    print("fk", res["fk_dangling_insert"], flush=True)

    # A second process while this one holds the file open for writing.
    probe = (
        "import duckdb,sys\n"
        "for ro in (False, True):\n"
        "    try:\n"
        "        duckdb.connect(sys.argv[1], read_only=ro); print('ro' if ro else 'rw', 'opened')\n"
        "    except Exception as e:\n"
        "        print('ro' if ro else 'rw', type(e).__name__, str(e).splitlines()[0][:140])\n"
    )
    out = subprocess.run([sys.executable, "-I", "-c", probe, str(DB)], capture_output=True, text=True).stdout
    res["second_process_while_writer_open"] = out.strip().splitlines()
    print(out, flush=True)
    con.close()
    out = subprocess.run([sys.executable, "-I", "-c", probe, str(DB)], capture_output=True, text=True).stdout
    res["second_process_after_writer_closed"] = out.strip().splitlines()
    print(out, flush=True)

    (S / "duck_results.json").write_text(json.dumps(res, indent=2, default=str))


if __name__ == "__main__":
    main()
