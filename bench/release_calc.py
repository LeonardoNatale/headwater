"""Calculation from a release: assembly, factorization, indicator vectors, SKUs, a study.

Builds a synthetic release (no licensed data) with the size of ecoinvent 3.10 cut-off: about
23,500 processes, 4,000 elementary flows, 30 lines per process, some treatments and waste lines.
Then times each step of `hw.load` on it, and the same on the toy release.

    uv run python bench/release_calc.py [processes] [--toy]

Results go to bench/results/release_calc_results.json: timings and counts only.
"""

import json
import platform
import sys
import tempfile
import time
from pathlib import Path

import duckdb
import numpy as np
import pyarrow as pa
import scipy

import headwater as hw
from headwater.calc import factorize, scores
from headwater.model import (
    Exchange,
    Factor,
    Flow,
    FlowKind,
    Indicator,
    Method,
    Process,
    ProcessKind,
    Role,
)
from headwater.model.database import Database
from headwater.query._internals import internals
from headwater.query._release import assemble_release
from headwater.release import Release, ReleaseInfo, build
from headwater.study import DatabaseFolder, read_method, read_process_folder

HERE = Path(__file__).resolve().parent
TOY = HERE.parent / "tests" / "fixtures" / "toy"
RESULTS = HERE / "results" / "release_calc_results.json"
INFO = ReleaseInfo(
    name="synthetic",
    source="synthetic",
    version="1",
    system_model="cutoff",
    licence="CC0-1.0",
    flow_list="synthetic",
)

results: dict[str, object] = {}


def timed(label: str, fn, repeat: int = 1):
    best = float("inf")
    out = None
    for _ in range(repeat):
        t = time.perf_counter()
        out = fn()
        best = min(best, time.perf_counter() - t)
    results[label] = round(best, 4)
    print(f"{label:<52} {best * 1e3:>10.1f} ms")
    return out


