# Findings

What we learned before designing headwater, and the evidence for each point. Timings and file sizes are in [appendix/benchmarks.md](appendix/benchmarks.md); counts and facts about data and code are here.

Scope of the investigation (October 2026):

- **Brightway 2.5** (bw2data 4.7, bw2calc 2.5.0, bw2io 0.9.17), read from source and run in a scratch environment.
- **ecoinvent 3.10 cut-off**, ecoSpold2 files from a local licensed copy: 23,522 datasets.
- **GitHub issues** of the main Brightway repos and Activity Browser, ranked by comment count.
- **Standards and docs**, read directly or through secondary sources (marked below): ecoinvent documentation, PEF Recommendation 2021/2279, the GHG Protocol Product Standard, ISO 14044 and ISO 14067.

## 1. Brightway in one paragraph

Brightway is a set of BSD-licensed Python packages from the `brightway-lca` GitHub organisation (67 active repos). Three do most of the work:

- **bw2io** reads source files, cleans them and links exchanges to suppliers.
- **bw2data** stores projects, nodes (processes and flows), edges (exchanges) and impact methods.
- **bw2calc** builds sparse matrices and solves them.

Two small packages connect storage to the solver: **bw_processing** freezes data into NumPy arrays, and **matrix_utils** turns those arrays into matrices. Its main author wrote 623 commits to the calculation engine; the next person wrote 20. Brightway 3 exists only as a written plan.

## 2. What Brightway gets right

Ideas worth keeping, without copying the code:

- **A clean hand-off to the math.** Storage produces a frozen package of arrays (row, column, value, sign flag, uncertainty). The solver never touches the database.
- **Scenarios as overlays.** Several array packages can be stacked; later ones replace cells.
- **Signs as a flag.** Inputs are stored positive with a flag that makes them negative in the matrix.
- **Many products in one solve**, and a pluggable solver.
- **Codes derived from source UUIDs** for ecoinvent, so the same dataset gets the same code across imports.
- **Import steps as plain functions** over lists of records, easy to test and reorder.
- **One explicit list of node types**, with products and processes allowed to be separate.

## 3. Failures we reproduced

Each was run in a throwaway folder. None raised an error at the moment things went wrong.

| What we did | What happened | Cause |
|---|---|---|
| Rewrote the emissions database with identical data, then edited the product | Footprint went from 1.19205 to 0.0 | Every node gets a new integer id on rewrite. The impact method still points at the old ids, and factors for unknown flows are dropped silently. |
| Rewrote a 2-node database with one exchange missing its type | Database had 0 nodes afterwards | `Database.write` deletes everything first, and deletes again on failure before re-raising. |
| Imported ecoinvent with one source file empty | One supplier of one market was removed; the import summary said "0 unlinked" | Unlinked exchanges are deleted during cleaning, so the summary checked afterwards finds none. With multiprocessing on, the empty file first crashed the import with an unrelated pickling error. |
| Changed a parameter used in a formula from 0.5 to 0.9 | Result stayed at 1.0 until someone called `parameters.recalculate()`, then 1.8 | Formulas are evaluated once and written into the exchange as plain numbers. |
| A method write failed halfway | Every later calculation with that method failed with `FileNotFoundError: datapackage.json` | Arrays are rewritten in place, not written to a temporary file then renamed. |
| Two processes registered a database each | Only the last one survived in `databases.json` | Registries are in-memory dicts, written whole to JSON, with no lock. |
| Switched project while holding a node object, then saved it | 0 rows updated, no error, and the text landed in the other project's search index | Global state rebinds storage on switch; old objects keep pointing at it. |
| Stored the amount 0.004 | Came back as 0.00400000019 | Matrix values are stored as float32, and duplicate cells are summed in float32 too. |
| Imported the library | It created a project registry and a default project on disk, and migrated an existing registry by adding columns | Importing has side effects. |

Two more from reading the code: a node created one way gets type "process" and fails with `OutsideTechnosphere`, while the same node written another way works; and codes for non-ecoinvent formats are an md5 of fields joined with no separator, so "steel" + "GLO" and "steelG" + "LO" collide.

## 4. Where the time and space go

