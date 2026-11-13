"""headwater's own `open` and `validate` of a database in the layout of 0.1.0 (`processes/`, one
YAML and one CSV per process) against the two tables of 0.2.0 (`processes.csv` and `exchanges.csv`,
D48).

    uv export --frozen --no-dev --no-emit-project --no-hashes > locked.txt
    uv venv venv-0.1.0 && uv pip install -p venv-0.1.0/bin/python -r locked.txt
    uv pip install -p venv-0.1.0/bin/python --no-deps headwater-0.1.0-py3-none-any.whl
    uv run python bench/database_layout.py venv-0.1.0/bin/python

The argument is the Python of an environment with headwater 0.1.0, the wheel from PyPI, and the
same dependencies as this one, from uv.lock: with newer pydantic or pyarrow there, the times
would compare the dependencies too. This environment, the branch, measures the tables, and that
one the old layout, with this same script run as `database_layout.py measure <folder> <runs>`,
which uses only `hw.load`, `hw.open` and `validate`, the same in both versions.

The databases are synthetic, of 160 and of 2,000 processes, on the toy release that `headwater
demo` ships, so no licensed data is read. Process `p0042` makes `p0042`, buys 0.25 kg of cotton
fabric, 0.5 kWh of electricity and 0.1 of the process before it, emits 0.01 kg of CO2 and sends
0.05 kg of textile waste to incineration: six lines, every supplier linked. The old layout is
written by this script, the tables by headwater's own writer. Each version must read the same
records from its layout (the hash of `records_of` is compared), or the script stops.

Each measure is a fresh `hw.open` of the folder, then `validate` against the background, which is
loaded once before anything is timed: the first run, then the median of the next ones, in
milliseconds. Standard output and bench/results/database_layout_results.json hold counts and
times only, with the load average before and after, because other work on the machine slows
every row.
"""

import json
import os
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
SIZES = (160, 2000)
RUNS = 7
RELEASE = "toy-background@6864b7dbb65d"


def measure(folder: Path, runs: int) -> dict[str, Any]:
    """Times of `hw.open` and `validate` of one database, in the headwater this Python has."""
    import headwater as hw
    from headwater.query._hash import records_of, sha256_of

    background = hw.load(RELEASE)
    opened: list[float] = []
    validated: list[float] = []
    database = None
    for _ in range(runs):
        start = time.perf_counter()
        database = hw.open(folder)
        opened.append((time.perf_counter() - start) * 1000)
        start = time.perf_counter()
        report = database.validate(background)
        validated.append((time.perf_counter() - start) * 1000)
        assert not report.errors, report.errors
    assert isinstance(database, hw.Database)
    from importlib.metadata import version

    return {
        "headwater": hw.__version__,
        "dependencies": {
            d: version(d) for d in ("numpy", "scipy", "pyarrow", "pydantic", "duckdb")
        },
        "processes": len(database.folder.processes),
        "files": sum(1 for p in folder.rglob("*") if p.is_file()),
        "records": sha256_of(records_of(database.folder)),
        "open_first_ms": round(opened[0], 1),
        "open_median_ms": round(statistics.median(opened[1:]), 1),
        "validate_first_ms": round(validated[0], 1),
        "validate_median_ms": round(statistics.median(validated[1:]), 1),
    }


def _lines(n: int) -> list[dict[str, str]]:
    code = f"p{n:04d}"
    lines = [
        {"line": "e01", "role": "reference", "flow": f"bench:{code}", "amount": "1", "unit": "kg"},
        {"line": "e02", "role": "input", "flow": "toy:cotton-fabric", "amount": "0.25"},
        {"line": "e03", "role": "input", "flow": "toy:electricity", "amount": "0.5"},
        {"line": "e04", "role": "emission", "flow": "toy:co2", "amount": "0.01", "unit": "kg"},
        {"line": "e05", "role": "waste_to_treatment", "flow": "toy:textile-waste"},
    ]
    lines[1] |= {"unit": "kg", "supplier": "toy:fabric-production"}
    lines[2] |= {"unit": "kWh", "supplier": "toy:electricity-production"}
    lines[4] |= {"amount": "0.05", "unit": "kg", "supplier": "toy:incineration-textile-waste"}
    if n > 0:
        before = f"p{n - 1:04d}"
        lines.append(
            {"line": "e06", "role": "input", "flow": f"bench:{before}", "amount": "0.1"}
            | {"unit": "kg", "supplier": f"bench:{before}"}
        )
    return lines


