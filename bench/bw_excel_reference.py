"""The Brightway side of `excel_parity.py`: import the prepared workbook, score every activity.

Run with `uv run --no-project --python 3.12 --with bw2data==4.5.4 --with bw2calc==2.3
--with bw2io==0.9.14 --with peewee==3.19.0 --with openpyxl --with pyarrow python
bench/bw_excel_reference.py`, as `excel_parity.py` shows.
`BRIGHTWAY2_DIR` must be a copy of the parity project under `HEADWATER_WORK` (never your own):
the import writes the workbook's database into it.

The workbook is linked the way Brightway users usually do it: to itself by name, unit,
reference product and location, then to ecoinvent, then to the biosphere by name and categories. Scores
are those of bw2calc's factorized solver, one unit of each activity, for the 3-flow climate
method of `bw_reference.py`. They go to `$HEADWATER_WORK/excel/bw_scores.json`.
"""

import json
import os
import platform
import sys
from pathlib import Path

work = Path(os.environ["HEADWATER_WORK"]).expanduser().resolve()
brightway = Path(os.environ.get("BRIGHTWAY2_DIR", "")).expanduser().resolve()
if not brightway.is_relative_to(work):
    sys.exit(f"BRIGHTWAY2_DIR {brightway} is not under HEADWATER_WORK {work}")

import bw2calc as bc  # noqa: E402
import bw2data as bd  # noqa: E402
import bw2io as bi  # noqa: E402

PROJECT = "headwater-parity-3.12"
METHOD = ("headwater-parity", "climate 3 flows")
BACKGROUND = "ecoinvent-3.12-cutoff"
BIOSPHERE = "ecoinvent-3.12-biosphere"


def _version(module: object) -> str:
    found = getattr(module, "__version__", "unknown")
    return ".".join(map(str, found)) if isinstance(found, tuple) else str(found)


def _edge_counts(database: "bd.Database") -> dict[str, int]:
    counts: dict[str, int] = {}
    for activity in database:
        for exchange in activity.exchanges():
            counts[exchange["type"]] = counts.get(exchange["type"], 0) + 1
    return counts


def main() -> None:
    bd.projects.set_current(PROJECT)
    bd.config.p["biosphere_database"] = BIOSPHERE
    imp = bi.ExcelImporter(str(work / "excel" / "inventory_310.xlsx"))
    if imp.db_name in bd.databases:
        del bd.databases[imp.db_name]
    imp.apply_strategies()
    imp.match_database(fields=["name", "unit", "reference product", "location"])
    imp.apply_strategies()
    imp.match_database(BACKGROUND, fields=["name", "unit", "location", "reference product"])
    imp.apply_strategies()
    imp.match_database(BIOSPHERE, fields=["name", "categories"])
    unlinked = list(imp.unlinked)
    if unlinked:
        sys.exit(f"{len(unlinked)} edges did not link in Brightway")
    imp.write_database()
    database = bd.Database(imp.db_name)
    first = next(iter(database))
    lca = bc.LCA({first.id: 1}, METHOD)
    lca.lci(factorize=True)
    lca.lcia()
    rows = []
    for activity in database:
        lca.redo_lcia({activity.id: 1})
        rows.append(
            {
                "name": activity["name"],
                "location": activity["location"],
                "product": activity["reference product"],
                "score": lca.score,
            }
        )
    out = work / "excel" / "bw_scores.json"
    out.write_text(json.dumps(rows, indent=1))
    (work / "excel" / "bw_versions.json").write_text(
        json.dumps(
            {
                "python": platform.python_version(),
                "bw2data": _version(bd),
                "bw2calc": _version(bc),
                "bw2io": _version(bi),
                "edges": {k: n for k, n in _edge_counts(database).items()},
            },
            indent=1,
        )
    )
    print(
        f"scored {len(rows)} activities with bw2calc {bc.__version__}, "
        f"Python {platform.python_version()}, written to {out}"
    )


if __name__ == "__main__":
    main()
