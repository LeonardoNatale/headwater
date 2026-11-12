# Roadmap

Build in this order. Each milestone ends with a test that must pass before the next one starts. Toy tests run in CI; tests on licensed data run only where `ECOINVENT_DIR` (and `BRIGHTWAY2_DIR` for parity) is set.

## M0. Skeleton

- Repository, packaging with uv, ruff, pyright, pytest, CI.
- Records in `model/` for the toy: Flow, Process, Exchange with roles, Unit, Method, Study.
- The toy background and the T-shirt study as fixtures ([calculation.md](calculation.md#the-toy-background)).
- `calc/`: assemble, factorize, forward and transposed solve, explain, levers.

**Exit tests (toy, in CI):**
- T-shirt scores 1.19205; with the waste line 1.25205.
- `h · s` equals `x[T-shirt]` to 1e-12.
- Sensitivity to kWh per kg fabric is 0.13245, and matches a finite difference.
- The contribution tree sums to the score at every level.
- Raising: an input with no supplier, an unknown unit, and a study's own flow with no characterisation factor each produce an error naming it.

## M1. Releases and calculation on real data

- ecoSpold2 importer: roles from by-product classification and exchange group, flow properties, uncertainty fields, pedigree.
- Linking by UUID for ecoinvent; issues list; freeze to Parquet with content hash.
- DuckDB assembly from Parquet, scaled factorization with residual checks and pivoting fallback, one indicator vector per indicator.
- Pull and cache releases from a local folder. Indicator vectors live in a derived release that pins its parent, so adding a method never changes the parent's hash.

**Exit tests:**
- All 26,533 ecoinvent 3.12 cut-off datasets: matrices equal to Brightway's, and accurate solves agreeing within 1e-9 (licensed, opt-in). Done: 0 differing cells, 7.2e-15.
- The waste sign rule matches the full solve on every activity with waste inputs, not just a sample (licensed, opt-in).
- Importing the same source twice gives the same release hash (toy, in CI).
- Benchmark the solve settings again and record them in [appendix/benchmarks.md](appendix/benchmarks.md).

## M2. Studies

- Study file format: `study.yaml`, a YAML and a CSV per process, overrides, parameters, cut-offs.
- Validation, calculation in supplier-first order, results with provenance.
- `diff` between git refs, with causes.
- CLI and a CI template that comments results diffs on pull requests.
- Excel import into study files, since many practitioners model in spreadsheets today.

**Exit tests:**
- Rebuild one real study from an existing notebook model and match its result within 1e-6 (licensed, opt-in).
- A pull request on the T-shirt study produces the expected diff comment (toy, in CI).

## M3. Full methods and explanations

- Full method import, with sub-indicators and the extra climate indicators: biogenic CO₂ uptake, biogenic CO₂ emission, direct land-use change.
- `contributions`, `where` and `sensitivity` in the API and CLI, including named-parameter sensitivities.
- Local overrides, and global changes inside named scenarios through low-rank updates.
- Studies that use other studies of the same repository, by folder (`uses: [dyes]`), so a shared library is written once, and a change to it shows in the results diff of every study that uses it.

**Exit tests:**
- Same top contributors as Brightway on 10 products (licensed, opt-in).
- Low-rank updates match a full refactorization for 1, 10 and 100 changed processes, and the measured crossover is recorded.
- Validate against ecoinvent's own published LCIA scores for the same method variant, if that file is available (licensed, opt-in).

## M4. Versions, importers, organisation layers

- Version upgrades through mapping tables (3.10 → 3.12), with RoW links flagged and a results diff. Done: `headwater upgrade` ([workflows.md](workflows.md#upgrading-to-a-new-release)). ecoinvent's correspondence files are not read yet.
- Importers: SimaPro CSV, openLCA JSON-LD, Brightway projects. Done: SimaPro CSV and openLCA JSON-LD, and ecoSpold1 ([D29](decisions.md) to [D31](decisions.md)), and a database of a Brightway project into a database (`headwater database import-brightway`, [D44](decisions.md), [workflows.md](workflows.md#importing-from-a-brightway-project)). ILCD is in [Later](#later).
- Pull releases from a bucket URL, with access limited to licence holders. Done: `headwater release pull` and `hw.load` take a folder, `file://`, `https://`, `gs://` (`headwater[gcs]`) or `s3://` (`headwater[s3]`), with the checks of a folder pull and the cloud's own login ([workflows.md](workflows.md#a-team-sharing-releases)). The toy pulled from fsspec's memory filesystem and from a local HTTP server has the same hash; a corrupted pull is refused with the cache left clean; a missing extra, a URL with credentials and a derived release without its parent are errors that name what to do (toy, in CI). No real bucket has been tried.
- Publishing a database: freezing it into a release that other repositories pin ([D46](decisions.md)).
- The first organisation package (`headwater-<org>`): storage, exports to internal systems, reporting rules.

**Exit tests:**
- Upgrade a 3.10 study to 3.12 and explain every result that moved by more than 1% (licensed, opt-in). Done on a real study of 40 products: 56 suppliers mapped (54 by the same dataset, 2 by reviewed rows, none by the same id: every activity UUID changed), and each of the 931 scores of 1,000 that moved by more than 1% splits line by line into what the release changed, the lines adding up to the change (tests/integration/test_upgrade.py). The upgraded study equals the one imported on 3.12, line for line and score for score.

## M5. Parity with Brightway

- Normalisation and weighting: a set of normalisation factors and weights per method, pinned by the method's hash, gives normalised scores, weighted scores and one single score, as SimaPro and openLCA do. Done for the toy ([calculation.md](calculation.md#single-scores)); readers of SimaPro's `Normalization-Weighting set` blocks and of JSON-LD `nwSets` are to come.
- Monte Carlo: a range for each product and study of one call, mean, median, standard deviation and percentiles per indicator. Iteration k draws from (seed, k), keyed by process and line id, so products and studies drawn with the same iterations stay paired, in one call or across calls. Each line is drawn on its own, as Brightway does. A study's own lines and parameters carry a distribution centred on their amount, in four more columns of a process file, and are drawn by their ids, so a study draws the same alone or beside others. A local override's copy draws the lines it keeps as the original, and a study in a scenario stays paired with the study without it. Done ([calculation.md](calculation.md#uncertainty)): on the toy, the T-shirt's fabric line lognormal with GSD 1.1 gives 1.034069, 1.19205 and 1.382479 for 2.5%, median and 97.5%. To come: a presampled background stored as a release, a market's shares drawn together, and a scenario's background line set by a formula of drawn parameters.
- The APOS system model: ecoinvent 3.12 APOS imports as its own release, `ecoinvent-3.12-apos@474501c24cdc`, with its own role table ([D35](decisions.md)). Done.

**Exit tests:**
- A toy method with two indicators and a toy set give the single score checked by hand in [calculation.md](calculation.md#single-scores) (0.161523 Pt for the T-shirt). Its contribution tree adds up at every level, and `where` and the sensitivities of its lines equal the weighted sums of theirs per indicator. A row for an unknown indicator is an error naming it, and an indicator the set does not cover is listed on the result, whose provenance names the set and its hash. Done (toy, in CI).
- Adding a set changes no method and no release hash: the toy, and EF 3.1 on ecoinvent 3.12 (licensed, opt-in). Done: with a set loaded, EF 3.1 keeps its hash, and the indicators release it writes has the hash of the one written without (tests/integration/test_single_score_ef.py).
- EF 3.1 single scores for the 3.12 pilot's products, with JRC's normalisation and weighting factors (licensed, opt-in). Waits for the factors file.
- Monte Carlo on the toy gives the T-shirt's analytic range (2.5%, median and 97.5% of 1.12434, 1.19205 and 1.27366 with fabric's 3 kWh lognormal, GSD 1.1), and two studies drawn with the same iterations stay paired (the T-shirt minus the T-shirt with waste is -0.06 in every iteration). On 3.12 with EF 3.1, 10 products and 200 iterations each, headwater's mean, 2.5 and 97.5 percentiles agree with Brightway's: 746 of 750 checks within 3 bootstrap standard errors (licensed, opt-in, [bench/monte_carlo.py](../bench/monte_carlo.py), [benchmarks](appendix/benchmarks.md#monte-carlo-on-ecoinvent-312)). Done (toy, in CI).
- ecoinvent 3.12 APOS imports in CI as a toy with APOS's system model id, and twice to the same hash, on the toy and on the real folder, while cut-off 3.12 keeps `b9428cdd8045`. Its matrices equal Brightway's in every cell, and the same matrices solved agree exactly; EF 3.1 maps onto it as onto cut-off and its scores are within 2.6 × 10⁻¹⁰ of ecoinvent's APOS cumulative LCIA, land use against the gross (licensed, opt-in, tests/integration/test_apos.py and test_method_import.py, [benchmarks](appendix/benchmarks.md#apos-against-brightway-and-ecoinvent)). Done (toy, in CI).

## M6. Ready for LCA users

- API polish: a Brightway user finds the Python API obvious in their first hour. `bg.score("toy:fabric-production")` gives 3.7086, `print(bg)` names the release and the method, every argument that takes a process id is `process_id`, and the public names of `headwater`, `Background`, `Study` and `Result` are fixed by a test ([calculation.md](calculation.md#what-you-can-call), [D38](decisions.md)). Done.
- Editing a study from Python, as in Brightway, while the study's files in git stay the truth: `hw.Study.new` and `hw.open(path).edit(bg)` add, change and remove processes, exchanges, flows, parameters, cut-offs and local overrides, and `save()` checks the study, and the studies that use it, before it writes only what changed ([calculation.md](calculation.md#building-and-editing-a-study), [D39](decisions.md)). Done. To come: editing scenarios, `no_factor.csv`, `flow_properties.csv` and `units.csv`.
- A migration trial: two or three existing Brightway notebooks rebuilt as headwater studies and short notebooks, outside this repository, with results compared within 1e-6 and a list of what was hard. Done: three notebooks, one built from an Excel inventory, one that looks activities up by exact name and one that loops over indicators. What was hard became the fixes below.
- Fixes from the trial: ids from the Excel import never move between imports, any process of a study scores with `process_id`, contributions have locations and a table, `bg.get_process` finds one process exactly, Brightway's direct and upstream are one call, and `Study.new` needs only a boundary ([calculation.md](calculation.md#python-api), [D40](decisions.md)). Done.
- Databases and studies ([D46](decisions.md)). Example: the toy T-shirt becomes the database `tshirt`, a folder of processes, and the study `tshirt-footprint`, whose scenario `cleaner-dye` changes a line of the database `dyes`. A database is scored per product, per unit of what each process makes. A study asks one ISO question of databases: goal and scope, a functional unit, products with amounts, methods and scenarios. `headwater schema` writes JSON schemas for the YAML files, and a script rewrites existing folders once. To come.

**Exit tests:**
- The public names of `headwater`, `Background`, `Study` and `Result` are listed by a test, `bg.score` adds up to `calc` within 1e-12, each main object prints on one line, checked as text, and the toy's release, method, set and input hashes are those from before the renames (toy, in CI, tests/query/test_api.py). Done.
- The T-shirt built in five calls is the fixture byte for byte, every toy study saved with no change is unchanged, an invalid edit raises and writes nothing, an override made through the editor scores 1.09205, and a parameter and a formula line survive an edit (toy, in CI, tests/study/test_edit.py). Done.
- Databases and studies: the toy scores are unchanged (1.19205, 1.25205, 0.13245, 1.242646); a study of two toy products with different amounts gives each score times its amount; the scenario `cleaner-dye`, line e03 of `dyes:reactive-dye` from 4 to 3.5, gives 1.237646; a database with no methods validates; the schemas validate the toy files and list the toy process ids; input hashes change once, and the test says so (toy, in CI).

## Later

| Item | Trigger |
|---|---|
| Consequential system models | Test unpivoted factorization on consequential data first |
| Regionalised characterisation factors | A method that needs them, with data that has locations to match |
| Editing without git | A real user who cannot use git |
| Other solver backends | Benchmarks show SciPy is the bottleneck on a target machine |
| Time-dependent (dynamic) LCA | A reporting requirement |
| Export a study to openLCA JSON-LD, tested by a round trip: exported, imported back, same results | Someone needs to hand a study to openLCA |
| ILCD importer | Someone needs EF datasets in ILCD |
