"""Import of an openLCA JSON-LD package into a release: the time of reading and linking, and of
freezing, with the load average.

    uv run python bench/olca_jsonld_import.py <package> <source> <version> <results.json>

Writes the release into a new temporary folder under `$HEADWATER_WORK` (or the system's), deleted
after the run. The JSON holds timings, counts and the release name only, never data of the
package: BAFU's licence allows citation, not copies.
"""

import json
import os
import platform
import shutil
import sys
import tempfile
import time
from collections import Counter
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

from headwater.io.formats import inventory_from
from headwater.io.source import InventoryOptions


def main() -> None:
    if len(sys.argv) != 5:
        raise SystemExit(__doc__)
    package, source, release_version, out = sys.argv[1:]
    options = InventoryOptions(version=release_version, source=source, licence="see the source")
    load_before = os.getloadavg()
    start = time.perf_counter()
    inventory = inventory_from(Path(package), options, format="olca-jsonld")
    read = time.perf_counter() - start
    result: dict[str, object] = {
        "date": datetime.now(UTC).isoformat(timespec="seconds"),
        "machine": f"{platform.machine()}, {os.cpu_count()} CPUs, {platform.platform()}",
        "python": platform.python_version(),
        "pyarrow": version("pyarrow"),
        "load_average_before": load_before,
        "read_and_link_s": round(read, 2),
        "processes": inventory.process_table.num_rows,
        "lines": inventory.exchange_table.num_rows,
        "flows": len(inventory.flows),
        "open_issues": dict(Counter(p.code for p in inventory.problems)),
    }
    if not inventory.problems:
        work = os.environ.get("HEADWATER_WORK")
        into = Path(tempfile.mkdtemp(prefix="olca-import-", dir=work))
        try:
            start = time.perf_counter()
            release = inventory.freeze(into=into)
            result["freeze_s"] = round(time.perf_counter() - start, 2)
            result["release"] = release.path.name
        finally:
            shutil.rmtree(into)
    result["load_average_after"] = os.getloadavg()
    Path(out).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
