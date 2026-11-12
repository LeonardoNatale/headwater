"""Monte Carlo on ecoinvent 3.12 with EF 3.1: the time of one iteration, and headwater's
distributions against Brightway's on 10 products.

    export HEADWATER_WORK=/path/to/scratch
    export HEADWATER_HOME=/path/to/headwater-home                 # holds the 3.12 release
    export MC_METHOD=$HEADWATER_WORK/pilot/methods/EF-3.1
    export OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2

    uv run python bench/monte_carlo.py time [iterations]      # the parts of one iteration
    uv run python bench/monte_carlo.py prepare                 # headwater's 200 iterations
    cp -R $HEADWATER_WORK/where-parity/brightway $HEADWATER_WORK/mc-parity/brightway
    BRIGHTWAY2_DIR=$HEADWATER_WORK/mc-parity/brightway PILOT_BW_PROJECT=... \
        uv run bench/bw_monte_carlo.py
    uv run python bench/monte_carlo.py compare

**time** draws every uncertain line of A and B, rebuilds A and B, factorizes A and solves the
scores of every indicator, as `hw.monte_carlo` does, and times each part. Run it alone: it
records the load average.

**prepare** scores the 10 products of `bench/where_parity.py` (one market per sector) for
every EF 3.1 indicator, 200 iterations of seed 0, and writes the samples to
`$HEADWATER_WORK/mc-parity/`. Brightway's side (`bw_monte_carlo.py`) draws its own matrices
200 times with bw2calc's `use_distributions`, characterisation factors fixed, and solves each
draw with an accurate scaled SuperLU, so a difference is one of the draws, not of a solver
(docs/decisions.md, D21).

**compare** checks, for each product and indicator, the mean and the 2.5 and 97.5 percentiles:
the difference between the two engines must be within 3 standard errors, the standard error of
the difference estimated by bootstrap (1,000 resamples of each side). 10 x 25 x 3 = 750 checks;
at 3 standard errors about 2 fail by chance, so up to 1% may fail, and each failure is listed.
Medians are compared the same way and shown beside the means: lognormal scales of up to 6.95
make means unstable with 200 draws. Values are licensed and stay in `$HEADWATER_WORK`;
`bench/results/monte_carlo_*` hold counts, timings and differences in standard errors only.
"""

import json
import os
import platform
import sys
import time
from pathlib import Path

import numpy as np
import scipy
from scipy import sparse

import headwater as hw
from headwater.calc import factorize, scores
from headwater.calc.monte_carlo import draw, uniforms
from headwater.query._internals import internals
from headwater.query._uncertainty import uncertain_lines
from headwater.release import find

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from where_parity import pick  # noqa: E402

RELEASE = "ecoinvent-3.12-cutoff@b9428cdd8045"
ITERATIONS = 200
SEED = 0
PERCENTILES = (2.5, 50.0, 97.5)
BOOTSTRAP = 1000
SIGMAS = 3.0
ALLOWED = 0.01


def work() -> Path:
    folder = Path(os.environ["HEADWATER_WORK"]).expanduser().resolve() / "mc-parity"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def method() -> Path:
    return Path(os.environ["MC_METHOD"]).expanduser()