def write(root: Path, size: int, layout: str) -> Path:
    """A synthetic database `bench` of `size` processes in `layout`, `old` or `tables`."""
    folder = root / layout / "bench"
    folder.mkdir(parents=True)
    (folder / "database.yaml").write_text(
        f"name: bench\nbackground: {RELEASE}\nmethods: [toy-climate]\n"
    )
    flows = ["id,name,kind,unit,compartment,cas"]
    flows += [f"bench:p{n:04d},product {n},product,kg,," for n in range(size)]
    (folder / "flows.csv").write_text("\n".join(flows) + "\n")
    columns = ("line", "role", "flow", "amount", "unit", "supplier")
    if layout == "old":
        (folder / "processes").mkdir()
        for n in range(size):
            code = f"p{n:04d}"
            (folder / "processes" / f"{code}.yaml").write_text(
                f"name: process {n}\nlocation: GLO\nkind: ordinary_transforming_activity\n"
            )
            rows = [",".join(columns)]
            rows += [",".join(line.get(c, "") for c in columns) for line in _lines(n)]
            (folder / "processes" / f"{code}.csv").write_text("\n".join(rows) + "\n")
        return folder
    from headwater.study._write import exchanges_csv, processes_csv

    codes = [f"p{n:04d}" for n in range(size)]
    rows = [
        {"id": code, "name": f"process {n}", "location": "GLO"}
        | {"kind": "ordinary_transforming_activity"}
        for n, code in enumerate(codes)
    ]
    lines = [{"process": code, **line} for n, code in enumerate(codes) for line in _lines(n)]
    (folder / "processes.csv").write_text(processes_csv(rows))
    (folder / "exchanges.csv").write_text(exchanges_csv(lines))
    return folder


def machine() -> str:
    brand = subprocess.run(
        ["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True
    ).stdout.strip()
    system = f"{platform.system()} {platform.release()}"
    return f"{brand or platform.machine()}, {os.cpu_count()} cores, {system}"


def _run(python: str, folder: Path, env: dict[str, str]) -> dict[str, Any]:
    done = subprocess.run(
        [python, "-I", __file__, "measure", str(folder), str(RUNS)],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    return json.loads(done.stdout)


def main(old_python: str) -> None:
    import headwater as hw

    result: dict[str, Any] = {
        "date": datetime.now(UTC).date().isoformat(),
        "machine": machine(),
        "python": platform.python_version(),
        "runs": RUNS,
        "release": RELEASE,
        "load_average_before": [round(v, 2) for v in os.getloadavg()],
        "sizes": {},
    }
    with tempfile.TemporaryDirectory(prefix="headwater-layout-") as temp:
        root = Path(temp)
        home = root / "home"
        env = {**os.environ, "HEADWATER_HOME": str(home)}
        demo = root / "demo"
        command = str(Path(sys.executable).parent / "headwater")
        for args in (["demo", str(demo)], ["release", "pull", str(demo / "releases" / RELEASE)]):
            subprocess.run([command, *args], check=True, capture_output=True, env=env)
        for size in SIZES:
            old = _run(old_python, write(root / str(size), size, "old"), env)
            new = _run(sys.executable, write(root / str(size), size, "tables"), env)
            if old["records"] != new["records"] or old["processes"] != size:
                raise SystemExit(f"{size}: the two layouts do not read the same records")
            if old["dependencies"] != new["dependencies"]:
                raise SystemExit("the two environments have different dependencies: see the top")
            result["sizes"][str(size)] = {"0.1.0": old, hw.__version__: new}
            shutil.rmtree(root / str(size))
    result["load_average_after"] = [round(v, 2) for v in os.getloadavg()]
    out = HERE / "results" / "database_layout_results.json"
    out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    if sys.argv[1] == "measure":
        print(json.dumps(measure(Path(sys.argv[2]), int(sys.argv[3]))))
    else:
        main(sys.argv[1])
