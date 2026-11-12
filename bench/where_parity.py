"""Top contributors against Brightway's contribution analysis, on 10 products of ecoinvent 3.12.

For each product and indicator, headwater's `bg.where(product)` lists the processes that emit
the score, largest first. Brightway's contribution analysis does the same: bw2analyzer's
`ContributionAnalysis.top_processes` sums `lca.characterized_inventory` over its flows, which is
each process's direct emissions times how much it runs, and sorts by size. Three steps, because
Brightway runs in its own environment:

    export HEADWATER_WORK=/path/to/scratch
    export HEADWATER_HOME=/path/to/headwater-home         # holds the 3.12 release
    export WHERE_METHOD=$HEADWATER_WORK/pilot/methods/EF-3.1
    cp -R $HEADWATER_WORK/pilot/brightway $HEADWATER_WORK/where-parity/brightway

    uv run python bench/where_parity.py prepare
    BRIGHTWAY2_DIR=$HEADWATER_WORK/where-parity/brightway PILOT_BW_PROJECT=... \
        uv run bench/bw_where_reference.py
    uv run python bench/where_parity.py compare

The Brightway project, named by `PILOT_BW_PROJECT`, is a copy of the one T8 built for the 3.12
pilot: ecoinvent 3.12 cut-off and the 3.12 workbook's methods, imported by bw2io 0.9.14 from the
local files. `prepare` checks that the method folder is the one the indicators release
`ecoinvent-3.12-cutoff.EF-3.1@21c61d3ac200` was solved with, then writes the products and
headwater's top contributors. Values are licensed and stay in `$HEADWATER_WORK/where-parity/`;
`bench/results/where_parity_results.json` holds counts and relative differences only.

**The products.** One market per sector, so that the supply chains differ: the market in GLO
where ecoinvent has one, else in RoW. The municipal solid waste market is a treatment, so its
unit is 1 kg treated: it checks the sign on both sides.

**The comparison.** For each product and indicator, the top 10 processes of both, with values
compared by |a - b| <= 1e-6 x max(|a|, |b|). Both sides list 12, so that two processes in 10th
and 11th place that are within 1e-6 of each other count as a near tie, not as a different set.
In the same way, two neighbours within 1e-6 of each other may be in either order: Brightway
stores amounts as float32, so near ties can swap (docs/decisions.md, D21). Brightway's values
come from bw2calc's own solve, and from an accurate solve of Brightway's own matrices; the
residual of each is recorded.
"""

import json
import os
import platform
import sys
import time
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

import headwater as hw
from headwater.query._internals import internals
from headwater.release import Release, find, method_hash

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results" / "where_parity_results.json"
RELEASE = "ecoinvent-3.12-cutoff@b9428cdd8045"
INDICATORS_RELEASE = "ecoinvent-3.12-cutoff.EF-3.1@21c61d3ac200"
PREFIX = "ecoinvent:3.12:cutoff:"
INDICATORS = ("climate-change", "land-use")
"""Climate change, and land use: it has negative factors, so it checks signs and the sort by
size."""
TOP = 10
LISTED = 12
TOLERANCE = 1e-6

PRODUCTS = (
    ("electricity", "market group for electricity, low voltage", "GLO"),
    ("metals", "market for steel, low-alloyed", "GLO"),
    ("minerals", "market for cement, Portland", "RoW"),
    ("plastics", "market for polyethylene terephthalate, granulate, amorphous", "GLO"),
    ("chemicals", "market for sodium hydroxide, without water, in 50% solution state", "RoW"),
    ("fibres", "market for fibre, cotton", "GLO"),
    ("agriculture", "market for inorganic nitrogen fertiliser, as N", "RoW"),
    ("transport", "market for transport, freight, sea, container ship, heavy fuel oil", "GLO"),
    ("paper", "market for kraft paper", "RoW"),
    ("waste", "market for municipal solid waste", "RoW"),
)
"""(sector, name, location), by the rule above."""


def work() -> Path:
    folder = Path(os.environ["HEADWATER_WORK"]).expanduser().resolve() / "where-parity"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def pick(release: Release) -> list[dict[str, str]]:
    """The process of each product, checked against the rule: one dataset of that name in that
    location, and in RoW only where there is no GLO one."""
    table = release.table("processes").select(["id", "name", "location"]).to_pylist()
    names = {name for _, name, _ in PRODUCTS}
    by_name: dict[str, dict[str, list[str]]] = {}
    for row in table:
        if row["name"] in names:
            by_name.setdefault(row["name"], {}).setdefault(row["location"], []).append(row["id"])
    found = []
    for sector, name, location in PRODUCTS:
        places = by_name.get(name, {})
        if len(places.get(location, [])) != 1:
            sys.exit(f"not one dataset {name!r} in {location}: {places.get(location, [])}")
        if location == "RoW" and "GLO" in places:
            sys.exit(f"{name!r} has a GLO dataset: the rule picks it")
        [process_id] = places[location]
        activity, product = process_id.removeprefix(PREFIX).split("_")
        found.append(
            {
                "sector": sector,
                "name": name,
                "location": location,
                "process_id": process_id,
                "activity": activity,
                "product": product,
            }
        )
    return found


