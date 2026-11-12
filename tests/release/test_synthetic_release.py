"""bench/release_calc.py on a small synthetic release. Its generator draws each process's
suppliers from the products made before it and a few hubs: with few processes there are fewer
than 6 of them, so it takes what there is."""

import importlib.util
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "bench" / "release_calc.py"


def release_calc() -> ModuleType:
    spec = importlib.util.spec_from_file_location("release_calc", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_release_calc_runs_on_100_processes() -> None:
    bench = release_calc()
    results = bench.RESULTS.read_bytes() if bench.RESULTS.is_file() else None

    processes, _, _, _, _ = bench.synthetic(100)
    bench.run_synthetic(100)

    assert len(processes) == 100
    for process in processes:
        suppliers = [e.supplier_id for e in process.exchanges if e.role.value == "input"]
        assert len(suppliers) == len(set(suppliers)) <= 6
        assert process.id not in suppliers
    assert bench.results["processes"] == 100
    assert (bench.RESULTS.read_bytes() if bench.RESULTS.is_file() else None) == results
