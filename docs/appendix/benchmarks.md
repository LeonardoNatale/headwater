# Benchmarks

Every measured number in these docs, in one place. Other files link here instead of repeating numbers.

## Scope

- **Machine:** one Apple Silicon Mac (arm64), macOS. No PARDISO (Intel only), no scikit-umfpack.
- **Software:** Python 3.12.11, NumPy 2.5.3, SciPy 1.18.1, DuckDB 1.5.6, PyArrow 25.0.1, bw2data 4.7, bw2calc 2.5.0, bw2io 0.9.17.
- **Data:** ecoinvent, ecoSpold2, from a local licensed copy. Each section says its **Version**. The sections of the first measurements, from 10 October 2026, are on ecoinvent 3.10 cut-off: 23,523 files, one of them empty, so 23,522 datasets. The later ones, and every claim the docs make today, are on 3.12 cut-off: 26,533 files, 26,533 datasets. Sections with no version line use no ecoinvent data (the toy, a synthetic release, or BAFU data).
- **Method:** a 3-flow climate subset, not EF 3.1: CO₂ fossil = 1, CH₄ fossil = 29.8, N₂O = 273.
- **Runs:** one run each. Treat times as rough.
- **Date:** 10 October 2026.
- **Scripts:** [bench/](../../bench/). Raw outputs: [bench/results/](../../bench/results/).

## Toy numbers, checked by hand

| Case | Value |
|---|---|
| T-shirt, 0.25 kg fabric, 0.5 kWh | 1.19205 |
| Same, plus 0.05 kg scraps to incineration (1.2 per kg treated) | 1.25205 |
| Same as first, fabric 0.30 kg | 1.37748 |
| Same as first, fabric 0.22 kg | 1.08079 |
| Sensitivity to kWh per kg fabric | 0.13245 |

## Brightway

**Version:** ecoinvent 3.10 cut-off, measured before the move to 3.12.

Scripts: `bench_ei.py`, `bench2.py`.

The memory column is the highest memory the process had reached by the end of that step. Later rows include the import's data still held in memory, so they overstate what that step alone needs.

| Step | Time | Process peak memory so far |
|---|---|---|
| Parse 23,522 XML files | 27.4 s | 3.4 GB |
| Run 22 cleaning and linking steps | 2.6 s | 3.5 GB |
| Write to SQLite, arrays and search index | 47.3 s | 4.0 GB |
| Load arrays and build matrices | 0.14 s | |
| Score one product, one solve | 2.3 s | |
| Factorize through bw2calc (SciPy default settings) | 57.2 s | 5.5 GB |
| Each further product, reusing the factorization | 41 ms | |
| 1,000 products in one MultiLCA | 17.0 s | 7.6 GB |
| Edit one exchange and save | 6 ms | |
| Score one product after that edit | 12.2 s (against 2.3 s without the edit) | |
| Copy the project | 0.4 s (file-system clone on this Mac) | |

Counts after import: 743,398 exchanges (270,261 technosphere, 449,615 biosphere, 23,522 production), 4,362 elementary flows.

## Storage size

**Version:** ecoinvent 3.10 cut-off, measured before the move to 3.12.

Scripts: `bench_ei.py`, `duck_bench.py`, `sign_and_size.py`.

| Form | Size |
|---|---|
| Brightway project, total | 1,499 MB |
| of which SQLite nodes and edges | 1,365 MB |
| of which search index | 101 MB |
| of which solver arrays | 32 MB |
| Every field kept, Parquet with zstd | 124.5 MB |
| Every field kept, DuckDB file with fields as raw JSON | 1,448 MB |
| Calculation fields only, DuckDB file | 68.7 MB |
| Calculation fields only, Parquet with zstd | 17.1 MB |

The saving comes from Parquet's compression, not from DuckDB. Typed columns instead of raw JSON would likely make the DuckDB file much smaller; not measured.

## DuckDB

**Version:** ecoinvent 3.10 cut-off, measured before the move to 3.12.

Script: `duck_bench.py`.

| Step | Result |
|---|---|
| Extract all nodes and exchanges from Brightway's pickles | 4.2 s |
| Load from Parquet into DuckDB, with primary and foreign keys | 2.1 s (from already-extracted Parquet, not from XML) |
| Build the matrices with SQL, straight into NumPy | 0.07 s |
| All 23,522 scores from one transposed solve, no stored factorization | 2.14 s |
| Difference against bw2calc on 10 random products | at most 7.2 × 10⁻⁸ relative |
| Update one exchange | 3 ms |
| Rebuild matrices and all scores after that update | 2.13 s |
| Insert an exchange pointing at a process that does not exist | rejected by the foreign key |
| Open the file from a second process while the first has it open for writing | fails, both read-write and read-only |
| Same, after the first process closes it | works |

DuckLake (`lake_test.py`, toy table): each write created a snapshot; reading `AT (VERSION => n)` returned the earlier values; `table_changes` listed the update as before and after rows.

## Calculation core

**Version:** ecoinvent 3.10 cut-off, measured before the move to 3.12. The supply graph is also counted on 3.12.

Scripts: `calc_lab.py`, `calc_lab2.py`, `calc_lab5.py`, `adjoint.py`.

**Structure of the supply graph, ecoinvent 3.10 (`calc_lab.py`):** 8,073 strongly connected groups. The largest has 15,374 processes (65%); the next ones have 13, 10, 6 and 6. 8,057 processes are in no loop.

