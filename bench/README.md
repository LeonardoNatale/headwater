# bench

The scripts behind every number in [docs/appendix/benchmarks.md](../docs/appendix/benchmarks.md), kept as they were run, with paths moved to environment variables. They are research scripts, not library code.

They need a licensed ecoinvent copy, version 3.12 cut-off, except steps 19 and 20, which need an ecoSpold1 package (and for step 20 its LCIA results). Nothing licensed is stored here: `results/` holds timings and counts only.

Every script that reads ecoinvent reads 3.12 by default. The results files without a version in their name (`bench_results.json`, `bw_reference_results.json` and so on) and the rows of [docs/appendix/benchmarks.md](../docs/appendix/benchmarks.md) that say 3.10 were measured on ecoinvent 3.10, before the move: they are kept as history. Results of a run on 3.12 have the release or the version in the file name, for example `parity_numerics_ecoinvent-3.12-cutoff_results.json`.

## Environment

```bash
export ECOINVENT_DIR=/path/to/ecoinvent/3.12-cutoff      # contains datasets/ and MasterData/
export ECOINVENT_APOS_DIR=/path/to/ecoinvent/3.12-apos   # the same for APOS, read by steps 11 and 25 only
export HEADWATER_HOME=/path/to/headwater-home            # holds the releases, `ecoinvent-3.12-cutoff@b9428cdd8045`
export HEADWATER_WORK=/path/to/scratch                   # scratch folder, also WORK below
export BRIGHTWAY2_DIR="$HEADWATER_WORK/brightway-parity-3.12" # scratch folder, never your real Brightway folder
WORK="$HEADWATER_WORK"                                   # scratch folder for DuckDB and Parquet copies
uv venv && uv pip install bw2data bw2calc bw2io duckdb pyarrow scipy numpy
```

`BRIGHTWAY2_DIR` must point at a scratch folder under `HEADWATER_WORK`: importing bw2data creates and migrates files in whatever folder it uses.

The data comes from ecoinvent's download page: the 3.12 unit processes (the folder `ECOINVENT_DIR` names), the LCIA workbook (`LCIA Implementation 3.12.xlsx`) and ecoinvent's own scores (the cumulative LCIA workbook). The 3.12 files stamp release 3.0 in every file, so `headwater release import` takes `--version 3.12`.

The parity scripts (steps 11 and 12) and the import timing (step 13) use `HEADWATER_WORK` instead of `WORK`, the scratch folder the integration tests read. Brightway is not a headwater dependency: `bw_reference.py` runs with `uv run --no-project`, as its docstring shows, and refuses a `BRIGHTWAY2_DIR` outside `HEADWATER_WORK`.

## Brightway versions

Comparisons with Brightway use the versions of the Science team's `lca/poetry.lock`, on Python 3.12: **bw2data 4.5.4, bw2calc 2.3, bw2io 0.9.14 and peewee 3.19.0**.

```bash
uv run --no-project --python 3.12 --with bw2data==4.5.4 --with bw2calc==2.3 --with bw2io==0.9.14 \
    --with peewee==3.19.0 python <script>
```

`bw_where_reference.py` lists every pin in its script header (the other packages Brightway needs, and bw2analyzer 0.11.8): `uv run bench/bw_where_reference.py` builds that environment. The commands in the docstrings of `bw_reference.py` and `bw_excel_reference.py` carry the same four pins.

peewee is pinned because bw2data 4.5.4 does not limit it. With the newest peewee (4.5.3, 24 October 2026) `import bw2data` fails with `ImportError: cannot import name 'SqliteExtDatabase' from 'playhouse.sqlite_ext'`. With peewee 3.19.0 it imports and opens a project.

The runs of steps 3, 4, 11 and 14 on 3.10 recorded in [docs/appendix/benchmarks.md](../docs/appendix/benchmarks.md) used bw2data 4.7, bw2calc 2.5.0 and bw2io 0.9.17, as the scope of each table says. The run of step 11 on 3.12, step 15 and the 3.12 pilot used the pinned versions.

## Order

