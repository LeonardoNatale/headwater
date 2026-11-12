"""headwater's IPCC 2021 GWP 100 on the BAFU:2026 ecoSpold1 import, against BAFU's own results.

    uv run python bench/bafu_gwp_check.py <results.json>

Reads `$ECOSPOLD1_DIR` (BAFU:2026's ecoSpold1 `datasets/` folder) and `$BAFU_LCIA_XLSX` (the
package's workbook of LCIA results, sheet `BAFU_2026 v1`). Imports the folder as source `bafu`,
version `2026`. The lines of the import that name no single supplier stay open, so the script
resolves them as data gaps in a temporary mapping: a process with one, or downstream of one, is
reported apart. It scores climate change with the GWP 100 factors below, which are IPCC AR6 values
for the gases of the package that have one, and matches each BAFU row to a process by name and
location. BAFU lists some processes per another unit than the file (MJ for kWh, m for km, m2 for
ha): the scores are compared after converting.

It also tests `Nm3 = m3`. The lines written in m3 that buy a product its maker writes in Nm3 are
the ones that link only because the reader renames Nm3. For the processes that have one, it works
out how far each reading of the unit is from BAFU's score: 1 m3 = 1 Nm3, and 1 m3 = 0.9317 Nm3
(0 degrees C against 20).

The JSON holds counts and quantiles only. The factors are data of this script, not a method of
the library.
"""

import csv
import json
import os
import platform
import sys
import tempfile
from collections import defaultdict
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

import numpy as np
import openpyxl
from lxml import etree

import headwater as hw
from headwater.io.ecospold1 import read
from headwater.io.inventory import link_inventory
from headwater.io.source import InventoryOptions
from headwater.link import read_supplier_mapping
from headwater.query._internals import internals
from headwater.release import Release

GWP100 = {
    "Carbon dioxide": 1,
    "Carbon dioxide, fossil": 1,
    "Carbon dioxide, land transformation": 1,
    "Methane, fossil": 29.8,
    "Methane, biogenic": 27.0,
    "Dinitrogen monoxide": 273,
    "Sulfur hexafluoride": 25200,
    "Nitrogen fluoride": 17400,
    "Ethane, 1,1,1,2-tetrafluoro-, HFC-134a": 1530,
    "Ethane, 1,1-difluoro-, HFC-152a": 164,
    "Ethane, pentafluoro-, HFC-125": 3740,
    "Methane, difluoro-, HFC-32": 771,
    "Methane, trifluoro-, HFC-23": 14600,
    "Propane, 1,1,1,3,3-pentafluoro-, HFC-245fa": 962,
    "Ethane, 1,1,2-trifluoro-, HFC-143": 364,
    "Methane, trichlorofluoro-, CFC-11": 6230,
    "Methane, dichlorodifluoro-, CFC-12": 12500,
    "Ethane, 1,1,2-trichloro-1,2,2-trifluoro-, CFC-113": 6520,
    "Ethane, 1,2-dichloro-1,1,2,2-tetrafluoro-, CFC-114": 9430,
    "Methane, chlorotrifluoro-, CFC-13": 16200,
    "Methane, tetrachloro-, CFC-10": 2200,
    "Methane, tetrafluoro-, CFC-14": 7380,
    "Ethane, hexafluoro-, HFC-116": 12400,
    "Methane, chlorodifluoro-, HCFC-22": 1960,
    "Ethane, 1,1,1-trichloro-, HCFC-140": 161,
    "Ethane, 2-chloro-1,1,1,2-tetrafluoro-, HCFC-124": 527,
    "Methane, bromotrifluoro-, Halon 1301": 7200,
    "Methane, bromochlorodifluoro-, Halon 1211": 1750,
    "Methane, bromo-, Halon 1001": 2.4,
    "Butane, perfluorocyclo-, PFC-318": 10300,
}
"""GWP 100 of the elementary flows to air of the package that have an IPCC AR6 value, by name.
Biogenic CO2 counts 0: counting it moves many more rows away from BAFU's scores."""