def prepare() -> None:
    out = work()
    method = Path(os.environ["WHERE_METHOD"]).expanduser()
    release = find(RELEASE)
    started = time.perf_counter()
    bg = hw.load(RELEASE, method=method)
    loaded = time.perf_counter() - started
    products = pick(release)
    _check_indicators_release(bg, release, [p["process_id"] for p in products])
    (out / "products.json").write_text(json.dumps(products, indent=1) + "\n")
    rows: dict[str, list[object]] = {
        "product_id": [],
        "indicator": [],
        "rank": [],
        "process_id": [],
        "value": [],
        "score": [],
    }
    seconds = []
    for product in products:
        for indicator in INDICATORS:
            started = time.perf_counter()
            where = bg.where(product["process_id"], indicator=indicator, top=LISTED)
            seconds.append(time.perf_counter() - started)
            for rank, emitter in enumerate(where.processes):
                rows["product_id"].append(product["process_id"])
                rows["indicator"].append(indicator)
                rows["rank"].append(rank)
                rows["process_id"].append(emitter.process_id)
                rows["value"].append(emitter.score)
                rows["score"].append(where.score)
    pq.write_table(pa.table(rows), out / "hw_top.parquet")
    _write(
        {
            "prepare": {
                "release": RELEASE,
                "indicators_release": INDICATORS_RELEASE,
                "method": bg.method.id,
                "products": len(products),
                "indicators": list(INDICATORS),
                "load_and_solve_s": round(loaded, 1),
                "where_s": {
                    "median": round(float(np.median(seconds)), 4),
                    "max": round(max(seconds), 4),
                },
                "load_average": os.getloadavg(),
                "versions": _versions(),
            }
        }
    )


def _check_indicators_release(bg: hw.Background, release: Release, product_ids: list[str]) -> None:
    """The indicators release is derived from `release` with the method `bg` was loaded with,
    and stores the scores `bg` solved for the products, within 1e-12."""
    indicators = find(INDICATORS_RELEASE)
    if indicators.parent is None or indicators.parent.hash != release.hash:
        sys.exit(f"{INDICATORS_RELEASE} is not derived from {RELEASE}")
    recorded = {m.id: m for m in indicators.methods()}
    if bg.method.id not in recorded or method_hash(recorded[bg.method.id]) != internals(bg).method_hash:
        sys.exit(f"the method is not the one {INDICATORS_RELEASE} was solved with")
    stored = pq.read_table(indicators.path / "indicators" / f"{bg.method.id}.parquet").to_pylist()
    by_id = {row["product_id"]: row for row in stored}
    for product_id in product_ids:
        row = internals(bg).index[product_id]
        sign = float(internals(bg).solved.block.reference_sign[row])
        for indicator in INDICATORS:
            solved = float(internals(bg).solved.x[indicator].values[row]) * sign
            if _relative(solved, by_id[product_id][indicator]) > 1e-12:
                sys.exit(f"{product_id} {indicator}: not the score {INDICATORS_RELEASE} stores")