**Structure of the supply graph, ecoinvent 3.12 (`supply_graph.py`, 25 October 2026, headwater's A without the recyclable lines, [raw output](../../bench/results/supply_graph_ecoinvent-3.12-cutoff_results.json)):** 9,499 strongly connected groups. The largest has 16,962 processes (64%); the next ones have 13, 10, 8 and 6. 9,484 processes are in no loop.

**Factorization settings (SciPy SuperLU):**

| Column ordering | Pivoting | Time | Non-zeros in L and U |
|---|---|---|---|
| COLAMD (SciPy default) | on (default) | 56.2 s | 53.7 million |
| COLAMD | off | 36.9 s | 38.6 million |
| Minimum degree on Aᵀ+A | on | 11.7 s | 21.9 million |
| Minimum degree on Aᵀ+A | off | 2.8 s | 3.4 million (about 40 MB) |

**With the stored factorization:**

| Operation | Time | Check |
|---|---|---|
| All scores, one indicator (transposed solve) | 4.3 ms | |
| One product's supply (forward solve) | 2.4 ms | `h · s` equals `x` to 10⁻¹⁵ |
| Contribution tree, 3 levels | 0.5 to 1.1 ms (two products) | sums to the score at every level |
| Sensitivities to all 292,955 matrix entries, one product | 18 ms | top one matched a 1% finite-difference test to 4 digits |
| Rank-1 update of all scores after one change | 4.6 ms (refactorizing: 2.8 s) | 5.9 × 10⁻¹⁵ from a full re-solve |
| Scores that moved after that one change (global electricity market) | 21,585 of 23,522 | |
| 50,000 synthetic SKUs, 20 random background inputs each, 25 indicators, sparse multiply | 7.9 ms | synthetic, no study tree |
| Same SKUs, 1 indicator, DuckDB join and group-by | 6.9 ms | synthetic |

`adjoint.py` (no stored factorization): all 23,522 scores in 2.17 s, matching bw2calc within 5.4 × 10⁻⁸ on 20 random products.

## Calculation from a release

Script: `release_calc.py`. Raw output: [bench/results/release_calc_results.json](../../bench/results/release_calc_results.json).

**Scope:** one Apple Silicon Mac (arm64), macOS 25.6. Python 3.14.3, NumPy 2.5.3, SciPy 1.18.1, DuckDB 1.5.6, PyArrow 26.0.0. 11 October 2026. The toy rows are the best of 20 runs, the others one run, or the best of 3 for the SKU rows. Treat times as rough.

**Data:** the toy release, and a synthetic release with no licensed data. The synthetic one is shaped like ecoinvent 3.10 cut-off: 23,500 processes (15% of them treatments), 4,000 elementary flows, 619,097 lines, 149,097 non-zeros in A, 470,000 in B, three indicators. Its supply graph is made up (half of the purchases go to the 1% most popular products), so its factorization time is not comparable with the ecoinvent rows above.

| Step | Time |
|---|---|
| Toy release (3 processes): `Release.connect()` and close | 6.0 ms |
| Toy: assemble A and B from the release | 25.0 ms |
| Toy: `hw.load` from the release, and from the folder | 27.0 ms, and 2.1 ms |
| Synthetic: `sum(amount)` over all lines, through the registered pyarrow dataset | 3.2 ms |
| Same, through `read_parquet` | 3.3 ms |
| Synthetic: assemble A and B from the release (SQL, slim records for validation, uncovered flows) | 594 ms |
| `hw.load` from the release: assemble, factorize, 3 transposed solves | 7.12 s |
| of which factorize A | 6.60 s |
| One transposed solve, one indicator | 2.5 ms |
| `write_indicators`, 3 indicators | 59 ms |
| `score_many`, 50,000 SKUs × 20 lines, 3 indicators, from the background | 55 ms |
| Same, from the indicators release | 54 ms |
| `Result.not_characterised`, first read (500 uncovered flows, all reached) | 5.3 ms |

On the toy, the 25 ms of assembly is fixed cost: opening DuckDB, registering seven pyarrow datasets and running about 15 queries. Registering the datasets does not slow a scan: 619,097 lines cost the same through the dataset and through `read_parquet`. The largest residual of the three indicator solves was 1.5 × 10⁻¹⁵.

## Study queries on a release

Script: `levers.py`. Raw output: [bench/results/levers_results.json](../../bench/results/levers_results.json).

**Scope:** one Apple Silicon Mac (arm64), macOS 25.6. Python 3.14.3, NumPy 2.5.3, SciPy 1.18.1, DuckDB 1.5.6, PyArrow 26.0.0. 11 October 2026. Best of 3 rounds, each on a fresh `hw.load`. Other tasks were running on the machine (load average about 2.7), so the times are rough; the gaps between rows are far larger than that noise.

**Data:** the synthetic release of the [calculation from a release](#calculation-from-a-release) rows: 23,500 processes, 619,097 lines, 149,097 non-zeros in A, 470,000 in B. The study is one process buying 20 products.

| Step | Time |
|---|---|
| `hw.load` from the release | 7.04 s |
| of which assemble A and B, with the amounts, signs and ids of every line | 0.61 s |
| `Study.calc` | 42 ms |
| of which `validate_study`, which builds a catalog over the whole background | 38 ms |
| `Result.contributions`, 3 levels | 2.5 ms |
| `Result.sensitivity(top=10)`, first call | 28 ms |
| Same, second call | 25 ms |
| `Result.sensitivity()` with no `top`: an object for each of 595,617 lines, before `Sensitivity` gained `name` and `supplier_name` | 1.34 s |
| Memory of the columns of lines a loaded background keeps | 21.8 MB |

## Accuracy

**Version:** ecoinvent 3.10 cut-off, measured before the move to 3.12.

Scripts: `calc_lab2.py`, `calc_lab3.py`, `calc_lab4.py`.

| Check | Result |
|---|---|
| Residual of all scores, relative to the largest | 1.4 × 10⁻¹⁴ without pivoting, 1.6 × 10⁻¹⁴ with SciPy defaults |
| After one step of iterative refinement | 9.7 × 10⁻¹⁵ |
| Products changing by more than 10⁻⁶ when pivoting is switched on and off (same ordering) | 1 without scaling, 0 with rows and columns scaled |
| Same, against SciPy's default solver | 9, all with scores below 0.004 |
| Range of scores | largest about 1.8 × 10¹¹ per unit, median 0.58 |

Tested on cut-off data only.

## LCA checks

**Version:** ecoinvent 3.10 cut-off, measured before the move to 3.12.

Scripts: `waste_sign.py`, `sign_and_size.py`, plus one DuckDB query on the `edge` table for markets.

| Check | Result |
|---|---|
| Waste treatments (reference product of −1) | 4,086 |
| of which with a negative raw score / positive / zero | 3,773 / 11 / 302 |
| Ordinary datasets with a negative raw score | 11 |
| Waste rule: positive kg sent × positive burden per kg treated, against the full solve, 50 activities with waste inputs | at most 1.6 × 10⁻¹² relative |
| Markets with at least one uncertain input | 4,695 of 7,220 |

## Brightway failure reproductions

Scripts: `tshirt.py`, `rewrite_demo.py`.

| Check | Result |
|---|---|
| T-shirt, fresh | 1.19205 |
| After rewriting the emissions database with identical data | 1.19205 |
| After reprocessing the T-shirt database, as any edit does | 0.0 |
| Amount 0.004 stored and read back | 0.00400000019 |

## Parity with Brightway

Scripts: `bw_reference.py` (the Brightway side, written to a scratch folder) and [tests/integration/test_parity.py](../../tests/integration/test_parity.py) (the comparison). Raw output: [bench/results/bw_reference_3.12_results.json](../../bench/results/bw_reference_3.12_results.json) for 3.12, [bench/results/bw_reference_results.json](../../bench/results/bw_reference_results.json) for 3.10.

### On ecoinvent 3.12

**Version:** ecoinvent 3.12 cut-off. These are the figures the docs quote.

**Scope:** one Apple Silicon Mac (arm64), macOS 25.6, 25 October 2026. headwater on Python 3.14.3, NumPy 2.5.3, SciPy 1.18.1, PyArrow 26.0.0. Brightway on Python 3.12.11 with the versions the Science team pins, bw2data 4.5.4, bw2calc 2.3, bw2io 0.9.14 and peewee 3.19.0, NumPy 2.5.4, SciPy 1.18.1, from a fresh import of the same files, in a new scratch project. The 3-flow climate method: 13 elementary flows on both sides. **Not a quiet run:** other work kept the load average between about 8 and 17 while Brightway ran (read from `uptime`; the script did not record it then), so Brightway's times are rough.

| Check | Result |
|---|---|
| Brightway import: parse, cleaning and linking, write | 27 s, 5.9 s, 83 s |
| Lines Brightway deletes while linking | 0: every file reads and every line links |
| bw2calc: build and factorize the matrices; then score each of the 26,533 products, one demand at a time | 86 s; 1,404 s |
| How Brightway stores amounts | float32, and bw2calc sums the lines of one cell in float32 |
| Technosphere: Brightway's 339,051 lines against headwater's, each rounded to float32 and summed in float32 | equal in all 335,748 cells, once the 2,426 recyclable lines are left out |
| The recyclable lines in Brightway | 2,426, all +kg |
| Biosphere, the same way | equal in all 553,891 cells, 3,297 flows |
| Brightway's negative technosphere inputs | 50,166: headwater's 47,740 waste lines and 2,426 recyclable lines, the same buyer and product |
| Own use in a treatment | none: 856 lines in 856 datasets, 0 of them in the 4,623 treatments |
| headwater's calc on Brightway's matrices, against headwater's calc on its own lines rounded to float32 | at most 7.2 × 10⁻¹⁵ relative; the same 2,041 products exactly 0 on both sides |
| headwater's calc with pivoting, on its own matrix and on Brightway's | one refinement after the exact zeros; residual 8.7 × 10⁻¹⁶ and 7.5 × 10⁻¹⁶; at most 9.8 × 10⁻¹⁵ from the solve without pivoting |
| headwater on its float64 release, against Brightway's float32 matrices | at most 1.7 × 10⁻⁷ relative; 23,653 products above 10⁻⁹, none above 10⁻⁶ |
| bw2calc's scores, against headwater on its float64 release | at most 1.9 × 10⁻⁴ relative; 23,849 products above 10⁻⁹, 97 above 10⁻⁶ |
| bw2calc's scores, against headwater's calc on Brightway's own matrices | at most 1.9 × 10⁻⁴ relative; 21,073 products above 10⁻⁹, 96 above 10⁻⁶ |
| Componentwise backward error of all 26,533 scores, Aᵀ x = h | bw2calc: largest 9.6 × 10⁻⁵, 21,673 rows above 10⁻¹². headwater: largest 3.8 × 10⁻¹⁵, none above 10⁻¹⁴ |
| The waste rule, kg sent × burden per kg treated, both positive, on every activity with waste inputs | 11,465 activities, at most 6 × 10⁻¹⁵ of the sum of their terms; 59 of them have no burden at all |
| Importing the same folder twice | the same release hash: two imports in one test run, with the default number of workers and with 2, and `b9428cdd8045` again in `tests/integration/test_import_hashes.py` |

bw2calc's scores come from one forward solve per product, so the backward error of the 26,533 of them together also counts how well they agree with each other. bw2calc's differences from headwater are larger on 3.12 than they were on 3.10 (1.9 × 10⁻⁴ against 3.3 × 10⁻⁵). The two runs differ in the data and in the Brightway versions (bw2calc 2.3 here, 2.5.0 on 3.10), and the cause is not isolated. Nothing in the test asserts on bw2calc's own scores.

### On ecoinvent 3.10

**Version:** ecoinvent 3.10 cut-off, measured before the move to 3.12 and kept as history.

**Scope:** one Apple Silicon Mac (arm64), macOS 25.6, 11 October 2026. headwater on Python 3.14.3, NumPy 2.5.3, SciPy 1.18.1, PyArrow 26.0.0. Brightway on Python 3.12.11, bw2data 4.7, bw2calc 2.5.0, bw2io 0.9.17, SciPy 1.18.1, from a fresh import of the same files. The 3-flow climate method: 13 elementary flows on both sides. Brightway's times are one run, with other work on the machine at the same time: treat them as rough.

| Check | Result |
|---|---|
| Brightway import: parse, 22 cleaning and linking steps, write | 50 s, 3.9 s, 118 s |
| Lines Brightway deletes while linking | 1, the line that buys from the empty file (in headwater, the data gap) |
| bw2calc: build and factorize the matrices; then score each of the 23,522 products, one demand at a time | 240 s; 1,708 s |
| How Brightway stores amounts | float32, and bw2calc sums the lines of one cell in float32 |
| Technosphere: Brightway's 293,783 lines against headwater's, each rounded to float32 and summed in float32 | equal in all 291,060 cells, once the 1,881 recyclable lines are left out |
| The recyclable lines in Brightway | 1,881, each in a cell of its own, all +kg in the row of a recycled-content dataset |
| Biosphere, the same way | equal in all 449,614 cells, 2,648 flows |
| Brightway's negative technosphere inputs | 46,145: headwater's 44,264 waste lines and 1,881 recyclable lines, the same buyer and product |
| headwater's calc on Brightway's matrices, against headwater's calc on its own lines rounded to float32 | at most 8.3 × 10⁻¹⁵ relative; the same 1,792 products exactly 0 on both sides |
| headwater's calc with pivoting, on its own matrix and on Brightway's | one refinement after the exact zeros; residual 1.4 × 10⁻¹⁵ and 1.1 × 10⁻¹⁵; at most 7.8 × 10⁻¹⁵ from the solve without pivoting |
| headwater on its float64 release, against Brightway's float32 matrices | at most 1.9 × 10⁻⁷ relative; 21,013 products above 10⁻⁹, none above 10⁻⁶ |
| bw2calc's scores, against headwater on its float64 release | at most 3.3 × 10⁻⁵ relative; 21,139 products above 10⁻⁹, 39 above 10⁻⁶ |
| bw2calc's scores, against headwater's calc on Brightway's own matrices | at most 3.3 × 10⁻⁵ relative; 19,689 products above 10⁻⁹, 43 above 10⁻⁶ |
| Componentwise backward error of all 23,522 scores, Aᵀ x = h | bw2calc: largest 1.7 × 10⁻⁵, 19,993 rows above 10⁻¹². headwater: largest 3 × 10⁻¹⁵, none above 10⁻¹⁴ |
| The waste rule, kg sent × burden per kg treated, both positive, on every activity with waste inputs | 10,314 activities, at most 5.1 × 10⁻¹⁵ of the sum of their terms |
| Importing the same folder twice | the same release hash: two imports in one test run, with 10 and 2 workers, and an import from the command line that gives the hash of one made earlier in another process |

bw2calc's scores come from one forward solve per product, so the backward error of the 23,522 of them together also counts how well they agree with each other.

## APOS against Brightway and ecoinvent

**Version:** ecoinvent 3.12 APOS (allocation at the point of substitution), 27,197 datasets.

Scripts: `ecospold2_line_kinds.py` (the kinds of lines), `bw_reference.py --system-model apos` (the Brightway side), [tests/integration/test_apos.py](../../tests/integration/test_apos.py) (import twice, flows, parity) and [tests/integration/test_method_import.py](../../tests/integration/test_method_import.py), run on the APOS release with the APOS cumulative LCIA. Raw output: [bench/results/ecospold2_line_kinds_ecoinvent-3.12-apos_results.json](../../bench/results/ecospold2_line_kinds_ecoinvent-3.12-apos_results.json), [the same for cut-off](../../bench/results/ecospold2_line_kinds_ecoinvent-3.12-cutoff_results.json), [bench/results/bw_reference_3.12-apos_results.json](../../bench/results/bw_reference_3.12-apos_results.json).

**Scope:** one Apple Silicon Mac (arm64), macOS 25.6, 1 November 2026. headwater on Python 3.14, with 2 workers and 2 BLAS threads. Brightway with the pinned versions of [Parity with Brightway](#parity-with-brightway), parsing in one process (`--one-process`), in a new scratch project. The 3-flow climate method of that section. **Not a quiet run:** the load average was between 1.5 and 9.7, so times are rough.

| Check | Result |
|---|---|
| Product lines cut-off's rules refuse in APOS | 349 Recyclable and 100 allocatable negative references, treating 88 flows; 818 negative allocatable inputs, each sent to one of those treatments |
| Product lines cut-off's rules accept but would cut off | 13,909 negative Recyclable inputs, each sent to one of those treatments (cut-off's 2,426 each go to a recycled-content dataset with a positive reference) |
| The 88 treated flows made or bought as a product | none; no treatment buys from itself |
| Brightway import: parse in one process, cleaning and linking, write | 166 s, 11 s, 110 s; no line deleted, none left unlinked |
| bw2calc: build and factorize; then score each of the 27,197 products, one demand at a time | 118 s; 1,847 s |
| Technosphere: Brightway's 947,565 lines against headwater's, each rounded to float32 and summed in float32 | equal in all 946,689 cells, with no line left out |
| Biosphere, the same way | equal in all 776,844 cells |
| headwater's calc on Brightway's matrices, against headwater's calc on its own lines rounded to float32 | 0 relative difference; the same 841 products exactly 0 on both sides |
| Importing the same folder three times (two in the test, one with `release import --workers 2`) | the same release, `ecoinvent-3.12-apos@474501c24cdc` |
| Cut-off imported again with the same code | `ecoinvent-3.12-cutoff@b9428cdd8045`, unchanged |
| Flows | MasterData is the same file in both folders; 14,249 flows, 88 of them wastes in APOS and products in cut-off |
| EF v3.1 from the LCIA workbook, mapped onto the APOS release | every factor mapped, the same method hash as on cut-off |
| EF v3.1 scores against ecoinvent's APOS cumulative LCIA, 27,197 products | largest relative difference 2.6 × 10⁻¹⁰ (climate change, land use and land use change), every other indicator but land use at most 8.1 × 10⁻¹¹; land use: 195 products whose score cancels, each within 1.2 × 10⁻¹⁶ of its gross, as on cut-off |

## Numerics on ecoinvent

Script: `parity_numerics.py`. Raw output: [bench/results/parity_numerics_ecoinvent-3.12-cutoff_results.json](../../bench/results/parity_numerics_ecoinvent-3.12-cutoff_results.json) for 3.12, [bench/results/parity_numerics_results.json](../../bench/results/parity_numerics_results.json) for 3.10.

### On ecoinvent 3.12

**Version:** ecoinvent 3.12 cut-off. These are the figures the docs quote.

**Scope:** the same Mac, Python 3.14.3, NumPy 2.5.3, SciPy 1.18.1, PyArrow 26.0.0, 25 October 2026. Release `ecoinvent-3.12-cutoff@b9428cdd8045` and the 3-flow climate method (13 elementary flows): 26,533 processes and 335,769 non-zeros in A. **Not a quiet run:** the load average was 13.7 when the script started and 5.4 when it ended, from other work on the machine, so every time here is slow and rough. The errors, the condition and the counts do not depend on the load.

**Solver settings, re-measured on the release.** Rows and columns scaled as headwater does, unless the row says otherwise. Errors are componentwise backward errors of the transposed solve for the 3-flow method.

| Column ordering | Pivoting | Factorize | Non-zeros in L and U | One solve | Largest error, no refinement | Rows above 10⁻¹², no refinement | Largest error, exact zeros and one refinement |
|---|---|---|---|---|---|---|---|
| Minimum degree on Aᵀ+A (headwater) | off | 3.8 s | 4.0 million | 5.1 ms | 3.8 × 10⁻¹⁵ | 0 | 2.7 × 10⁻¹⁶ |
| Minimum degree on Aᵀ+A | on | 5.0 s | 7.0 million | 9.6 ms | 1 | 1,134 | 8.7 × 10⁻¹⁶ |
| COLAMD | off | 58.3 s | 46.7 million | 68 ms | 8.1 × 10⁻¹⁵ | 0 | 4.5 × 10⁻¹⁶ |
| COLAMD | on | 28.2 s | 31.6 million | 46 ms | 1 | 4,098 | 1.5 × 10⁻¹⁴ |
| COLAMD, not scaled (SciPy defaults, as in bw2calc) | on | 74.6 s | 61.8 million | | 1.6 × 10⁻⁴ | 9,512 | |

An error of 1 is rounding noise in a product whose exact score is 0, as on 3.10. `hw.load` takes 6.8 s, of which `factorize` 3.6 s, with its check solve and condition estimate.

**The condition and its limit.** headwater refuses a matrix whose componentwise condition estimate is above 10¹⁰. The normwise one is listed beside it, for comparison only.

| Matrix | Componentwise (the limit applies to this) | Normwise, 1-norm, after scaling | Normwise, not scaled | Against the limit of 10¹⁰ |
|---|---|---|---|---|
| ecoinvent 3.12 cut-off | 30.0, in 21 ms | 3.4 × 10¹⁴ | 1.0 × 10²⁴ | passes, 3.3 × 10⁸ times below |
| The toy with a loop of gain 1 + 2 × 10⁻¹⁶ (singular up to rounding) | 1.8 × 10¹⁶ | 1.8 × 10¹⁶ | | fails, 1.8 × 10⁶ times above |

| Check | Result |
|---|---|
| Smallest score for an emission of 1 per run of every process (a loop that uses more than it makes would give a negative one) | 1 |
| Backward error of every forward solve, one per product | largest 5.0 × 10⁻¹⁵, none above 10⁻¹⁴ (26,533 solves, 86.4 s) |
| Backward error of the transposed solve | largest 3.8 × 10⁻¹⁵ |
| Products with no emission of the 3 flows anywhere upstream | 2,041, of which 97 recycled-content datasets; all exactly 0 from the unpivoted solve |
| Finding them from the sparsity of A, done on every solve | 4.1 ms |
| One checked transposed solve (`calc.scores`), exact zeros included | 10.4 ms, against 5.1 ms for the bare solve |
| Products whose score is below 10⁻³ of the sum of its positive and negative parts | 0; the smallest share is 0.47, and 8 processes have negative direct emissions |
| Every line rounded to float32 | scores move by at most 1.3 × 10⁻⁷ relative; 22,414 above 10⁻⁹, none above 10⁻⁶ |

**Copies of A**, per call:

| Operation | Time |
|---|---|
| One transposed solve, for comparison | 5.1 ms |
| Copy A into canonical CSC (`as_matrix`) | 0.72 ms |
| The same check on a matrix already canonical, with no copy (contributions and sensitivity) | 0.43 ms |
| `abs(A)` | 0.11 ms |
| Residual check computing \|A\|, and with \|A\| kept with the factors | 1.13 ms and 0.76 ms |
| Stacked matrix of a study with one process, built once per result, on the first `contributions` or `sensitivity` call | 2.8 ms |
| `calc.explain`, 3 levels, and `calc.levers` on one product | 1.1 ms and 26.2 ms |
| A in memory | 5.6 MB |

### On ecoinvent 3.10

**Version:** ecoinvent 3.10 cut-off, measured before the move to 3.12 and kept as history.

**Scope:** the same Mac, Python 3.14.3, NumPy 2.5.3, SciPy 1.18.1, PyArrow 26.0.0, 11 October 2026. Release `ecoinvent-3.10-cutoff@74d864daab6b` and the 3-flow climate method. Factorizations are one run; the rows in ms are the best of 20, or 5 for the slower ones. Times moved by up to a third between two runs of the script on this machine; the errors did not move.

**Solver settings, re-measured on the release.** Rows and columns scaled as headwater does, unless the row says otherwise. Errors are componentwise backward errors of the transposed solve for the 3-flow method.

| Column ordering | Pivoting | Factorize | Non-zeros in L and U | One solve | Largest error, no refinement | Rows above 10⁻¹², no refinement | Largest error, exact zeros and one refinement |
|---|---|---|---|---|---|---|---|
| Minimum degree on Aᵀ+A (headwater) | off | 2.9 s | 3.4 million | 4.3 ms | 3.0 × 10⁻¹⁵ | 0 | 2.6 × 10⁻¹⁶ |
| Minimum degree on Aᵀ+A | on | 3.7 s | 6.1 million | 7.8 ms | 1 | 1,570 | 1.4 × 10⁻¹⁵ |
| COLAMD | off | 37.9 s | 42.3 million | 57 ms | 8.1 × 10⁻¹⁵ | 0 | 4.4 × 10⁻¹⁶ |
| COLAMD | on | 17.8 s | 26.4 million | 38 ms | 1 | 3,421 | 4.1 × 10⁻¹⁴ |
| COLAMD, not scaled (SciPy defaults, as in bw2calc) | on | 61.8 s | 52.8 million | | 4.5 × 10⁻⁵ | 8,524 | |

An error of 1 is rounding noise in a product whose exact score is 0: pivoted factors leave about 10⁻⁹ there. `hw.load` takes 3.9 s, of which `factorize` 2.75 s, with its check solve and condition estimate.

**The condition and its limit.** headwater refuses a matrix whose componentwise condition estimate is above 10¹⁰. The normwise one is listed beside it, for comparison only.

| Matrix | Componentwise (the limit applies to this) | Normwise, 1-norm, after scaling | Normwise, not scaled | Against the limit of 10¹⁰ |
|---|---|---|---|---|
| ecoinvent 3.10 cut-off | 29.3, in 16 ms | 3.6 × 10¹⁴ | 1.0 × 10²⁴ | passes, 3.4 × 10⁸ times below |
| The toy with a loop of gain 1 + 2 × 10⁻¹⁶ (singular up to rounding) | 1.8 × 10¹⁶ | 1.8 × 10¹⁶ | | fails, 1.8 × 10⁶ times above |

| Check | Result |
|---|---|
| Smallest score for an emission of 1 per run of every process (a loop that uses more than it makes would give a negative one) | 1 |
| Backward error of every forward solve, one per product | largest 5.0 × 10⁻¹⁵, none above 10⁻¹⁴ (23,522 solves, 78.5 s) |
| Backward error of the transposed solve | largest 3.0 × 10⁻¹⁵ |
| Products with no emission of the 3 flows anywhere upstream | 1,792, of which 78 recycled-content datasets; all exactly 0 from the unpivoted solve |
| Finding them from the sparsity of A, done on every solve | 4.0 ms |
| One checked transposed solve (`calc.scores`), exact zeros included | 11.0 ms, against 5.3 ms for the bare solve |
| Products whose score is below 10⁻³ of the sum of its positive and negative parts | 0; the smallest share is 0.48, and 8 processes have negative direct emissions |
| Every line rounded to float32 | scores move by at most 1.6 × 10⁻⁷ relative; 19,899 above 10⁻⁹, none above 10⁻⁶ |

**Copies of A**, per call:

| Operation | Time |
|---|---|
| One transposed solve, for comparison | 5.3 ms |
| Copy A into canonical CSC (`as_matrix`) | 0.76 ms |
| The same check on a matrix already canonical, with no copy (contributions and sensitivity) | 0.45 ms |
| `abs(A)` | 0.11 ms |
| Residual check computing \|A\|, and with \|A\| kept with the factors | 1.17 ms and 0.78 ms |
| Stacked matrix of a study with one process, built once per result, on the first `contributions` or `sensitivity` call | 2.8 ms |
| `calc.explain`, 3 levels, and `calc.levers` on one product | 1.2 ms and 24.5 ms |
| A in memory | 4.8 MB |

## Import of ecoinvent

**Version:** ecoinvent 3.10 cut-off for the before and after rows, measured before the move to 3.12; the 3.12 row is below.

Script: `import_timing.py`. Raw output: [bench/results/import_timing_results.json](../../bench/results/import_timing_results.json).

**Scope:** one Apple Silicon Mac (arm64), 10 CPUs, macOS 25.6. Python 3.14.3, PyArrow 26.0.0, lxml 6.1.3, 11 October 2026. ecoinvent 3.10 cut-off, the empty file excluded. "Before" is the code of that day, records from end to end. "After" sends Arrow tables from the parsing workers to `build_tables`, and freezing makes the new checks. The two ran in turns, three runs each, from two checkouts. The machine was not quiet: other work kept the load average between 3.5 and 5.8, so the best run of each is shown and the times are rough.

| Step | Before | After |
|---|---|---|
| Read: parse 23,522 files in 10 worker processes, check, link | 13.7 s | 11.5 s |
| Freeze: check, hash and write the release | 12.3 s | 4.1 s |
| Total | 26.0 s | 15.6 s |
| Release | `ecoinvent-3.10-cutoff@74d864daab6b` | `ecoinvent-3.10-cutoff@07ebaa9e28bc` |

**On ecoinvent 3.12** (`import_timing.py`, 25 October 2026, [raw output](../../bench/results/import_timing_3.12_results.json)): reading 26,533 files in 10 worker processes took 27.3 s and freezing 6.4 s, 33.7 s in all, the best of two runs, at a load average of 4.8 before it. The second run took 37.3 s at a load of 10.6. Release `ecoinvent-3.12-cutoff@b9428cdd8045`, no file excluded. **Not a quiet run.**

The release hash changes because the format changes: a `geographies` table (541 rows), `resolved_at` with no time zone, and the format `headwater-release/2`. The rows of the other tables are the same: the exchanges' digest did not move on the toy.

Where the time goes after the change. The read row is from one profiled run, slower than the best above; the freeze rows time each check once, on the tables of that run.

| Part | Time |
|---|---|
| Read: the main process waiting for the workers, which parse XML | 11.1 s of 16.6 s |
| Freeze: ids defined twice | 0.1 s |
| Freeze: every row a valid record (743,399 lines, 4.5 million properties) | 0.9 s |
| Freeze: lines against flows, suppliers, units and issues | 0.9 s |
| Freeze: sorting and digests of the exchanges | 0.7 s |

Before the change, building the exchanges table from 743,399 records took 7.7 s of the freeze, under the profiler. Reading is now bound by parsing XML in the workers, about 2.6 ms per file in one process (1,961 files, one in twelve).

## Import of ecoSpold1

Script: `import_ecospold1.py`. Raw output: [bench/results/import_ecospold1_results.json](../../bench/results/import_ecospold1_results.json).

**Scope:** one Apple Silicon Mac (arm64), 10 CPUs, macOS 25.6. Python 3.14.3, PyArrow 26.0.0, lxml 6.1.3, 25 October 2026. BAFU:2026 as ecoSpold1, version 1, from a local licensed copy: 11,947 files, one process each. Three runs, one after the other. The machine was not quiet: other work kept the load average between 8.0 and 13.7 before the runs, so the best run is shown and the times are rough.

| Step | Time |
|---|---|
| Read: parse 11,947 files in 10 worker processes, assemble the tables | 13.3 s |
| Stages: unit check, allocation, link by name | 1.1 s |
| Total | 14.4 s |

The read gave 11,947 processes, 420,063 lines and 10,732 flows. 114,369 lines buy from a supplier, and 114,332 of them linked by name. 37 are open: 27 name no process of the package at their location (`no-supplier`) and 10 give no location for a product that 81 processes make (`ambiguous-supplier`).

## BAFU:2026 against its own LCIA results

Script: `bafu_gwp_check.py`. Raw output: [bench/results/bafu_gwp_check_results.json](../../bench/results/bafu_gwp_check_results.json).

**Scope:** the machine and software of [Import of ecoSpold1](#import-of-ecospold1), 25 October 2026. The ecoSpold1 files of BAFU:2026 version 1 and the workbook of BAFU's own LCIA results (same version), both from local licensed copies.

**Setup.** Climate change only, IPCC 2021 GWP 100, with the AR6 values of the 30 elementary flows to air of the package that have one (carbon dioxide, methane, dinitrogen monoxide, SF6, NF3 and the main HFCs, CFCs, HCFCs and halons) as data of the script. Biogenic CO2 counts 0. The 37 lines that name no single supplier stay open in the import, so the script resolves them as data gaps. BAFU rows match a process by name and location: 11,836 of 11,947 do. BAFU lists some processes per another unit than the file (MJ for kWh, m for km, m2 for ha), so the scores are compared after converting. 11,661 of the rows have a non-zero BAFU score.

| Rows | Number | Median relative difference | 90th percentile | Above 10⁻² | Above 10⁻¹ |
|---|---|---|---|---|---|
| All | 11,661 | 6.7 × 10⁻⁴ | 3.3 × 10⁻³ | 211 | 29 |
| With, or downstream of, a line left as a data gap | 11,540 | 6.8 × 10⁻⁴ | 3.3 × 10⁻³ | 193 | 26 |
| Not touched by a gap | 121 | 3.6 × 10⁻⁵ | 1.9 × 10⁻² | 18 | 3 |

Most processes are downstream of a gap, because a few of the open lines are electricity mixes that many processes buy. The differences that remain are most likely gases that have no factor here.

**`Nm3` read as `m3`.** The package writes some flows in both. 40 lines are written in m3 and buy a product that its maker writes in Nm3: they link only because the reader reads the two as one unit. They are in 29 processes with a non-zero BAFU score, and through them comes a median 8.9 % of the process's score. The table gives how far those 29 scores are from BAFU's.

| Reading of the unit | Median relative difference | 90th percentile | Above 10⁻² |
|---|---|---|---|
| 1 m3 = 1 Nm3 | 8.7 × 10⁻⁵ | 2.5 × 10⁻⁴ | 0 of 29 |
| 1 m3 = 0.9317 Nm3 (0 °C against 20 °C) | 6.1 × 10⁻³ | 4.6 × 10⁻² | 12 of 29 |

## Diff attribution

Script: `diff_cost.py`. Raw output: [bench/results/diff_cost_results.json](../../bench/results/diff_cost_results.json).

**Scope:** one Apple Silicon Mac (arm64), 10 CPUs, macOS 25.6. Python 3.14.3, NumPy 2.5.3, SciPy 1.18.1, DuckDB 1.5.6, PyArrow 26.0.0, 12 October 2026. A synthetic release of ecoinvent size (no licensed data): 23,500 processes, 4,000 elementary flows, 619,097 lines, and a method of 25 indicators with 3,500 factors each, the size of EF 3.1 with its sub-indicators. The study has 5 processes and 105 lines (a product that buys 4 parts and 16 background products, and 4 parts that buy 20 each). Best of 3 runs, except `Study.calc` and `Study.scores` (best of 5). The machine was not quiet: the load average was 5.84 before the run and 6.53 after, from other work, so the times are rough and a quiet machine is faster.

The diff calculates the old and the new study in full for each indicator (`Study.calc`, 2 × 25 calculations). Then each difference is made alone to the old study and scored for all indicators at once (`Study.scores`): the study is checked, assembled and factorized once, and each indicator takes one small solve from the stored scores of the background, so nothing is solved against the 23,500-process matrix. Each effect equals what `Study.calc` gives for that difference alone: on 10 changed lines and 25 indicators the largest gap, relative to the old score, was 0: the numbers are identical.

| Step | Time |
|---|---|
| `hw.load` of the release for the 25-indicator method (assembly, factorization, solves), paid once per diff | 7.5 s |
| `Study.calc` of the study, one indicator | 7.4 ms |
| `Study.scores` of the study, all 25 indicators | 3.3 ms |
| `diff_studies`, 1 line changed, 25 indicators (50 calculations, 1 scoring) | 0.30 s |
| `diff_studies`, 10 lines changed, 25 indicators (50 calculations, 10 scorings) | 0.30 s |
| `diff_studies`, 100 lines changed, 25 indicators (50 calculations, 100 scorings) | 0.59 s |

A scoring costs about 3 ms, so each changed line adds about 3 ms, and the old and the new study in full take about 0.3 s. Loading the background is the larger cost, and a CI job pays it once for each study. The release size hardly matters for the diff: on a synthetic release of 2,000 processes (load average 5.93, [raw output](../../bench/results/diff_cost_2000_results.json)), the load took 0.4 s and the diff of 100 lines 0.48 s. The limit that the M2 plan set, 30 s for 100 changed lines, is met with a wide margin.

## Diff of every product

Script: `diff_all_products.py`. Raw output: [bench/results/diff_all_products_results.json](../../bench/results/diff_all_products_results.json), counts and times only.

**Scope:** one Apple Silicon Mac (arm64), 10 CPUs, macOS 25.6, BLAS limited to 2 threads. Python 3.14.3, NumPy 2.5.3, SciPy 1.18.1, 2 November 2026. Median of 3 runs. The machine was not quiet: the load average was 3.27 before the run and 4.36 after, from other work. The database is the 3.12 pilot's, on ecoinvent 3.12 cut-off with EF 3.1 (licensed data, so no values here): 40 processes, 40 products, 759 lines, 25 indicators. A branch changes two lines, one in the process of the reference flow and one in another process, so 2 products moved.

`headwater diff` scores the reference flow alone, or every product. The second adds one scoring of the old and of the new study for each product, and each line that differs made alone to the old study for each product: for 40 products and 2 differences, 160 scorings of all 25 indicators at once (`Study.scores`), not 40 calculations of each indicator.

| Step | Reference flow only | Every product | Times as long |
|---|---|---|---|
| `headwater diff --json`, a new process each time, release loaded | 8.18 s (8.16 to 8.61) | 9.32 s (9.24 to 9.69) | 1.14 |
| `diff_studies` alone, release already loaded | 1.11 s (1.04 to 1.25) | 2.22 s (2.15 to 2.24) | 2.01 |
| Scores in the result | 25 | 1,000 | |
| JSON printed by the command | 36,883 bytes | 869,050 bytes | |

Loading the release for the method takes 6.24 s and is paid once by either. The limit of this task, every product at most 3 times as long as the reference flow alone, is met by both lines. The text of the diff of every product is 118 lines and 11,176 bytes: the 2 products that moved, a line for the other 38.

## Excel import

**Version:** ecoinvent 3.10 cut-off, measured before the move to 3.12.

Scripts: `excel_parity.py` (`prepare`, then `compare`) and `bw_excel_reference.py` (the Brightway side). Raw output: [bench/results/excel_parity_results.json](../../bench/results/excel_parity_results.json).

**Scope:** one Apple Silicon Mac (arm64), macOS 25.6, 12 October 2026. headwater on Python 3.14.3. Brightway on Python 3.12.11, bw2data 4.7, bw2calc 2.5.0, bw2io 0.9.17, in a copy of the project of the parity check. ecoinvent 3.10 cut-off, release `ecoinvent-3.10-cutoff@07ebaa9e28bc`, and the 3-flow climate method. The workbook is a real Brightway inventory, kept private and out of this repository: 40 activities, 759 exchange rows, no formulas or parameters, written for ecoinvent 3.12. 3.12 is not on this machine, so the lines that were renamed between the two versions do not link.

| Check | Result |
|---|---|
| The workbook as written, linked to 3.10 | 675 of 759 rows mapped, 84 not, 0 formulas not copied. All 84 are `not-in-release`, from 2 different lines |
| The same, with a mapping file of those 2 lines | 759 mapped, 0 not |
| Brightway's ExcelImporter on the workbook with those two decisions written into its rows | 759 edges (655 technosphere, 64 biosphere, 40 production), none unlinked: the same rows |
| Score of each of the 40 activities, bw2calc's against headwater's calc of the converted study | at most 9.1 × 10⁻⁸ relative; all 40 within 10⁻⁶ |

Brightway ignores an elementary flow that its method has no factor for, and headwater refuses a study that emits one. The workbook has 5 such flows for the 3-flow method (water, and flows that measure water quality). For the comparison both sides use the method with a zero factor for each. Scores against the numbers the workbook's authors published (ecoinvent 3.12, EF 3.1) are the work of T8, because the versions and methods differ.

## Carbon indicators on ecoinvent 3.12

**Version:** ecoinvent 3.12 cut-off.

**Scope:** one Apple Silicon Mac (arm64), 10 CPUs, macOS 26.6 (Darwin 25.6). Python 3.14.3, NumPy 2.5.3, SciPy 1.18.1, PyArrow 26.0.0, lxml 6.1.3, openpyxl 3.1.5, 12 October 2026. Release `ecoinvent-3.12-cutoff@b9428cdd8045`, ecoinvent's `LCIA Implementation 3.12.xlsx` and its `Cut-off Cumulative LCIA v3.12.xlsx`. EF v3.1 is imported whole. The carbon categories are imported with `--category`: three of `IPCC 2021 (incl. biogenic CO2)` and one of `IPCC 2021`, loaded together with EF v3.1. Counts and differences only, no times. Other work kept the load average between 4 and 5, which does not change these numbers.

Scripts: [tests/integration/test_carbon_indicators.py](../../tests/integration/test_carbon_indicators.py), run with `ECOINVENT_LCIA`, `ECOINVENT_RELEASE` and `ECOINVENT_CUMULATIVE_LCIA` set, and [bench/carbon_cancellation.py](../../bench/carbon_cancellation.py). Raw output of the second: [bench/results/carbon_cancellation_results.json](../../bench/results/carbon_cancellation_results.json).

| Import | Factors | Mapped | Elementary flows with no factor |
|---|---|---|---|
| EF v3.1, 25 indicators | 27,415 | all | 1,890 of 9,850 |
| `climate change: biogenic removals (incl. CO2)` | 2 | all | |
| `climate change: biogenic emissions (incl. CO2)` | 8 | all | |
| `climate change: biogenic (incl. CO2)`, the sum of the two | 10 | all | |
| `climate change: direct land use change` | 12 | all | |

Each product's score against ecoinvent's cumulative LCIA, relative to the larger of the two, on all 26,533 products. A product with nothing upstream that emits or takes a flow of the indicator is an exact zero in headwater, set from the structure of A; ecoinvent's file holds rounding noise there, so the difference is compared as an absolute number.

| Indicator | Largest relative difference | Above 10⁻⁹ | Exact zeros in headwater | Largest absolute difference at an exact zero |
|---|---|---|---|---|
| Biogenic removals | 2.6 × 10⁻¹² | 0 | 2,136 | 1.7 × 10⁻¹⁹ |
| Biogenic emissions | 5.3 × 10⁻¹¹ | 0 | 2,081 | 2.0 × 10⁻¹⁹ |
| Their sum | 1.7 × 10⁻⁷ | 6 | 2,062 | 2.8 × 10⁻²⁰ |
| Direct land-use change | 2.3 × 10⁻¹¹ | 0 | 1,910 | 1.5 × 10⁻¹⁹ |

The 6 products of the sum above 10⁻⁹ all cancel: each score is 1.8 × 10⁻⁵ of its gross, the sum of the sizes of its positive and negative terms, and each difference is at most 3.1 × 10⁻¹² of the gross. That is rounding in a sum whose parts cancel, as [calculation.md](../calculation.md#factorize) expects.

Products whose score cancels: below 10⁻³ of its gross, from a forward solve per product. Indicators with none are left out.

| Indicator | Products flagged | Below 10⁻² of the gross |
|---|---|---|
| Biogenic sum, `climate change: biogenic (incl. CO2)` | 183 | 1,460 |
| EF v3.1 land use | 281 | 372 |
| EF v3.1 human toxicity, carcinogenic | 2 | 12 |
| Biogenic removals, biogenic emissions, direct land-use change, EF v3.1 climate change | 0 | 2, 0, 1, 5 |

## Top contributors against Brightway

**Version:** ecoinvent 3.12 cut-off.

Scripts: `where_parity.py` (`prepare`, then `compare`) and `bw_where_reference.py` (the Brightway side). Raw output: [bench/results/where_parity_results.json](../../bench/results/where_parity_results.json).

**Scope:** one Apple Silicon Mac (arm64), macOS 25.6, 12 October 2026. headwater on Python 3.14.3, NumPy 2.5.3, SciPy 1.18.1. Brightway on Python 3.12.11, bw2data 4.5.4, bw2calc 2.3, bw2io 0.9.14, bw2analyzer 0.11.8, NumPy 1.26.4, SciPy 1.17.0, in a copy of the 3.12 pilot's project, with the EF v3.1 methods bw2io imported from the 3.12 workbook. ecoinvent 3.12 cut-off, release `ecoinvent-3.12-cutoff@b9428cdd8045`, and EF 3.1 as in `ecoinvent-3.12-cutoff.EF-3.1@21c61d3ac200`. 10 products, one market per sector, the municipal solid waste market among them (the list is in the script), and 2 indicators: climate change, and land use, which has negative factors. Three other tasks ran on the machine at the same time.

| Check | Result |
|---|---|
| Products and indicators compared | 10 × 2 = 20 |
| headwater's top 10 processes (`bg.where`) against bw2analyzer's `top_processes` on bw2calc's solve | the same 10 in 20 of 20 |
| In the same order | 20 of 20; 0 swaps of near ties |
| Values within 10⁻⁶ relative | 200 of 200, at most 4.4 × 10⁻⁷; 3 of them negative |
| The same against an accurate solve of Brightway's own matrices | the same 10 and the same order in 20 of 20, 200 of 200 values within 10⁻⁶, at most 4.4 × 10⁻⁷ |
| Backward error of each forward solve, row by row; a row below 10⁻¹² of the largest is measured against 10⁻¹² of the largest | bw2calc: at most 1.8 × 10⁻³. The accurate solve: at most 7.4 × 10⁻¹² |
| `bg.where` on one product, top 12, with other work on the machine (load average 6.5). The first call also sorts the locations of the background | median 8.4 ms, at most 24 ms |

The values differ by the same amount from both of Brightway's solves, so the difference comes from its float32 storage, not from its solve (D21).

## Monte Carlo on ecoinvent 3.12

**Version:** ecoinvent 3.12 cut-off.

Scripts: `monte_carlo.py` (`time`, `prepare`, then `compare`) and `bw_monte_carlo.py` (the Brightway side). Raw output: [bench/results/monte_carlo_time_ecoinvent-3.12-cutoff_results.json](../../bench/results/monte_carlo_time_ecoinvent-3.12-cutoff_results.json), [monte_carlo_prepare_ecoinvent-3.12-cutoff_results.json](../../bench/results/monte_carlo_prepare_ecoinvent-3.12-cutoff_results.json) and [monte_carlo_parity_ecoinvent-3.12-cutoff_results.json](../../bench/results/monte_carlo_parity_ecoinvent-3.12-cutoff_results.json).

**Scope:** one Apple Silicon Mac (arm64, 10 cores), macOS 25.6, 30 October 2026, 2 BLAS threads. headwater on Python 3.14.3, NumPy 2.5.3, SciPy 1.18.1. Brightway on Python 3.12.11, bw2data 4.5.4, bw2calc 2.3, NumPy 1.26.4, SciPy 1.17.0, in a copy of the 3.12 pilot's project with the EF v3.1 methods bw2io imported from the 3.12 workbook. Release `ecoinvent-3.12-cutoff@b9428cdd8045` and EF 3.1, 25 indicators, as in `ecoinvent-3.12-cutoff.EF-3.1@21c61d3ac200`. The 10 products of [Top contributors against Brightway](#top-contributors-against-brightway). **Not a quiet run:** other sessions worked on the machine, load average 1.3 to 1.4 for `time` and 2.2 to 2.5 for `prepare`.

| Measure | Result |
|---|---|
| Lines with a distribution | 270,167 in A, 347,143 in B: 610,544 lognormal, 5,949 normal, 816 triangular, 1 uniform |
| One iteration (`time`, median of 20): draw, rebuild A and B, factorize, 25 solves | 3.65 s: 0.023, 0.023, 3.34 and 0.25 s |
| Reading the distributions from the release, once per call | 1.2 s |
| 200 iterations of 10 products (`prepare`) | 749 s, 3.74 s per iteration |
| Condition estimate of 200 iterations, limit 10¹⁰ | median 32; 9 above 10³, 1 above 10⁵ (largest 4.3 × 10⁵), none above 10⁷. The release itself: 30 |
| Iterations that needed pivoting | 0 |
| Draws of the opposite sign of their line's amount | 438,571 in 200 iterations, 2,193 per iteration; the normal distributions of the release predict 2,190 (2,150 of them emissions) |
| An own use drawn above its reference, in 1,000 draws | 0 |
| EF v3.1 factors with a distribution in Brightway | 0 |
| Scores with no draw, headwater against Brightway's matrices | within 6.7 × 10⁻⁷ relative (float32 storage, D21) |
| Brightway, one draw solved with a scaled SuperLU, 25 indicators | median 3.59 s; largest residual 9.8 × 10⁻¹³, no pivoting |
| Mean, 2.5 and 97.5 percentiles, 10 products × 25 indicators, within 3 bootstrap standard errors (1,000 resamples of each side) | 746 of 750; up to 7 (1%) may fail by chance |
| Largest difference, in standard errors | mean 2.64, 2.5% 3.12, median 2.98, 97.5% 3.23 |

The 4 checks outside 3 standard errors are all 2.5% or 97.5% percentiles, of toxicity and eutrophication indicators, at 3.02 to 3.23 standard errors; the medians of the same items are at 0.43 to 2.62. With 200 draws a tail percentile rests on about 5 of them, and the bootstrap underestimates its error for heavy tails.

## Monte Carlo of a study on ecoinvent 3.12

**Version:** ecoinvent 3.12 cut-off.

Script: `monte_carlo_study.py` (`build`, `run`, then `check`). Raw output: [bench/results/monte_carlo_study_ecoinvent-3.12-cutoff_results.json](../../bench/results/monte_carlo_study_ecoinvent-3.12-cutoff_results.json). `check` prints its numbers; the results file holds timings and counts.

**Scope:** one Apple Silicon Mac (arm64, 10 cores), macOS 25.6, 1 November 2026, 2 BLAS threads, Python 3.14.3, NumPy 2.5.3, SciPy 1.18.1. Release `ecoinvent-3.12-cutoff@b9428cdd8045` and EF 3.1, 25 indicators. The study is a copy of the 3.12 pilot (40 processes) with every line but the references lognormal with a GSD of 1.1, an override of one emission of a supplier of its reference process (at 0.8 times its amount), and a scenario that sets one purchase of that supplier at 0.9 times, for everyone, which changes a column of A. **Not a quiet run:** macOS indexing new files, load average 4.0 to 5.5 before and 3.4 to 4.0 after.

| Measure | Result |
|---|---|
| Lines drawn | 617,310 of the release, as in [Monte Carlo on ecoinvent 3.12](#monte-carlo-on-ecoinvent-312); 719 of the study, by their own keys |
| 200 iterations of the study and of the study in the scenario (`run`) | 783 s; 3.90 s per iteration, setup left out |
| The background alone, in the same run | 3.71 s per iteration |
| Iterations that needed pivoting; largest residual | 0; 9.9 × 10⁻¹³ |
| Draws of the opposite sign; largest residual; largest condition | 438,571; 9.9 × 10⁻¹³; 4.29 × 10⁵: each the same as the 200 iterations of seed 0 of [Monte Carlo on ecoinvent 3.12](#monte-carlo-on-ecoinvent-312), drawn before study lines existed. The study's lognormals keep their sign, and its draws leave the background's as they were |
| Score with no draw against `calc` (`check`) | 1.9 × 10⁻¹⁶ relative |

The override and the scenario were picked by rule, the first supplier with both an emission and a purchase. The ranges of this run are not published: they come from the pilot's data. 3.90 s against 3.71 s per iteration fits the scenario being solved by an update of each iteration's factors, but this run did not record it: `scenario_refactorized` is in the output from the next run. The background alone ran last, when the load had dropped.

## Scenario crossover

**Version:** ecoinvent 3.10 cut-off (23,522 processes), measured before the move to 3.12. 3.12 has 26,533 processes and is not yet measured: `CROSSOVER` is the 3.10 value.

Script: `scenario_crossover.py`. Raw output: [bench/results/scenario_crossover_results.json](../../bench/results/scenario_crossover_results.json).

**Scope:** one Apple Silicon Mac (arm64, 10 cores), macOS 25.6, 12 October 2026. Python 3.14.3, NumPy 2.5.3 (Accelerate), SciPy 1.18.1. **Not a quiet run:** the load average was 4.05 when the script started, between 3.97 and 5.58 around each row, and 4.64 at the end, from other work on the machine. Both paths run one after the other for each row, under the same load. Each time is the best of 3 runs.

**Data:** release `ecoinvent-3.10-cutoff@07ebaa9e28bc` and the 3-flow climate method: 23,522 processes and 291,074 non-zeros in A. A scenario changes k random columns of A: each purchase of those processes is multiplied by 1.1. Two different sets of k columns for each k.

**What is timed:** the whole way to the scores of every product for one indicator and the supply of one demand. Update: `low_rank_update` (k transposed solves, its checks and the condition estimate), the scores from the stored ones, the supply (two solves). Refactorize: `refactorize` (a factorization with its checks and condition estimate), the scores, the supply (one solve). The stored factorization of the release took 3.0 s, and one transposed solve 8.5 ms.

| Changed columns | Update, set 1 | Update, set 2 | Refactorize, set 1 | Refactorize, set 2 |
|---|---|---|---|---|
| 1 | 0.050 s | 0.050 s | 2.81 s | 2.80 s |
| 10 | 0.102 s | 0.088 s | 2.80 s | 2.84 s |
| 30 | 0.190 s | 0.185 s | 2.80 s | 2.78 s |
| 100 | 0.502 s | 0.495 s | 2.81 s | 2.81 s |
| 120 | 0.605 s | 0.593 s | 2.81 s | 2.79 s |
| 150 | refused after 0.67 s | 0.735 s | 2.81 s | 2.80 s |
| 175 | refused after 0.79 s | refused after 0.78 s | 2.80 s | 2.81 s |
| 200 | refused after 0.91 s | refused after 1.01 s | 2.82 s | 2.89 s |
| 300 | refused after 1.41 s | refused after 1.36 s | 2.87 s | 2.85 s |
| 400 | refused after 1.80 s | refused after 2.01 s | 2.80 s | 2.82 s |
| 500 | refused after 2.28 s | refused after 2.31 s | 2.80 s | 2.95 s |
| 700 | refused after 3.50 s | refused after 3.38 s | 2.90 s | 2.78 s |
| 1,000 | refused after 4.82 s | refused after 5.08 s | 2.76 s | 2.81 s |

| What | Time |
|---|---|
| Update, 100 columns: build / scores / supply | 0.48 s / 12 ms / 7 ms |
| Refactorize, any k: factorization / scores / supply | 2.80 s / 9 ms / 3.5 ms |

- The update costs about 5 ms per changed column, and refactorizing a flat 2.8 s.
- The update **refuses the edit** with `SingularMatrixError` ("the changed processes make the matrix singular") from 150 columns, in one of the two sets, and in both from 175. Refactorizing the same edits passes all its checks in every row. The refusal comes before the time runs out: the update is still 4.6 times faster at 120 columns. A likely cause is the scale of the update's capacitance matrix on a release whose units span many orders of magnitude; this was not measured by the script.
- A refused update costs the time until it refuses plus a refactorization. The attempt alone takes 2.3 s at 500 columns, 3.4 to 3.5 s at 700 and 4.8 to 5.1 s at 1,000, more than the 2.8 s of refactorizing at 700 and 1,000.
- The time at which a working update would stop being faster was not reached, so it is not measured.

**Result:** `CROSSOVER` in `calc/update.py` is 120: the largest k, among those measured, where the update worked and was faster in both sets. Up to it, `calc` updates. Above it, and when an update refuses an edit, `calc` factorizes the edited A again and records why in `provenance.update_route`. The constant is the limit of the update on this release for this kind of edit, not the point where the times cross. Another kind of edit can be refused earlier, and the fallback to refactorizing then applies at any k.

## Finding a process

**Version:** ecoinvent 3.12 cut-off.

Script: `find_timing.py`. Raw output: [bench/results/find_timing_results.json](../../bench/results/find_timing_results.json).

**Scope:** one Apple Silicon Mac (arm64, 10 cores), macOS 25.6, 24 October 2026. Python 3.14.3, NumPy 2.5.3, SciPy 1.18.1, PyArrow 26.0.0. **Not a quiet run:** the load average (1 minute) was 4.47 when the script started and 4.11 when it ended, from other work on the machine. Release `ecoinvent-3.12-cutoff@b9428cdd8045`: 26,533 processes. Each query is timed warm, 20 runs, as the median and the largest. The table is timed on its first call and as the best of 5. The commands are fresh processes: 5 runs of `find`, 3 of `--help`. The queries use names of generic markets and the word `steel`.

| What | Time |
|---|---|
| `headwater find`, an exact name with its place, in a fresh process | best 2.40 s, worst 2.47 s |
| `headwater --help` in a fresh process, which does no work | best 2.54 s, worst 2.57 s |
| `import headwater.cli` (`python -X importtime`), of which `pyarrow.dataset`, of which `pandas` | 2.22 s, 0.91 s, 0.87 s |
| Search table from the release (`table_of_release`), first call / best of 5 | 48 ms / 44 ms |
| `bg.find` on a loaded background, first call (builds the table from its records) / best of 5 after it | 45 ms / 8.2 ms |
| `hw.load` of the same release with EF 3.1, which `find` does not need | 5.5 s |
| Query, an exact name with its place (2 found) | 3.9 ms / 8.3 ms |
| Query, an exact name in every place (2 found) | 10.9 ms / 13.7 ms |
| Query, the words of a name in any order (2 found) | 8.4 ms / 11.5 ms |
| Query, a word that many names start with, `limit` 20 | 8.5 ms / 9.1 ms |
| Query, a product word with a product filter and a place, `limit` 20 | 2.0 ms / 2.5 ms |
| Query, no match | 9.8 ms / 10.3 ms |
| Query, one letter that almost every name has, no limit (25,722 found, all of them built and sorted) | 70 ms / 86 ms |

- The search is a scan of the rows with plain string tests. There is no index, so nothing is stored, rebuilt or stale.
- The time of the command is the start-up of the package, not the search: `headwater find` takes as long as `headwater --help`. Of the 2.22 s of import time, `pyarrow.dataset` takes 0.91 s, and the import of pandas 0.87 s of that. pandas is in this environment through the `notebooks` dependency group. This run is from before the start-up change in [Command start-up](#command-start-up), which has the times without pandas.

## Processes read from a release

**Version:** ecoinvent 3.12 cut-off.

Script: `processes_read.py`. Raw output: [before](../../bench/results/processes_read_before_results.json), [after](../../bench/results/processes_read_after_results.json) and [the layout of the exchanges table](../../bench/results/processes_read_layout_results.json), all in [bench/results/](../../bench/results/).

**Scope:** one Apple Silicon Mac (Apple M2 Pro, 10 cores), macOS 25.6, 1 November 2026. Python 3.14.3, NumPy 2.5.3, SciPy 1.18.1, PyArrow 26.0.0. **Not a quiet run:** the load average (1 minute) was 2.01 when the before run started and 2.66 when it ended, then 2.40 and 2.51 for the after run, from other work on the machine; the two runs were made one after the other. Release `ecoinvent-3.12-cutoff@b9428cdd8045`: 26,533 processes, 892,943 exchanges, 14,249 flows. The method (EF 3.1) is only there to load the background, which took 5.4 s and is not timed. 300 processes chosen at random (seed 0): 50 are read by one `bg.processes([id])` call each, every id new to the background, and 250 by five calls of 50 ids. Times are the median of the calls, with the largest in brackets. The split by table wraps `pyarrow.dataset.dataset`: it counts what the code opens and scans, and the rest of a call is its own time. Before is the code just before the change, after is the change, both run with the same script.

| What | Before | After |
|---|---|---|
| `bg.processes([id])`, a new id, 50 calls | 44.6 ms (54.3) | 42.5 ms (48.9) |
| of which scan of `exchanges` (8 lines found) | 37.3 ms | 36.8 ms |
| of which scan of `processes` (1 row) | 4.3 ms | 4.4 ms |
| of which scan of `flows` (6.5 rows) | 1.6 ms | not read |
| of which opening each dataset | 0.1 to 0.3 ms | 0.2 to 0.3 ms |
| 50 calls, one new id each, together | 2.19 s | 2.08 s |
| `bg.processes(ids)`, 50 new ids in one call, 5 calls | 236 ms (246), 4.7 ms per id | 231 ms (234), 4.6 ms per id |
| of which scan of `exchanges` / `flows` / `processes` | 192 / 1.9 / 4.5 ms | 196 / not read / 4.8 ms |
| A call with an id already read, 50 calls | 0.001 ms | 0.001 ms |

- The cost of a call with new ids is the scan of `exchanges`: 84% of it before and 87% after. The table is sorted by process id in 7 row groups of up to 131,072 rows, with ranges that do not overlap, so one id decodes one whole row group, 98.6 MB in memory for the first (`properties` 34.7 MB, `comment` 21.5 MB), to return about 8 lines. A call with 50 ids reads the 7 groups once. That is about 4.6 ms per id, about nine times faster per id than a call per id. The layout is in [processes_read_layout_results.json](../../bench/results/processes_read_layout_results.json).
- The change takes the flow names from the flows the background already holds, so the 1.6 ms read of the `flows` table is gone: 4% of a call. That is inside the difference between runs on a machine that was not quiet, so single calls are not measurably faster. What a loop over `bg.processes([id])` saves is by calling it once with every id.
- Memory cost of the change: none of its own. The mapping `Internals._flows` has one entry per flow of the release, 14,249 entries and a 0.4 MB dict, over the `Flow` records that the background holds already. `study_catalog` builds it too, so it is built once for both.
- Not done: keeping the decoded row groups. They are 79 to 99 MB each, 648 MB for the 7, for ids that fall in a group already read.

## Plain and scaled solves against ecoinvent's scores

**Version:** ecoinvent 3.12 cut-off.

Script: `plain_vs_scaled.py`; `tests/integration/test_method_import.py::test_scores_match_ecoinvent_cumulative_lcia` makes the same comparison for headwater, and also requires each product above 10⁻⁶ to cancel (below 10⁻³ of its gross). Raw output: [bench/results/plain_vs_scaled_results.json](../../bench/results/plain_vs_scaled_results.json).

**Scope:** one Apple Silicon Mac (arm64), macOS 25.6, 24 October 2026. Python 3.14.3, NumPy 2.5.3, SciPy 1.18.1. **Not a quiet run:** the load average was 5.54 at the start and 8.20 at the end, from other work on the machine, which included a run of the licensed test. Counts and errors do not depend on it; the factorization times do. Release `ecoinvent-3.12-cutoff@b9428cdd8045`, EF v3.1 from `LCIA Implementation 3.12.xlsx`, and ecoinvent's `Cut-off Cumulative LCIA v3.12.xlsx`: 26,533 products, 25 indicators, 335,769 non-zeros in A. Counts and differences only.

Three solves of Aᵀ x = h give the score of every product, for each indicator:

- **headwater:** rows and columns scaled, no pivoting, exact zeros from the structure of A, one refinement (`hw.load`).
- **Plain, LU of Aᵀ:** `splu(Aᵀ)` with SciPy's defaults (COLAMD, partial pivoting), no scaling, no refinement.
- **Plain, LU of A:** `splu(A)` with the same defaults, solved with `trans="T"`: the factors of A as bw2calc's factorized solver has them, used for Aᵀ.

Each is compared with ecoinvent's score, relative to the larger of the two. A product that is an exact zero by structure in headwater (1,645 to 2,155 per indicator) is left out of that comparison, since ecoinvent's file holds rounding noise there; the largest absolute value each solve gives at those products is reported below. A product above 10⁻⁶ is also compared with its gross, the sum of the sizes of its positive and negative terms, from an accurate forward solve. Each cell is the largest relative difference, then the number of products above 10⁻⁶.

| Indicator | Exact zeros | headwater | Plain, LU of Aᵀ | Plain, LU of A |
|---|---|---|---|---|
| `acidification` | 2,018 | 9.6 × 10⁻¹², 0 | 6.4 × 10⁻⁷, 0 | 1.3 × 10⁻⁴, 194 |
| `climate-change` | 1,779 | 3.1 × 10⁻¹², 0 | 9.5 × 10⁻⁷, 0 | 2.8 × 10⁻⁴, 127 |
| `climate-change-biogenic` | 2,081 | 1.9 × 10⁻¹², 0 | 1.3 × 10⁻⁵, 7 | 1.8 × 10⁻³, 686 |
| `climate-change-fossil` | 2,039 | 3.1 × 10⁻¹², 0 | 6.6 × 10⁻⁷, 0 | 2.3 × 10⁻⁴, 154 |
| `climate-change-land-use-and-land-use-change` | 1,910 | 2.3 × 10⁻¹¹, 0 | 1.2 × 10⁻⁵, 1 | 1.4 × 10⁻³, 660 |
| `ecotoxicity-freshwater` | 1,889 | 1.3 × 10⁻¹¹, 0 | 5.1 × 10⁻⁷, 0 | 3.3 × 10⁻⁴, 232 |
| `ecotoxicity-freshwater-inorganics` | 1,891 | 1.3 × 10⁻¹¹, 0 | 2.1 × 10⁻⁶, 5 | 1.6 × 10⁻⁴, 301 |
| `ecotoxicity-freshwater-organics` | 2,003 | 6.9 × 10⁻¹², 0 | 4.9 × 10⁻⁷, 0 | 2.5 × 10⁻⁴, 329 |
| `energy-resources-non-renewable` | 2,147 | 2.7 × 10⁻¹⁰, 0 | 2.9 × 10⁻⁶, 1 | 1.8 × 10⁻⁴, 336 |
| `eutrophication-freshwater` | 1,976 | 1.2 × 10⁻¹², 0 | 8.3 × 10⁻⁶, 7 | 3.3 × 10⁻⁴, 381 |
| `eutrophication-marine` | 1,919 | 1.1 × 10⁻¹², 0 | 5.5 × 10⁻⁷, 0 | 1.9 × 10⁻⁴, 70 |
| `eutrophication-terrestrial` | 2,015 | 8.3 × 10⁻¹², 0 | 8.0 × 10⁻⁷, 0 | 1.2 × 10⁻⁴, 155 |
| `human-toxicity-carcinogenic` | 1,894 | 2.6 × 10⁻¹¹, 0 | 2.0 × 10⁻⁶, 1 | 2.0 × 10⁻⁴, 340 |
| `human-toxicity-carcinogenic-inorganics` | 1,899 | 1.4 × 10⁻¹¹, 0 | 1.9 × 10⁻⁵, 12 | 4.2 × 10⁻⁴, 468 |
| `human-toxicity-carcinogenic-organics` | 2,099 | 1.4 × 10⁻¹¹, 0 | 1.5 × 10⁻⁶, 1 | 4.3 × 10⁻⁴, 388 |
| `human-toxicity-non-carcinogenic` | 1,887 | 3.8 × 10⁻¹¹, 0 | 1.1 × 10⁻⁵, 6 | 2.8 × 10⁻⁴, 472 |
| `human-toxicity-non-carcinogenic-inorganics` | 1,893 | 3.8 × 10⁻¹¹, 0 | 9.6 × 10⁻⁷, 0 | 3.8 × 10⁻⁴, 523 |
| `human-toxicity-non-carcinogenic-organics` | 2,001 | 4.3 × 10⁻¹¹, 0 | 5.5 × 10⁻⁶, 3 | 4.0 × 10⁻⁴, 694 |
| `ionising-radiation-human-health` | 2,142 | 1.8 × 10⁻⁹, 0 | 2.2 × 10⁻⁵, 1 | 1.4 × 10⁻³, 393 |
| `land-use` | 1,645 | 1.4, 195 | 1.4, 207 | 1.4, 1,950 |
| `material-resources-metals-minerals` | 2,155 | 2.9 × 10⁻¹⁰, 0 | 9.7 × 10⁻⁵, 15 | 1.9 × 10⁻², 2,162 |
| `ozone-depletion` | 2,154 | 1.1 × 10⁻¹⁰, 0 | 4.6 × 10⁻⁷, 0 | 2.2 × 10⁻⁴, 277 |
| `particulate-matter-formation` | 2,003 | 1.4 × 10⁻¹¹, 0 | 8.1 × 10⁻⁷, 0 | 2.0 × 10⁻⁴, 228 |
| `photochemical-oxidant-formation-human-health` | 2,013 | 2.1 × 10⁻¹², 0 | 6.1 × 10⁻⁷, 0 | 1.3 × 10⁻⁴, 167 |
| `water-use` | 2,091 | 2.5 × 10⁻¹², 0 | 2.0 × 10⁻⁶, 1 | 9.2 × 10⁻⁴, 231 |

- **headwater:** 24 indicators have no product above 10⁻⁶, and the largest difference is 1.8 × 10⁻⁹ (ionising radiation). Land use has 195 products above 10⁻⁶, up to 1.4 relative. All of them cancel: each difference is at most 1.2 × 10⁻¹⁶ of the product's gross.
- **Before this change** the test compared relative to ecoinvent's score with a floor of 10⁻⁶ of the median, and failed on land use alone: 22 products above 10⁻⁶, the largest 1.49 × 10⁻⁵ (run on main, 24 October 2026, load average 3.3 to 6.7).
- **Plain, LU of Aᵀ** gives 268 products above 10⁻⁶ in 14 indicators. The largest is 9.7 × 10⁻⁵ for material resources (15 products); climate change, land use and land-use change has 1.2 × 10⁻⁵. 65 of the 268 are off by more than 10⁻⁶ of their gross, so they are solver error and not cancellation: all 61 outside land use, and 4 of the 207 in land use, by at most 9.7 × 10⁻⁵ of the gross.
- **Plain, LU of A** is worse: 11,918 products above 10⁻⁶, 10,936 of them off by more than 10⁻⁶ of their gross, up to 1.9 × 10⁻² (material resources). Its factors have 61.8 million non-zeros and took 86.0 s, against 7.4 million and 2.9 s for the LU of Aᵀ, at the load above.
- **The residuals do not show it.** The normwise backward error of the solve, ‖h − Aᵀy‖∞ / (‖A‖₁‖y‖∞ + ‖h‖∞), is at most 2.3 × 10⁻²¹ for the LU of A, 1.5 × 10⁻²⁷ for the LU of Aᵀ and 2.4 × 10⁻²⁷ for headwater: the largest scores dominate the denominator. Row by row, each against its own terms, headwater is at most 2.5 × 10⁻¹⁴ and has no row above 10⁻¹². The LU of Aᵀ has 1,042 to 1,826 rows above 10⁻¹² in each indicator and 1 as its largest. That is the error that rounding noise gives at a product whose score is exactly 0, as in [Numerics on ecoinvent](#numerics-on-ecoinvent); not checked row by row. The LU of Aᵀ leaves noise of up to 2.8 × 10⁻⁹ at those products, the LU of A exactly 0. The LU of A has 9,363 to 10,001 rows above 10⁻¹², up to 0.17.

## Brightway project import

**Version:** ecoinvent 3.12 cut-off.

Script: [bench/brightway_import_parity.py](../../bench/brightway_import_parity.py). Raw output: [bench/results/brightway_import_parity_results.json](../../bench/results/brightway_import_parity_results.json).

**Scope:** one Apple Silicon Mac (arm64), macOS 26.6, 2 November 2026, 2 BLAS threads. headwater on Python 3.14.3, NumPy 2.5.3, SciPy 1.18.1. One database of a real Brightway project (bw2data 4.5.4), imported with `import_brightway` from the project's files into a new study, release `ecoinvent-3.12-cutoff@b9428cdd8045`, EF v3.1 imported from ecoinvent's 3.12 workbook. Each process scored for one unit of its product with `study.scores`. Brightway's scores are those of bw2calc 2.3 on the same project; the earlier study is the one imported from the same data with `import-excel` and a mapping file, matched by name and location.

| Check | Result |
|---|---|
| Activities and exchanges read | 40 and 759 |
| Lines linked, with no mapping file | 759 of 759: 40 references, 599 to the release, 56 to the study's own activities, 64 flows of nature (all through `/unspecified`) |
| Validation after the first import | 4 errors, all flows the method has no factor for, which the earlier study's `no_factor.csv` acknowledges |
| Scores within 10⁻⁶ relative of Brightway's | 1,000 of 1,000 (40 products × 25 indicators), at most 1.9 × 10⁻⁷ |
| Scores equal to the earlier study's | 1,000 of 1,000, largest difference 0 |
| Import, from the project's files to a validated study, one run | 6.0 s |

The differences from Brightway are the size of its float32 storage (D21).

## Command start-up

Script: `cli_startup.py`. Raw output: [bench/results/cli_startup_results.json](../../bench/results/cli_startup_results.json).

**Scope:** one Apple Silicon Mac (M2 Pro, 10 cores), macOS 26.6.2, 25 October 2026. Python 3.14.3, Typer 0.27.2, NumPy 2.5.3, SciPy 1.18.1, DuckDB 1.5.6, PyArrow 26.0.0, Pydantic 2.14.0. **Not a quiet run:** other work ran on the machine, so read each time with the load average (1 minute) next to it. The toy fixtures only, no licensed data. Each command is a fresh process of the installed `headwater` script, best of 5 runs after one warm-up run of each. `find, release` is the toy as a release in a temporary `HEADWATER_HOME`; `find, folder` and `calc, folder` read the toy folders. The first columns are without pandas, the last two in an environment with the `notebooks` dependency group, which installs it.

| Command | Without pandas, before | after | With pandas, before | after |
|---|---|---|---|---|
| `headwater --help` | 1.63 s | 0.36 s | 2.33 s | 0.35 s |
| `headwater find --help` | 1.59 s | 0.66 s | 2.38 s | 0.57 s |
| `headwater find`, toy folder | 1.45 s | 0.81 s | 3.49 s | 0.69 s |
| `headwater find`, toy release | 1.41 s | 0.64 s | 2.58 s | 0.72 s |
| `headwater calc`, toy folder | 1.43 s | 1.26 s | 2.65 s | 2.21 s |
| Load average at start / end | 5.4 / 6.0 | 14.0 / 13.2 | 5.9 / 14.0 | 12.2 / 12.7 |

- Importing `headwater.cli` loads none of NumPy, SciPy, PyArrow, DuckDB, lxml, openpyxl, Pydantic or pandas. A command's module is imported when the command is run, and each module that a small command loads keeps the solver, the file readers and DuckDB for the function that needs them. `tests/cli/test_startup.py` checks this in a fresh interpreter.
- `find` loads PyArrow, Pydantic and NumPy and no SciPy, DuckDB or `pyarrow.dataset`. A release table is read with `pq.ParquetFile`, because `pq.read_table` opens a dataset.
- headwater never imports pandas. PyArrow imports it itself when it is installed: in `pyarrow.dataset`, and at the first `pa.array` of a Python list. `--help` and `find` reach neither. `calc` still reaches the second, so with pandas installed it loads pandas; without it, it does not.
- `calc` is the solver's own import and work: NumPy, SciPy, and DuckDB for the checks of an in-memory release. The "before" runs were made at load 5 to 6 (14 at the end of the run with pandas), the "after" runs at load 12 to 14.
