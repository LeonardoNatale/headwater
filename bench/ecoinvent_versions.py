"""What changes from one ecoinvent version to the next, for headwater: ids, flows and factors.

    export HEADWATER_HOME=/path/to/headwater-home           # holds both releases
    uv run python bench/ecoinvent_versions.py \
        ecoinvent-3.12-cutoff@b9428cdd8045 ecoinvent-3.13-cutoff@9bbbb9d97a81 \
        "/path/to/LCIA Implementation 3.12.xlsx" "/path/to/LCIA Implementation 3.13.xlsx" \
        bench/results/ecoinvent_versions_3.12_3.13_results.json

It counts, from the old release to the new one:

- `datasets`: the ids `<activity uuid>_<product uuid>` that carry over, the activity UUIDs that
  carry over, and the datasets whose name, product and location (the key of the same-dataset
  rule of `headwater upgrade`) carry over. The upgrade maps a dataset with none of these only by
  a correspondence or a mapping row.
- `elementary_flows`: those added and removed, by id.
- `methods`: EF v3.1 and the carbon categories of IPCC 2021 (D27), each mapped onto the new
  release: factors, those mapped and not, and elementary flows with no factor.
- `new_flows`: the flows added, with a row in any method of the new workbook, with a row of EF
  v3.1, and with a factor once mapped. `removed_flows`: rows of the new workbook naming them.
- `ef_rows`: EF v3.1's rows by category, flow name and compartment, in the two workbooks: the
  same row with another factor, rows only in one, and how many of the changed and new rows the
  mapping gives with the new workbook's factor.

The JSON holds counts and the names of the categories and removed flows only, no factor.
"""

import json
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import openpyxl

from headwater.io.lcia import MethodImport, import_method, read_workbook
from headwater.model import FlowKind
from headwater.release import Release, find

CARBON = {
    "IPCC 2021 (incl. biogenic CO2)": (
        "climate change: biogenic removals (incl. CO2)",
        "climate change: biogenic emissions (incl. CO2)",
        "climate change: biogenic (incl. CO2)",
    ),
    "IPCC 2021": ("climate change: direct land use change",),
}
"""The carbon categories imported next to EF v3.1 (D27)."""

type Row = tuple[str, str, str]
"""A workbook row's category, flow name and compartment."""


def datasets(old: Release, new: Release) -> dict[str, int]:
    def keys(release: Release) -> dict[str, tuple[str, str, str]]:
        rows = release.connect().execute(
            "SELECT id, name, reference_flow_id, location FROM processes"
        )
        return {i.rsplit(":", 1)[1]: (n, f, loc) for i, n, f, loc in rows.fetchall()}

    a, b = keys(old), keys(new)
    activities = {k.split("_")[0] for k in a} & {k.split("_")[0] for k in b}
    same = set(a.values()) & set(b.values())
    return {
        "old": len(a),
        "new": len(b),
        "same_id": len(set(a) & set(b)),
        "same_activity_uuid": len(activities),
        "activity_uuids_old": len({k.split("_")[0] for k in a}),
        "same_dataset": sum(v in same for v in a.values()),
        "no_same_dataset": sum(v not in same for v in a.values()),
        "new_by_dataset": sum(v not in same for v in b.values()),
        "product_uuids_kept": len({v[1] for v in a.values()} & {v[1] for v in b.values()}),
        "product_uuids_old": len({v[1] for v in a.values()}),
    }


def methods(book: Path, release: Release) -> dict[str, MethodImport]:
    found = {"EF-3.1": import_method(read_workbook(book, "EF v3.1"), release)}
    for name, categories in CARBON.items():
        imported = import_method(read_workbook(book, name, categories=categories), release)
        found[imported.method.id] = imported
    return found


def workbook_rows(book: Path) -> set[tuple[str, str]]:
    """Every (flow name, compartment) of the workbook's CFs sheet, in any method."""
    sheet = openpyxl.load_workbook(book, read_only=True, data_only=True)
    try:
        rows = sheet["CFs"].iter_rows(values_only=True)
        header = list(next(rows))
        n, c, s = (header.index(x) for x in ("Name", "Compartment", "Subcompartment"))
        return {(str(r[n]), f"{r[c]}/{r[s]}") for r in rows if r[n]}
    finally:
        sheet.close()


