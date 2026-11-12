"""Brightway reference for the parity tests: a fresh import of ecoinvent 3.12, its matrices and scores.

Imports the ecoSpold2 folder of one system model into a new Brightway project, then writes to
$HEADWATER_WORK/reference-3.12/ for cut-off, or reference-3.12-apos/ for APOS:

- `ids.parquet`: Brightway node id and code, activity and product uuid, headwater process id
- `technosphere.parquet`, `biosphere.parquet`: the matrices bw2calc builds, one row per non-zero
  cell, keyed by headwater ids
- `technosphere_lines.parquet`: the processed technosphere array before duplicate cells are
  summed: signed amount as stored, and the flip flag
- `method.parquet`: the 3-flow climate method, by headwater flow id
- `scores.parquet`: bw2calc's score of one unit of every product, solved with its factorized
  solver (`lci(factorize=True)`, then `lcia({id: 1})`), the path MultiLCA uses
- `reference.json`: ecoinvent version, system model, Brightway versions, counts, dtypes and timings

Licensed values stay in $HEADWATER_WORK. bench/results/bw_reference_3.12_results.json (cut-off)
and bw_reference_3.12-apos_results.json get counts and timings only. The 3.10 run of 11 October
2026 is in bw_reference_results.json.

    export ECOINVENT_DIR=/path/to/ecoinvent/3.12-cutoff   # holds datasets/ and MasterData/
    export HEADWATER_WORK=/path/to/scratch
    export BRIGHTWAY2_DIR=$HEADWATER_WORK/brightway-parity-3.12    # a new folder, never your own
    uv run --no-project --python 3.12 --with bw2data==4.5.4 --with bw2calc==2.3 \\
        --with bw2io==0.9.14 --with peewee==3.19.0 --with pyarrow python bench/bw_reference.py

APOS reads `ECOINVENT_APOS_DIR` instead, into its own project, database and reference folder:

    export ECOINVENT_APOS_DIR=/path/to/ecoinvent/3.12-apos
    export BRIGHTWAY2_DIR=$HEADWATER_WORK/brightway-parity-3.12-apos
    uv run --no-project ... python bench/bw_reference.py --system-model apos [--one-process]

`--one-process` parses the files in this process instead of one per CPU, on a shared machine.

Cut-off stops when Brightway leaves a line unlinked or drops one: every line links in 3.12. APOS
records both counts in its results instead, for the parity test to check.
"""

import argparse
import hashlib
import json
import os
import platform
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
VERSION = "3.12"
SYSTEM_MODELS = {"cutoff": "ECOINVENT_DIR", "apos": "ECOINVENT_APOS_DIR"}
"""Each system model and the variable that names its ecoSpold2 folder."""
BIOSPHERE = f"ecoinvent-{VERSION}-biosphere"
FACTORS = {"Carbon dioxide, fossil": 1.0, "Methane, fossil": 29.8, "Dinitrogen monoxide": 273.0}
METHOD = ("headwater-parity", "climate 3 flows")

# Names for cut-off, the first system model compared. `names` sets them for another one.
SYSTEM_MODEL = "cutoff"
RESULTS = HERE / "results" / f"bw_reference_{VERSION}_results.json"
REFERENCE = f"reference-{VERSION}"
PROJECT = f"headwater-parity-{VERSION}"
DATABASE = f"ecoinvent-{VERSION}-cutoff"
PREFIX = f"ecoinvent:{VERSION}:cutoff:"


def names(system_model: str) -> None:
    """Every name that depends on the system model. Cut-off keeps the names it always had, so its
    reference stays where tests/integration/test_parity.py reads it."""
    global SYSTEM_MODEL, RESULTS, REFERENCE, PROJECT, DATABASE, PREFIX
    SYSTEM_MODEL = system_model
    suffix = "" if system_model == "cutoff" else f"-{system_model}"
    RESULTS = HERE / "results" / f"bw_reference_{VERSION}{suffix}_results.json"
    REFERENCE = f"reference-{VERSION}{suffix}"
    PROJECT = f"headwater-parity-{VERSION}{suffix}"
    DATABASE = f"ecoinvent-{VERSION}-{system_model}"
    PREFIX = f"ecoinvent:{VERSION}:{system_model}:"


def environment() -> tuple[Path, Path, Path]:
    """Check the folders before bw2data is imported: it writes to whatever folder it is given."""
    work = Path(os.environ["HEADWATER_WORK"]).expanduser().resolve()
    ecoinvent = Path(os.environ[SYSTEM_MODELS[SYSTEM_MODEL]]).expanduser()
    bw_dir = os.environ.get("BRIGHTWAY2_DIR")
    if not bw_dir:
        sys.exit("set BRIGHTWAY2_DIR to a new folder under HEADWATER_WORK")
    brightway = Path(bw_dir).expanduser().resolve()
    if not brightway.is_relative_to(work):
        sys.exit(f"BRIGHTWAY2_DIR {brightway} is not under HEADWATER_WORK {work}")
    brightway.mkdir(parents=True, exist_ok=True)
    return work, ecoinvent, brightway