def synthetic(n: int, seed: int = 1):
    rng = np.random.default_rng(seed)
    n_flows = 4_000
    treatment = rng.random(n) < 0.15
    flows = [
        Flow(
            id=f"synth:{'w' if treatment[i] else 'p'}{i}",
            name=f"flow {i}",
            kind=FlowKind.waste if treatment[i] else FlowKind.product,
            unit="kg",
        )
        for i in range(n)
    ]
    elementary = [
        Flow(
            id=f"synth:e{k}",
            name=f"elementary {k}",
            kind=FlowKind.elementary,
            unit="kg",
            compartment="air",
        )
        for k in range(n_flows)
    ]
    products = np.flatnonzero(~treatment)
    treatments = np.flatnonzero(treatment)
    # Real supply graphs have hubs (electricity, transport) that many processes buy from, and a
    # few loops among them. Half of the purchases go to the 1% most popular products, chosen with
    # weights 1/rank. The rest go to products made earlier in the list, so they form no loops.
    hubs = products[: max(2, len(products) // 100)]
    hub_weights = 1.0 / np.arange(1, len(hubs) + 1)
    hub_weights /= hub_weights.sum()

    def suppliers(i: int) -> list[int]:
        # 6 suppliers, or as many as there are: the hubs and the products made before i, not i.
        # Below about 800 processes there can be fewer than 6, and asking for 6 never ends.
        earlier_count = int(np.searchsorted(products, i))
        is_hub = earlier_count < len(hubs) and products[earlier_count] == i
        wanted = min(6, max(len(hubs), earlier_count) - int(is_hub))
        found: set[int] = set()
        while len(found) < wanted:
            if rng.random() < 0.5:
                pick = int(rng.choice(hubs, p=hub_weights))
            else:
                earlier = products[products < i]
                if not len(earlier):
                    pick = int(rng.choice(hubs, p=hub_weights))
                else:
                    pick = int(rng.choice(earlier))
            if pick != i:
                found.add(pick)
        return sorted(found)

    processes = []
    for i in range(n):
        pid = f"synth:proc{i}"
        lines = [("e000", Role.reference, flows[i].id, 1.0, None)]
        if not treatment[i]:
            for j, supplier in enumerate(suppliers(i)):
                amount = float(rng.uniform(0.01, 0.12))
                lines.append((f"i{j:03d}", Role.input, flows[supplier].id, amount, f"synth:proc{supplier}"))
            if len(treatments) and rng.random() < 0.3:
                t = int(rng.choice(treatments))
                lines.append(("w000", Role.waste_to_treatment, flows[t].id, float(rng.uniform(0.01, 0.1)), f"synth:proc{t}"))
        for j, flow in enumerate(rng.choice(n_flows, 20, replace=False)):
            lines.append((f"x{j:03d}", Role.emission, elementary[flow].id, float(rng.lognormal(-3, 1.5)), None))
        processes.append(
            Process(
                id=pid,
                name=f"process {i}",
                location="GLO",
                kind=ProcessKind.ordinary_transforming_activity,
                exchanges=tuple(
                    Exchange(
                        process_id=pid, line_id=line, role=role, flow_id=flow, amount=amount,
                        unit="kg", supplier_id=supplier,
                    )
                    for line, role, flow, amount, supplier in lines
                ),
            )
        )
    covered = rng.choice(n_flows, 3_500, replace=False)
    method = Method(
        id="synth-method",
        version="1",
        flow_list="synthetic",
        indicators=tuple(Indicator(id=f"ind-{c}", name=f"Indicator {c}", unit="u") for c in "abc"),
        factors=tuple(
            Factor(indicator=f"ind-{c}", flow_id=elementary[k].id, factor=float(rng.uniform(0.1, 30)))
            for c in "abc"
            for k in covered
        ),
    )
    return processes, [*flows, *elementary], method, [flows[i].id for i in products], products


def run_toy() -> None:
    folder = read_process_folder(TOY / "background", namespace="toy")
    method = read_method(TOY / "methods" / "toy-climate")
    with tempfile.TemporaryDirectory() as tmp:
        release = build(
            ReleaseInfo(
                name="toy-background", source="toy", version="1", system_model="cutoff",
                licence="CC0-1.0", flow_list="toy",
            ),
            processes=folder.processes, flows=folder.flows, methods=[method], into=tmp,
        )
        print("\ntoy release, 3 processes (best of 20)")
        timed("toy: Release.connect() and close", lambda: release.connect().close(), 20)
        timed("toy: assemble_release", lambda: assemble_release(release, method), 20)
        timed("toy: hw.load(release)", lambda: hw.load(release.path, method="toy-climate"), 20)
        timed(
            "toy: hw.load(folder)",
            lambda: hw.load(TOY / "background", method=TOY / "methods" / "toy-climate", namespace="toy"),
            20,
        )


def run_synthetic(n: int) -> None:
    print(f"\nsynthetic release, {n} processes")
    processes, flows, method, product_flows, products = synthetic(n)
    with tempfile.TemporaryDirectory() as tmp:
        release = timed(
            "synthetic: build release",
            lambda: build(INFO, processes=processes, flows=flows, methods=[method], into=tmp),
        )
        assert isinstance(release, Release)
        results["lines"] = int(release.manifest.tables["exchanges.parquet"].rows)
        results["processes"] = n
        print(f"  {results['lines']} lines, {n} processes")
        timed("synthetic: Release.open (sha256 of every file)", lambda: Release.open(release.path))
        # The same scan through the registered pyarrow dataset, and through read_parquet.
        path = release.path / "exchanges.parquet"
        con = release.connect()
        timed(
            "scan exchanges: sum(amount), pyarrow dataset view",
            lambda: con.execute("SELECT sum(amount) FROM exchanges").fetchone(),
            3,
        )
        con.close()
        con = duckdb.connect()
        timed(
            "scan exchanges: sum(amount), read_parquet",
            lambda: con.execute(f"SELECT sum(amount) FROM read_parquet('{path}')").fetchone(),
            3,
        )
        con.close()
        timed("assemble_release (SQL to A and B)", lambda: assemble_release(release, method), 3)
        background = timed("hw.load(release): assemble, factorize, solve", lambda: hw.load(release.path, method="synth-method"))
        assert isinstance(background, hw.Background)
        block = internals(background).solved.block
        results["nnz_A"] = int(block.A.nnz)
        results["nnz_B"] = int(block.B.nnz)
        print(f"  A {block.A.shape} nnz {block.A.nnz}, B {block.B.shape} nnz {block.B.nnz}")
        timed("factorize A (alone, same settings)", lambda: factorize(block.A))
        x = internals(background).solved.x["ind-a"]
        timed("one transposed solve, one indicator", lambda: scores(internals(background).solved.factorization, internals(background).solved.h["ind-a"]))
        results["residual"] = float(x.residual)
        derived = timed("write_indicators: 3 indicators", lambda: background.write_indicators(into=tmp + "/derived"))
        assert isinstance(derived, Release)

        # 50,000 SKUs buying 20 random products each.
        rng = np.random.default_rng(7)
        n_skus = 50_000
        rows = n_skus * 20
        table = pa.Table.from_pydict(
            {
                "sku": np.repeat(np.arange(n_skus), 20).astype(str).tolist(),
                "product_id": [f"synth:proc{products[i]}" for i in rng.integers(0, len(products), rows)],
                "amount": rng.uniform(0.0, 2.0, rows),
            }
        )
        timed("score_many: 50,000 SKUs x 20 lines, 3 indicators (background)", lambda: hw.score_many(background, table), 3)
        timed("score_many: same, from the indicators release", lambda: hw.score_many(Release.open(derived.path), table), 3)

        # A database of one process buying 20 products.
        lines = [Exchange(process_id="st:sku", line_id="e000", role=Role.reference, flow_id="st:sku", amount=1.0, unit="unit")]
        for j, i in enumerate(rng.choice(products, 20, replace=False)):
            lines.append(Exchange(process_id="st:sku", line_id=f"e{j + 1:03d}", role=Role.input, flow_id=f"synth:p{i}", amount=0.1, unit="kg", supplier_id=f"synth:proc{i}"))
        study = hw.Database(
            DatabaseFolder(
                path=Path("."), namespace="st",
                processes=(Process(id="st:sku", name="SKU", location="GLO", kind=ProcessKind.ordinary_transforming_activity, exchanges=tuple(lines)),),
                flows=(Flow(id="st:sku", name="SKU", kind=FlowKind.product, unit="unit"),),
                flow_properties=(), units=(),
                database=Database(
                    name="st", background=f"{release.name}@{release.hash[:12]}",  # pyright: ignore[reportArgumentType]
                    methods=("synth-method",),
                ),
            )
        )
        result = timed("Study.calc, one study process, 20 purchases", lambda: study.calc(background, "st:sku", indicator="ind-a"), 3)
        assert isinstance(result, hw.Result)
        timed("Result.levers (loads the lines of the background once)", lambda: result.sensitivity(top=10))
        timed("Result.levers again", lambda: result.sensitivity(top=10))
        timed("Result.explain", lambda: result.contributions())
        timed("Result.not_characterised (first use)", lambda: result.not_characterised)
        results["not_characterised_reached"] = len(result.not_characterised)
        results["not_characterised_background"] = len(background.not_characterised)
        print(f"  score {result.score:.6g}, forward {result.forward_score:.6g}")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    results["machine"] = f"{platform.machine()} {platform.system()} {platform.release()}"
    results["software"] = (
        f"Python {platform.python_version()}, NumPy {np.__version__}, SciPy {scipy.__version__}, "
        f"DuckDB {duckdb.__version__}, PyArrow {pa.__version__}"
    )
    run_toy()
    if "--toy" not in sys.argv:
        run_synthetic(int(args[0]) if args else 23_500)
    RESULTS.write_text(json.dumps(results, indent=2) + "\n")
    print(f"\nwrote {RESULTS}")
