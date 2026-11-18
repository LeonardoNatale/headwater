"""The inventory times each indicator's factors against the stored score, on ecoinvent 3.13.

For 10 products, one market per sector (those of bench/where_parity.py), `bg.inventory`
gives the total of each elementary flow. Times the factors of each indicator of EF 3.1, read
from the method folder by flow id, it must give the stored score, `bg.score`:

    |sum(amount x factor) - score| <= 1e-9 x sum(|amount x factor|)

The bound is relative to the sum of the terms' sizes, not to the score: land use has negative
factors, and a score can cancel to near 0. Run it with the 3.13 cut-off release in the cache
and the EF 3.1 method folder imported from the 3.13 LCIA workbook:

    export HEADWATER_HOME=/path/to/headwater-home   # holds ecoinvent-3.13-cutoff@9bbbb9d97a81
    export INVENTORY_METHOD=/path/to/methods-3.13/EF-3.1
    uv run python bench/inventory_check.py

Values are licensed, so `bench/results/inventory_check_results.json` holds pass or fail, the
largest relative difference, counts and times only.
"""

import json
import math
import os
import platform
import statistics
import sys
import time
from pathlib import Path

import numpy as np

import headwater as hw
from headwater.release import Release, find
from headwater.study import read_method

sys.path.insert(0, str(Path(__file__).resolve().parent))
from where_parity import PRODUCTS  # noqa: E402

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results" / "inventory_check_results.json"
RELEASE = "ecoinvent-3.13-cutoff@9bbbb9d97a81"
TOLERANCE = 1e-9
REPEATS = 5


def pick(release: Release) -> list[dict[str, str]]:
    """The process of each product, by the rule of where_parity.py, applied to 3.13: the one
    dataset of that name in GLO, else in RoW. ecoinvent 3.13 has no GLO cotton fibre market, so
    its RoW one is used."""
    table = release.table("processes").select(["id", "name", "location"]).to_pylist()
    names = {name for _, name, _ in PRODUCTS}
    by_name: dict[str, dict[str, list[str]]] = {}
    for row in table:
        if row["name"] in names:
            by_name.setdefault(row["name"], {}).setdefault(row["location"], []).append(row["id"])
    found = []
    for sector, name, _ in PRODUCTS:
        places = by_name.get(name, {})
        location = "GLO" if "GLO" in places else "RoW"
        ids = places.get(location, [])
        if len(ids) != 1:
            sys.exit(f"not one dataset {name!r} in {location}: {ids}")
        found.append({"sector": sector, "name": name, "location": location, "id": ids[0]})
    return found


def check(
    totals: tuple[hw.FlowTotal, ...], factors: dict[str, float], score: float
) -> tuple[bool, float]:
    """Whether the inventory times the factors gives the score, and the difference relative
    to the sum of the terms' sizes."""
    terms = [t.amount * factors[t.flow_id] for t in totals if t.flow_id in factors]
    found, gross = math.fsum(terms), math.fsum(abs(term) for term in terms)
    difference = abs(found - score)
    relative = difference / gross if gross else difference
    return relative <= TOLERANCE, relative


def main() -> None:
    method_folder = Path(os.environ["INVENTORY_METHOD"]).expanduser()
    method = read_method(method_folder)
    factors = {i: dict(f) for i, f in method.factors_by_indicator.items()}
    characterised = method.characterised_flow_ids
    release = find(RELEASE)
    products = pick(release)

    start = time.perf_counter()
    bg = hw.load(release.path, method=method_folder)
    load_seconds = time.perf_counter() - start

    start = time.perf_counter()
    bg.inventory(products[0]["id"])  # the first call also reads the flow table once
    first_ms = (time.perf_counter() - start) * 1e3

    rows = []
    for product in products:
        times = []
        for _ in range(REPEATS):
            start = time.perf_counter()
            totals = bg.inventory(product["id"])
            times.append((time.perf_counter() - start) * 1e3)
        checked = [
            check(totals, factors[i.id], bg.score(product["id"], indicator=i.id))
            for i in method.indicators
        ]
        rows.append(
            {
                "sector": product["sector"],
                "name": product["name"],
                "location": product["location"],
                "flows": len(totals),
                "flows_with_no_factor": sum(t.flow_id not in characterised for t in totals),
                "units": len({t.unit for t in totals}),
                "indicators": len(checked),
                "passed": sum(ok for ok, _ in checked),
                "largest_relative_difference": max(relative for _, relative in checked),
                "ms_median": statistics.median(times),
                "ms_best": min(times),
            }
        )
        print(
            f"{product['sector']:12} {rows[-1]['passed']}/{rows[-1]['indicators']} "
            f"largest {rows[-1]['largest_relative_difference']:.2e} "
            f"{rows[-1]['ms_median']:.1f} ms, {rows[-1]['flows']} flows"
        )

    checks = sum(r["indicators"] for r in rows)
    passed = sum(r["passed"] for r in rows)
    results = {
        "release": RELEASE,
        "method": {"id": method.id, "version": method.version, "indicators": len(method.indicators)},
        "tolerance": TOLERANCE,
        "products": rows,
        "checks": checks,
        "passed": passed,
        "pass": passed == checks,
        "largest_relative_difference": max(r["largest_relative_difference"] for r in rows),
        "load_seconds": load_seconds,
        "first_call_ms": first_ms,
        "ms_median_of_medians": statistics.median(r["ms_median"] for r in rows),
        "repeats": REPEATS,
        "versions": _versions(),
    }
    RESULTS.write_text(json.dumps(results, indent=2) + "\n")
    print(f"{passed} of {checks} checks pass; written to {RESULTS.relative_to(HERE.parent)}")
    if passed != checks:
        sys.exit(1)


def _versions() -> dict[str, str]:
    import scipy

    return {
        "python": platform.python_version(),
        "headwater": hw.__version__,
        "numpy": np.__version__,
        "scipy": scipy.__version__,
        "machine": f"{platform.machine()} {platform.system()} {platform.release()}",
    }


if __name__ == "__main__":
    main()