results: dict[str, object] = {}


def mark(label: str, start: float, **extra: object) -> None:
    took = time.perf_counter() - start
    results[label] = {"seconds": round(took, 2), **extra}
    print(f"{label}: {took:.1f} s {extra}", flush=True)
    RESULTS.write_text(json.dumps(results, indent=2) + "\n")


def technosphere_count(data: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for ds in data:
        for exc in ds["exchanges"]:
            counts[exc["type"]] = counts.get(exc["type"], 0) + 1
    return counts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--system-model", choices=sorted(SYSTEM_MODELS), default="cutoff")
    parser.add_argument(
        "--one-process", action="store_true", help="parse in one process, on a shared machine"
    )
    args = parser.parse_args()
    names(args.system_model)
    work, ecoinvent, brightway = environment()
    out = work / REFERENCE
    out.mkdir(parents=True, exist_ok=True)

    import bw2calc as bc
    import bw2data as bd
    import bw2io as bi
    import numpy as np
    import pyarrow as pa
    import pyarrow.parquet as pq
    import scipy

    results["versions"] = {
        "python": platform.python_version(),
        "bw2data": ".".join(map(str, bd.__version__)) if isinstance(bd.__version__, tuple) else str(bd.__version__),
        "bw2calc": str(bc.__version__),
        "bw2io": ".".join(map(str, bi.__version__)) if isinstance(bi.__version__, tuple) else str(bi.__version__),
        "numpy": np.__version__,
        "scipy": scipy.__version__,
        "machine": f"{platform.machine()} {platform.system()} {platform.release()}",
        "load_average_at_start": [round(v, 2) for v in os.getloadavg()],
    }
    bd.projects.set_current(PROJECT)
    if Path(bd.projects.dir).resolve().is_relative_to(brightway) is False:
        sys.exit(f"Brightway project folder {bd.projects.dir} is not under {brightway}")

    if not len(bi.migrations):
        bi.create_core_migrations()
    if DATABASE not in bd.databases:
        datasets = ecoinvent / "datasets"
        empty = [f.name for f in datasets.iterdir() if f.stat().st_size == 0]
        if empty:
            sys.exit(f"empty dataset files: {empty}")
        t = time.perf_counter()
        bio = bi.importers.Ecospold2BiosphereImporter(
            name=BIOSPHERE, version=VERSION,
            filepath=ecoinvent / "MasterData" / "ElementaryExchanges.xml",
        )
        bio.apply_strategies()
        bio.write_database()
        mark("biosphere_import", t, flows=len(bd.Database(BIOSPHERE)))

        t = time.perf_counter()
        imp = bi.SingleOutputEcospold2Importer(
            str(datasets), DATABASE, biosphere_database_name=BIOSPHERE, use_mp=not args.one_process
        )
        before = technosphere_count(imp.data)
        mark("parse", t, datasets=len(imp.data), exchanges=before)

        t = time.perf_counter()
        imp.apply_strategies()
        after = technosphere_count(imp.data)
        unlinked_n = imp.statistics(print_stats=False)[2]
        mark("strategies", t, exchanges=after, unlinked=unlinked_n)
        dropped = {k: before.get(k, 0) - after.get(k, 0) for k in before}
        # Cut-off: every file is read and every line links, Brightway deletes none.
        expected = {"production": 0, "technosphere": 0, "biosphere": 0}
        if SYSTEM_MODEL == "cutoff" and (unlinked_n != 0 or dropped != expected):
            sys.exit(f"unexpected unlinked {unlinked_n} or dropped exchanges {dropped}")
        results["dropped_exchanges"] = dropped
        results["unlinked_exchanges"] = unlinked_n

        t = time.perf_counter()
        imp.write_database()
        mark("write", t)

    bio_db = bd.Database(BIOSPHERE)
    method_data = [(f.key, FACTORS[f["name"]]) for f in bio_db if f["name"] in FACTORS]
    bd.Method(METHOD).register()
    bd.Method(METHOD).write(method_data)
    results["method_flows"] = len(method_data)

    # Ids: Brightway's code for an ecoinvent dataset is md5(activity uuid + product uuid).
    nodes = list(bd.Database(DATABASE))
    id_rows = []
    for node in nodes:
        activity, product = node["activity"], node["flow"]
        code = hashlib.md5((activity + product).encode("utf-8")).hexdigest()
        if code != node["code"]:
            sys.exit(f"code of node {node.id} is not md5(activity + product)")
        id_rows.append((node.id, node["code"], activity, product, f"{PREFIX}{activity}_{product}", node["name"]))
    hw_of_node = {r[0]: r[4] for r in id_rows}
    if len(set(hw_of_node.values())) != len(nodes):
        sys.exit("two Brightway nodes map to one headwater id")
    flow_of_node = {f.id: f"ecoinvent:flow:{f['code']}" for f in bio_db}
    pq.write_table(
        pa.table(
            {
                "node_id": [r[0] for r in id_rows],
                "code": [r[1] for r in id_rows],
                "activity_uuid": [r[2] for r in id_rows],
                "product_uuid": [r[3] for r in id_rows],
                "process_id": [r[4] for r in id_rows],
                "name": [r[5] for r in id_rows],
            }
        ),
        out / "ids.parquet",
    )
    pq.write_table(
        pa.table(
            {
                "flow_id": [flow_of_node[bd.get_node(key=k).id] for k, _ in method_data],
                "factor": [float(v) for _, v in method_data],
            }
        ),
        out / "method.parquet",
    )

    # The processed technosphere array, before bw2calc sums duplicate cells.
    package = bd.Database(DATABASE).datapackage()
    group = f"{DATABASE}_technosphere_matrix"
    indices = package.get_resource(f"{group}.indices")[0]
    data = package.get_resource(f"{group}.data")[0]
    flip = package.get_resource(f"{group}.flip")[0]
    pq.write_table(
        pa.table(
            {
                "row_id": [hw_of_node[int(i)] for i in indices["row"]],
                "col_id": [hw_of_node[int(i)] for i in indices["col"]],
                "amount": pa.array(data),
                "flip": pa.array(flip),
            }
        ),
        out / "technosphere_lines.parquet",
    )
    results["processed_dtype"] = str(data.dtype)

    t = time.perf_counter()
    first = nodes[0]
    lca = bc.LCA({first.id: 1}, method=METHOD)
    lca.lci(factorize=True)
    lca.lcia()
    mark("load_and_factorize", t, solver=type(lca.solver).__name__ if hasattr(lca, "solver") else None)

    def export(matrix, rows_of, cols_of, row_name, path):
        coo = matrix.tocoo()
        coo.eliminate_zeros()
        row_key = {v: k for k, v in rows_of.items()}
        col_key = {v: k for k, v in cols_of.items()}
        pq.write_table(
            pa.table(
                {
                    row_name: [row_key[int(i)] for i in coo.row],
                    "col_id": [hw_of_node[col_key[int(j)]] for j in coo.col],
                    "value": pa.array(coo.data),
                }
            ),
            path,
        )
        return str(matrix.dtype), int(coo.nnz)

    product_of = {node: hw_of_node[node] for node in lca.dicts.product}
    t_dtype, t_nnz = export(
        lca.technosphere_matrix,
        {product_of[k]: v for k, v in lca.dicts.product.items()},
        dict(lca.dicts.activity),
        "row_id",
        out / "technosphere.parquet",
    )
    b_dtype, b_nnz = export(
        lca.biosphere_matrix,
        {flow_of_node[k]: v for k, v in lca.dicts.biosphere.items()},
        dict(lca.dicts.activity),
        "flow_id",
        out / "biosphere.parquet",
    )
    results["matrices"] = {
        "technosphere": {"dtype": t_dtype, "non_zeros": t_nnz, "shape": list(lca.technosphere_matrix.shape)},
        "biosphere": {"dtype": b_dtype, "non_zeros": b_nnz, "shape": list(lca.biosphere_matrix.shape)},
    }
    RESULTS.write_text(json.dumps(results, indent=2) + "\n")

    t = time.perf_counter()
    ids, scores = [], []
    for k, node in enumerate(nodes):
        lca.lcia({node.id: 1})
        ids.append(hw_of_node[node.id])
        scores.append(float(lca.score))
        if k % 2000 == 0:
            print(f"scored {k:,} of {len(nodes):,} in {time.perf_counter() - t:.0f} s", flush=True)
    mark("scores", t, products=len(scores))
    pq.write_table(pa.table({"process_id": ids, "score": scores}), out / "scores.parquet")

    results["versions"]["load_average_at_end"] = [round(v, 2) for v in os.getloadavg()]
    reference = {
        **results,
        "ecoinvent_version": VERSION,
        "system_model": SYSTEM_MODEL,
        "database": DATABASE,
        "brightway_dir": str(brightway),
        "method": list(METHOD),
    }
    (out / "reference.json").write_text(json.dumps(reference, indent=2) + "\n")
    print("done", flush=True)


if __name__ == "__main__":
    main()