| Step | Script | Measures |
|---|---|---|
| 1 | `tshirt.py`, `rewrite_demo.py` | Toy example in Brightway; the "rewrite identical data, footprint drops to 0" failure |
| 2 | `find_bad.py "$ECOINVENT_DIR/datasets"` | Source files that do not parse (headwater's import reads all 26,533 files of the 3.12 copy; the 3.10 copy had one empty file) |
| 3 | `bench_ei.py results/bench_3.12_results.json "$ECOINVENT_DIR/datasets"` | Brightway import of ecoinvent, sizes, first calculations |
| 4 | `bench2.py results/bench2_3.12_results.json` | Brightway factorization cost, cost of one edit, project copy |
| 5 | `duck_bench.py $WORK` | Copy into DuckDB and Parquet, matrices from SQL, transposed solve, lock behaviour |
| 6 | `sign_and_size.py $WORK`, `waste_sign.py $WORK` | Waste sign rule, sizes with every field kept |
| 7 | `adjoint.py` | All scores from one transposed solve, against bw2calc |
| 8 | `calc_lab.py $WORK` | Graph structure, factorization settings, explain, sensitivities, rank-1 update, SKU batch |
| 9 | `calc_lab2.py`, `calc_lab3.py`, `calc_lab4.py`, `calc_lab5.py` (each `$WORK`) | Accuracy checks, scaling, examples on an untouched product |
| 10 | `lake_test.py` | DuckLake snapshots, time travel and change feed on a toy table |
| 11 | `bw_reference.py [--system-model apos]` | A fresh Brightway import of 3.12; its matrices, ids and scores for the 3-flow method, to `$HEADWATER_WORK/reference-3.12/` for `tests/integration/test_parity.py`. With `--system-model apos` it reads `$ECOINVENT_APOS_DIR` into its own project and writes `reference-3.12-apos/` for `tests/integration/test_apos.py` |
| 12 | `parity_numerics.py <release folder>` | Solver settings, condition, backward errors, exact zeros, float32 rounding and copies of A, on a headwater release |
| 13 | `import_timing.py results/import_timing_3.12_results.json [runs] --label <name> [--exclude <exclude.csv>]` | headwater's import of `$ECOINVENT_DIR`: read and freeze, best of the runs. 3.12 has no empty file, so `--exclude` is not needed. Run it from two checkouts in turns, with one run each time, to compare them |
| 14 | `excel_parity.py prepare`, `bw_excel_reference.py`, `excel_parity.py compare` | The Excel import on a real Brightway workbook: counts of mapped and unmapped rows, and each activity's score against Brightway's, on the same release. The three steps and their environment are in the docstring of `excel_parity.py` |
| 15 | `where_parity.py prepare`, `bw_where_reference.py`, `where_parity.py compare` | headwater's top 10 contributors (`bg.where`) on 10 products of ecoinvent 3.12 against Brightway's contribution analysis: the same processes, their order and their values. The three steps and their environment are in the docstring of `where_parity.py` |
| 16 | `scenario_crossover.py <release folder>` | Update or refactorize: both ways of solving a scenario, timed end to end for 1 to 1,000 changed columns of the real release, two column sets for each. Gives the largest number of columns where the update works and is faster than refactorizing (`CROSSOVER` in `calc/update.py`), and where it starts to refuse edits. Run it alone: it records the load average and marks a run that starts above 1 as not quiet |
| 17 | `find_timing.py <release> [<method folder>]` | `headwater find` on a release: the whole command in a fresh process, the search table, and each kind of query warm, with the load average before and after. With a method folder, it also times `bg.find` on a loaded background |
| 18 | `plain_vs_scaled.py` | A plain SuperLU solve of Aᵀ (SciPy defaults, no scaling) against headwater's scaled solve, each against ecoinvent's Cumulative LCIA on EF v3.1 and 3.12: products above 1e-6, the largest relative difference, and backward errors. Counts only. Needs `ECOINVENT_LCIA`, `ECOINVENT_RELEASE` and `ECOINVENT_CUMULATIVE_LCIA`, as `tests/integration/test_method_import.py` does. Run it alone: it records the load average |
| 19 | `import_ecospold1.py results/import_ecospold1_results.json [runs]` | headwater's import of a folder of ecoSpold1 files (`$ECOSPOLD1_DIR`, as BAFU:2026): read and stages, best of the runs, with the counts and the load average before each. Needs no ecoinvent copy. Run it alone |
| 20 | `bafu_gwp_check.py results/bafu_gwp_check_results.json` | headwater's IPCC 2021 GWP 100 on the BAFU:2026 ecoSpold1 import against the scores in BAFU's own LCIA workbook, with the 30 AR6 factors as data of the script, and a test of reading `Nm3` as `m3`. Needs `ECOSPOLD1_DIR` and `BAFU_LCIA_XLSX`. Counts and quantiles only |
| 21 | `cli_startup.py [runs] [--json out.json]` | Start-up of the `headwater` command on the toy fixtures: `--help`, `find --help`, `find` and `calc`, each a fresh process, best of the runs, and the heavy libraries that `import headwater.cli` loads. Needs no licensed data. Run it in both environments, with and without the `notebooks` group, because `import pyarrow.dataset` imports pandas when it is installed. It records the load average |
| 22 | `supply_graph.py <release folder>` | The strongly connected groups of a release's supply graph: how many, and how large the largest is. Counts only |
| 23 | `monte_carlo.py time`, `monte_carlo.py prepare`, `bw_monte_carlo.py`, `monte_carlo.py compare` | Monte Carlo on 3.12 with EF 3.1: the time of one iteration and its parts (draw, rebuild, factorize, solves), then headwater's distributions against Brightway's on the 10 products of step 15, 200 iterations each. The steps and their environment are in the docstring of `monte_carlo.py`; `bw_monte_carlo.py` lists its Brightway pins in its header, as `bw_where_reference.py` does |
| 24 | `monte_carlo_study.py build`, `monte_carlo_study.py run` | Monte Carlo of a study on 3.12 with EF 3.1: the 3.12 pilot with every line lognormal (GSD 1.1), a local override and a scenario that changes a column of A, 200 iterations, timed against 20 iterations of the background alone. The results file holds timings and counts; the ranges are printed, not written, because they come from the pilot's data. The steps and their environment are in its docstring |
| 25 | `ecospold2_line_kinds.py <folder> results/<name>_results.json [--workers 2]` | Every kind of product line of an ecoSpold2 folder (side, by-product class, sign), with the role its system model's table gives it, how many name a supplier and the kind of that supplier's reference. Counts only; the first files of each kind the table rejects are printed, not written |
| 26 | `processes_read.py <release> <method folder> <name>` | `bg.processes` on a release with ids it has not read: one call per id, one call with 50 ids, and a repeat, each split by table (opening and scanning), with the load average before and after. Counts and times only, to `results/processes_read_<name>_results.json`. `processes_read.py layout <release>` writes how the exchanges table is laid out (row groups, order, decoded size) |
| 27 | `bw_toy_project.py tests/fixtures/brightway` | Writes the toy Brightway project the import tests read, with bw2data 4.5.4 (the four pins above), and checks its scores in Brightway: 1.25205, 1.242646 and 5.0596 |
| 28 | `brightway_import_parity.py --project <name> --database <name> ...` | `import_brightway` on a real project: lines linked, and every score of the study against Brightway's and against an earlier study of the same data. The arguments are in its docstring |
| 29 | `database_layout.py <python of a 0.1.0 environment>` | headwater's own `hw.open` and `validate` of synthetic databases of 160 and 2,000 processes, in the layout of 0.1.0 (`processes/`) with that Python and in the two tables of 0.2.0 with this one, the same dependencies in both. Counts and times only, with the load average, to `results/database_layout_results.json`. Needs no licensed data |

Steps 3 and 4 need `if __name__ == "__main__":` (already in place) because bw2io's importer uses multiprocessing.

## Known caveats

- Step 11 uses a new Brightway folder, not the one from steps 3 and 4.
- Step 4 multiplies one input of one process by 1.1 and saves it. The DuckDB copy in step 5 is taken after that, so that process and everything that buys from it differ slightly from the original data. Step 9 picks an example product with no supply-chain link to it.
- One run each, on one machine. Treat times as rough.
