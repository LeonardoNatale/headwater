"""Parity of `headwater database import-brightway` on a real Brightway project (licensed, local only).

The database is imported from the project's files, every process of the study is scored for every
indicator of the method, and each score is compared with the score of the same process in an
earlier study of the same data, and with Brightway's score for the activity of the same name. The
earlier study's `no_factor.csv`, a person's file that an import keeps, is copied into the new one:

    export HEADWATER_HOME=/path/to/headwater-home              # holds the release
    export BRIGHTWAY2_DIR=$HEADWATER_WORK/<scratch>/brightway   # holds the project, only read
    OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 uv run python bench/brightway_import_parity.py \\
        --project <project> --database <database> \\
        --release ecoinvent-3.12-cutoff@b9428cdd8045 --method <method folder> \\
        --out <scratch folder>/<study> --study <earlier study folder> \\
        --brightway-scores <csv with columns activity, indicator, brightway>

Scores are licensed values and stay in the scratch folder. The results file
(`bench/results/brightway_import_parity_results.json` by default) holds counts and the largest
relative differences only: no names, ids or values of the project.
"""

import argparse
import csv
import json
import math
import platform
import shutil
import time
from collections import Counter
from pathlib import Path

import headwater as hw
from headwater.io.brightway import import_brightway
from headwater.cli._common import open_release

TOLERANCE = 1e-6


def relative(a: float, b: float) -> float:
    size = max(abs(a), abs(b))
    return 0.0 if size == 0 else abs(a - b) / size


def compare(new: dict[str, dict[str, float]], old: dict[str, dict[str, float]]) -> dict[str, object]:
    shared = sorted(set(new) & set(old))
    differ = [
        p for p in shared if any(relative(new[p][i], old[p][i]) > TOLERANCE for i in old[p])
    ]
    differences = [relative(new[p][i], old[p][i]) for p in shared for i in old[p]]
    return {
        "products": len(shared),
        "products_within_tolerance": len(shared) - len(differ),
        "products_differ": len(differ),
        "only_in_import": len(set(new) - set(old)),
        "only_in_reference": len(set(old) - set(new)),
        "scores": len(differences),
        "scores_over_tolerance": sum(d > TOLERANCE for d in differences),
        "max_relative_difference": max(differences, default=0.0),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    for name in ("--project", "--database", "--release", "--method", "--out"):
        parser.add_argument(name, required=True)
    parser.add_argument("--study")
    parser.add_argument("--brightway-scores")
    parser.add_argument(
        "--results",
        default=str(Path(__file__).parent / "results" / "brightway_import_parity_results.json"),
    )
    args = parser.parse_args()
    release = open_release(args.release)
    method = Path(args.method)

    start = time.perf_counter()
    done = import_brightway(args.project, args.database, release, args.out, method=method)
    import_s = time.perf_counter() - start
    report = done.report
    statuses = Counter(o.status.value for o in report.outcomes)

    if args.study and (Path(args.study) / "no_factor.csv").is_file():
        # A person's file, which an import keeps: the flows of the study the method has no
        # factor for, acknowledged in the earlier study.
        shutil.copy(Path(args.study) / "no_factor.csv", done.folder / "no_factor.csv")
    background = hw.load(release.path, method=method)
    indicators = {i.id: i.name for i in background.indicators}
    study = hw.open(done.folder)
    new = {p.id: study.scores(background, process_id=p.id) for p in study.folder.processes}
    results: dict[str, object] = {
        "machine": f"{platform.platform()}, {platform.processor()}, Python {platform.python_version()}",
        "release": f"{release.name}@{release.hash[:12]}",
        "tolerance": TOLERANCE,
        "import_s": round(import_s, 2),
        "activities": report.activities,
        "exchange_rows": report.exchange_rows,
        "lines": dict(sorted(statuses.items())),
        "linked_by": dict(sorted(report.mapped_by.items())),
        "parameters_copied": sum(p.copied for p in report.parameter_outcomes),
        "notes": len(report.notes),
        "clean": done.clean,
        "validation_errors": dict(Counter(e.code for e in done.validation.errors))
        if done.validation
        else None,
        "processes": len(new),
        "indicators": len(indicators),
    }
    if args.study:
        earlier = hw.open(args.study)
        # The earlier study may have another name, and ids from before D40: a process is matched
        # by its name and location.
        old = {
            f"{p.name}|{p.location}": earlier.scores(background, process_id=p.id)
            for p in earlier.folder.processes
        }
        results["against_study"] = compare(
            {f"{p.name}|{p.location}": new[p.id] for p in study.folder.processes}, old
        )
    if args.brightway_scores:
        by_name: dict[str, list[str]] = {}
        for p in study.folder.processes:
            by_name.setdefault(p.name, []).append(p.id)
        ids_of = {**{name: i for i, name in indicators.items()}, **{i: i for i in indicators}}
        brightway: dict[str, dict[str, float]] = {}
        unmatched = 0
        with open(args.brightway_scores, newline="", encoding="utf-8") as file:
            for row in csv.DictReader(file):
                ids = by_name.get(row["activity"], [])
                indicator = ids_of.get(row["indicator"])
                if len(ids) != 1 or indicator is None:
                    unmatched += 1
                    continue
                brightway.setdefault(ids[0], {})[indicator] = float(row["brightway"])
        results["against_brightway"] = {**compare(new, brightway), "rows_not_matched": unmatched}
    nonfinite = sum(not math.isfinite(s) for scores in new.values() for s in scores.values())
    results["nonfinite_scores"] = nonfinite
    Path(args.results).write_text(json.dumps(results, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