Measured on ecoinvent 3.10 cut-off; numbers in [appendix/benchmarks.md](appendix/benchmarks.md#brightway).

- **The math is cheap.** Building the matrices and solving for one product takes seconds or less.
- **Converting between storage forms is expensive.** Parsing XML, writing one pickled row per exchange, and rebuilding all arrays after any edit dominate.
- **Storage is large.** Every project carries its own full copy of ecoinvent, mostly as pickled Python dicts in SQLite. The same data with every field kept is about 11 times smaller as Parquet.
- **Factorization settings matter a lot.** The same SciPy routine runs about 20 times faster with a different column ordering and no pivoting. Other solver libraries were not tested.

## 5. Code health

| Package | Lines of code | Functions with any type hint |
|---|---|---|
| bw2io | 18,829 | 26% |
| bw2data | 10,487 | 25% |
| bw_processing | 3,787 | 75% |
| bw2calc | 3,247 | 64% |
| matrix_utils | 1,465 | 67% |

Import and linking (bw2io) is the largest package by far. That is where most of the domain complexity sits, and any replacement has to deal with the same complexity.

Most-discussed issues: install on Apple Silicon (75 comments), a renamed MKL DLL breaking the solver (30), parameter scenarios giving wrong results (30), and a score of 465,000 instead of about 1.8 on ARM from an integer cast. Collaboration on one dataset has been an open request for years.

## 6. What Brightway's maintainers want to change

- **Strategic plan:** separate projects, storage and import; one API for nodes, edges and methods; eILCD as the exchange format; all storage in SQLite; calculations on cloud workers.
- **bw_interface_schemas:** one typed graph, with separate product and process nodes. Their own words on implicit production edges: "ultimately led to many hacks".
- **bw2data issue #181:** node and edge tables with a JSON payload, branches, Postgres, a web API, and the database as an append-only log.
- **bw_processing issue #112:** float32 dates from old solvers; float64 would break existing callers.

Most of our criticism of the data layer is already written down by the maintainers. What holds them back is migrating an existing research ecosystem, not knowing what is wrong.

## 7. LCA facts any engine must handle

Counts from ecoinvent 3.10 cut-off.

**Waste is recorded with negative signs.**
- 4,086 datasets are waste treatments, with a reference product of -1 kg.
- Producers record waste as negative inputs (44,264), and recyclables too (1,881).
- All recyclables link to 78 "Recycled Content cut-off" datasets that carry no burden.
- Solved naively, 3,773 of the 4,086 treatments get a negative score. 11 ordinary datasets also have negative scores, so "negative" cannot be used to detect waste.
- A role per exchange, derived from ecoinvent's by-product classification, handles this. Tested on 50 real activities with waste inputs: results matched the full solve within 1.6 × 10⁻¹².

**Negative amounts are sometimes legitimate.**
- 1,673 emissions are negative, mostly heavy metals to agricultural soil.
- 195 resource inputs are negative, most of them a non-fossil CO₂ correction.
- CO₂ uptake is a positive input of "Carbon dioxide, in air".
- Substitution (avoided products) is negative by nature. The GHG Protocol excludes avoided emissions from inventory results.

**Released datasets are already allocated.**
- Every cut-off dataset has one reference product.
- 1,773 multi-output activities were split into 4,527 datasets by ecoinvent.
- An engine needs its own allocation only for processes users model themselves, following ISO 14044: avoid allocation, then physical relationships, then economic.

**Ordinary inputs are never negative.** All 46,145 negative technosphere inputs Brightway imports are the 44,264 waste lines plus the 1,881 recyclables; no other input is negative. No treatment uses its own product either (0 such lines among the 4,086 treatments).

**Processes can consume their own product.** 828 datasets do (731 of them markets), to model losses.

**Units need per-flow properties.**
- ecoinvent ships 774 unit conversion factors, all within one quantity.
- Converting across quantities needs flow properties. Natural gas is counted in m³ and carries a heating value in MJ, and 608 of 4,057 products are counted in "unit".
- Exchanges carry heating values (101,392), wet mass (181,761) and prices (88,955).

**Supplier links go to markets.**
- 94.6% of linked inputs point to market datasets, which carry supplier mix, transport and losses.
- Of 4,022 products with markets:
  - 2,724 have only a global (GLO) market
  - 969 have a "rest of world" (RoW) market plus regional markets
  - 329 have only regional markets
- No product has both GLO and RoW. RoW means "the world minus the regions that have their own dataset", so its meaning changes between versions.

**One climate number is not enough.**
- PEF reports fossil, biogenic and land-use climate change separately when each is above 5%.
- ISO 14067 and the GHG Protocol want biogenic emissions and removals, and land-use change, reported separately.
- EF 3.x gives CO₂ taken from the air a factor of 0, so even EF's biogenic indicator leaves out uptake. Extra indicators are needed.

**Uncertainty is everywhere, and correlated.**
- 509,416 exchanges have lognormal uncertainty, plus 2,551 normal and 1 uniform; 511,968 carry a pedigree matrix.
- Sampling pre-solved product scores independently ignores the correlation between products that share suppliers. Lesage, Mutel, Schenker and Margni (2018) show this underestimates single-product uncertainty and badly overestimates it in comparisons.
- 4,695 of 7,220 markets have inputs with uncertainty, so a market's supplier shares must be sampled together or they stop adding up to 1.

**A plain solve looks accurate and is not.** On 3.12, a SuperLU solve of Aᵀ with SciPy's defaults and no scaling has a normwise residual as small as headwater's (about 10⁻²⁷), yet its EF v3.1 scores are up to 9.7 × 10⁻⁵ off ecoinvent's (material resources) and 1.2 × 10⁻⁵ on climate change, land use and land-use change; headwater's are within 1.8 × 10⁻⁹, apart from 195 land-use products whose scores cancel ([benchmarks](appendix/benchmarks.md#plain-and-scaled-solves-against-ecoinvents-scores)).

**The supply graph is one big loop.** 15,374 of 23,522 processes (65%) are in one strongly connected group: each is directly or indirectly a supplier of every other. Changing any of them changes most scores.

**Scores span 11 orders of magnitude.** The largest product score is about 1.8 × 10¹¹ per unit (infrastructure counted per unit), the median 0.58, and some markets score around 10⁻⁴. Accuracy checks must be relative to each product, not global.

**Other rules:**
- **Cut-off.** PEF allows cut-offs up to 3% cumulative, each one made explicit and justified. A data gap is a different status.
- **Regionalised factors.** EF 3.1 water (AWARE) and land use are regionalised, but ecoinvent's own EF implementation uses global factors only. ecoinvent 3.10 has 4,362 elementary flows and none is regionalised.
- **System models.** Cut-off, APOS and consequential are separate, separately linked databases. Consequential uses substitution, with negative entries.
- **Mixing backgrounds.** Mixing ecoinvent with EF secondary datasets or other databases needs flow-list mapping and a double-counting check.
- **Test oracle.** ecoinvent publishes per-dataset LCIA scores and cumulative inventories, which make a good test reference.

## 8. Storage options we evaluated

| Option | What we measured or read | Verdict |
|---|---|---|
| SQLite with pickled rows (Brightway) | Large, slow to write, not queryable outside Python, no shared transaction with the other files | Avoid |
| A shared DuckDB file | Fast and small for calculation fields. Rejected a dangling reference (foreign keys work). While one process holds the file for writing, no other process can open it, not even to read. | Use DuckDB in memory, never as a shared file |
| Parquet files | Every field of ecoinvent in about 11 times less space than Brightway; readable by DuckDB, pandas, Polars and warehouses | Use for frozen releases |
| DuckLake | Tested: every write is a snapshot, old versions are readable, a change feed lists edits. No primary or foreign keys; multi-user needs a Postgres catalog. Version 1.0 since April 2026. | Later, for people who do not use git |
| Postgres | Full constraints and many writers, but a server to run | Not needed for v1 |
| Git with text files | Branches, review, history and blame; one line per exchange diffs cleanly | Use for studies |

DuckDB 1.5.6 is current; 2.0 is planned for 21 October 2026 with a new default file format. Keeping no long-lived `.duckdb` files avoids being stuck on either format.

## 9. Market context

The commercial market is led by SimaPro (PRé Sustainability) and LCA for Experts (Sphera, formerly GaBi). openLCA (GreenDelta) is the best-known free desktop tool. Brightway leads among open-source, code-first engines, mostly in research and data pipelines. No public market-share data exists. As a rough signal, PyPI downloads in the 30 days before 10 October 2026: bw2calc 18,718, bw2data 19,570, and olca-ipc (openLCA's Python client) 5,719. These include CI installs.

## 10. Not verified

- ISO 14044 and ISO 14067 wording was read through secondary sources.
- Lesage et al. (2018) was read from its abstract only.
- A forum post claims ecoinvent 3.11 changed how waste signs are recorded. Not checked. This is why roles must be derived from each release's own classification, never hard-coded.
- DuckDB's documented access to Google Cloud Storage covers HMAC keys only; other login methods were not checked.