def compare() -> None:
    out = work()
    products = json.loads((out / "products.json").read_text())
    hw_top = pq.read_table(out / "hw_top.parquet").to_pylist()
    bw_top = pq.read_table(out / "bw_top.parquet").to_pylist()
    bw_runs = json.loads((out / "bw_runs.json").read_text())

    def listed(rows: list[dict], **key: object) -> list[tuple[str, float]]:
        chosen = [r for r in rows if all(r[k] == v for k, v in key.items())]
        return [(r["process_id"], r["value"]) for r in sorted(chosen, key=lambda r: r["rank"])]

    report: dict[str, object] = {}
    for source in ("bw2calc", "accurate"):
        counts = {
            "comparisons": 0,
            "same_top_set": 0,
            "same_set_after_boundary_ties": 0,
            "values_compared": 0,
            "values_within_tolerance": 0,
            "negative_values_compared": 0,
            "same_order": 0,
            "near_tie_swaps": 0,
            "order_differences_not_near_ties": 0,
            "largest_relative_difference": 0.0,
            "largest_relative_difference_of_score": 0.0,
        }
        by_indicator: dict[str, dict[str, int]] = {}
        for product in products:
            for indicator in INDICATORS:
                ours = listed(hw_top, product_id=product["process_id"], indicator=indicator)
                theirs = listed(
                    bw_top, product_id=product["process_id"], indicator=indicator, source=source
                )
                found = _compare(ours, theirs)
                score = next(
                    r["score"]
                    for r in hw_top
                    if r["product_id"] == product["process_id"] and r["indicator"] == indicator
                )
                bw_score = bw_runs[product["process_id"]][indicator][f"score_{source}"]
                counts["comparisons"] += 1
                counts["same_top_set"] += found["same_set"]
                counts["same_set_after_boundary_ties"] += found["same_set_after_ties"]
                counts["values_compared"] += found["compared"]
                counts["values_within_tolerance"] += found["within"]
                counts["negative_values_compared"] += found["negative"]
                counts["same_order"] += found["same_order"]
                counts["near_tie_swaps"] += found["near_tie_swaps"]
                counts["order_differences_not_near_ties"] += found["other_order_differences"]
                counts["largest_relative_difference"] = max(
                    counts["largest_relative_difference"], found["largest"]
                )
                counts["largest_relative_difference_of_score"] = max(
                    counts["largest_relative_difference_of_score"], _relative(score, bw_score)
                )
                passed = (
                    found["same_set_after_ties"]
                    and found["within"] == found["compared"]
                    and found["other_order_differences"] == 0
                )
                tally = by_indicator.setdefault(indicator, {"products": 0, "passed": 0})
                tally["products"] += 1
                tally["passed"] += int(passed)
                if not passed:
                    print(f"{source} {indicator} {product['sector']}: {found}")
        counts["by_indicator"] = by_indicator
        report[source] = counts
        print(source, json.dumps(counts, indent=1))
    residuals = [run[i] for run in bw_runs.values() for i in INDICATORS]
    report["residuals"] = {
        "bw2calc_largest": max(r["residual_bw2calc"] for r in residuals),
        "accurate_largest": max(r["residual_accurate"] for r in residuals),
    }
    print(json.dumps(report["residuals"]))
    _write({"compare": report})


def _compare(ours: list[tuple[str, float]], theirs: list[tuple[str, float]]) -> dict[str, object]:
    """The top 10 of `ours` against `theirs`, each listed 12 deep, largest first."""
    our_top = [p for p, _ in ours[:TOP]]
    their_top = [p for p, _ in theirs[:TOP]]
    our_value = dict(ours)
    their_value = dict(theirs)
    same_set = set(our_top) == set(their_top)
    # A process in one top 10 and not the other is a boundary tie when it is in the other's list
    # at 11th or 12th place, within the tolerance of that list's 10th value.
    boundary = set(our_top) ^ set(their_top)
    ties = all(
        p in our_value
        and p in their_value
        and _close(our_value[p], ours[TOP - 1][1])
        and _close(their_value[p], theirs[TOP - 1][1])
        for p in boundary
    )
    compared = [p for p in our_top if p in their_value]
    differences = [_relative(our_value[p], their_value[p]) for p in compared]
    # Two neighbours in the other order are one swap, not two places that differ.
    near_tie_swaps = 0
    other = 0
    k = 0
    while k < TOP:
        mine, theirs_k = our_top[k], their_top[k]
        if mine == theirs_k:
            k += 1
            continue
        swapped = k + 1 < TOP and (our_top[k + 1], their_top[k + 1]) == (theirs_k, mine)
        if theirs_k in our_value and _close(our_value[mine], our_value[theirs_k]):
            near_tie_swaps += 1
        elif k == TOP - 1 and ties:
            near_tie_swaps += 1
        else:
            other += 1
        k += 2 if swapped else 1
    return {
        "same_set": int(same_set),
        "same_set_after_ties": int(same_set or ties),
        "compared": len(compared),
        "within": sum(d <= TOLERANCE for d in differences),
        "negative": sum(our_value[p] < 0 for p in compared),
        "largest": max(differences, default=0.0),
        "same_order": int(our_top == their_top),
        "near_tie_swaps": near_tie_swaps,
        "other_order_differences": other,
    }


def _relative(a: float, b: float) -> float:
    size = max(abs(a), abs(b))
    return abs(a - b) / size if size else 0.0


def _close(a: float, b: float) -> bool:
    return _relative(a, b) <= TOLERANCE


def _versions() -> dict[str, str]:
    import scipy

    return {
        "python": platform.python_version(),
        "headwater": hw.__version__,
        "numpy": np.__version__,
        "scipy": scipy.__version__,
        "machine": f"{platform.machine()} {platform.system()} {platform.release()}",
    }


def _write(update: dict[str, object]) -> None:
    results = json.loads(RESULTS.read_text()) if RESULTS.exists() else {}
    results.update(update)
    RESULTS.write_text(json.dumps(results, indent=2) + "\n")


if __name__ == "__main__":
    steps = {"prepare": prepare, "compare": compare}
    if len(sys.argv) != 2 or sys.argv[1] not in steps:
        sys.exit(f"usage: where_parity.py {' | '.join(steps)}")
    steps[sys.argv[1]]()
