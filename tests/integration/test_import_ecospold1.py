"""Import of a licensed ecoSpold1 package, BAFU:2026. Runs only when ECOSPOLD1_DIR is set, to the
folder that holds its XML files, `datasets/`.

Nothing of the package is kept: the test reports counts, and run with `-s` it prints them with the
import time and the load average. The numbers asserted are the package's own structure (one
dataset per file) and the reasons lines can fail to link, not its content.
"""

import os
import re
import time
from collections import Counter
from pathlib import Path

import pytest

from headwater.io.ecospold1 import read
from headwater.io.formats import choose
from headwater.io.inventory import Inventory, link_inventory
from headwater.io.source import InventoryOptions, SourceKind
from headwater.model import InvalidDataError, IssueStatus, Role

ECOSPOLD1_DIR = os.environ.get("ECOSPOLD1_DIR")

pytestmark = pytest.mark.skipif(not ECOSPOLD1_DIR, reason="ECOSPOLD1_DIR is not set")

WORKERS = os.environ.get("ECOSPOLD1_WORKERS")
"""Processes that parse files. Unset: one per CPU."""

OPTIONS = InventoryOptions(
    version="2026", source="bafu", licence="BAFU, cite", workers=int(WORKERS) if WORKERS else None
)

OPEN_REASONS = {"no-supplier", "ambiguous-supplier"}
"""Why a line may be left open: its supplier is not in the package, or the line gives too little
to choose between several. Anything else (a unit that does not convert, a process that cannot be
split, a refused line) would stop the import."""


@pytest.fixture(scope="module")
def folder() -> Path:
    assert ECOSPOLD1_DIR
    return Path(ECOSPOLD1_DIR)


@pytest.fixture(scope="module")
def imported(folder: Path) -> tuple[Inventory, dict[str, float]]:
    """The import, and the seconds to read the files and to run the shared stages, with the load
    average before."""
    load = os.getloadavg()[0]
    start = time.perf_counter()
    source = read(folder, OPTIONS)
    middle = time.perf_counter()
    inventory = link_inventory(source)
    end = time.perf_counter()
    return inventory, {"load": load, "read": middle - start, "stages": end - middle}


@pytest.fixture(scope="module")
def inventory(imported: tuple[Inventory, dict[str, float]]) -> Inventory:
    return imported[0]


@pytest.fixture(scope="module")
def timed(imported: tuple[Inventory, dict[str, float]]) -> dict[str, float]:
    return imported[1]


def test_the_folder_is_detected_as_ecospold1(folder: Path) -> None:
    assert choose(folder, SourceKind.inventory).name == "ecospold1"


def _types(folder: Path) -> dict[str, str]:
    """The `dataSetInformation@type` of each file: 1 a unit process, 2 an aggregated one."""
    found: dict[str, str] = {}
    for file in folder.glob("*.xml"):
        match = re.search(rb'dataSetInformation[^>]*type="(\d)"', file.read_bytes()[:20000])
        found[file.name] = match.group(1).decode() if match else "?"
    return found


def test_every_file_is_one_process_and_the_report_counts_what_did_not_link(
    folder: Path, inventory: Inventory, timed: dict[str, float]
) -> None:
    files = sum(1 for _ in folder.glob("*.xml"))
    lines = inventory.exchange_table
    roles = Counter(lines.column("role").to_pylist())
    types = _types(folder)
    aggregated = {
        p["id"]
        for p in inventory.process_table.to_pylist()
        if types[p["source_ref"].split("#")[0]] == "2"
    }
    buying = sum(roles[r.value] for r in (Role.input, Role.own_use, Role.avoided_product))
    in_aggregated = sum(
        1
        for r in lines.to_pylist()
        if r["process_id"] in aggregated and r["link_status"] == "data_gap"
    )
    gaps = [i for i in inventory.issues if i.status is IssueStatus.data_gap]
    reasons = Counter(p.code for p in inventory.problems)
    linked = buying - len(inventory.problems) - len(gaps)
    warnings = Counter(w.code for w in inventory.warnings)
    unsplit = [p for p in inventory.problems if p.code == "not-allocated"]
    several = [p for p in inventory.process_table.to_pylist() if ":" in p["id"].split(":", 2)[-1]]
    print(
        f"\nprocesses {inventory.process_table.num_rows:,} from {files:,} files "
        f"(by dataSetInformation type: {dict(sorted(Counter(types.values()).items()))}), "
        f"lines {lines.num_rows:,}, flows {len(inventory.flows):,}"
        f"\nroles {dict(sorted(roles.items()))}"
        f"\nbuying lines {buying:,}: linked by name {linked:,}, "
        f"open {len(inventory.problems):,} by reason {dict(reasons)}"
        f"\nmulti-output processes split {len(several)}, left unsplit as open issues {len(unsplit)}"
        f"\nnegative product inputs read as avoided_product {roles[Role.avoided_product.value]}"
        f"\naggregated (type 2) processes {len(aggregated)}, with {in_aggregated} buying lines "
        f"resolved as data gaps ({len(gaps)} issues)"
        f"\nwarnings {dict(sorted(warnings.items()))}"
        f"\nread {timed['read']:.1f} s, stages {timed['stages']:.1f} s, "
        f"load average before {timed['load']:.1f}"
    )
    assert inventory.process_table.num_rows == files
    assert not unsplit
    assert set(reasons) <= OPEN_REASONS
    assert len(inventory.problems) == sum(reasons.values())
    assert in_aggregated == len(gaps)


def test_freezing_refuses_while_lines_are_open_and_names_each(
    inventory: Inventory, tmp_path: Path
) -> None:
    with pytest.raises(InvalidDataError) as raised:
        inventory.freeze(into=tmp_path)

    assert {p.code for p in raised.value.problems} <= OPEN_REASONS
    assert len(raised.value.problems) == len(inventory.problems)