BAFU_TO_FILE = {
    ("MJ", "kWh"): 1 / 3.6,
    ("m", "km"): 1e-3,
    ("m2", "ha"): 1e-4,
    ("km*a", "my"): 1e3,
    **{k: 1.0 for k in [("Item(s)", "unit"), ("m2*a", "m2a"), ("h", "hr"), ("p*km", "personkm")]},
    **{k: 1.0 for k in [("t*km", "tkm"), ("km*a", "kmy")]},
}
"""How many of the file's unit one BAFU unit is worth, for the units the two write differently:
what BAFU's score per MJ is, from headwater's per kWh."""

NM3_IN_M3 = 0.9317
"""One m3 at 20 degrees C in normal cubic metres (0 degrees C), the physical reading of Nm3."""

OPEN = "bench: open line left as a data gap"


def quantiles(rel: np.ndarray) -> dict[str, float | int]:
    return {
        "n": int(rel.size),
        "median": float(np.median(rel)),
        "p90": float(np.percentile(rel, 90)),
        "p99": float(np.percentile(rel, 99)),
        "above_1e-3": int((rel > 1e-3).sum()),
        "above_1e-2": int((rel > 1e-2).sum()),
        "above_1e-1": int((rel > 1e-1).sum()),
    }


def written_in_nm3(folder: Path) -> dict[tuple[str, tuple[str, str]], float]:
    """The lines written in m3 that buy a product its maker writes in Nm3, by buyer (name and
    location), as (product, location) and amount."""
    makers: dict[tuple[str, str], str] = {}
    buyers: list[tuple[tuple[str, str], tuple[str, str | None], float]] = []
    for file in sorted(folder.glob("*.xml")):
        d = etree.parse(str(file)).getroot().find("dataset")
        info = d.find("metaInformation/processInformation")
        location = info.find("geography").get("location")
        process = (info.find("referenceFunction").get("name"), location)
        for e in d.iter("exchange"):
            out, inp = e.find("outputGroup"), e.find("inputGroup")
            if out is not None and out.text == "0":
                makers[e.get("name"), e.get("location") or location] = e.get("unit")
            elif inp is not None and inp.text in ("1", "2", "3", "5") and e.get("unit") == "m3":
                buyers.append((process, (e.get("name"), e.get("location")), float(e.get("meanValue"))))
    found: dict = defaultdict(list)
    for process, product, amount in buyers:
        if makers.get(product) == "Nm3":
            found[process].append((product, amount))
    return found