def main() -> None:
    if len(sys.argv) != 6:
        raise SystemExit(__doc__)
    old, new = find(sys.argv[1]), find(sys.argv[2])
    old_book, new_book, out = Path(sys.argv[3]), Path(sys.argv[4]), Path(sys.argv[5])

    elementary = {
        r: {f.id: f for f in r.flows() if f.kind is FlowKind.elementary} for r in (old, new)
    }
    added = {i: f for i, f in elementary[new].items() if i not in elementary[old]}
    removed = {i: f for i, f in elementary[old].items() if i not in elementary[new]}

    mapped = methods(new_book, new)
    ef = mapped["EF-3.1"]
    any_row = workbook_rows(new_book)
    ef_keys = {(r.name, r.compartment) for r in ef.workbook.rows}
    ef_flows = {f.flow_id for f in ef.method.factors}
    referenced = {f.flow_id for m in mapped.values() for f in m.method.factors}

    rows_old = {
        (r.category, r.name, r.compartment): r.factor
        for r in read_workbook(old_book, "EF v3.1").rows
    }
    rows_new = {(r.category, r.name, r.compartment): r.factor for r in ef.workbook.rows}
    changed = {k for k in rows_old.keys() & rows_new.keys() if rows_old[k] != rows_new[k]}
    only_new = set(rows_new) - set(rows_old)
    indicator = {r.indicator.name: r.indicator.id for r in ef.indicators}
    flow_id = {(f.name, f.compartment): i for i, f in elementary[new].items()}
    factor = {(f.indicator, f.flow_id): f.factor for f in ef.method.factors}

    def given(keys: set[Row]) -> int:
        """Rows the mapping gives with the new workbook's factor."""
        found = 0
        for category, name, compartment in keys:
            i, f = indicator.get(category), flow_id.get((name, compartment))
            if i and f and factor.get((i, f)) == rows_new[category, name, compartment]:
                found += 1
        return found

    results = {
        "date": datetime.now(UTC).date().isoformat(),
        "old": old.path.name,
        "new": new.path.name,
        "old_workbook": old_book.name,
        "new_workbook": new_book.name,
        "datasets": datasets(old, new),
        "elementary_flows": {
            "old": len(elementary[old]),
            "new": len(elementary[new]),
            "added": len(added),
            "removed": len(removed),
            "removed_names": sorted({f.name for f in removed.values()}),
        },
        "methods": {
            i: {
                "workbook_version": m.workbook.version,
                "factors": sum(r.factors for r in m.indicators),
                "mapped": sum(r.mapped for r in m.indicators),
                "unmapped": sum(len(r.unmapped) for r in m.indicators),
                "flows": m.flows,
                "no_factor": len(m.not_characterised),
            }
            for i, m in mapped.items()
        },
        "new_flows": {
            "with_a_row_in_any_method": sum(
                (f.name, f.compartment) in any_row for f in added.values()
            ),
            "with_a_row_of_ef": sum((f.name, f.compartment) in ef_keys for f in added.values()),
            "with_an_ef_factor": len(set(added) & ef_flows),
        },
        "removed_flows": {
            "with_a_row_in_any_method": sum(
                (f.name, f.compartment) in any_row for f in removed.values()
            ),
            "with_a_factor": len(set(removed) & referenced),
        },
        "ef_rows": {
            "old": len(rows_old),
            "new": len(rows_new),
            "changed": len(changed),
            "changed_by_category": dict(sorted(Counter(c for c, _, _ in changed).items())),
            "only_new": len(only_new),
            "only_old": len(set(rows_old) - set(rows_new)),
            "changed_given": given(changed),
            "only_new_given": given(only_new),
        },
    }
    out.write_text(json.dumps(results, indent=2) + "\n")
    print(
        json.dumps(
            {k: results[k] for k in ("datasets", "elementary_flows", "new_flows", "ef_rows")},
            indent=1,
        )
    )


if __name__ == "__main__":
    main()