def machine() -> dict[str, object]:
    return {
        "machine": f"{platform.machine()} {platform.system()} {platform.release()}",
        "threads": {v: os.environ.get(v) for v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS")},
        "numpy": np.__version__,
        "scipy": scipy.__version__,
        "headwater": hw.__version__,
    }


def write(name: str, data: dict[str, object]) -> None:
    path = HERE / "results" / name
    path.write_text(json.dumps(data, indent=2) + "\n")
    print(json.dumps(data, indent=2))


def time_iterations(iterations: int) -> None:
    load = os.getloadavg()
    started = time.perf_counter()
    bg = hw.load(RELEASE, method=method())
    load_s = time.perf_counter() - started
    release = bg.release
    assert release is not None
    block = internals(bg).solved.block
    started = time.perf_counter()
    found = uncertain_lines(release, block)
    read_s = time.perf_counter() - started
    indicators = [i.id for i in bg.method.indicators]
    n = len(block.ids)
    parts: dict[str, list[float]] = {k: [] for k in ("draw", "build", "factorize", "solves")}
    conditions = []
    for k in range(iterations):
        t0 = time.perf_counter()
        drawn = draw(found.uncertain, uniforms(SEED, k, len(found.uncertain)))
        a = block.lines.amount.copy()
        a[found.position[found.in_a]] = drawn[found.in_a]
        b = block.emissions.amount.copy()
        b[found.position[~found.in_a]] = drawn[~found.in_a]
        t1 = time.perf_counter()
        A = sparse.csc_array(
            (block.lines.sign * a, (block.lines.row, block.lines.col)), shape=(n, n)
        )
        B = sparse.csc_array((b, (block.emissions.row, block.emissions.col)), shape=block.B.shape)
        t2 = time.perf_counter()
        f = factorize(A)
        t3 = time.perf_counter()
        for indicator in indicators:
            solution = scores(f, np.asarray(B.T @ internals(bg).solved.c[indicator]))
            f = solution.fallback or f
        t4 = time.perf_counter()
        for key, value in zip(parts, (t1 - t0, t2 - t1, t3 - t2, t4 - t3), strict=True):
            parts[key].append(value)
        conditions.append(f.condition)
        print(k, {key: round(v[-1], 3) for key, v in parts.items()}, f"{f.condition:.3g}")
    total = np.sum([parts[k] for k in parts], axis=0)
    write(
        "monte_carlo_time_ecoinvent-3.12-cutoff_results.json",
        {
            "release": RELEASE,
            "method": bg.method.id,
            "indicators": len(indicators),
            "processes": n,
            "uncertain_lines": found.counts,
            "uncertain_in_a": int(found.in_a.sum()),
            "uncertain_in_b": int((~found.in_a).sum()),
            "load_seconds": round(load_s, 2),
            "read_uncertainty_seconds": round(read_s, 2),
            "iterations": iterations,
            "median_seconds": {k: round(float(np.median(v)), 3) for k, v in parts.items()},
            "median_iteration_seconds": round(float(np.median(total)), 3),
            "conditions": {
                "median": float(np.median(conditions)),
                "max": float(np.max(conditions)),
                "above_1e3": int(np.sum(np.array(conditions) > 1e3)),
            },
            "load_average_before": [round(x, 2) for x in load],
            "load_average_after": [round(x, 2) for x in os.getloadavg()],
            **machine(),
        },
    )


def prepare() -> None:
    out = work()
    load = os.getloadavg()
    release = find(RELEASE)
    products = pick(release)
    (out / "products.json").write_text(json.dumps(products, indent=1) + "\n")
    bg = hw.load(RELEASE, method=method())
    started = time.perf_counter()
    mc = hw.monte_carlo(
        bg, [p["process_id"] for p in products], iterations=ITERATIONS, seed=SEED
    )
    took = time.perf_counter() - started
    np.save(out / "hw_samples.npy", mc.samples)
    np.save(out / "hw_scores.npy", mc.scores)
    (out / "hw_info.json").write_text(
        json.dumps({"items": list(mc.items), "indicators": list(mc.indicators)}, indent=1)
    )
    conditions = mc.conditions
    p = mc.provenance
    write(
        "monte_carlo_prepare_ecoinvent-3.12-cutoff_results.json",
        {
            "release": RELEASE,
            "method": p.method,
            "products": len(products),
            "indicators": len(mc.indicators),
            "iterations": ITERATIONS,
            "seed": SEED,
            "seconds": round(took, 1),
            "seconds_per_iteration": round(took / ITERATIONS, 3),
            "uncertain_lines": dict(p.uncertain_lines),
            "opposite_signs": p.opposite_signs,
            "pivoted": p.pivoted,
            "residual": p.residual,
            "conditions": {
                "median": float(np.median(conditions)),
                "max": float(conditions.max()),
                "above_1e3": int(np.sum(conditions > 1e3)),
                "above_1e5": int(np.sum(conditions > 1e5)),
                "above_1e7": int(np.sum(conditions > 1e7)),
                "limit": p.solver.condition_limit,
            },
            "load_average_before": [round(x, 2) for x in load],
            "load_average_after": [round(x, 2) for x in os.getloadavg()],
            **machine(),
        },
    )


def statistics(samples: np.ndarray) -> np.ndarray:
    """Mean, then the percentiles of PERCENTILES, along the last axis."""
    return np.concatenate(
        [samples.mean(axis=-1)[..., None], np.moveaxis(np.percentile(samples, PERCENTILES, axis=-1), 0, -1)],
        axis=-1,
    )


def bootstrap_se(samples: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """The standard error of each statistic, from BOOTSTRAP resamples of the iterations."""
    n = samples.shape[-1]
    found = np.empty((BOOTSTRAP, *samples.shape[:-1], 1 + len(PERCENTILES)))
    for r in range(BOOTSTRAP):
        found[r] = statistics(samples[..., rng.integers(0, n, n)])
    return found.std(axis=0, ddof=1)


def compare() -> None:
    out = work()
    hw_samples = np.load(out / "hw_samples.npy")
    bw_samples = np.load(out / "bw_samples.npy")
    hw_scores = np.load(out / "hw_scores.npy")
    bw_scores = np.load(out / "bw_scores.npy")
    info = json.loads((out / "hw_info.json").read_text())
    bw_info = json.loads((out / "bw_info.json").read_text())
    if bw_info["items"] != info["items"] or bw_info["indicators"] != info["indicators"]:
        sys.exit("the two sides do not list the same products and indicators")
    rng = np.random.default_rng(12345)
    difference = statistics(hw_samples) - statistics(bw_samples)
    se = np.sqrt(bootstrap_se(hw_samples, rng) ** 2 + bootstrap_se(bw_samples, rng) ** 2)
    with np.errstate(divide="ignore", invalid="ignore"):
        z = np.where(se > 0, difference / se, np.where(difference == 0, 0.0, np.inf))
    names = ["mean", *(f"p{p:g}" for p in PERCENTILES)]
    checked = [0, names.index("p2.5"), names.index("p97.5")]
    failures = []
    for i, product in enumerate(info["items"]):
        for j, indicator in enumerate(info["indicators"]):
            for s in checked:
                if not abs(z[i, j, s]) <= SIGMAS:
                    failures.append(
                        {
                            "product": product,
                            "indicator": indicator,
                            "statistic": names[s],
                            "z": round(float(z[i, j, s]), 2),
                            "median_z": round(float(z[i, j, names.index("p50")]), 2),
                        }
                    )
    checks = z.shape[0] * z.shape[1] * len(checked)
    with np.errstate(divide="ignore", invalid="ignore"):
        score_gap = np.abs(hw_scores - bw_scores) / np.maximum(np.abs(hw_scores), np.abs(bw_scores))
    write(
        "monte_carlo_parity_ecoinvent-3.12-cutoff_results.json",
        {
            "products": len(info["items"]),
            "indicators": len(info["indicators"]),
            "iterations": {"headwater": int(hw_samples.shape[-1]), "brightway": int(bw_samples.shape[-1])},
            "checks": checks,
            "rule": f"|difference| <= {SIGMAS:g} bootstrap standard errors, up to {ALLOWED:.0%} failing",
            "failed": len(failures),
            "allowed": int(checks * ALLOWED),
            "passed": len(failures) <= checks * ALLOWED,
            "largest_abs_z": {n: round(float(np.max(np.abs(z[..., k]))), 2) for k, n in enumerate(names)},
            "abs_z_above_2": {n: int(np.sum(np.abs(z[..., k]) > 2)) for k, n in enumerate(names)},
            "failures": failures,
            "deterministic_scores_largest_relative_gap": float(np.nanmax(score_gap)),
            "brightway_factors_with_a_distribution": bw_info.get("factors_with_a_distribution"),
            **machine(),
        },
    )


if __name__ == "__main__":
    match sys.argv[1:]:
        case ["time", *rest]:
            time_iterations(int(rest[0]) if rest else 5)
        case ["prepare"]:
            prepare()
        case ["compare"]:
            compare()
        case _:
            sys.exit(__doc__)
