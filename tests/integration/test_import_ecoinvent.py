"""Import of a licensed ecoinvent 3.12 cut-off folder. Runs only when ECOINVENT_DIR is set.

`ECOINVENT_DIR` is the folder that holds `datasets/` and `MasterData/`, for example
`/path/to/ecoinvent/3.12-cutoff`. Every file of the folder reads and every line links, so
nothing is excluded and nothing is a data gap. The counts are measured on that folder. Run with
`-s` to see the counts and timings.
"""

import os
import time
from collections import Counter
from pathlib import Path

import pytest

from headwater.io.ecospold2 import read_ecospold2
from headwater.io.inventory import Inventory
from headwater.model import DEFAULT_UNITS, Catalog, FlowKind, Role

ECOINVENT_DIR = os.environ.get("ECOINVENT_DIR")

pytestmark = pytest.mark.skipif(not ECOINVENT_DIR, reason="ECOINVENT_DIR is not set")


@pytest.fixture(scope="module")
def folder() -> Path:
    assert ECOINVENT_DIR
    return Path(ECOINVENT_DIR)


@pytest.fixture(scope="module")
def source(folder: Path) -> Inventory:
    start = time.perf_counter()
    found = read_ecospold2(folder, version="3.12")
    print(f"\nread {len(found.processes):,} datasets in {time.perf_counter() - start:.1f} s")
    return found


def test_every_line_links_and_nothing_is_a_data_gap(source: Inventory) -> None:
    assert source.issues == ()
    assert source.problems == ()


def test_counts_of_ecoinvent_3_12(source: Inventory) -> None:
    catalog = Catalog(source.flows, source.processes, source.flow_properties, source.units)
    lines = [e for p in source.processes for e in p.exchanges]
    roles = Counter(e.role for e in lines)
    own_use = {e.process_id for e in lines if e.role is Role.own_use}
    flows = Counter(f.kind for f in source.flows)
    counts = {
        "datasets": len(source.processes),
        "treatments": sum(catalog.is_treatment(p) for p in source.processes),
        "waste inputs": roles[Role.waste_to_treatment],
        "recyclables": roles[Role.recyclable],
        "own-use datasets": len(own_use),
        "elementary flows": flows[FlowKind.elementary],
        "negative emissions": sum(e.role is Role.emission and e.amount < 0 for e in lines),
        "negative resources": sum(e.role is Role.resource and e.amount < 0 for e in lines),
    }
    print({**counts, "roles": dict(roles), "flows": dict(flows), "lines": len(lines)})

    assert counts == {
        "datasets": 26_533,
        "treatments": 4_623,
        "waste inputs": 47_740,
        "recyclables": 2_426,
        "own-use datasets": 856,
        "elementary flows": 9_850,
        "negative emissions": 2_288,
        "negative resources": 405,
    }
    assert (
        roles[Role.input]
        + roles[Role.own_use]
        + roles[Role.waste_to_treatment]
        + roles[Role.recyclable]
        == 312_518
    )
    assert roles[Role.emission] + roles[Role.resource] == 553_892
    assert roles[Role.reference] == 26_533
    assert len(lines) == 892_943


def test_units_merge_with_the_built_in_table(source: Inventory) -> None:
    DEFAULT_UNITS.merge(source.units)


def test_the_release_freezes(source: Inventory, tmp_path: Path) -> None:
    start = time.perf_counter()
    release = source.freeze(into=tmp_path)
    took = time.perf_counter() - start
    size = sum(p.stat().st_size for p in release.path.rglob("*") if p.is_file())
    print(f"\nfroze {release.path.name} in {took:.1f} s, {size / 1e6:.1f} MB")

    assert release.table("processes").num_rows == 26_533
    assert release.table("exchanges").num_rows == 892_943
    assert release.table("issues").num_rows == 0
    assert release.table("geographies").num_rows == 541
    con = release.connect()
    unknown = con.execute(
        "SELECT count(*) FROM processes WHERE location NOT IN (SELECT code FROM geographies)"
    ).fetchone()
    assert unknown == (0,)  # every location of the 335 the datasets use is a geography
