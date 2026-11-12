"""How many products of a release the cancellation flag marks, per indicator.

    uv run python bench/carbon_cancellation.py <release> <method folder> [<method folder> ...]

The method folders are loaded together, as `hw.load(release, method=[...])` does.

For every product, solves A s = e for one unit of it, in batches. Each indicator's score is then
split into its positive and negative parts exactly: a term c_f B_fp s_p is positive when c_f B_fp
and s_p have the same sign, so per process the sums of positive and of negative c_f B_fp are
enough. A score cancels when it is below 1e-3 of its gross (headwater.calc.cancellation). Products
whose score is an exact zero by structure have no parts and are left out.

Writes bench/results/carbon_cancellation_results.json: counts only, no score.
"""

import json
import platform
import sys
from pathlib import Path

import numpy as np
from scipy import sparse

import headwater as hw
from headwater.calc.cancellation import CANCELS_BELOW
from headwater.query._internals import internals

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results" / "carbon_cancellation_results.json"
BATCH = 500
SHARES = (1e-6, 1e-3, 1e-2)


def main(release: str, folders: list[str]) -> None:
    bg = hw.load(release, method=[Path(f) for f in folders])
    solved = internals(bg).solved
    n = len(solved.ids)
    B = sparse.csc_array(solved.block.B)
    indicators: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
    for method in bg.methods:
        for indicator in (i.id for i in method.indicators):
            terms = B.multiply(solved.c[indicator][:, None]).tocsc()
            plus = np.asarray(terms.maximum(0).sum(axis=0)).ravel()
            minus = np.asarray(terms.minimum(0).sum(axis=0)).ravel()
            indicators[f"{method.id}/{indicator}"] = (plus, minus, solved.x[indicator].exact_zeros)
    positive = {k: np.zeros(n) for k in indicators}
    negative = {k: np.zeros(n) for k in indicators}
    for start in range(0, n, BATCH):
        stop = min(start + BATCH, n)
        e = np.zeros((n, stop - start))
        e[np.arange(start, stop), np.arange(stop - start)] = 1.0
        s = solved.factorization.solve(e)
        up, down = np.maximum(s, 0.0), np.minimum(s, 0.0)
        for key, (plus, minus, _) in indicators.items():
            positive[key][start:stop] = plus @ up + minus @ down
            negative[key][start:stop] = minus @ up + plus @ down
        print(f"{stop:,} of {n:,} products", flush=True)
    found: dict[str, dict[str, int]] = {}
    for key, (_, _, zero) in indicators.items():
        gross = positive[key] - negative[key]
        net = np.abs(positive[key] + negative[key])
        keep = ~zero & (gross > 0)
        share = net[keep] / gross[keep]
        found[key] = {
            "products": n,
            "exact_zeros": int(zero.sum()),
            "both_signs": int(((positive[key] > 0) & (negative[key] < 0) & ~zero).sum()),
            **{f"below_{t:g}_of_gross": int((share < t).sum()) for t in SHARES},
            "flagged": int((share < CANCELS_BELOW).sum()),
        }
        print(key, found[key], flush=True)
    RESULTS.parent.mkdir(exist_ok=True)
    RESULTS.write_text(
        json.dumps(
            {
                "release": Path(release).name,
                "methods": [m.id for m in bg.methods],
                "machine": f"{platform.machine()}, {platform.system()} {platform.release()}",
                "python": platform.python_version(),
                "cancels_below": CANCELS_BELOW,
                "indicators": found,
            },
            indent=1,
        )
        + "\n"
    )


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2:])
