"""Import of an ecoSpold2 folder into a release: the time of each stage, best of a few runs.

    uv run python bench/import_timing.py <results.json> [runs] [--label before] [--exclude exclude.csv]

Reads `$ECOINVENT_DIR` (a folder holding datasets/ and MasterData/, the 3.12 cut-off) and writes
each release into a new temporary folder under `$HEADWATER_WORK`, deleted after the run. The
3.12 files have no empty file, so no exclude file is needed; `--exclude` takes one, as for
`headwater release import`.

Runs are added to those already in the JSON under the same label, so two versions of headwater
can be timed in turns (before, after, before, after) by running this script from each checkout
with one run at a time. The best run of each label is kept as `best`.

The JSON holds timings, counts and the release name only. Run it on a quiet machine: the
parse stage uses every CPU, and the load average before each run is recorded.
"""

import json
import os
import platform
import shutil
import sys
import tempfile
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

from headwater.io.ecospold2 import read_ecospold2
from headwater.io.exclude import Exclusion, read_exclude


def run(folder: Path, exclude: Sequence[Exclusion], work: Path) -> dict[str, object]:
    start = time.perf_counter()
    source = read_ecospold2(folder, version="3.12", exclude=exclude)
    read = time.perf_counter() - start
    into = Path(tempfile.mkdtemp(prefix="import-timing-", dir=work))
    try:
        start = time.perf_counter()
        release = source.freeze(into=into)
        freeze = time.perf_counter() - start
        rows = {name: entry.rows for name, entry in release.manifest.tables.items()}
        return {
            "read_s": round(read, 2),
            "freeze_s": round(freeze, 2),
            "total_s": round(read + freeze, 2),
            "release": release.path.name,
            "hash": release.hash,
            "rows": rows,
        }
    finally:
        shutil.rmtree(into)


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    exclude = (
        read_exclude(Path(sys.argv[sys.argv.index("--exclude") + 1]))
        if "--exclude" in sys.argv
        else ()
    )
    out = Path(sys.argv[1])
    runs = int(sys.argv[2]) if len(sys.argv) > 2 and not sys.argv[2].startswith("--") else 3
    label = sys.argv[sys.argv.index("--label") + 1] if "--label" in sys.argv else "run"
    folder = Path(os.environ["ECOINVENT_DIR"])
    work = Path(os.environ["HEADWATER_WORK"])
    found = []
    for i in range(runs):
        load = os.getloadavg()[0]
        result = run(folder, exclude, work)
        result["load_before"] = round(load, 2)
        found.append(result)
        print(f"run {i + 1}: {result['read_s']} s read, {result['freeze_s']} s freeze", flush=True)
    results = json.loads(out.read_text()) if out.exists() else {}
    found = [*results.get(label, {}).get("runs", []), *found]
    best = min(found, key=lambda r: r["total_s"])  # pyright: ignore[reportArgumentType, reportUnknownLambdaType]
    hashes = {r["hash"] for r in found}
    if len(hashes) != 1:
        raise SystemExit(f"runs gave different hashes: {hashes}")
    results[label] = {
        "machine": f"{platform.machine()}, {platform.system()} {platform.release()}",
        "cpus": os.cpu_count(),
        "python": platform.python_version(),
        "pyarrow": version("pyarrow"),
        "lxml": version("lxml"),
        "date": datetime.now(UTC).date().isoformat(),
        "runs": found,
        "best": {k: best[k] for k in ("read_s", "freeze_s", "total_s")},
        "release": best["release"],
        "rows": best["rows"],
    }
    out.write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps(results[label]["best"]))


if __name__ == "__main__":
    main()
