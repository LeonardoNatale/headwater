"""Start-up time of the `headwater` command: wall time of a few commands, best and median of N runs.

    uv run python bench/cli_startup.py [runs] [--json out.json]

Runs the `headwater` script of the active environment as a subprocess, so the time is what a
person waits for: the interpreter, the imports and the command. The commands are `--help`,
`find --help`, `find` on the toy as a release (built into a temporary HEADWATER_HOME) and as a
folder, and `calc` on the toy T-shirt. It also lists which of the heavy libraries (`pandas`,
`pyarrow.dataset`, `duckdb`, `scipy`, `numpy`, `pydantic`, `lxml` and `openpyxl`) `import
headwater.cli` loads. The toy is in tests/fixtures/toy: no licensed data.

`import pyarrow.dataset` imports pandas when pandas is installed, so run it in both
environments, with and without the `notebooks` group, and read the JSON's `pandas_installed`:

    uv run python bench/cli_startup.py
    uv run --group notebooks python bench/cli_startup.py

The load average before and after the runs is printed and kept in the JSON: run it on a quiet
machine, and read the times with that number next to them.
"""

import argparse
import json
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import time
from importlib.metadata import version
from importlib.util import find_spec
from pathlib import Path

TOY = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "toy"
BACKGROUND = ["--release", str(TOY / "background"), "--namespace", "toy"]
METHOD = ["--method", str(TOY / "methods" / "toy-climate")]

COMMANDS = {
    "--help": ["--help"],
    "find --help": ["find", "--help"],
    "find, folder": ["find", "electricity", *BACKGROUND],
    "calc, folder": ["calc", str(TOY / "studies" / "tshirt"), *BACKGROUND, *METHOD],
}

HEAVY = (
    "pandas",
    "pyarrow",
    "pyarrow.dataset",
    "pyarrow.parquet",
    "duckdb",
    "scipy",
    "scipy.sparse.linalg",
    "numpy",
    "pydantic",
    "lxml",
    "openpyxl",
)

LOADED = """
import sys
import headwater.cli
heavy = {heavy!r}
print(*[name for name in heavy if name in sys.modules])
"""


def headwater() -> str:
    """The `headwater` script that was installed next to this interpreter."""
    return str(Path(sys.executable).parent / "headwater")


def toy_release(home: Path) -> str:
    """Build the toy background as a release in `home`, and return its `name@hash` reference."""
    from headwater.release import ReleaseInfo, build
    from headwater.study import read_method, read_process_folder

    os.environ["HEADWATER_HOME"] = str(home)
    folder = read_process_folder(TOY / "background", namespace="toy")
    info = ReleaseInfo(
        name="toy-background",
        source="toy",
        version="1",
        system_model="cutoff",
        licence="CC0-1.0",
        flow_list="toy",
    )
    release = build(
        info,
        processes=folder.processes,
        flows=folder.flows,
        flow_properties=folder.flow_properties,
        methods=(read_method(TOY / "methods" / "toy-climate"),),
    )
    return f"{release.name}@{release.hash[:12]}"


def time_command(args: list[str]) -> float:
    start = time.perf_counter()
    done = subprocess.run([headwater(), *args], capture_output=True, text=True)
    elapsed = time.perf_counter() - start
    if done.returncode != 0:
        raise SystemExit(f"headwater {' '.join(args)} exited {done.returncode}:\n{done.stderr}")
    return elapsed


def loaded_libraries() -> list[str]:
    code = LOADED.format(heavy=HEAVY)
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    return done.stdout.split()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("runs", nargs="?", type=int, default=7, help="runs of each command")
    parser.add_argument("--json", type=Path, help="write the timings to this file")
    args = parser.parse_args()
    runs, out = args.runs, args.json

    results: dict[str, dict[str, float]] = {}
    load_before = os.getloadavg()
    with tempfile.TemporaryDirectory(prefix="cli-startup-") as home:
        release = ["find", "electricity", "--release", toy_release(Path(home))]
        commands = {**COMMANDS, "find, release": release}
        # one run first, so the bytecode caches and the file cache are warm for every command
        for command in commands.values():
            time_command(command)
        for name, command in commands.items():
            times = [time_command(command) for _ in range(runs)]
            results[name] = {
                "best_s": round(min(times), 3),
                "median_s": round(statistics.median(times), 3),
            }
    load_after = os.getloadavg()
    libraries = loaded_libraries()

    pandas = find_spec("pandas") is not None
    print(f"{'command':<15}{'best':>8}{'median':>9}")
    for name, row in results.items():
        print(f"{name:<15}{row['best_s']:>7.3f}s{row['median_s']:>8.3f}s")
    print(f"pandas installed: {pandas}")
    print(f"import headwater.cli loads: {', '.join(libraries) or 'none of the heavy libraries'}")
    print(f"load average before {load_before[0]:.2f}, after {load_after[0]:.2f}, {runs} runs each")

    if out is not None:
        record = {
            "date": time.strftime("%Y-%m-%d"),
            "machine": platform.platform(),
            "python": platform.python_version(),
            "headwater": version("headwater"),
            "pandas_installed": pandas,
            "runs": runs,
            "load_before": round(load_before[0], 2),
            "load_after": round(load_after[0], 2),
            "commands": results,
            "libraries_loaded": libraries,
        }
        out.write_text(json.dumps(record, indent=2) + "\n")


if __name__ == "__main__":
    main()
