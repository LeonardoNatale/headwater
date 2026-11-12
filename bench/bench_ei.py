"""Time a real ecoinvent 3.12 cut-off import and common operations in Brightway.

Run with BRIGHTWAY2_DIR pointing at a scratch folder.
"""

import json
import os
import random
import resource
import sqlite3
import sys
import time
from pathlib import Path

import bw2calc as bc
import bw2data as bd
import bw2io as bi

EI = Path(os.environ["ECOINVENT_DIR"])  # folder with datasets/ and MasterData/
OUT = Path(sys.argv[1])
results = {}


def mark(name, t0, **extra):
    dt = time.perf_counter() - t0
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e6  # macOS: bytes
    results[name] = {"seconds": round(dt, 3), "peak_rss_mb": round(rss, 1), **extra}
    print(f"{name}: {dt:.2f}s peak_rss={rss:.0f}MB {extra}", flush=True)
    OUT.write_text(json.dumps(results, indent=2))


def dir_size(p):
    return sum(f.stat().st_size for f in Path(p).rglob("*") if f.is_file())



def main():
    bd.projects.set_current("ei312-bench")
    proj_dir = Path(bd.projects.dir)

    t = time.perf_counter()
    bio = bi.importers.Ecospold2BiosphereImporter(name="ei312-biosphere", version="3.12", filepath=EI / "MasterData" / "ElementaryExchanges.xml")
    bio.apply_strategies()
    bio.write_database()
    mark("biosphere_import", t, flows=len(bd.Database("ei312-biosphere")))

    t = time.perf_counter()
    imp = bi.SingleOutputEcospold2Importer(sys.argv[2], "ei312-cutoff", biosphere_database_name="ei312-biosphere")
    mark("ecoinvent_extract", t, datasets=len(imp.data))

    t = time.perf_counter()
    imp.apply_strategies()
    mark("ecoinvent_strategies", t, strategies=len(imp.strategies))

    t = time.perf_counter()
    stats = imp.statistics(print_stats=False)
    mark("ecoinvent_statistics", t, datasets_exchanges_unlinked=list(stats))

    t = time.perf_counter()
    imp.write_database()
    mark("ecoinvent_write", t)

    con = sqlite3.connect(f"file:{proj_dir / 'lci' / 'databases.db'}?mode=ro", uri=True)
    counts = {
        "activities": con.execute("select count(*) from activitydataset where database='ei312-cutoff'").fetchone()[0],
        "exchanges_by_type": dict(con.execute("select type, count(*) from exchangedataset where output_database='ei312-cutoff' group by type").fetchall()),
        "activity_types": dict(con.execute("select type, count(*) from activitydataset group by type").fetchall()),
    }
    sample = con.execute("select data from activitydataset where database='ei312-cutoff' limit 1").fetchone()[0]
    con.close()
    import pickle

    node_fields = sorted(pickle.loads(sample).keys())
    results["counts"] = counts
    results["node_fields_sample"] = node_fields
    results["sizes_mb"] = {
        "lci_databases_db": round((proj_dir / "lci" / "databases.db").stat().st_size / 1e6, 1),
        "processed_dir": round(dir_size(proj_dir / "processed") / 1e6, 1),
        "search_dir": round(dir_size(proj_dir / "search") / 1e6, 1) if (proj_dir / "search").exists() else None,
        "project_total": round(dir_size(proj_dir) / 1e6, 1),
    }
    print(results["counts"], results["sizes_mb"], flush=True)
    OUT.write_text(json.dumps(results, indent=2))

    # A small climate method, enough to exercise the C matrix.
    cfs = {"Carbon dioxide, fossil": 1.0, "Methane, fossil": 29.8, "Dinitrogen monoxide": 273.0}
    method_data = [(f.key, cfs[f["name"]]) for f in bd.Database("ei312-biosphere") if f["name"] in cfs]
    method = ("bench", "GWP100 subset")
    bd.Method(method).register()
    bd.Method(method).write(method_data)

    acts = [a for a in bd.Database("ei312-cutoff")]
    random.seed(42)
    pick = random.sample(acts, 1000)

    t = time.perf_counter()
    lca = bc.LCA({pick[0]: 1}, method=method)
    lca.lci(factorize=True)
    lca.lcia()
    mark("first_lca_build_factorize_solve", t, technosphere_shape=list(lca.technosphere_matrix.shape), technosphere_nnz=int(lca.technosphere_matrix.nnz), biosphere_shape=list(lca.biosphere_matrix.shape))

    t = time.perf_counter()
    for a in pick[1:101]:
        lca.lcia({a.id: 1})
    mark("100_redo_lcia_factorized", t)

    t = time.perf_counter()
    lca2 = bc.LCA({pick[0]: 1}, method=method)
    lca2.lci()
    lca2.lcia()
    mark("second_lca_cold_object", t)

    t = time.perf_counter()
    mlca = bc.MultiLCA(
        demands={f"d{i}": {a.id: 1} for i, a in enumerate(pick)},
        method_config={"impact_categories": [method]},
        data_objs=bd.get_multilca_data_objs({f"d{i}": {a.id: 1} for i, a in enumerate(pick)}, {"impact_categories": [method]}),
    )
    mlca.lci()
    mlca.lcia()
    mark("multilca_1000_demands", t)

    # Change one exchange amount, then recompute.
    node = pick[1]
    exc = next(iter(node.technosphere()))
    t = time.perf_counter()
    exc["amount"] = exc["amount"] * 1.1
    exc.save()
    mark("edit_one_exchange_save", t)
    t = time.perf_counter()
    lca3 = bc.LCA({pick[0]: 1}, method=method)
    lca3.lci()
    lca3.lcia()
    mark("lca_after_one_edit", t)

    t = time.perf_counter()
    bd.projects.copy_project("ei312-bench-copy", switch=False)
    mark("copy_project", t)

    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
