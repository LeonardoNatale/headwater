"""Monte Carlo of a study on ecoinvent 3.12 with EF 3.1: its database's own lines drawn, a local
override, and the study in a scenario, timed against a run that draws the background alone.

    export HEADWATER_WORK=/path/to/scratch
    export HEADWATER_HOME=/path/to/headwater-home       # holds the 3.12 release
    export MC_METHOD=$HEADWATER_WORK/pilot/methods/EF-3.1
    export MC_DATABASE=$HEADWATER_WORK/pilot/study      # the database whose lines are drawn
    export OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2

    uv run python bench/monte_carlo_study.py build             # the study with distributions
    uv run python bench/monte_carlo_study.py run [iterations]  # 200 by default
    uv run python bench/monte_carlo_study.py check             # against the plain database

**build** copies the database in `MC_DATABASE` (the 3.12 pilot's, 40 processes on 3.12) to
`$HEADWATER_WORK/mc-studies/<its name>` and gives every line but the references, with a number
other than 0 as its amount, a lognormal with a GSD of 1.1. It writes the study
`$HEADWATER_WORK/mc-studies/mc-study` of one unit of one product: `MC_PRODUCT`, a process id, or
else the product of the mc-study it writes again, so a rerun scores the same product as the
results file; with neither it stops and asks for `MC_PRODUCT`. It adds a local override to the database and a scenario to
the study, on the first background process the product buys from that has both an emission and
a purchase: the override sets that emission at 0.8 times its amount, and the scenario
`mc-scenario` the purchase at 0.9 times, for everyone, which changes a column of A. It ends by
validating the study and scoring it in the scenario, so a mistake stops here.

**run** scores the study and the study in `mc-scenario` for every EF 3.1 indicator, `iterations`
of seed 0, then the background alone (one product). Each is also run for 2 iterations, so the
time per iteration leaves out the setup (reading the distributions, preparing the study).
**check** copies the database as it is to `$HEADWATER_WORK/mc-studies/plain`, and scores the
product in both on 20 iterations of seed 0 for the three climate indicators: whether the score
with no draw is what `calc` gives, how wide each range is, and how much of the spread the
database's own lines add.
Values are licensed and stay in `$HEADWATER_WORK`. The results file holds counts and timings.
The ranges come from the pilot's data: `run` and `check` print them and write none.
"""

import csv
import io
import json
import os
import platform
import shutil
import sys
import time
from pathlib import Path

import numpy as np
import scipy

import headwater as hw
from headwater.query._internals import internals
from headwater.query.monte_carlo import Item, monte_carlo

HERE = Path(__file__).resolve().parent
RELEASE = "ecoinvent-3.12-cutoff@b9428cdd8045"
SCENARIO = "mc-scenario"
SEED = 0
GSD_LOG = 0.09531017980432493  # ln 1.1


def work() -> Path:
    return Path(os.environ["HEADWATER_WORK"]).expanduser().resolve()


def source() -> Path:
    """The database whose own lines are drawn, from `MC_DATABASE`: no default."""
    folder = os.environ.get("MC_DATABASE")
    if not folder:
        raise SystemExit("set MC_DATABASE to the folder of the database whose lines are drawn")
    return Path(folder).expanduser().resolve()


def name() -> str:
    """The pilot database's name: the folder that holds its copy has it, so the study finds it."""
    return str(hw.open(source()).name)


def target() -> Path:
    """The study, next to the copy of the database."""
    return work() / "mc-studies" / "mc-study"


def product() -> str:
    """The process id of the product scored: `MC_PRODUCT`, else the product of the study that
    `build` is about to write again, so a rebuild scores the product of the results file. Stops
    when there is neither: the first process of the database is another product."""
    chosen = os.environ.get("MC_PRODUCT")
    if chosen:
        return chosen
    if (target() / "study.yaml").is_file():
        study = hw.open(target())
        assert isinstance(study, hw.Study)
        [found] = [p.process for p in study.folder.products]
        return found
    raise SystemExit(
        f"set MC_PRODUCT to the process id to score: there is no study at {target()} to take "
        "it from"
    )


