"""Parity of the Excel import with Brightway, on a real workbook (licensed, local only).

The same workbook is imported by Brightway's ExcelImporter and by `headwater database import-excel`,
and each activity is scored by both with the 3-flow climate method on the same ecoinvent
release. Three steps, because Brightway runs in its own environment:

    export HEADWATER_WORK=/path/to/scratch
    export HEADWATER_HOME=/path/to/headwater-home            # holds the release
    export EXCEL_WORKBOOK=".../inventory.xlsx"       # a Brightway-format workbook, not in the repo
    export EXCEL_RELEASE=ecoinvent-3.12-cutoff@b9428cdd8045
    export EXCEL_METHOD=$HEADWATER_WORK/methods/climate-3-flows-3.12   # made by parity_numerics.py
    export EXCEL_MAPPING=$HEADWATER_WORK/excel-mapping.csv   # the decisions for lines that do not link
    cp -R $HEADWATER_WORK/brightway-parity-3.12 $HEADWATER_WORK/brightway-excel

    uv run python bench/excel_parity.py prepare
    BRIGHTWAY2_DIR=$HEADWATER_WORK/brightway-excel uv run --no-project --python 3.12 \\
        --with bw2data==4.5.4 --with bw2calc==2.3 --with bw2io==0.9.14 --with peewee==3.19.0 \\
        --with openpyxl --with pyarrow python bench/bw_excel_reference.py
    uv run python bench/excel_parity.py compare

`prepare` writes a copy of the workbook with the mapping file's decisions written into its
rows and the database names Brightway knows, so that Brightway links the lines to the same
suppliers. `compare` imports that copy, scores every activity and compares with Brightway's
scores. Scores are licensed values and stay in `$HEADWATER_WORK/excel/`;
`bench/results/excel_parity_<release name>_results.json` holds counts and relative differences only.
"""

import csv
import json
import os
import platform
import shutil
import sys
import tempfile
from pathlib import Path

import openpyxl
import yaml

import headwater as hw
from headwater.io.excel import (
    ImportMapping,
    ReleaseIndex,
    Status,
    import_excel,
    read_mapping,
    read_workbook,
)
from headwater.release import find

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results" / "excel_parity_results.json"
"""The run on 3.10 of 12 October 2026. `compare` writes `excel_parity_<release name>_results.json`."""
BIOSPHERE = "ecoinvent-3.12-biosphere"
BACKGROUND = "ecoinvent-3.12-cutoff"


def environment() -> tuple[Path, Path, str, Path, Path | None]:
    work = Path(os.environ["HEADWATER_WORK"]).expanduser().resolve() / "excel"
    work.mkdir(parents=True, exist_ok=True)
    mapping = os.environ.get("EXCEL_MAPPING")
    return (
        work,
        Path(os.environ["EXCEL_WORKBOOK"]).expanduser(),
        os.environ["EXCEL_RELEASE"],
        Path(os.environ["EXCEL_METHOD"]).expanduser(),
        Path(mapping).expanduser() if mapping else None,
    )


def prepare() -> None:
    """Copy the workbook with the decisions of the mapping file written into its rows."""
    work, source, release_name, _, mapping_path = environment()
    release = find(release_name)
    index = ReleaseIndex(release)
    mapping = read_mapping(mapping_path) if mapping_path else ImportMapping()
    book = read_workbook(source)
    sheets = openpyxl.load_workbook(source)
    decided = 0
    for activity in book.activities:
        for row in activity.exchanges:
            sheet_name, _, cell = row.source.partition("!A")
            ws = sheets[sheet_name]
            header = _header_of(ws, int(cell))
            at = lambda label: ws.cell(int(cell), header[label])  # noqa: E731
            if "database" in header and row.type in ("technosphere", "substitution"):
                if (row.database or "").casefold().startswith("ecoinvent"):
                    at("database").value = BACKGROUND
            if "database" in header and row.type == "biosphere":
                at("database").value = BIOSPHERE
            rule = mapping.technosphere_row(row.name, row.reference_product, row.location)
            if rule is not None:
                supplier = index.supplier(rule.target)
                assert supplier is not None, rule.target
                at("name").value = supplier.name
                at("reference product").value = supplier.product
                at("location").value = supplier.location
                decided += 1
    sheets.save(work / "inventory_310.xlsx")
    print(f"wrote {work / 'inventory_310.xlsx'}: {decided} lines rewritten by the mapping file")


