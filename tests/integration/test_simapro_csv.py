"""A real SimaPro CSV process export, read and linked. Runs only when `SIMAPRO_CSV` is set.

`SIMAPRO_CSV` is the export's path. `SIMAPRO_METHOD_CSV`, a SimaPro method export from the same
SimaPro version, is optional: its methods are mapped onto the release. The data may be licensed,
so nothing from it is written here: counts only. Run with `-s` to see the report.
"""

import os
import time
from collections import Counter
from pathlib import Path
from typing import cast

import pytest

from headwater.io.formats import inventory_from, method_from
from headwater.io.simapro_csv.export import read_export
from headwater.io.simapro_csv.text import ProductText
from headwater.io.source import InventoryOptions, MethodOptions

SIMAPRO_CSV = os.environ.get("SIMAPRO_CSV")
SIMAPRO_METHOD_CSV = os.environ.get("SIMAPRO_METHOD_CSV")

pytestmark = pytest.mark.skipif(not SIMAPRO_CSV, reason="SIMAPRO_CSV is not set")

OPTIONS = InventoryOptions(version="0", source="real", licence="the export's own terms")


def test_a_real_export_reads_links_and_reports(tmp_path: Path) -> None:
    assert SIMAPRO_CSV
    path = Path(SIMAPRO_CSV)
    load = os.getloadavg()[0]
    start = time.perf_counter()
    inventory = inventory_from(path, OPTIONS, format="simapro-csv")
    seconds = time.perf_counter() - start

    export = read_export(path)
    texts = {r.text for p in export.processes for r in p.lines if not r.section.is_elementary}
    keys = {ProductText.parse(t) for t in texts}
    lines = inventory.exchange_table
    linked = lines.num_rows - lines["supplier_id"].null_count
    reasons = Counter(p.code for p in inventory.problems)
    ids = cast(list[str], inventory.process_table["id"].to_pylist())
    split = [i for i in ids if i.count(":") > 2]
    print(
        f"\n{path.name}: {len(export.processes)} processes read, "
        f"{inventory.process_table.num_rows} after allocation, {lines.num_rows} lines; "
        f"{linked} lines linked by name; open issues {dict(reasons) or 'none'}; "
        f"{len(inventory.flows)} flows, {len(inventory.units)} units; "
        f"{len(split)} processes from allocation splits; "
        f"{len(texts)} product texts give {len(keys)} supplier keys; "
        f"warnings {dict(Counter(w.code for w in inventory.warnings)) or 'none'}; "
        f"read and linked in {seconds:.1f} s, load average {load:.2f} before"
    )
    assert inventory.process_table.num_rows >= len(export.processes)
    if not inventory.problems:
        inventory.freeze(into=tmp_path / "releases")


@pytest.mark.skipif(not SIMAPRO_METHOD_CSV, reason="SIMAPRO_METHOD_CSV is not set")
def test_a_real_method_maps_onto_the_real_export(tmp_path: Path) -> None:
    assert SIMAPRO_CSV and SIMAPRO_METHOD_CSV
    inventory = inventory_from(Path(SIMAPRO_CSV), OPTIONS, format="simapro-csv")
    if inventory.problems:
        pytest.skip("the export has open issues, so it does not freeze")
    release = inventory.freeze(into=tmp_path / "releases")
    names = [m.name for m in read_export(Path(SIMAPRO_METHOD_CSV)).methods]
    for name in names:
        mapped = method_from(
            Path(SIMAPRO_METHOD_CSV), release, MethodOptions(method=name), format="simapro-csv"
        )
        factors = sum(r.factors for r in mapped.indicators)
        unmapped = sum(len(r.unmapped) for r in mapped.indicators)
        print(
            f"\n{name}: {len(mapped.indicators)} indicators, {factors} factors, "
            f"{unmapped} unmapped, {len(mapped.not_characterised)} of {mapped.flows} "
            "elementary flows with no factor"
        )
