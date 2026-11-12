"""BAFU:2026 as an openLCA JSON-LD package. Runs only when `BAFU_JSONLD` is set.

`BAFU_JSONLD` is the package openLCA 2 exports (File > Export > JSON-LD) from BAFU's .zolca
backup: a .zip or its folder. `BAFU_LCIA_RESULTS` is BAFU's own LCIA results workbook; with it,
the IPCC 2021 scores are compared with BAFU's. The data is licensed (citation required, no
resale): the test prints counts only, and nothing of it is written into the repository. Run
with `-s` to see the counts and the import time.
"""

import os
import time
from collections import Counter
from pathlib import Path
from typing import Any

import openpyxl
import pytest

import headwater as hw
from headwater.io.formats import inventory_from, method_from
from headwater.io.inventory import Inventory
from headwater.io.method import write_method
from headwater.io.olca_jsonld.package import Package
from headwater.io.source import InventoryOptions, MethodOptions
from headwater.model import IssueStatus
from headwater.query._internals import internals
from headwater.release import Release

PACKAGE = os.environ.get("BAFU_JSONLD")
RESULTS = os.environ.get("BAFU_LCIA_RESULTS")

pytestmark = pytest.mark.skipif(not PACKAGE, reason="BAFU_JSONLD is not set")

OPTIONS = InventoryOptions(
    version="2026", source="bafu", licence="BAFU:2026 terms of use: citation, no resale"
)
COMPARED = ("IPCC 2021", "GWP 100 [kg CO2-eq]")
"""The method of the package, and the column of BAFU's results it is compared with."""


def _load() -> str:
    return ", ".join(f"{x:.1f}" for x in os.getloadavg())


@pytest.fixture(scope="module")
def inventory() -> Inventory:
    assert PACKAGE
    load = _load()
    start = time.perf_counter()
    found = inventory_from(Path(PACKAGE), OPTIONS, format="olca-jsonld")
    took = time.perf_counter() - start
    print(f"\nread and linked in {took:.1f} s, load average {load} before, {_load()} after")
    return found


@pytest.fixture(scope="module")
def release(inventory: Inventory, tmp_path_factory: pytest.TempPathFactory) -> Release:
    if inventory.problems:
        pytest.skip(f"{len(inventory.problems)} open issues: the release cannot be frozen")
    start = time.perf_counter()
    found = inventory.freeze(into=tmp_path_factory.mktemp("releases"))
    print(f"\nfrozen in {time.perf_counter() - start:.1f} s, load average {_load()}")
    return found


def test_the_package_reads_with_every_unlinked_line_an_open_issue(inventory: Inventory) -> None:
    lines = inventory.exchange_table
    statuses = Counter(lines.column("link_status").to_pylist())
    issues = Counter(i.status for i in inventory.issues)
    reasons = Counter(p.code for p in inventory.problems)
    methods = Counter(
        w.message.rsplit(" ", 1)[1] for w in inventory.warnings if w.code == "allocation-method"
    )
    ids = inventory.process_table.column("id").to_pylist()
    splits = sum(1 for p in ids if str(p).count(":") > 2)
    print(
        f"\nprocesses {inventory.process_table.num_rows:,} (of which splits {splits:,}), "
        f"lines {lines.num_rows:,}, flows {len(inventory.flows):,}"
        f"\nlines by link status: {dict(statuses)}"
        f"\nissues by status: {dict(issues)}; open issues by reason: {dict(reasons)}"
        f"\nprocesses with several products, by default allocation method: {dict(methods)}"
        f"\nwarnings by code: {dict(Counter(w.code for w in inventory.warnings))}"
    )
    assert issues[IssueStatus.open] == len(inventory.problems)


def test_every_method_of_the_package_maps(release: Release, tmp_path: Path) -> None:
    assert PACKAGE
    with Package(Path(PACKAGE)) as package:
        names = sorted(str(data["name"]) for _, data in package.all("lcia_methods"))
    for name in names:
        mapped = method_from(Path(PACKAGE), release, MethodOptions(method=name))
        factors = sum(r.factors for r in mapped.indicators)
        unmapped = sum(len(r.unmapped) for r in mapped.indicators)
        print(
            f"\n{name}: {len(mapped.indicators)} indicators, {factors:,} factors, "
            f"{factors - unmapped:,} mapped, {unmapped:,} not in the release"
        )
        assert factors > unmapped


@pytest.mark.skipif(not RESULTS, reason="BAFU_LCIA_RESULTS is not set")
def test_scores_against_bafus_own_results(release: Release, tmp_path: Path) -> None:
    assert PACKAGE and RESULTS
    method, column = COMPARED
    mapped = method_from(Path(PACKAGE), release, MethodOptions(method=method))
    background = hw.load(release.path, method=write_method(mapped, tmp_path / "method"))
    [indicator] = [i.id for i in mapped.method.indicators if "100" in i.id] or [
        i.id for i in mapped.method.indicators
    ]
    scores = internals(background).solved.x[indicator].values
    ours = {
        f"{p.name} - {p.location}": scores[internals(background).index[p.id]]
        for p in release.processes()
        if p.id in internals(background).index
    }
    theirs = _results(Path(RESULTS), column)
    matched = sorted(set(ours) & set(theirs))
    differences = sorted(
        (abs(ours[k] - theirs[k]) / max(abs(ours[k]), abs(theirs[k]), 1e-300), k)
        for k in matched
        if ours[k] != theirs[k]
    )
    above = [d for d in differences if d[0] > 1e-6]
    largest = differences[-1][0] if differences else 0.0
    print(
        f"\n{method} {indicator}: {len(theirs):,} products in BAFU's results, "
        f"{len(matched):,} matched by name and location, largest relative difference "
        f"{largest:.3g}, {len(above):,} above 1e-6"
    )
    assert matched


def _results(path: Path, column: str) -> dict[str, float]:
    """BAFU's score of each product in one column, by `<name> - <location>`."""
    book = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        rows = book.worksheets[1].iter_rows(values_only=True)
        next(rows)
        header: tuple[Any, ...] = next(rows)
        at = header.index(column)
        return {
            str(r[0]): float(str(r[at])) for r in rows if r[0] is not None and r[at] is not None
        }
    finally:
        book.close()