def main(out: Path) -> None:
    folder = Path(os.environ["ECOSPOLD1_DIR"])
    source = read(folder, InventoryOptions(version="2026", source="bafu", licence="BAFU, cite"))
    inventory = link_inventory(source)
    open_lines = {(i.process_id, i.line_id) for i in inventory.issues if i.status.value == "open"}
    keys = {
        (r["supplier_product"], r["supplier_location"] or "", r["supplier_unit"])
        for r in source.lines.to_pylist()
        if (r["process_id"], r["line_id"]) in open_lines
    }
    work = Path(tempfile.mkdtemp(prefix="bafu-gwp-"))
    mapping = work / "mapping.csv"
    with mapping.open("w", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(
            "supplier_id name product location unit supplier reason who when comment".split()
        )
        for product, location, unit in sorted(keys):
            writer.writerow(["", "", product, location, unit, "", OPEN, "bench", "2026-10-25", ""])
    linked = link_inventory(source, suppliers=read_supplier_mapping(mapping))
    release = Release.open(linked.freeze(into=work / "releases").path)
    names = {f.id: f.name for f in release.flows()}
    factors = [
        (f.id, GWP100[f.name])
        for f in release.flows()
        if f.kind.value == "elementary"
        and (f.compartment or "").startswith("emissions to air")
        and f.name in GWP100
    ]
    method = work / "method"
    method.mkdir()
    (method / "method.yaml").write_text(
        "id: gwp\nversion: '1'\nflow_list: bafu-2026\nindicators:\n"
        "  - id: gwp\n    name: GWP 100\n    unit: kg CO2-eq\n"
    )
    (method / "factors.csv").write_text(
        "indicator,flow,location,factor\n" + "".join(f"gwp,{i},,{c}\n" for i, c in factors)
    )
    background = hw.load(release.path, method=method)
    score = internals(background).solved.x["gwp"].values
    processes = {p.id: p for p in release.processes()}
    unit = {
        p.id: next(e.unit for e in p.exchanges if e.role.value == "reference")
        for p in processes.values()
    }
    mine = {(p.name, p.location): p.id for p in processes.values()}

    book = openpyxl.load_workbook(
        os.environ["BAFU_LCIA_XLSX"], read_only=True, data_only=True
    )
    rows = list(book["BAFU_2026 v1"].iter_rows(min_row=3, values_only=True))
    compared: dict[str, tuple[float, float]] = {}
    unconverted = 0
    for row in rows:
        if not row[0]:
            continue
        name, _, location = str(row[0]).rpartition(" - ")
        pid = mine.get((name, location))
        if pid is None:
            continue
        theirs_unit, ours_unit = str(row[3]), unit[pid]
        factor = 1.0 if theirs_unit == ours_unit else BAFU_TO_FILE.get((theirs_unit, ours_unit))
        if factor is None:
            unconverted += 1
            continue
        compared[pid] = (float(row[4]), float(score[internals(background).index[pid]]) * factor)

    gapped = {i.process_id for i in linked.issues if i.reason == OPEN}
    buyers_of = defaultdict(list)
    for p in processes.values():
        for e in p.exchanges:
            if e.supplier_id:
                buyers_of[e.supplier_id].append(p.id)
    affected, queue = set(gapped), list(gapped)
    while queue:
        for buyer in buyers_of[queue.pop()]:
            if buyer not in affected:
                affected.add(buyer)
                queue.append(buyer)

    ids = [pid for pid, (theirs, _) in compared.items() if abs(theirs) > 1e-9]
    theirs = np.array([compared[p][0] for p in ids])
    ours = np.array([compared[p][1] for p in ids])
    rel = np.abs(ours - theirs) / np.abs(theirs)
    touched = np.array([p in affected for p in ids])

    reference = {}
    for p in processes.values():
        for e in p.exchanges:
            if e.role.value == "reference":
                reference[names[e.flow_id], p.location] = p.id
    nm3_lines = written_in_nm3(folder)
    nm3_rows = []
    for process, lines in nm3_lines.items():
        pid = mine.get(process)
        if pid is None or pid not in ids:
            continue
        via = sum(
            amount * float(score[internals(background).index[reference[product[0], product[1]]]])
            for product, amount in lines
            if (product[0], product[1]) in reference
        )
        nm3_rows.append((compared[pid][0], compared[pid][1], via))
    a = np.array(nm3_rows)

    def distance(share: float) -> np.ndarray:
        """Relative distance to BAFU when the m3 lines count `share` of a normal cubic metre."""
        return np.abs(a[:, 1] - (1 - share) * a[:, 2] - a[:, 0]) / np.abs(a[:, 0])

    result = {
        "machine": f"{platform.machine()}, {platform.system()} {platform.release()}",
        "date": datetime.now(UTC).date().isoformat(),
        "python": platform.python_version(),
        "headwater_import": {"processes": source.processes.num_rows, "lines": source.lines.num_rows},
        "factors": len(GWP100),
        "factors_with_a_flow_in_the_package": len({names[i] for i, _ in factors}),
        "bafu_rows": sum(1 for r in rows if r[0]),
        "matched_by_name_and_location": len(compared) + unconverted,
        "not_converted": unconverted,
        "open_lines_resolved_as_gaps": len(open_lines),
        "processes_with_or_downstream_of_a_gap": int(touched.sum()),
        "all": quantiles(rel),
        "with_or_downstream_of_a_gap": quantiles(rel[touched]),
        "not_touched_by_a_gap": quantiles(rel[~touched]),
        "nm3": {
            "lines": sum(len(v) for v in nm3_lines.values()),
            "processes": len(nm3_rows),
            "one_m3_is_one_nm3": quantiles(distance(1.0)),
            "one_m3_is_0.9317_nm3": quantiles(distance(NM3_IN_M3)),
            "share_of_score_through_the_lines_median": float(np.median(a[:, 2] / a[:, 1])),
        },
        "versions": {"numpy": np.__version__, "lxml": version("lxml")},
    }
    out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    main(Path(sys.argv[1]))