def _header_of(ws: "openpyxl.worksheet.worksheet.Worksheet", row: int) -> dict[str, int]:  # type: ignore[name-defined]
    """The columns of the exchanges table that holds `row`: label to 1-based column."""
    for r in range(row, 0, -1):
        labels = {
            str(c.value).strip().lower(): c.column
            for c in ws[r]
            if isinstance(c.value, str) and c.value.strip()
        }
        if {"name", "amount"} <= labels.keys():
            return labels
    raise ValueError(f"no table header above row {row}")


def with_zero_factors(method: Path, folder: Path, flows: set[str]) -> Path:
    """The method with a zero factor for each flow the database emits that it does not cover.
    Brightway ignores such a flow, and headwater refuses it, so the comparison needs it covered."""
    if folder.exists():
        shutil.rmtree(folder)
    shutil.copytree(method, folder)
    indicator = yaml.safe_load((folder / "method.yaml").read_text())["indicators"][0]["id"]
    with (folder / "factors.csv").open(newline="") as file:
        known = {row["flow"] for row in csv.DictReader(file)}
    with (folder / "factors.csv").open("a") as file:
        for flow in sorted(flows - known):
            file.write(f"{indicator},{flow},,0\n")
    return folder


def compare() -> None:
    work, source, release_name, method, mapping_path = environment()
    release = find(release_name)
    prepared = work / "inventory_310.xlsx"
    scores = json.loads((work / "bw_scores.json").read_text())
    first = work / "database-first"
    if first.exists():
        shutil.rmtree(first)
    import_excel(prepared, release, first, method=method)
    flows = {
        row["flow"]
        for f in (first / "processes").glob("*.csv")
        for row in csv.DictReader(f.open(newline=""))
        if row["role"] in ("emission", "resource")
    }
    covered = with_zero_factors(method, work / "method", flows)
    database = work / "database"
    if database.exists():
        shutil.rmtree(database)
    done = import_excel(prepared, release, database, method=covered)
    report = done.report
    background = hw.load(release.path, method=covered)
    processes: dict[tuple[str, str], str] = {}
    for y in (database / "processes").glob("*.yaml"):
        d = yaml.safe_load(y.read_text())
        processes[(d["name"], d["location"])] = f"{report.name}:{y.stem}"
    differences: list[float] = []
    opened = hw.open(database)
    assert isinstance(opened, hw.Database)
    for item in scores:
        process = processes[(item["name"], item["location"])]
        score = opened.calc(background, process).score
        differences.append(abs(score - item["score"]) / max(abs(item["score"]), 1e-300))
    as_written = {}
    for label, decisions in (("without_mapping_file", None), ("with_mapping_file", mapping_path)):
        copy = Path(tempfile.mkdtemp()) / "database"
        counts = import_excel(
            source, release, copy, method=covered, mapping=decisions
        ).report
        as_written[label] = {
            "mapped": counts.count(Status.mapped),
            "unmapped": counts.count(Status.unmapped),
            "unexpressible": counts.count(Status.unexpressible),
            "waiting": counts.count(Status.waiting),
            "unmapped_by_code": dict(sorted(counts.codes(Status.unmapped).items())),
            "distinct_unmapped_lines": len({o.line.split(" | ", 1)[-1] for o in counts.outcomes if o.status is Status.unmapped}),
        }
    result = {
        "machine": f"{platform.machine()} {platform.system()} {platform.release()}",
        "python": platform.python_version(),
        "release": report.release,
        "workbook": {
            "activities": report.activities,
            "exchange_rows": report.exchange_rows,
            "parameter_rows": report.parameter_rows,
        },
        "brightway": json.loads((work / "bw_versions.json").read_text()),
        "workbook_as_written": as_written,
        "prepared_workbook": {
            "mapped": report.count(Status.mapped),
            "mapped_by": dict(sorted(report.mapped_by.items())),
            "unmapped": report.count(Status.unmapped),
            "unexpressible": report.count(Status.unexpressible),
        },
        "flows_given_a_zero_factor": len(
            flows - {r["flow"] for r in csv.DictReader((method / "factors.csv").open())}
        ),
        "activities_compared": len(differences),
        "largest_relative_difference": max(differences),
        "within_1e-6": sum(d < 1e-6 for d in differences),
        "within_1e-4": sum(d < 1e-4 for d in differences),
    }
    out = RESULTS.with_name(f"excel_parity_{release_name.split('@')[0]}_results.json")
    out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    steps = {"prepare": prepare, "compare": compare}
    if len(sys.argv) != 2 or sys.argv[1] not in steps:
        sys.exit("usage: excel_parity.py prepare|compare")
    steps[sys.argv[1]]()