def build() -> None:
    chosen = product()
    into = work() / "mc-studies" / name()
    for folder in (into, target()):
        if folder.exists():
            shutil.rmtree(folder)
    shutil.copytree(source(), into)
    drawn = 0
    for path in sorted((into / "processes").glob("*.csv")):
        rows = list(csv.DictReader(io.StringIO(path.read_text())))
        assert rows, path
        columns = [*rows[0].keys(), "distribution", "scale", "minimum", "maximum"]
        for row in rows:
            row.update(distribution="", scale="", minimum="", maximum="")
            if row["role"] == "reference":
                continue
            try:
                amount = float(row["amount"])
            except ValueError:
                continue  # a formula: its parameters carry its spread
            if amount != 0:
                row.update(distribution="lognormal", scale=repr(GSD_LOG))
                drawn += 1
        out = io.StringIO()
        writer = csv.DictWriter(out, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
        path.write_text(out.getvalue())

    bg = hw.load(RELEASE, method=Path(os.environ["MC_METHOD"]).expanduser())
    database = hw.open(into)
    assert isinstance(database, hw.Database)
    process = next(p for p in database.folder.processes if p.id == chosen)
    suppliers = [
        e.supplier_id
        for e in process.exchanges
        if e.supplier_id is not None and e.supplier_id in internals(bg).index
    ]
    found = bg.processes(suppliers)
    for supplier in suppliers:
        lines = found[supplier].exchanges
        emission = next((e for e in lines if e.role.value == "emission" and e.amount), None)
        purchase = next((e for e in lines if e.role.value == "input" and e.amount), None)
        if emission is not None and purchase is not None:
            break
    else:
        raise SystemExit("no supplier of the product has an emission and a purchase")
    (into / "overrides").mkdir()
    (into / "overrides" / "mc-override.csv").write_text(
        "process,line,role,flow,amount,unit,supplier\n"
        f"{supplier},{emission.line_id},emission,{emission.flow_id},"
        f"{0.8 * emission.amount!r},{emission.unit},\n"
    )
    (target() / "scenarios").mkdir(parents=True)
    (target() / "study.yaml").write_text(
        "name: mc-study\n"
        "goal: Monte Carlo of one product of the 3.12 pilot, a benchmark\n"
        "functional_unit: 1 unit of the product\n"
        f"products:\n  - {{process: {chosen}, amount: 1}}\n"
        "boundary: gate-to-gate\n"
        f"methods: [{bg.method.id}]\n"
        "cut_off: {criteria: [mass], max_cumulative: 0}\n"
    )
    (target() / "scenarios" / f"{SCENARIO}.yaml").write_text(
        "description: The supplier of the first purchase buys 10% less of its first input.\n"
        "changes:\n"
        f"  - process: {supplier}\n    line: {purchase.line_id}\n"
        f"    amount: {0.9 * purchase.amount!r}\n"
    )
    study = hw.open(target())
    report = study.validate(bg)
    if report.errors:
        raise SystemExit(f"the study does not validate: {report.errors}")
    assert isinstance(study, hw.Study)
    study.calc(bg, indicator="climate-change", scenario=SCENARIO)
    print(f"{drawn} lines drawn; override and scenario on one background process")


def run(iterations: int) -> None:
    load = os.getloadavg()
    bg = hw.load(RELEASE, method=Path(os.environ["MC_METHOD"]).expanduser())
    study = hw.open(target())
    assert isinstance(study, hw.Study)
    [chosen] = [p.process for p in study.folder.products]
    items: list[Item] = [(study, chosen), (study, chosen, SCENARIO)]
    product = internals(bg).solved.block.ids[0]

    def timed(what: list[Item], count: int) -> tuple[hw.MonteCarlo, float]:
        started = time.perf_counter()
        found = monte_carlo(bg, what, iterations=count, seed=SEED)
        return found, time.perf_counter() - started

    _, setup = timed(items, 2)
    mc, seconds = timed(items, iterations)
    _, alone_setup = timed([product], 2)
    _, alone = timed([product], 22)

    def widths(item: str) -> dict[str, list[float]]:
        found: dict[str, list[float]] = {}
        for indicator in mc.indicators:
            r = mc.range(item, indicator)
            if r.median == 0:
                continue
            low, high = r.percentiles[2.5], r.percentiles[97.5]
            found[indicator] = [
                round(100 * low / r.median, 2),
                round(100 * high / r.median, 2),
            ]
        return found

    base, scenario = mc.items
    paired = {
        i: round(mc.difference(base, scenario, i).sd / mc.range(base, i).sd, 3)
        for i in mc.indicators
        if mc.range(base, i).sd > 0
    }
    p = mc.provenance
    data: dict[str, object] = {
        "release": RELEASE,
        "method": bg.method.id,
        "study": "the 3.12 pilot, 1 of its 40 processes, every line lognormal with GSD 1.1, one override",
        "items": list(mc.items),
        "iterations": iterations,
        "seed": SEED,
        "seconds": round(seconds, 1),
        "seconds_per_iteration": round((seconds - setup) / (iterations - 2), 3),
        "background_alone_seconds_per_iteration": round((alone - alone_setup) / 20, 3),
        "background_lines": dict(p.uncertain_lines),
        "database_lines": dict(p.database_lines),
        "parameters": p.parameters,
        "largest_condition": float(f"{p.largest_condition:.3g}"),
        "residual": float(f"{p.residual:.3g}"),
        "pivoted": p.pivoted,
        "scenario_refactorized": p.scenario_refactorized,
        "opposite_signs": p.opposite_signs,
        "load_average_before": [round(x, 2) for x in load],
        "load_average_after": [round(x, 2) for x in os.getloadavg()],
        "machine": f"{platform.machine()} {platform.system()} {platform.release()}",
        "threads": {v: os.environ.get(v) for v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS")},
        "numpy": np.__version__,
        "scipy": scipy.__version__,
        "headwater": hw.__version__,
    }
    path = HERE / "results" / "monte_carlo_study_ecoinvent-3.12-cutoff_results.json"
    path.write_text(json.dumps(data, indent=2) + "\n")
    # The ranges come from the pilot's data: printed, never written to the results file.
    ranges = {
        "width_percent_of_median": {item: widths(item) for item in mc.items},
        "difference_sd_over_study_sd": paired,
    }
    print(json.dumps({**data, **ranges}, indent=2))


def check() -> None:
    plain_path = work() / "mc-studies" / "plain"
    if plain_path.exists():
        shutil.rmtree(plain_path)
    shutil.copytree(source(), plain_path)
    bg = hw.load(RELEASE, method=Path(os.environ["MC_METHOD"]).expanduser())
    drawn, plain = hw.open(target()), hw.open(plain_path)
    assert isinstance(drawn, hw.Study) and isinstance(plain, hw.Database)
    [chosen] = [p.process for p in drawn.folder.products]
    cc = "climate-change"
    [base] = [r.score for r in drawn.calc(bg, indicator=cc)]
    [scenario] = [r.score for r in drawn.calc(bg, indicator=cc, scenario=SCENARIO)]
    indicators = [cc, "climate-change-biogenic", "climate-change-fossil"]
    items: list[Item] = [(drawn, chosen), (plain, chosen)]
    mc = monte_carlo(bg, items, iterations=20, seed=SEED, indicators=indicators)
    spread = {
        f"{ind} {name}": {
            "sd_over_median": round(float(s.std(ddof=1) / abs(np.median(s))), 3),
            "below_0": int((s < 0).sum()),
        }
        for j, ind in enumerate(indicators)
        for i, name in enumerate(("drawn", "plain"))
        for s in [mc.samples[i, j]]
    }
    own = mc.samples[0, 0] - mc.samples[1, 0]
    print(
        json.dumps(
            {
                "scenario_minus_base_over_base": (scenario - base) / base,
                "no_draw_against_calc": abs(mc.scores[0, 0] - base) / abs(base),
                "iterations": 20,
                "spread": spread,
                "own_lines_sd_over_study_sd": round(
                    float(own.std(ddof=1) / mc.samples[0, 0].std(ddof=1)), 3
                ),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    match sys.argv[1:]:
        case ["build"]:
            build()
        case ["run"]:
            run(200)
        case ["run", count]:
            run(int(count))
        case ["check"]:
            check()
        case _:
            raise SystemExit(__doc__)
