# Design

How headwater is built: principles, architecture, stack, data model and file formats. The calculation itself is in [calculation.md](calculation.md). Why each choice was made is in [findings.md](findings.md) and [decisions.md](decisions.md).

## Principles

1. **Import once.** A background database is imported once per source, version and system model into a frozen release. Databases point at it by hash and never copy it.
2. **Ids never change.** A dataset's id is its source identity plus version. Matrix row numbers exist only during a calculation.
3. **Never edit in place.** Releases are immutable. Databases change only through files in git, so every change has an author, a reason and a way back.
4. **Fail loudly.** Anything that would make a number wrong stops the work with a list of what to fix.
5. **One meaning per field.** Every exchange has an explicit role. Signs are derived from roles in one place.
6. **Explain everything.** Every result can say what it buys, where emissions happen, and which inputs matter most.
7. **A library, not an app.** It runs in your Python process. Organisation-specific code lives in separate packages.

## Architecture

Two lanes. The release lane runs once per source version. The database lane runs all day. They meet at a release: a frozen set of Parquet files named by a hash of their content.

```
RELEASE LANE (once per source version, e.g. ecoinvent 3.12 cut-off with EF 3.1)

  parse ──> validate and link ──> issues list ──> freeze ──> precompute
  ecoSpold2,   units, roles,       must be empty   Parquet,    factorize, one
  ILCD, ...    suppliers           or resolved     hashed      vector per indicator

                                  │ databases pin a release by hash
                                  v

DATABASE LANE (every day)

  edit files ──> validate ──> calculate ──> review ──> publish (M4)
  YAML + CSV     same rules   DuckDB in     pull       freeze the database as a
  in git, using               memory +      request,   release that other
  databases next              SciPy         CI diff    repositories pin,
  to them                                              organisation exports
```

### Packages and modules

```
headwater/
  model/     records: Flow, Process, Exchange, Role, Unit, FlowProperty,
             Method, Indicator, Database, Issue
  io/        importers: one reader per format, the shared stages, the
             registry of formats (see Importers); excel for databases
  link/      linking by id and by name, flow mapping, mapping files
  release/   build, validate, freeze, hash, read; storage through URLs
             (local folder, gs://, s3://)
  study/     read and write database and study files, validate, overrides, scenarios,
             JSON schemas
  calc/      pure functions on arrays: assemble, scale, factorize, solve,
             scores, contributions, where, sensitivity, update
  query/     public Python API and the planner; caches by input hash
  cli/       the `headwater` command

headwater-<org>/   separate, private: storage credentials, exports to
                   internal systems, reporting rules, CI templates
```

## Stack

| Part | Choice | Why |
|---|---|---|
| Language | Python 3.13+, developed on 3.14 | Follows SPEC 0 (scientific Python supports versions for 3 years). Python 3.15 is added once DuckDB ships wheels for it. Practitioners work in notebooks, and NumPy/SciPy hold the solvers. The measured cost in Brightway is converting between storage forms, not the math, so a faster language would buy little. |
| Release files | Parquet with zstd | With every field kept, ecoinvent is about 11 times smaller than Brightway's SQLite. Readable by DuckDB, pandas, Polars and warehouses. |
| Query engine | DuckDB, in memory | Reads Parquet directly. Builds the matrices from SQL in well under a second. Never used as a shared `.duckdb` file: a process holding one for writing locks out every other process, even readers. |
| Databases and history | YAML and CSV in git | Branches, review, history and blame, with tools people already use. One exchange per line diffs cleanly. |
| Solver | SciPy SuperLU, pluggable | With a fill-reducing ordering and no pivoting it factorizes ecoinvent about 20 times faster than with default settings. Other backends can be added behind the same interface. |
| Records | Pydantic v2 | Validated records at every boundary: files in, files out, API. |
| ecoSpold2 parsing | lxml | Fast, and the format is XML. |
| Storage access | fsspec | One URL syntax for local folders and cloud buckets. |
| CLI | Typer | Thin layer over the Python API. |
| Tooling | uv, ruff, pyright, pytest | Standard and fast. |

Numbers behind these reasons: [appendix/benchmarks.md](appendix/benchmarks.md).

**Not in v1:** Postgres, a server, a web API. If people who do not use git need to edit, DuckLake with a Postgres catalog is the planned path (see [decisions.md](decisions.md)).

## Data model

Example rows use the toy T-shirt or made-up values.

Where a field has a standard vocabulary, its values come from the standard and are not invented: a process's kind is an ecoSpold2 special activity type, and its location is an ecoinvent geography short code.

| Record | Holds | Example |
|---|---|---|
| Release | A frozen version of a source. A database frozen into one, for other repositories to pin, comes in M4 | `ecoinvent-3.12-cutoff`, hash `9f2c…`, licence ecoinvent, flow list ecoinvent-3.12 |
| Unit | Units within one quantity | `g` = 0.001 `kg` |
| Flow | A product, a waste or an elementary flow, with a reference unit | `natural gas, high pressure`, product, m³ |
| FlowProperty | Conversion across quantities, per flow | natural gas: 1 m³ = 36 MJ (heating value, net) |
| Process | An activity making one reference flow, at a place and a time, with an ecoSpold2 activity type | `cotton fabric production`, IN, 2024, `ordinary_transforming_activity` |
| Exchange | One line of a process, with a role | input, `electricity`, 3 kWh, supplier `electricity production, IN` |
| Allocation | How a multi-output process that a user models is split | cotton gin: fibre 0.9, seed 0.1, economic |
| Method | Indicators and characterisation factors, pinned to a flow list | EF 3.1, "no long-term" variant, flow list ecoinvent-3.12 |
| Indicator | One impact category or sub-indicator | climate change, fossil |
| Factor | Characterisation factor per flow and indicator, optionally per location | `Methane, fossil` to air, climate change: 29.8 |
| IndicatorVector | Solved scores of every product of a release, per method, stored in a derived indicators release that pins its parent | one row per product, one column per indicator |
| Database | Processes in git, each a product per its declared unit, and the release they buy from | `tshirt`: 1 process, `toy-background@fixture`, methods `toy-climate` |
| Study | Goal and scope, and the products that meet its functional unit, with amounts | `tshirt-footprint`: `tshirt:tshirt` × 1, cradle-to-gate, cut-off 3% |
| Override | Changed lines of a background process, for one database | electricity input of fabric production: 3 → 2.5 kWh |
| Issue | Something unresolved, and how it was resolved | input with no supplier; resolved as cut-off, share 0.02%, reason, who, when |
| Mapping | Reviewed match between ids or versions | 3.10 → 3.12: old product id → new product id, factor 1, approved |
| Result | A computed number and its provenance | T-shirt, climate change, 1.19205, release hashes, input hash, residual |

### Ids

| Thing | Id | Stable across versions? |
|---|---|---|
| ecoinvent process | `ecoinvent:3.12:cutoff:<activity uuid>_<product uuid>` | No: the version is part of it, and ecoinvent gives every activity a new UUID in each version. `headwater upgrade` moves a database ([Upgrades](#upgrades)). |
| ecoinvent product flow | `ecoinvent:product:<intermediate exchange uuid>` | Yes |
| ecoinvent elementary flow | `ecoinvent:flow:<elementary exchange uuid>` | Yes |
| Database process | `<database>:<slug>`, for example `tshirt:sewing` | Yes, until someone renames it on purpose |
| Exchange | the process id plus a line id, for example `e03` | Yes, so diffs and overrides can point at one line |

Names are for people. Links always use ids. A database file may show a name next to an id, and validation checks that the two still match.

### Roles and signs

Every exchange has one role. Amounts in files and in the API are positive unless the physics says otherwise (marked below). One function in `calc/` turns roles into matrix signs.

| Role | Meaning | Example |
|---|---|---|
| `reference` | What the process makes. For a treatment process, the waste it treats. | 1 kg cotton fabric; 1 kg textile waste treated |
| `input` | A product bought from a supplier | 0.25 kg cotton fabric |
| `own_use` | The process uses some of its own product, for losses | a market losing 2% of its electricity |
| `waste_to_treatment` | Waste sent to a treatment process, in kg sent | 0.05 kg fabric scraps to incineration |
| `recyclable` | Material sent to recycling under the cut-off rule, no burden | 0.1 kg aluminium scrap |
| `avoided_product` | A product displaced by this process (substitution) | electricity exported to the grid. Excluded from GHG Protocol inventory totals. |
| `emission` | Elementary flow to nature. Can be negative. | 0.01 kg CO₂, fossil, to air |
| `resource` | Elementary flow from nature. Can be negative. | CO₂ taken from the air by a crop |

How waste is computed: inside the matrix, headwater uses ecoinvent's own convention (a treatment's reference is -1, a waste sent is a negative input). The API reports a treatment's burden as a positive number per kg treated, and a database's waste line as positive kg sent. Both give the same result; the equivalence was checked on 50 real activities with waste inputs ([benchmarks](appendix/benchmarks.md#lca-checks)).

Roles are derived from each source's own classification when importing (for ecoSpold2: the by-product classification and the exchange group), never from the sign of the amount, because some ordinary datasets also have negative scores.

### Units and properties

- Every flow has a reference unit. Amounts can be entered in any unit of the same quantity. They keep the unit written in the file and are converted to the reference unit when the matrices are built.
- A unit table gives each unit its quantity and its factor to the reference unit of that quantity. A unit defined two ways is an error.
- Conversion across quantities uses the flow's properties. A property says that one reference unit of the flow holds `value` of `unit`. Natural gas has the reference unit m³ and a net heating value of 36 MJ, so 72 MJ of natural gas is 2 m³. Only net heating value, density and mass per item convert. Gross heating value is kept as data and never used for conversion. A missing property is an error, not a factor of 1.
- Properties kept per flow: heating value (net, gross), density, mass per item, dry mass, water content, carbon content (fossil, biogenic), price.

### Methods and indicators

- A method has an id, a version, a variant (for example EF 3.1, "no long-term", EN 15804) and the flow list its factors were mapped to. Example: `EF-3.1`, version `3.1`, variant `EF v3.1`, flow list `ecoinvent-3.12`.
- An indicator is one impact category or sub-indicator of a method, with the unit of its scores: `climate-change-fossil`, in kg CO2-Eq. Where the source gives it, an indicator also has its category indicator, as ISO 14044 calls what a category's score measures: `global warming potential (GWP100)`. ecoinvent's workbook gives it; SimaPro CSV and openLCA JSON-LD name the category only.
- A method's factors are rows of indicator, flow id, location and factor: the method folder ([A method](#a-method)). That folder is the contract between a source of factors and headwater. A reader turns a source's method into it, finding each factor's flow by id, and nothing after the import depends on where the factors came from. ecoinvent's LCIA implementation workbook has a reader (below), and so do the methods of an openLCA JSON-LD package ([The formats](#the-formats)).
- A flow covered for some indicators counts 0 for the others, as usual in LCA (CO₂ adds nothing to ozone depletion). A flow the method does not cover at all is never dropped without a trace: in a database's own processes it is an error until the database acknowledges it in `no_factor.csv` ([Flows with no factor](#flows-with-no-factor)); in the background it is listed on the result.
- A factor for a compartment can also cover its subcompartments, as SimaPro reads one: `Carbon dioxide, fossil` to `air` (unspecified) also counts CO₂ to `air/high. pop.` and to `air/urban air close to ground` when the method gives those no factor of their own. SimaPro and Brightway's SimaPro import both do this, so a score matches SimaPro's. The SimaPro reader marks such factors, and the flow mapping gives each one to every elementary flow of the release with the same name in a subcompartment of its compartment that has no factor of its own for that indicator. The import report counts them in a column, `copied`, and `method.yaml` says how many. ecoinvent's workbook and JSON-LD name each subcompartment, so their factors cover their own flow only.
- A normalisation and weighting set divides the score of each indicator it names by a normalisation factor and multiplies it by a weight, and adds the weighted scores into a single score, in points, as SimaPro and openLCA do. For EF 3.1 a normalisation factor is one person's yearly impact, and the weights add up to 100%. A set is its own folder, beside its method's, and pins the method by hash, so adding one never changes a method or a release ([A normalisation and weighting set](#a-normalisation-and-weighting-set)). Its scores are weighted sums of the method's, worked out with no solve ([calculation.md](calculation.md#single-scores)).
- Several methods can be used together, each kept as it is ([Several methods together](#several-methods-together)). That is how the indicators reporting standards ask for separately (D8), biogenic CO₂ removals and emissions and direct land-use change, sit next to EF 3.1 ([Carbon indicators](#carbon-indicators)).

**Importing a method from ecoinvent.** `headwater method import "LCIA Implementation 3.12.xlsx" --method "EF v3.1" --flow-list ecoinvent-3.12-cutoff@b9428cdd8045` reads ecoinvent's LCIA implementation workbook and writes the method folder `methods/EF-3.1` (see [A method](#a-method)).

- The workbook names each flow by name, compartment and subcompartment, not by UUID. A row maps to the one elementary flow of the release with exactly that name and compartment: `Methane, fossil`, `air`, `urban air close to ground` maps to the flow written `air/urban air close to ground` in the release. No case folding, synonyms or CAS numbers. The method folder holds the flow's id, `ecoinvent:flow:<uuid>`, so everything after the import is by id.
- That key is unique within one ecoinvent version (9,850 keys for 9,850 elementary flows in ecoinvent 3.12). Where two elementary flows share a name and compartment, only a factor with that key is refused, naming both flows, and a row of the flow mapping says which one it is (`ambiguous-flow`). The other factors map as usual. Names change between versions, so the release must be ecoinvent's, of the version the workbook's "Read me" sheet gives.
- The method's id, version and variant come from the workbook's method name: `EF v3.1` gives `EF-3.1`, `3.1` and `EF v3.1`; `EF v3.1 no LT` gives `EF-3.1-no-LT`. A name with no `v` version takes a year in it as its version: `IPCC 2021 (incl. biogenic CO2)` gives `IPCC-2021-incl-biogenic-CO2` and `2021`. Each can be given instead. The flow list is the release's.
- `--category` chooses categories of the method by name, once per category. The others are left out unread, and the output and the comments of `method.yaml` name them. `IPCC 2021` needs it: its category `climate change: total (excl. biogenic CO2)` has five indicators (GWP20, GWP100, GWP500, GTP50, GTP100), and a category is read as one indicator.
- Each category becomes an indicator, its id made from the category name: `climate change: fossil` gives `climate-change-fossil`. The name is the category's, the category indicator is the workbook's `Indicator` column, `global warming potential (GWP100)`, and the unit is the workbook's: `kg CO2-Eq`. Zero factors are kept, so the flow counts as covered.
- The import reports, per indicator: factors in the workbook, factors mapped, every factor whose flow is not in the flow list, and the count of flows with no factor. It also gives the flows with no factor for any indicator: the command lists the first 20 and counts the rest, and the Python result holds them all. On ecoinvent 3.12, all 27,415 EF v3.1 factors map to 25 indicators, and 1,890 of the 9,850 elementary flows have no factor.

#### Carbon indicators

EF v3.1, as ecoinvent implements it in 3.12, gives no climate factor to `Carbon dioxide, in air` (1 resource flow), `Carbon dioxide, non-fossil` (4 air compartments), `Carbon dioxide, non-fossil, resource correction` (1), `Carbon monoxide, non-fossil` (4), `Carbon monoxide, from soil or biomass stock` (4), or `Carbon, organic, decrease in soil or biomass stock` and `Carbon, organic, increase in soil or biomass stock` (1 each). Its sub-indicator `climate change: biogenic` counts non-fossil methane only. Its sub-indicator `climate change: land use and land use change` adds the two together: CO₂ from and to soil or biomass stock, and methane from soil or biomass stock.

The same workbook has the carbon indicators in two other methods, and headwater imports them as they are, with no rule of its own, as Brightway users get them:

| Indicator | Method and category in the workbook | Flows | Factors in 3.12 |
|---|---|---|---|
| Biogenic removals | `IPCC 2021 (incl. biogenic CO2)`, `climate change: biogenic removals (incl. CO2)` | `Carbon dioxide, in air` and `Carbon dioxide, non-fossil, resource correction`, which count negative | 2 |
| Biogenic emissions | `IPCC 2021 (incl. biogenic CO2)`, `climate change: biogenic emissions (incl. CO2)` | `Carbon dioxide, non-fossil` and `Methane, non-fossil`, to 4 air compartments each | 8 |
| Their sum | `IPCC 2021 (incl. biogenic CO2)`, `climate change: biogenic (incl. CO2)` | The flows of both, with the same factors | 10 |
| Direct land-use change | `IPCC 2021`, `climate change: direct land use change` | `Carbon dioxide, from soil or biomass stock` and `Methane, from soil or biomass stock` to 4 air compartments; `Carbon dioxide, to soil or biomass stock` to 4 soil compartments, which counts negative | 12 |

```
headwater method import "LCIA Implementation 3.12.xlsx" --method "IPCC 2021 (incl. biogenic CO2)" \
  --category "climate change: biogenic removals (incl. CO2)" \
  --category "climate change: biogenic emissions (incl. CO2)" \
  --category "climate change: biogenic (incl. CO2)" --flow-list ecoinvent-3.12-cutoff@b9428cdd8045
headwater method import "LCIA Implementation 3.12.xlsx" --method "IPCC 2021" \
  --category "climate change: direct land use change" --flow-list ecoinvent-3.12-cutoff@b9428cdd8045
```

These write `methods/IPCC-2021-incl-biogenic-CO2` and `methods/IPCC-2021`, with only those categories. All 32 factors map to the 3.12 release. On all 26,533 products of 3.12 the four scores match ecoinvent's own cumulative LCIA within 5.3 × 10⁻¹¹ relative, except the sum on 6 products whose parts cancel, within 1.7 × 10⁻⁷ ([benchmarks](appendix/benchmarks.md#carbon-indicators-on-ecoinvent-312)).

The sum is where a score near 0 can hide large parts: a product that takes 1.5 kg of CO₂ from the air and gives it back scores 0. Such a score is flagged ([calculation.md](calculation.md#factorize)). Removals and emissions each count one kind of flow, so they rarely cancel.

#### Several methods together

Example: `hw.load("ecoinvent-3.12-cutoff@b9428cdd8045", method=["methods/EF-3.1", "methods/IPCC-2021-incl-biogenic-CO2", "methods/IPCC-2021"])` scores the 25 indicators of EF 3.1 and the 4 carbon indicators with one factorization, and a database's `calc` gives each of them.

- Each method stays as it is: its own folder, id, version, factors and hash.
- A database lists them in `database.yaml`: `methods: [EF-3.1, IPCC-2021-incl-biogenic-CO2, IPCC-2021]`, and one method is a list of one: `methods: [EF-3.1]`. The command line takes `--method` once per method, an id of a method the release holds or a method folder. Files always list the ids, one by one.
- Results name them together by their ids joined by `+`, in the order given: `EF-3.1+IPCC-2021-incl-biogenic-CO2+IPCC-2021`, version `3.1+2021+2021`. A method id cannot hold `+`.
- An indicator id in two methods is refused, naming both. IPCC 2021's `climate change: fossil` gives `climate-change-fossil`, as EF 3.1's does: import one of them without that category.
- A flow covered by one of the methods is covered. `Carbon dioxide, non-fossil` counts in the biogenic indicators and 0 in EF 3.1's, so a database that emits it needs no `no_factor.csv` row.
- A `no_factor.csv` row names one of the methods, and holds for a flow that has no factor in any of them. So the rows a database wrote for EF 3.1 alone, such as COD to water, stay valid when the IPCC methods are added.
- The methods must be mapped to the same flow list.
- One indicators release holds them all, each with its own factors, indicators file and hash, in the order given. Its name joins their ids with dots: `ecoinvent-3.12-cutoff.EF-3.1.IPCC-2021-incl-biogenic-CO2.IPCC-2021@<hash>`. A release of one method is as before, with the same hash.
- A result's provenance lists each method loaded, with its id, version and hash (`methods`), and the method its indicator comes from (`indicator_method`): `IPCC-2021` for `climate-change-direct-land-use-change`. `method_hash` is the hash of their hashes.

### Databases

A database is a folder of processes in git (D46). Every process is a product, scored per unit of what it makes, its declared unit. Example: the toy T-shirt is the database `tshirt`, one process, `tshirt:tshirt`, scored per T-shirt. A database of knitted fabrics would hold 16 processes, each scored per kg of its fabric. `database.yaml` says what the database is: its name, the release it buys from, the databases it uses, and the methods it is scored with by default ([the file](#a-database)).

A database holds no question: no goal, functional unit or reference flow. Those belong to a study, which names products of databases with amounts (D46).

A database can also hold four more things, each in its own file ([the files](#a-database)):

- **Named parameters.** Example: `fabric_kg` is 0.25, and the T-shirt's fabric line says `fabric_kg` instead of 0.25. A parameter is a value or a formula of other parameters. Formulas stay formulas in the files, and sensitivities are reported per parameter. Example: "+1% on `fabric_kg` gives +0.78% on the score". The syntax is in [Parameters and formulas](#parameters-and-formulas).
- **Overrides** of background processes, used by this database only: [Overrides and scenarios](#overrides-and-scenarios).
- **Cut-offs**, the inputs the database leaves out on purpose: [Cut-offs and data gaps](#cut-offs-and-data-gaps).
- **Flows with no factor**, the flows its processes emit that the method does not cover, each acknowledged so it counts 0: [Flows with no factor](#flows-with-no-factor).

A database can also buy from the processes of other databases in the same repository, the ones its `uses:` names. A shared library of processes, such as dyes or chemicals, is then written once, as a database of its own, and git versions it with the databases that use it ([Databases it uses](#databases-it-uses)).

### Studies

A study asks one question of databases (D46). Example: the toy study `tshirt-footprint`, next to the databases `tshirt` and `dyes`, asks for the footprint of 1 T-shirt: its product is `tshirt:tshirt`, amount 1, which scores 1.242646. Its scenario `cleaner-dye` sets line e03 of `dyes:reactive-dye` from 4 to 3.5 kg CO₂, a line of a database the T-shirt uses, and gives 1.237646.

A study is a folder with `study.yaml` ([the file](#a-study)), and optional `scenarios/` and `no_factor.csv`. It holds no processes.

- **Its products** are process ids with amounts. Each is scored for its amount: two products with amounts compare products for one functional unit, as 1 cotton T-shirt against 1.25 polyester T-shirts for 50 wears. A product is a process of a database, or of the release, the background.
- **It finds a database by the prefix of its products' ids**: `tshirt:tshirt` is in the folder `tshirt` next to the study, which must hold a database named `tshirt`. Databases and studies sit side by side in one parent folder. A product whose prefix names no database folder is a process of the background.
- **Its goal and scope** are ISO 14044's: goal, functional unit, boundary, cut-off criteria and limit, methods, and optional audience, comparison, data quality, PCR, assumptions and limitations.
- **Its scenarios** reach every line and parameter its products reach: the lines of the products' databases and of the databases they use, and the background's ([Scenarios](#scenarios)).
- **Cut-offs.** A study lists every cut-off its products reach, and refuses a database that allows a larger share to be cut off than the study does.
- **Flows with no factor.** A study may add rows to `no_factor.csv` for its methods, for a flow of any database it reaches ([Flows with no factor](#flows-with-no-factor)).
- **Its pin.** A study pins no release when its products are in databases: each database pins the release it buys from, and they must pin the same one. A study whose products are all processes of a release pins it in `background:`.

### Overrides and scenarios

Example: the T-shirt buys 0.5 kWh of electricity, and the background electricity emits 0.5 kg CO₂ per kWh. This mill has a wind contract at 0.3 kg. There are two ways to say so, and they give different numbers.

| | Local override | Global change, in a named scenario |
|---|---|---|
| Meaning | This database buys a copy of the electricity that emits 0.3. | The grid itself emits 0.3, for everything. |
| Who uses the new electricity | Only the database's own purchase. | Every process that buys electricity, the fabric too. |
| Electricity the T-shirt buys | 0.3298 per kWh | 0.3298 per kWh |
| Fabric the T-shirt buys | 3.7086 (unchanged) | 3.1086 |
| T-shirt | 1.09205 | 0.94205 |

- **Local override (the default).** The database gets its own copy of a background process, built from the release plus the changed lines. Only the database's own purchases from that process use the copy. The copy still buys from the background as it is, and the background is unchanged. A local override is what a supplier-specific claim means.
- **A copy has its own id**, `<database>:copy:<background id>`, such as `tshirt:copy:toy:electricity-production`, so one id names one thing in results, sensitivity analyses and diffs. Its `copy_of` names the process it copies.
- **A copy never buys from another copy.** Override electricity at 0.3 kg CO₂ per kWh and fabric at 2.5 kWh per kg: the fabric copy buys the grid's electricity at 0.5298, so the T-shirt is 0.25 × (2.1192 + 2.5 × 0.5298) + 0.5 × 0.3298 = 1.025825, not 0.900825. Chaining copies is not in M2.
- **Global change.** It exists only inside a named scenario of a study. It changes the background process for everything in that scenario, and is computed with a low-rank update ([calculation.md](calculation.md#edits-and-scenarios)). It answers "what if the world changes".

Nothing outside a scenario changes the background, so the two cannot be mixed up by mistake.

An override is a file of changed lines of a database, and a scenario a file of changes of a study. Both are read and checked when their folder is opened, and checked against the background by validation. `database.calc(bg, "tshirt:tshirt")` uses the overrides, and `study.calc(bg, scenario="clean-grid")` a scenario ([calculation.md](calculation.md#python-api)).

The rules for each file are under [A database](#a-database) and [A study](#a-study).

### Linking

1. A database's input names a product. The supplier defaults to the **market** for that product.
2. Geography fallback: exact location, then the smallest region containing it, then RoW or GLO (a product never has both), then an issue: "no supplier".
3. The author can set a specific supplier per line.
4. Approved matches between names, codes or versions are stored as Mapping records and reused by every database, not redone per notebook.
5. Links to RoW are flagged when moving to a new ecoinvent version, because RoW changes meaning ([Upgrades](#upgrades)).

### Cut-offs and data gaps

An unresolved input becomes an Issue. It is resolved as one of:

- **linked:** a supplier was found.
- **cut-off:** excluded on purpose. Records the reason, criterion (mass, energy or environmental significance), estimated share, running cumulative share, who and when. Validation fails if the cumulative share exceeds the database's maximum.
- **data gap:** no data exists, reported as such.

A release cannot be frozen while any Issue is still open.

In a database, a cut-off is a row of `cut_offs.csv` for a line that has no supplier. Example: the T-shirt uses 0.01 kg of packaging tape, and the background has no tape. Its mass share is 0.01 of the 0.26 kg of material the T-shirt buys, which is 3.85%. The row says so, with the reason, who decided and when. The line then buys nothing, and the score stays 1.19205.

The cumulative share is worked out by criterion, in the order of the lines, and each criterion must stay within `max_cumulative` of `database.yaml`. Example: with a maximum of 5% the tape is accepted. With 3% it is refused, and the message lists the lines that add up. Mass and energy have separate budgets, because their shares are shares of different totals. The share of each line is the author's estimate: headwater does not recompute it.

A line with no supplier and no cut-off is an error. A database cannot record a data gap: only a release can.

### Provenance

Every Result carries the release hashes, method id and version, input hash, engine version, solver settings, the residual check, and its positive and negative parts, with a flag when the score is near 0 because they cancel ([calculation.md](calculation.md#factorize)). A score of a normalisation and weighting set also carries the set's id, version and hash, and the method's indicators the set leaves out. A product of a study also carries the study (`provenance.study`): saved or not, and the commit the repository that holds it is at, with `changed` true when the study or a database it reaches differs from that commit. A study in memory is not saved, and its input hash names its inputs: its products and amounts, methods, own no_factor rows, the scenario, and the records of the databases it reaches, never its name or its text. A product of a database scored on its own carries the database (`provenance.database`): its name, the commit the repository that holds it is at, or None outside git, and `changed`, true when the database or a database it uses differs from that commit. Both are checked at the database's first calc, not when it is opened. Neither commit is part of the input hash.

A Monte Carlo run's samples live in memory, in the `MonteCarlo` it returns: one float64 per item, indicator and iteration, 2 MB for 10 products, the 25 indicators of EF 3.1 and 1,000 iterations. Nothing is written. Its provenance holds what reproduces it: the release, the methods, the seed, the number of iterations, the generator and the numpy and scipy versions ([calculation.md](calculation.md#uncertainty)).

## Importers

The toy fabric process buys 3 kWh of electricity. Written in three formats, it ends as the same release row, `input, electricity, 3 kWh, supplier <electricity production>`:

| Format | What the reader writes on the line | Stage that resolves it |
|---|---|---|
| ecoSpold2 | `supplier_id = ecoinvent:1.0:cutoff:<activity>_<product>` | link by id |
| SimaPro CSV | `supplier_name = electricity production`, `supplier_product = electricity`, `supplier_location = GLO`, from the text `electricity {GLO}\| electricity production` | link by name |
| ecoSpold1 | `supplier_product = electricity`, `supplier_location = GLO`, `supplier_unit = kWh` | link by name |

A reader only reads its format into typed tables, with the source's own ids and keys. It never links a line, maps a flow or splits a process. Those are shared stages, one implementation each, that run in a fixed order. So a format is a reader, a toy fixture and one line in the registry, with nothing to chain and no order to learn (D29).

```
inventory:  reader ──> InventorySource ──> unit check ──> allocation ──> link by id / by name ──> Inventory ──> freeze
method:     reader ──> MethodSource ──> flow mapping ──> MappedMethod ──> method folder
```

### What a reader gives

An inventory reader returns an `InventorySource` (`headwater/io/source.py`):

| Field | Holds |
|---|---|
| `info` | the release's default name, source, version, system model, licence and flow list |
| `processes` | the processes table of a release, without `reference_flow_id`: a process may make several products until it is split |
| `lines` | `LINES`, below |
| `flows`, `flow_properties`, `units` | records, in the source's own nomenclature. A source that gives conversions between units makes its table with `unit_table` (`headwater/io/units.py`), which puts each unit in the quantity of the built-in units it converts to: ecoSpold2's `1 kWh = 3.6 MJ` puts kWh in `energy` |
| `geographies` | the places the source names, or an empty table |
| `allocation` | `ALLOCATION`, below: factors for processes that make several products |
| `left_out` | suppliers left out on purpose, by id, each with a reason, who and when: lines that buy from one become data gaps. ecoSpold2 fills it from the exclude file. |

Processes, lines and allocation factors are Arrow tables, because ecoinvent has 750,000 lines. Flows, properties, units and factors are records: there are thousands, and every check wants records.

`LINES` has the columns of `exchanges.parquet` without `link_status`, which only the link stages set, plus:

| Column | Holds |
|---|---|
| `flow_name` | the flow's name as written on the line, checked against the flow, then dropped |
| `supplier_id` | the supplier by id, as the source gives it |
| `supplier_name`, `supplier_product`, `supplier_location`, `supplier_unit` | the supplier by name: what the line gives of the supplier's process name, reference product, location and reference unit |

- A line names its supplier by id, by name, or not at all. A line that names it both ways, or names one on a role that buys nothing, is refused.
- A line always names its flow by id. A format with flow ids (ecoSpold2, JSON-LD, ILCD) uses them. A format that names flows only by name (SimaPro, ecoSpold1) gets an id from its reader and keeps the name in `flows.parquet`:
  - an elementary flow is named by name and compartment: `flow_id(namespace, name, compartment)`, a uuid5 of that key;
  - a product is named by name and the quantity of its unit: `product_flow_id(namespace, name, quantity)`, a uuid5 of `product`, the name and the quantity name of the flow's reference unit (`mass`, `energy`, `volume`, as the unit table names it). The same product counted in kg and in m3 is two flows with one name and two units, and in kWh and in MJ it is one. The id is worked out again from `flows.parquet` and the unit table. A buyer does not give its supplier's category, so the key has none.
  The same key always gives the same id, so the same source gives the same release. An elementary flow counted in units of two quantities is refused by the unit check.
- A release keeps its source's flows. Flow mapping is for methods only: a method is mapped onto a release's flow list.

`ALLOCATION` has one row per product, `process_id`, `product` (the line id of a reference line) and `factor`, and an optional `line_id` for a factor that holds for one line only.

A method reader returns a `MethodSource`: the method's name in the source, its id, version, variant and indicators, its factors keyed as the source keys them (`FlowKey`: an id, or a name and compartment, and optionally the unit a factor is per), and `flow_list`, the flow list those keys are written in, such as `ecoinvent-3.12`.

### The stages

In this order, for every format:

| Stage | Does | Fails how |
|---|---|---|
| Unit check | Every line's unit is known and converts to its flow's unit. Amounts keep the unit written in the source and convert when the matrices are built. | An error naming the line: electricity in kg |
| Allocation | Splits a process that makes several products into one process per product, `<process id>:<product line id>`. Each keeps its product line and every other line scaled by its product's factor, or by the factor for that line; a source that gives a factor for every line, as openLCA's causal allocation does, needs none for the products. A line that bought from the process by id buys from the split that makes its flow, and so does the process's own use of one of its products. | An open issue on the process, never a guess, when a line has no factor from a product, when they do not sum to 1 within 1e-9, when one is outside 0 and 1, or when it uses a flow as its own product that it makes on no line, or on two |
| Link by id | The supplier is a process of the source: linked. Left out on purpose: a data gap, with a resolved issue. | An open issue, `missing-supplier` or `unlinked-line`, naming both ends |
| Link by name | The one process that fits every field the line gives, exactly, and makes its product in a unit of the line unit's quantity: no case folding, no fuzzy match, no geography fallback. | An open issue: `no-supplier`, with close names to read, never applied; `ambiguous-supplier`, naming the processes that fit; or `supplier-other-quantity`, when processes fit only in other quantities (gas bought in kg, made in m3) |

A row of the supplier mapping decides before either link stage, and the line keeps an issue with who and when: resolved as linked, or as a data gap. Every line that does not link is an open issue with a reason, and `freeze` refuses while there is one. The `Issue` record carries no reason while it is open, so the reason is in the problem of the same line. Before the stages, what a reader must never give stops the import, whatever the format: a flow defined twice, a line whose flow is not a flow of the source, a process with no reference line, and a line that names its supplier both ways.

Example of allocation, the cotton gin of the data model: it takes 1 kg of seed cotton and 0.2 kWh, and makes 0.35 kg of fibre and 0.6 kg of seed, with the economic factors 0.9 and 0.1. `gin:p1` makes 0.35 kg of fibre from 0.9 kg of seed cotton and 0.18 kWh; `gin:p2` makes 0.6 kg of seed from 0.1 kg and 0.02 kWh. ecoinvent's datasets each make one product, so ecoSpold2 never splits anything.

Own use in a split process is bought from the split that makes the product. Example: a CHP plant burns gas that emits 2 kg of CO₂, makes 1 kWh of electricity (line p1, physical factor 0.6) and 3 kWh of heat (line p2, 0.4), and uses 0.05 kWh of its own electricity. `chp:p1` emits 1.2 kg and keeps 0.03 kWh as its own use, so it scores 1.2 / 0.97 = 1.2371134 kg CO₂ per kWh. `chp:p2` emits 0.8 kg and buys 0.02 kWh from `chp:p1`, so it scores (0.8 + 0.02 × 1.2371134) / 3 = 0.2749141 per kWh. Together 0.95 kWh of electricity and 3 kWh of heat carry the 2 kg. The own use is bought by id, whether the reader named it by id or by name.

For a method, one stage: **flow mapping**. For each factor, in order: a row of the flow mapping; the same id among the release's elementary flows; the same name and compartment, refused when it fits two elementary flows and no row decides it; else unmapped, with the reason. A factor given per another unit than its flow's is converted within one quantity: 0.001 per g is 1 per kg. The method's flow list must be the release's, unless a flow mapping file is given (`flow-list-mismatch`): names and ids change between flow lists, so a silent match across them is refused. Two factors of one indicator that land on one flow are refused. A factor that covers subcompartments also goes to the flows of its subcompartments with no factor of their own ([Methods and indicators](#methods-and-indicators)).

### Mapping files

Both are CSV files a person writes and reviews in git, like `cut_offs.csv`. `who` and `when` (an ISO date or time) are required on every row, a key decided twice is an error, and a row nothing needed is a warning, "row not needed".

The supplier mapping (`--supplier-mapping` on `headwater release import`) decides a line's supplier, or makes the line a data gap. A row names the supplier as the line does, by `supplier_id` or by at least one of `name`, `product`, `location` and `unit`, and gives either `supplier` or `reason`:

```
supplier_id,name,product,location,unit,supplier,reason,who,when,comment
,"market for | Cut-off, U","Electricity, medium voltage",FR,,agribalyse:3.2:p7,,leo,2026-10-24,two processes make it
,market for,Tap water,CH,,,Not in the package,leo,2026-10-24,
```

The flow mapping (`--flow-mapping` on `headwater method import`) gives the release's flow for a key of the method, by `source_id` or by `source_name` and `source_compartment`:

```
source_id,source_name,source_compartment,target,who,when,comment
,"Carbon dioxide, fossil",air/high. pop.,ecoinvent:flow:<uuid>,leo,2026-10-24,SimaPro name
```

### The formats

| Format | `--format` | Reads | Suppliers | Flows | Methods |
|---|---|---|---|---|---|
| ecoSpold2 (ecoinvent) | `ecospold2` | inventory | by id: activity link and product UUID | by id: UUID, MasterData | separate: ecoinvent's workbook |
| ecoinvent LCIA workbook | `ecoinvent-lcia` | method | | factors by name and compartment | is one |
| openLCA JSON-LD (BAFU, USLCI) | `olca-jsonld` | inventory and method, from one package | by id: default provider UUID | by id: flow UUID, `olca:flow:<uuid>` | in the package, by flow UUID |
| SimaPro CSV (Agribalyse) | `simapro-csv` | inventory and method, from separate exports | by name: name, product and location from the product text | by name and compartment, `flow_id()` | SimaPro method CSV, by name and compartment |
| ILCD (EF datasets and package) | `ilcd`, M4 | inventory and method | mostly none, the datasets are aggregated; by process UUID when given | by id: EF flow list UUID | LCIA method datasets, by flow UUID |
| ecoSpold1 (BAFU, UVEK) | `ecospold1` | inventory | by name: product, location, unit | elementary by name and category, products by name and unit quantity, `flow_id()` | none |

A package that holds both, such as JSON-LD, is read twice by the same format: `release import --format olca-jsonld pkg.zolca`, then `method import --format olca-jsonld pkg.zolca --method "EF 3.1"`.

**ecoSpold2.** `headwater release import <folder> --version 3.12 [--exclude exclude.csv]` reads `datasets/` in parallel (`--workers`, one per CPU by default) and checks every line against `MasterData/`. The files do not record the database version, so `--version` is given and must be the newest release the files name; ecoinvent 3.12 writes 3.0 in every file, so its version is taken as given with the warning `version-unchecked`. An empty file stops the import, naming it, until the exclude file lists it with a reason, who and when; lines that bought from it become data gaps. Each file names its system model: cut-off and APOS are read, each into its own release with its own ids, `ecoinvent-3.12-apos@<hash>` and `ecoinvent:3.12:apos:<activity uuid>_<product uuid>`, and each with its own role table (`ROLES` in `io/ecospold2/dataset.py`). A folder that mixes system models is refused, naming the files of each, and any other system model is refused. APOS's table is cut-off's but for the materials it treats ([D35](decisions.md)). APOS splits a recycling activity into the product it makes and the treatment of the material it takes: "paper production, newsprint, recycled" gives a dataset that makes paper and one whose reference is -1 kg of "waste paper, sorted", class Recyclable. So a negative reference of class Recyclable or allocatable product is a treatment, and its flow a waste in the release, as ecoinvent calls waste and recyclables alike materials for treatment. A negative input of either class is `waste_to_treatment`, sent to that treatment, which shares its burden; cut-off cuts a Recyclable off instead. A flow sent to treatment is a waste too, so a line that sends it to a file left out by the exclude file is a data gap like any other. A treated flow that a line makes or buys as a product is refused (`treated-flow-as-product`). Every other flow is MasterData's, the same file in both folders, so a method maps onto both releases alike.

**ecoSpold1.** `headwater release import <folder> --format ecospold1 --version 2026 --source bafu --licence "..."` reads every `.xml` file under the folder, one dataset or several per file, in parallel (`--workers`). The files say neither which database they are nor its version, so `--source`, `--version` and `--licence` are given: `bafu` and `2026` give the ids `bafu:2026:<uuid>` and the flow list `bafu-2026`. The root is `ecoSpold` in the ecoSpold1 namespace or in none (openLCA writes none), so detection never claims an ecoSpold2 folder. A file with another root is skipped with a warning.

- **Process.** The reference function gives name, location, unit and the time period. The id is a uuid5 of name, location, unit, category, subcategory and the infrastructure flag, never of a file path. Two datasets with the same ones are refused, naming both files. Category is part of the id only: a process has nowhere to keep it. A period that runs backwards, which BAFU has, is kept as no period, with a warning.
- **Lines.** The role comes from `inputGroup` or `outputGroup`, then the sign of the amount. A line has a number in its file, which several lines of a dataset may share: the first keeps it as its line id, the next are `<number>-2`, `<number>-3`.

  | Group in the file | Role |
  |---|---|
  | out0 reference product, positive | `reference` |
  | out0 reference product, negative | `reference`, its flow a waste: a treatment, as in ecoSpold2 |
  | out2 allocated by-product | `reference`: the process makes several products |
  | out1 include avoided product system | `avoided_product` |
  | out3 waste to treatment | `waste_to_treatment`, its flow a waste |
  | out4 to nature | `emission` |
  | in1, in2, in3, in5, zero or positive | `input`, or `own_use` when it buys the process's own product |
  | in1, in2, in3, in5, negative | `avoided_product`, or `waste_to_treatment` when its flow is a waste |
  | in4 from nature | `resource` |

  A negative input is an avoided product here, unlike in the Excel import, which reports it and leaves the choice to the person. The Excel workbook has a column to mark a substitution. ecoSpold1 has no marker, and background data cannot be fixed at its source, so the negative input is the format's own way to write a credit. It is the same matrix entry as the negative input. A flow is a waste when any dataset says so, and a line that uses it as a product is refused.
- **Suppliers.** A line that buys gives `supplier_product`, `supplier_location` and `supplier_unit` from its name, location and unit, for the link stage by name. Example: `electricity`, `CH`, `kWh`.
- **Flows.** An elementary flow is named by name, category and subcategory: `Carbon dioxide, fossil` in `air` and `low population density` is the compartment `air/low population density`. A product is named by name and the quantity of its unit, whatever its location or category, as above: freezing needs a line's flow to be the flow its supplier makes. A CAS number loses the zero padding of the schema: `000124-38-9` is `124-38-9`.
- **Units.** `p` is `unit`. `Nm3` is read as `m3`, with a warning that counts the lines, because the BAFU package writes the same flows in both. On BAFU:2026 the 29 processes that buy through such a line match BAFU's own scores with a median difference of 8.7 × 10⁻⁵ that way, against 6.1 × 10⁻³ with a physical conversion ([benchmarks](appendix/benchmarks.md#bafu2026-against-its-own-lcia-results)). A unit headwater does not know is kept as written, as a quantity of its own, except `Bq` and `kBq`.
- **Aggregated datasets.** A dataset of `dataSetInformation` type 2 holds cumulative results, so its emissions already include its suppliers'. Its lines that buy are not linked: each is a data gap, with the resolved issue "aggregated dataset: upstream already included". This is a rule of the format, not a person's decision about a line.
- **Uncertainty.** Type 1 is a lognormal (`standardDeviation95` is the square of the geometric standard deviation), 2 a normal (it is twice the standard deviation), 3 a triangular and 4 a uniform. A pedigree is the five scores at the start of the comment: `(1,2,3,4,5,na)`.
- **Allocation.** Each `allocation` gives a co-product its percent for the lines it names. The factor most lines share is the product's, and a line that differs gets a row.

**openLCA JSON-LD.** `headwater release import bafu.zip --source bafu --version 2026 --licence "<terms>"` reads a JSON-LD package of schema 2, as openLCA 2 exports it: a folder, a `.zip` or a `.zolca`. A JSON-LD package does not say its source, version or licence, so all three are given; a process is `bafu:2026:<uuid>` and a line its exchange's `internalId`. A flow is `olca:flow:<uuid>` in every package, in the flow list `olca`: openLCA names a flow by the same UUID in every database, so a method maps onto any JSON-LD release by id, and a factor whose UUID is not in the release is listed as unmapped. Roles come from the flow's type and the exchange's direction: a product output or a waste input is a `reference`, a product input an `input` from its default provider (`own_use` when that is the process itself), a waste output `waste_to_treatment`, an avoided product `avoided_product`; an avoided waste has no role and is refused. A process that makes several products gets the factors of its default allocation method (physical, economic, or causal per line), and the import warns `allocation-method` for each, naming the method. Uncertainty maps one to one: openLCA's four distributions are headwater's four. An exchange's data quality entry, cost and own location have no place in a release yet: the import warns `not-kept` with how many exchanges have each. JSON-LD has no ecoSpold2 activity type, so every process is `ordinary_transforming_activity` and the format cannot carry `process_kind`. A line or a factor given in another flow property than its flow's reference one is refused, naming it. A `.zolca` that openLCA writes when it backs up a database holds Derby files, not JSON-LD: it is refused, with the advice to open it in openLCA and export it as JSON-LD. `headwater method import bafu.zip --method "EF 3.1" --flow-list <release>` reads one method of the same package.

**SimaPro CSV.** `headwater release import agribalyse.csv --source agribalyse --version 3.2 --licence "..."` reads a SimaPro process export, and `headwater method import ef.csv --method "EF 3.1" --flow-list agribalyse-3.2@<hash>` a method export: one format reads both, and refuses the other kind. SimaPro names a process by its product text, and a line names its supplier by that text. The text gives what it holds (D31):

| Text | `supplier_name` | `supplier_product` | `supplier_location` |
|---|---|---|---|
| `Electricity, medium voltage {FR}\| market for \| Cut-off, U` | `market for \| Cut-off, U` | `Electricity, medium voltage` | `FR` |
| `Soft wheat grain, at farm gate {FR} U` | | `Soft wheat grain, at farm gate` | `FR` |
| `Dyeing of cotton fabric` | | `Dyeing of cotton fabric` | |

- The product is a flow, one wherever it is made, and one per quantity of the units its lines give. A process is named by the name its first product's text gives, else by the block's process name, else by the whole text, and its location is the text's, else `unspecified`. A process whose products give two names or locations is refused.
- Two texts that differ only after the location and give no name, such as `... {FR} U` and `... {FR} S`, give one key: a line that buys either is `ambiguous-supplier` until a row of the supplier mapping decides it.
- Flows and the flow list are named by SimaPro's version: `simapro-9.5`, `flow_id("simapro-9.5", name, compartment)` and `product_flow_id("simapro-9.5", product, quantity)`. A compartment is the section and the subcompartment, `air/high. pop.`, or `air` for a blank or `(unspecified)` one, in both readers. So a method export of the same SimaPro version maps onto the release by name with no mapping file.
- Units come from the export's `Units` block and the built-in table, with SimaPro's `l`, `p` and `ton` read as `L`, `unit` and `t`. A unit defined in neither is refused. A product's allocation percentage becomes its factor. A lognormal's parameter is the squared geometric standard deviation, so `scale` is ln √GSD².
- Amounts must be numbers: export with SimaPro's "Convert expressions to constants". Formulas are not read, so a formula is refused, and parameter blocks are read and not kept. Waste scenarios are refused. SimaPro has no process kinds, so every process is an `ordinary_transforming_activity`.
- A factor for a compartment's unspecified subcompartment also counts in its other subcompartments that have no factor of their own, as in SimaPro ([Methods and indicators](#methods-and-indicators)).

**ecoinvent's workbook.** See [Methods and indicators](#methods-and-indicators). Its keys are written in the flow list `ecoinvent-<version>` of its "Read me" sheet, so it maps onto that release only, unless a flow mapping is given.

### Excel import

A Brightway Excel inventory is a foreground source: it is not imported into a release, it becomes a database folder, written to be edited, that links to a release which already exists (`headwater database import-excel`). Its reader, `io/excel/workbook.py`, only reads. It uses the shared stages where they fit: suppliers are matched by exact name, reference product and location through `Suppliers.of_release`, the index that SimaPro and ecoSpold1 link by name with, and the mapping file is read like the other decision files, by the one CSV reader and one `when` parser, with `who` and `when` on each row. Three things differ from a release import. A line that does not link is written with no supplier and an `UNMAPPED` comment, not left as an issue, because the database is validated and edited, not frozen. Flows of nature are matched by name and category, and units from Brightway's names, by the Excel import's own stages. And importing again replaces the process files but merges database.yaml: the import sets `name`, `background` and `uses`, and any other field only while the person has not changed it ([Importing again](workflows.md#importing-from-excel)).

**Ids.** An activity's id comes from the activity alone, `<database>:<name>-<location>`, so it is the same whatever else the workbook holds and however often it is imported. `T-shirt` in GLO is `tshirt:t-shirt-glo`. The location is added unless the name already ends with it: `Autoclave no EVA input GLO` is `autoclave-no-eva-input-glo` in GLO and `autoclave-no-eva-input-glo-vn` in VN. The id is also the name of the process's files, so the part after `<database>:` is at most 100 characters, about the length of an ecoinvent id, which already names a file of `overrides/`. A longer name is cut after a whole word, and 6 characters of the sha256 of its whole name follow, so two names cut alike stay apart: `transport-freight-lorry-with-refrigeration-machine-16-32-metric-ton-euro6-r134a-004a2a-rer`. The location is never cut. Activities whose names and locations give one id take it from their reference products instead, every one of them, by the same rule. Example: three steps all named `assembly` in GLO, which make `upper`, `sole` and `shoe`, are `upper-glo`, `sole-glo` and `shoe-glo`, whatever their order, and the report says so for each. An activity whose own name gives its id keeps it, so a step that would take that id is numbered instead. Only an id still taken by an earlier activity, such as two `assembly` that both make `upper`, gets a number after the 100 characters, `-2`, in the order of the workbook: a workbook line to that name is ambiguous anyway. A Brightway project's activities come in the order of their codes, and its lines link by key, so there the `-2` twin is not ambiguous. Adding a second activity of a name moves the first one's id to its product's, once, and so does a re-import of a workbook made before this rule (D40). A line that does not link gets its flow by the same rule, `<database>:unmapped-<name>-<location>`, numbered too when two such lines differ only in product, unit or compartment.

### Brightway project import

A database of a Brightway project is a foreground source too, read into the records of the Excel import and converted by the same stages, so a line is linked, reported and written as a workbook line is (`headwater database import-brightway`, [D44](decisions.md)). Its reader, `io/brightway/project.py`, reads the files bw2data 4.5.4 and 4.7 write, without bw2data: `projects.db` for the project's folder, `databases.json`, the activities and exchanges of `lci/databases.db`, and `parameters.db`. It opens them read-only and checks their tables before it reads a row. A record's fields beyond its key are a pickle, read by an unpickler that refuses every class. One thing differs from a workbook: an exchange names its supplier by key, so the row is filled with the supplier node's own name, reference product, location, unit and categories, a line to an activity of the database links to that activity by its key, and only the rows the database's exchanges point to are read, never a whole background database. Distributions are copied by the conversion, with the mapping of `io/excel/uncertainty.py` that a workbook's go through too, and the same reader of distribution columns that reads a database file, so what is written reads back the same.

### The registry and the command line

`headwater/io/formats.py` lists every format, one line each:

```python
FORMATS: tuple[Format, ...] = (
    ecospold2.FORMAT,
    lcia.FORMAT,
    olca_jsonld.FORMAT,
    simapro_csv.FORMAT,
)
```

A `Format` has a `name` for `--format`, a `title` for `--help`, a cheap `detect` (names and first bytes, never a parse), its readers, the options each reader takes, and `cannot_carry`. A format that reads both kinds lists the options of each reader apart, so `--version` given to its inventory reader is checked against that reader only.

`inventory_from(path, options, format=None, suppliers=None)` and `method_from(path, release, options, format=None, flows=None)`, in the same file, are the one way in: they choose the format, check the options, read, and run the stages. The command line, the conformance suite and the hash tests all use them. `headwater release import PATH` and `headwater method import PATH` take `--format`; without it, the one format of that kind whose `detect` recognises the path is used, and none or several is an error that lists the formats. An option the reader does not take is an error: `--workers does not apply to format ecoinvent-lcia`. `--version`, `--source` and `--licence` give what a format's files do not say: ecoSpold2 takes `--version` only, since its files name ecoinvent and its licence.

### The conformance suite

The toy background and toy-climate are written in every format, under `tests/fixtures/formats/<format>/`. An inventory format has `toy/`, version 1.0, and `toy-climate-flows.csv`, the reviewed flow mapping from the toy's flow ids to its own. A method format has `toy-climate/`, the method named `toy climate v1`, in the flow list of one of the inventory fixtures. One parametrised test imports each inventory, scores it with toy-climate through its flow mapping, and compares it with the toy, after matching processes by name and location, flows by name and kind, and lines by process, role and flow: process kinds, flow units and CAS numbers, distributions, A, B, and every score within 1e-9. Another maps each method onto the inventory fixture of its flow list and compares its factors with toy-climate's. A format lists in `cannot_carry` what it cannot hold (`process_kind`, `cas`, `supplier_links`, `uncertainty`), and the test skips exactly those.

### Adding a format, in five steps

The example is ecoSpold2 and the toy.

1. **Write the toy in the format.** `tests/fixtures/formats/ecospold2/toy/` holds the toy's three processes and five flows as three datasets and MasterData, with ecoinvent release 1.0, and `toy-climate-flows.csv` maps `toy:co2` and `toy:ch4` to its two elementary flows. A method format adds `toy-climate/`.
2. **Write the reader.** `io/ecospold2/reader.py` has `read(path, options) -> InventorySource`. It parses, checks what only it can check (a product classified two ways, a release that is not the version given), keeps the source's ids, and fills `left_out` from the exclude file. It calls no stage.
3. **Register it.** `io/ecospold2/__init__.py` defines `FORMAT = Format(name="ecospold2", read_inventory=read, inventory_options=..., ...)`, and `FORMATS` gets one line.
4. **Run the conformance suite.** `uv run pytest tests/io/test_conformance.py`. List in `cannot_carry` only what the format cannot hold, never what the reader does not read yet.
5. **Test one real public file**, opt-in like the ecoinvent tests, and add the format's row to the table above.

## Upgrades

Example: the toy T-shirt buys cotton fabric from `toy:1:fabric-a`, `cotton fabric production | cotton fabric | RoW` in the release toy-1. toy-2 has no process with that id, but it has one with the same name, product and location, `toy:2:fabric-b`. `headwater upgrade` writes that id on the line, the new pin in `database.yaml`, and nothing else:

```
e02,input,toy:cotton-fabric,0.25,kg,toy:1:fabric-a,cotton fabric,
e02,input,toy:cotton-fabric,0.25,kg,toy:2:fabric-b,cotton fabric,
```

**What moves.** Every id of the old release that a database names: the suppliers of its lines, the processes its overrides change and their line ids, and the processes of `parameters.yaml`. The scenarios of a study given move too, with the products and pin of a study of release products, and the databases a study's products are in move with it. A `flow_name` written next to a flow that the new release renamed takes the new name. The databases it uses move with it, and a database next to it that uses one of them must move too, because a database and the databases it uses pin one release.

**The rules.** Each process of the old release gets its successor from the first rule that decides it:

| Rule | Decides | Example |
|---|---|---|
| Mapping row | A row of the upgrade mapping, keyed by the old process's id, or by its name, product, location and unit. A decision, with who and when. | `dye production \| dye \| GLO` to `toy:2:dye-r` |
| Same id | The same ids under the new version: `ecoinvent:3.10:cutoff:<a>_<p>` is `ecoinvent:3.12:cutoff:<a>_<p>`. | `toy:1:elec` to `toy:2:elec` |
| Correspondence | A row of ecoinvent's correspondence files between the two versions, chained by id. One successor maps. Several are a split: unmapped, with the successors as suggestions. | |
| Same dataset | The one process with the same name, product id and location, ecoinvent's own key, unique within a version. | `toy:1:fabric-a` to `toy:2:fabric-b` |
| Unmapped | Listed with its reason (`not-in-release`, `split`, `deleted`, `not-a-process`), the processes it might be (the same product elsewhere, then `find` on its name and product), which are never applied, and a mapping row to fill in. | `tape production \| tape \| GLO`, maybe `toy:2:tape-rer` |

The mapping file has the format of the supplier mapping of the importers ([Mapping files](#mapping-files)): `who` and `when` are required, a key decided twice is an error, and a row nothing needed is a warning. A row with a `reason` instead of a supplier is refused: in a release it makes a data gap, which a database cannot hold. A row decides before the rules, so a person can send a RoW link to a region, and the report says what the rules would have given.

From ecoinvent 3.10 to 3.12, every activity UUID changed: 0 of 20,768 carried over, so the same id maps nothing there. Product UUIDs carried over (4,026 of 4,031), and so did the dataset key, for 22,243 of the 23,522 datasets, with the same unit every time. ecoinvent's correspondence files are not read yet: their reader is written once the files are at hand, and the rule keeps its place with nothing to read.

**What stops the upgrade.** Nothing is written until every supplier maps and nothing else stops it:

- a line an override or a study's scenario changes that has no single line to move to, named with its study and scenario file. ecoinvent's line ids change with each version too, so a line is found by its role and flow, and by the successor of its supplier when several fit
- a flow of the old release that the new one does not hold

**Flags** are for reading, and never stop the upgrade:

- RoW: every link whose old or new supplier is in RoW, with the places that have a dataset of their own (the same name and product) before and after. RoW is the world without them, so a place added makes RoW smaller. Example: toy-2 adds `cotton fabric production | cotton fabric | IN`, so its RoW leaves out IN.
- Markets: a market whose suppliers changed, or one of whose shares moved by 1 point or more, with the suppliers added and removed and the largest share that moved.

**Written in place.** Only the cells that name the old release change. Every other record of a CSV keeps its text, and a YAML value is replaced where it stands, comments kept. So the pull request of an upgrade shows what moved, line by line, and nothing else.

**The diff across a release change.** The old database is scored on its release, the new one on its own. Between them is the bridge: the old database with only its suppliers moved to the new release, by the rules that need no person (the same id, the same dataset). A supplier with no such successor takes the one the new database buys from at the same line, and a line the new database no longer has is left out of the bridge.

- **What the release changed** is the bridge's score minus the old score. The database's processes and amounts are the same in both, so it splits exactly into one term per line: the amount the product needs, times the change of what one unit of the supplier scores, or of the factor of an emission. The terms add up to the change, with nothing left for interaction.
- **What the database changed** is the new score minus the bridge's, explained on the new release as any diff is ([workflows.md](workflows.md#command-line)): one difference at a time, then the interaction. A dataset a person chose instead of the one the rules give, such as a region instead of RoW, is part of it.

Example, the toy T-shirt from toy-1 to toy-2, with its fabric lowered from 0.25 to 0.22 kg in the same pull request: 1.2048 → 0.795. The release changed -0.3273: fabric -0.1875 (0.25 kg × (2.75 - 3.5), the same dataset), electricity -0.125 (the same id), dye -0.01, the factor of methane -0.0028, tape -0.002. The database changed -0.0825: line e02, 0.03 kg less at 2.75.

## File formats

### A release

A release is a folder of Parquet files and a `release.json`, named `<name>@<first 12 characters of its hash>`. It is written once and never edited. There are two kinds:

- **Source release:** one source, version and system model, imported once. Example: `ecoinvent-3.12-cutoff@9f2c1a0b3d4e`. Its hash covers every row.
- **Indicators release:** the solved score of every product of one source release, for one method. It pins its source release by hash and lives beside it, so adding a method never changes the source release or its hash. Example: `ecoinvent-3.12-cutoff.EF-3.1@4b7d0e9c2a11`. Its hash covers its recipe, not its scores: see below.

```
releases/ecoinvent-3.12-cutoff@9f2c1a0b3d4e/
  release.json            name, source, version, system model, licence, flow list,
                          methods, row count and digest of every table, release hash,
                          engine version, sha256 of every file
  processes.parquet       id, name, reference_flow_id, location, time_start, time_end,
                          kind (ecoSpold2 activity type), source_ref
  flows.parquet           id, name, kind (product | waste | elementary), unit,
                          compartment, cas
  flow_properties.parquet flow_id, property, value, unit
  units.parquet           unit, quantity, factor_to_reference
  exchanges.parquet       process_id, line_id, role, flow_id, amount (float64), unit,
                          supplier_id, link_status, uncertainty (struct),
                          pedigree (struct), properties (map), comment
  issues.parquet          every row resolved: linked, cut_off or data_gap
  geographies.parquet     code, name: the places the source names
  methods/EF-3.1/factors.parquet      indicator, flow_id, location, factor

releases/ecoinvent-3.12-cutoff.EF-3.1@4b7d0e9c2a11/
  release.json            the source's name, version, system model, licence and flow
                          list; parent (name and hash of the source release); the method
                          with its hash; recipe (engine version, solver settings);
                          provenance (steps of each solve, largest residual, exact zeros)
  indicators/EF-3.1.parquet           product_id, one float64 column per indicator
  methods/EF-3.1/factors.parquet      the method's factors, so the release stands alone
```

`kind` holds the ecoSpold2 special activity types, as snake_case names: `ordinary_transforming_activity`, `market_activity`, `market_group`, `production_mix`, `import_activity`, `supply_mix`, `export_activity`, `re_export_activity`, `correction_activity`, `residual_activity` and `io_activity`. Treatment is not a kind: a process is a treatment when its reference flow is a waste.

Columns:

- Amounts, factors, values and scores are float64. Pedigree scores are int8, because they are codes from 1 to 5, not quantities.
- `units.parquet` holds only the units the release uses.
- `link_status` is `linked`, `cut_off` or `data_gap` on a line that buys from a supplier, and blank on a line that buys nothing. A `cut_off` or `data_gap` line has no supplier and a resolved issue that says why.
- `uncertainty` is a struct with the stats_arrays names: `distribution` (`lognormal`, `normal`, `uniform`, `triangular`), `loc`, `scale`, `shape`, `minimum`, `maximum`. For lognormal, `loc` is the mean of ln|x| and `scale` its standard deviation.
- `pedigree` is a struct of five scores: `reliability`, `completeness`, `temporal_correlation`, `geographical_correlation`, `further_technological_correlation`.
- `properties` maps a property name, as the source writes it, to its `value` and `unit`. Example: `price` → 0.11 `EUR2005`.
- `product_id` is the id of the process that makes the product: each process makes exactly one.
- A database file's `flow_name` column is checked against the flow, then not stored. In a release, a flow's name is in `flows.parquet`.
- `resolved_at` in `issues.parquet` is UTC, stored with no time zone, so DuckDB reads it without pytz. Read back as a record it is UTC again.
- `geographies.parquet` has one row per place the source names: its short `code`, the one a process's `location` holds, and its `name`. Example: `RoW`, Rest-of-World. ecoinvent's come from `MasterData/Geographies.xml`, 541 in 3.12, and every location of its datasets is one of them. `Geographies.xml` has no containment: it does not say which region contains which, so the table has no parent regions. Containment comes in M3, as a separate table of codes and the regions that contain them. A release built without geographies has an empty table.

The `release.json` of the toy background, shortened:

```json
{
  "format": "headwater-release/2",
  "kind": "source",
  "name": "toy-background",
  "source": "toy",
  "version": "1",
  "system_model": "cutoff",
  "licence": "CC0-1.0",
  "flow_list": "toy",
  "methods": [{"id": "toy-climate", "version": "1", "variant": null, "flow_list": "toy",
               "indicators": [{"id": "climate-change", "name": "Climate change",
                               "unit": "kg CO2-eq"}],
               "hash": "<method hash>"}],
  "parent": null,
  "recipe": null,
  "provenance": null,
  "tables": {"exchanges.parquet": {"rows": 9, "content_sha256": "<table digest>"}, ...},
  "hash": "<release hash>",
  "engine_version": "0.1.0.dev0",
  "files": {"exchanges.parquet": "<sha256 of the file>", ...}
}
```

**The release hash is computed from rows, not from file bytes.** A Parquet file records the version of the library that wrote it, and its bytes change with compression and row groups. So the hash is defined on content:

1. **Table digest.** The table's rows are sorted by its key, ascending, in UTF-8 byte order, with nulls first. Keys are unique: building fails on a duplicate. The digest is the sha256 of:
   - the line `headwater-table/1`
   - the columns as JSON with no spaces, `[[name, type], ...]`, then a newline. Types are named `string`, `float64`, `int8`, `date32`, `timestamp[us]`, `struct<name:type,...>` and `map<key type,value type>`.
   - the row count, as an int64
   - each column in order: one validity byte per row (1, or 0 for null), then the values. A float64 is its 8 IEEE 754 bytes. An int8, a date32 (days) and a timestamp (microseconds since 1970, in UTC) are an int64 each. A string column is an int64 byte length per row, then the UTF-8 bytes of all rows. A struct is its fields in order, each as a column. A map is an int64 entry count per row, then its keys, then its values, each as a column. A null value counts as 0, or as an empty string.
   - every number is little-endian
2. **Release hash of a source release.** The sha256 of `release.json` without the keys `hash`, `engine_version` and `files`, written as JSON with sorted keys, no spaces and UTF-8. `tables` gives each table's row count and digest, so the hash covers every row and every field of metadata, and nothing about how the files were written.
3. **Release hash of an indicators release: its recipe.** The same, also without `tables` and `provenance`. What is left says how the scores are made: the parent's hash, the method with its hash, and `recipe`, which holds the engine version and the solver settings asked for. Example: `{"engine_version": "0.1.0", "solver": {"solver": "scipy.sparse.linalg.splu", "ordering": "MMD_AT_PLUS_A", "scaling": "...", "tolerance": 1e-12, "condition_limit": 1e10}}`. Two machines solving the same recipe get scores that differ in the last bits, and the same release. Whether the factors were pivoted (`diag_pivot_thresh`) is not in the recipe: the solver falls back to pivoting when rounding on that machine fails the residual check, so it is in `provenance`, with the steps of each solve, the largest residual and the exact zeros. The rows are still checked: each file's sha256 when the release is opened, and the digests in `tables` by `verify()`.
4. **Method hash.** The sha256 of the same kind of JSON holding `format` (`headwater-method/1`), `id`, `version`, `variant`, `flow_list`, the indicators sorted by id, and the digest of the method's factors.

| Table | Key |
|---|---|
| processes, flows | id |
| flow_properties | flow_id, property |
| units | unit |
| exchanges, issues | process_id, line_id |
| geographies | code |
| methods/&lt;id&gt;/factors | indicator, flow_id, location |
| indicators/&lt;method&gt; | product_id |

Map entries are sorted by name, and indicator columns by indicator id. So the hash does not change when the files are rewritten with other settings or another Parquet writer. The files themselves are written with sorted rows and fixed settings (zstd level 3, row groups of 131,072 rows), so one version of pyarrow always writes the same bytes. The sha256 of each file in `release.json` is an integrity check, not the identity of the release.

**Freezing** takes records (`build`) or Arrow tables with the columns above (`build_tables`), so an importer need not make a record per line. Records become those tables, and both are checked the same way. Freezing refuses, naming every problem:

- an id defined twice: a process, a flow, a flow's property, an issue, a geography's code, a method, or a line id within its process
- a row that is not a valid record. A table can hold what a record refuses, so the record's rules are applied to it, with the same codes: an amount that is not finite, a negative amount on a role other than emission or resource, a reference of 0, a process without exactly one reference line, a link status that does not fit the supplier, an uncertainty or pedigree out of range, a property given twice. A process's `reference_flow_id` must be its reference line's flow, and text must not have spaces around it, since a record strips them. Example: `toy:fabric-production line e02` with an amount of NaN is `invalid-field`.
- a line whose flow, unit or supplier is not in the release, or whose `flow_name` is not its flow's name
- a role that does not fit its flow's kind: an emission of `toy:cotton-fabric`, a product
- a role that has no sign in its process: an own use in a process that treats waste, whatever its link status. Example: a cut-off own use of 0.1 kg of fabric in `toy:incineration-textile-waste` is `role-no-sign`
- a supplier that does not make the flow bought from it, an own use bought from another process, or a supplier on a role that buys nothing
- a line in a unit of another quantity than its flow's, which no property of the flow converts: electricity bought in kg
- a line that buys from no supplier and is not resolved as `cut_off` or `data_gap`
- an issue that is still open, or that does not match its line
- a method mapped to another flow list
- for an indicators release: scores that are not exactly one finite float64 per process of the source release, for each indicator of the method, or an indicator called `product_id`

So a release of this format has passed the line checks, and `hw.load` does not make them again. As a guard it still refuses a line of a process the release does not hold, and an own use in a process that treats waste (`no-sign`). A release frozen before the sign check can hold the second. Freezing ecoinvent 3.12, checks included, took 6.4 s, the best of two runs at a load average of 4.8 ([benchmarks](appendix/benchmarks.md#import-of-ecoinvent)).

**Writing** goes to a hidden `.tmp-<name>-<random>` folder beside the target. The files are read back and checked against their digests, synced to disk, then the folder is renamed to `<name>@<hash12>` in one step. A crash leaves no release folder, at most a hidden temporary folder that nothing reads. Building a release that already exists keeps the existing folder. For an indicators release, the scores are compared first: the same recipe with a score more than 1e-9 apart, relative to the larger, is refused, since the engine changed without a new version.

**Opening** checks `release.json` against its own hash, the folder name against the hash, and the sha256 of every file, and refuses a file that is not listed. A release in an older format is refused with a message to build it again. Hidden files, such as `.DS_Store`, are ignored. `verify()` also recomputes every table digest from the rows. Tables are read as Arrow, or through a DuckDB connection with one view per table, named like the file without `.parquet`: `exchanges`, `"methods/EF-3.1/factors"`.

**The local cache** is `$HEADWATER_HOME/releases/`, by default `~/.headwater/releases/`. `pull(source)` takes a release folder or the URL of one, checks it and puts it there; pulling it again changes nothing. `find("toy-background@9f2c1a0b3d4e")` opens a cached release from its name and at least 12 characters of its hash.

**Pulling from a URL** reads through fsspec. The URL is the release's folder, whose last part is `name@hash12`, the folder name in the cache:

```
pull("/shared/releases/toy-background@9f2c1a0b3d4e")
pull("https://example.com/releases/toy-background@9f2c1a0b3d4e")
pull("gs://acme-lca/releases/toy-background@9f2c1a0b3d4e")
```

`file://` and `http(s)://` work in the base install. `gs://` needs `headwater[gcs]` and `s3://` needs `headwater[s3]`, which add gcsfs and s3fs; without them the error names the extra. `gcs://` and `s3a://` are aliases of `gs://` and `s3://`, and `memory://` is fsspec's in-process filesystem, which the tests use. Other schemes are refused. A pull goes in this order, and stops at the first problem with nothing left in the cache:

1. The URL is checked before anything is read. A user name, a password, a query or a fragment in it is refused (a signed URL is a credential), and the message does not repeat it. Credentials are the cloud's own login: application default credentials for `gs://`, an AWS profile for `s3://`. The 12 characters of hash in the URL are the pin.
2. `release.json` is downloaded into a hidden `.tmp-<name>-<random>` folder of the cache and checked against its own hash. Its full hash must start with the 12 characters in the URL, so a release that is not the one named is refused before any data is fetched. Nothing is listed on the server: a web server has no listing, and the manifest says which files the release holds.
3. If the cache already has the release, the temporary folder is removed and the cached one is opened. Only `release.json` was fetched.
4. Each listed file is downloaded in blocks. A file that is not there is `remote-file-missing`; a status, a timeout or a dropped connection is `remote-error`, both naming the URL.
5. The folder gets the checks of a folder pull: the sha256 of every file, then every table's rows against the digests in `release.json`. A changed file is refused. So are rows rewritten with a fixed sha256, because the digests are part of the hash.
6. An indicators release needs the source release it was solved from. If the cache has it, that is enough. If not, it is pulled from the folder beside the release, the same way. If it is not there either, the pull stops with `parent-missing`, naming it by name and full hash. This holds for folders as well as URLs, and the source release is checked before the indicators release is moved into place, so a bad indicators release leaves no source release behind.
7. The folder is renamed to `<name>@<hash12>` in one step. If another process pulled it first, the temporary folder is removed.

One release is pulled per call. `hw.load(url)` pulls the URL and loads the release from the cache, so a later `hw.load("toy-background@9f2c1a0b3d4e")` needs no network.

### A database

```
tshirt/
  database.yaml
  processes/tshirt.yaml    name, location, time and kind of the process
  processes/tshirt.csv     its exchanges
  flows.csv                optional: flows the database defines itself
  flow_properties.csv      optional: properties of those flows
  units.csv                optional: units the built-in table does not have
  parameters.yaml          optional: named values and formulas
  overrides/               optional: changed lines of background processes, any name.csv
  cut_offs.csv             optional: inputs left out on purpose
  no_factor.csv            optional: flows the method has no factor for, acknowledged
```

A process is a pair of files with the same name: a `.csv` for its exchanges and a `.yaml` for the rest. A `.csv` without its `.yaml` is an error, and so is a `.yaml` without its `.csv`. The process id is the database name and the file name: `tshirt:tshirt`.

A file or folder in the database folder that has the name of a known one but the wrong kind (`overrides` as a file), or a name close to a known one (`override/`, `cutoffs.csv`, `parameters.yml`), is an error, so a misspelt file is never ignored. Hidden files and notes (`.md`, `.txt`) are left alone.

`database.yaml`:

```yaml
name: tshirt
background: toy-background@<hash>
methods: [toy-climate]
```

- `name` is the start of every process id of the database, and the name of its folder when another database uses it.
- `background` pins the release the database buys from. Every database of one calculation pins the same release ([Databases it uses](#databases-it-uses)).
- `methods`, optional, lists the methods the database is scored with when no other is given, as by CI: `methods: [EF-3.1, IPCC-2021]` ([Several methods together](#several-methods-together)). A database with no methods is still read and validated, and `calc` scores it with the method the background was loaded with. A database that lists methods is refused on a background loaded with other ones.
- `uses`, optional, names the databases next to this one that it buys from: `uses: [dyes]`.
- `description`, optional, is text for people.
- `allocation` and `end_of_life`, optional, say how the database's multi-output processes are split and which end-of-life rule its processes follow: `allocation: {rule: physical, basis: mass}`, `end_of_life: Cut-off approach`. They are text for people: no calculation reads them.
- `cut_off`, only when `cut_offs.csv` has rows: the criteria and the largest share that may be left out in all, `cut_off: {criteria: [mass, energy], max_cumulative: 0.05}` ([Cut-offs](#cut-offs)).

Any other field is an error, a field of a study among them (`goal`, `functional_unit`, `reference_flow`). A folder that still holds a `study.yaml` of the format before D46, and no `database.yaml`, is refused with a message that says so.

`processes/tshirt.yaml`. `kind` is one of the activity types listed under [A release](#a-release), and `location` is an ecoinvent geography short code. Quote a code that YAML reads as yes or no, such as `'NO'` for Norway. `time_start` and `time_end` are optional ISO dates, for example `2024-01-01`. The name in results comes from this file, not from the file name.

```yaml
name: T-shirt
location: GLO
kind: ordinary_transforming_activity
```

`processes/tshirt.csv`, one process per pair of files, one exchange per line, sorted by line id:

```
line,role,flow,amount,unit,supplier
e01,reference,tshirt:tshirt,1,unit,
e02,input,toy:cotton-fabric,0.25,kg,toy:fabric-production
e03,input,toy:electricity,0.5,kWh,toy:electricity-production
e04,waste_to_treatment,toy:textile-waste,0.05,kg,toy:incineration-textile-waste
```

Optional columns: `flow_name`, a flow's name written next to its id (validation checks the two still match), `comment`, and `distribution`, `scale`, `minimum` and `maximum`, the spread of the amount ([Uncertainty](#uncertainty)). An `amount` is a number or a formula: see [Parameters and formulas](#parameters-and-formulas).

`flows.csv` has the columns `id,name,kind,unit`, and optionally `compartment` and `cas`. `flow_properties.csv` has `flow,property,value,unit`. `units.csv` has `unit,quantity,factor_to_reference`. A flow listed in the database's `flows.csv` can be used by its processes, and a flow of the background, or of a database it uses, can be used without being listed again.

With the toy background in [calculation.md](calculation.md), this database scores 1.25205 kg CO₂-eq (1.19205 without line e04).

The toy background is a folder of the same shape without `database.yaml`. The caller gives it the namespace `toy`, so its processes get ids such as `toy:fabric-production`.

#### Databases it uses

Example: the T-shirt buys 0.01 kg of reactive dye, and the dye is a process of the database `dyes`, in the folder next to the T-shirt's. Its `database.yaml` says `uses: [dyes]`, and its line names the dye's process id as supplier:

```
tshirt-dyed/
  dyes/                       name: dyes
    processes/reactive-dye.csv
  tshirt/                     name: tshirt, uses: [dyes]
    processes/tshirt.csv      e04,input,dyes:reactive-dye,0.01,kg,dyes:reactive-dye
```

This is the toy case `tests/fixtures/toy/tshirt-dyed`. Each toy case is a parent folder of its own, so the T-shirt of every case is the database `tshirt` in a folder `tshirt`.

The dye takes 2 kWh of electricity and emits 4 kg of CO₂ per kg, so it scores 2 × 0.5298 + 4 = 5.0596 per kg on the toy background, and the T-shirt 1.19205 + 0.01 × 5.0596 = 1.242646.

- An entry of `uses` is the name of a database folder next to this one, in the same parent folder. The database in it must have the name of its folder, so its process ids are `dyes:<file name>`, and is refused otherwise, naming both. There is no version: git versions both databases, and a change to `dyes` is a change of every database that uses it. A pinned form such as `textile-chemicals@v3` is refused. Frozen databases that other repositories pin come later (M4).
- A line may buy from a process of its own database, of a database its `uses` names, or of the background. A supplier from any other database is refused, naming the line, even one that comes along through another database.
- What a used database uses comes along. If `dyes` uses `chemicals`, the T-shirt's calculation has the processes of both, without the T-shirt listing `chemicals`. Each database is read once.
- A cycle, two or more databases that use each other (`dyes → chemicals → dyes`), is refused, naming every database in it. So is a database that uses itself.
- Every database of the chain pins the same background, written the same way, or it is refused, naming each pin. The method or methods the background is loaded with apply to every process of the chain: the `methods` of a used database count only when it is calculated itself.
- What travels with a used database: its processes, its overrides (its own copies of background processes, for its own lines, named `dyes:copy:<background id>`), its parameters (for its own formulas only), its cut-offs, listed apart on the result, and the rows of its `no_factor.csv`, for the flows of its own processes.
- A database holds no scenarios: a scenario is a study's, and changes a line of a used database as well as one of the database itself. An override cannot change a line of a used database: change the used database's own files.
- `validate` checks the used databases as part of the database: each problem is at its place in their folder, such as `../dyes/processes/reactive-dye.csv`. `contributions`, `where`, `sensitivity` and the results diff name a used database's process by its full id, `dyes:reactive-dye`, and its line as `dyes:reactive-dye line e03`, so a reader sees which database it belongs to.

#### Parameters and formulas

`parameters.yaml` has two sections, both optional. `database:` holds parameters that every formula of the database can use. `processes:` holds, for one process id, parameters that only that process can use. The process is a process of the database, or a background process that an override changes.

```yaml
database:
  fabric_kg:
    value: 0.25
    unit: kg
    comment: Fabric in one T-shirt, from the bill of materials
  loss: 0.05                          # a number is a value
  fabric_gross: fabric_kg * (1 + loss)    # any other text is a formula
processes:
  tshirt:tshirt:
    cut_ratio: {value: 0.1}
```

An entry is a number, a formula, or a mapping with `value` or `formula` (not both), and optionally `unit`, `comment` and a distribution of its value, with the keys of the columns below ([Uncertainty](#uncertainty)): `fabric_kg: {value: 0.25, distribution: lognormal, scale: 0.0953102}`. A text that is a number, such as `"0.30"`, is a number. Parameters can be written in any order: headwater works them out in dependency order. A loop is an error that names the parameters in it, for example `a → b → c → a`. A parameter name is letters, digits and underscores, and cannot be the name of a function or constant below, or a Python keyword. YAML turns an unquoted `yes`, `no`, `on` or `off` into true or false, so a value or a name written that way is an error that asks for quotes. A parameter of a process may not have the name of a parameter of the database.

A **formula** is Python-like arithmetic on parameter names. It reads like the formulas in Brightway's Excel files, so they can be copied over without translation.

| Part | Allowed |
|---|---|
| Numbers | `0.25`, `1e-3` |
| Names | parameters visible from where the formula is written |
| Operators | `+ - * / // % **` and parentheses. `^` is an error: write `**` for a power. |
| Choice | `a if x > 0 else b`, with `== != < <= > >=`, `and`, `or`, `not`. A comparison gives 1 or 0. |
| Functions | `abs`, `ceil`, `exp`, `floor`, `log` (natural, or `log(x, base)`), `log10`, `max`, `min` (two or more values), `round` (`round(x, digits)`), `sqrt` |
| Constant | `pi` |

Anything else is refused with a message that names it: text in quotes, attribute access (`a.b`), indexing, lists, lambdas, and unknown functions (with a suggestion). Formulas are read and computed by headwater's own evaluator, never by Python's. Every number is a float, so `10 ** 10 ** 10` is an error at once. A formula that divides by zero, gives a number too large for a float, or does not give a finite real number is an error that names the formula.

In a process CSV, a cell that is a number is a number, and any other text is a formula. Example: with `fabric_kg` above, line e02 of the T-shirt can say `fabric_kg` as its amount. The record keeps both: `amount` is what the formula gives with the values in `parameters.yaml` (0.25), and `formula` is its text (`fabric_kg`). Everything that reads amounts sees a number. A name that is not a parameter is an error with a suggestion. A decimal comma (`0,25`) is read as a mistake in a number, not as a formula. A process folder without a database, such as a background, has no parameters, so its amounts are numbers.

#### Uncertainty

Example: the T-shirt's 0.25 kg of fabric varies by about 10% from one batch to the next. Its line says so in four more columns of the process file:

```
line,role,flow,amount,unit,supplier,distribution,scale,minimum,maximum
e02,input,toy:cotton-fabric,0.25,kg,toy:fabric-production,lognormal,0.0953102,,
e03,input,toy:electricity,0.5,kWh,toy:electricity-production,,,,
```

`hw.monte_carlo` draws the line in each iteration ([calculation.md](calculation.md#uncertainty)); `calc` uses the amount. The amount is always the centre of its distribution, so the centre is never written:

| `distribution` | Centre | Needs | Example, around 0.25 kg |
|---|---|---|---|
| `lognormal` | the median | `scale`, the natural log of the geometric standard deviation (GSD) | GSD 1.1: `lognormal,0.0953102,,` |
| `normal` | the mean | `scale`, the standard deviation | `normal,0.02,,` |
| `triangular` | the mode | `minimum` and `maximum` | `triangular,,0.2,0.3` |
| `uniform` | | `minimum` and `maximum`, around the amount | `uniform,,0.2,0.3` |

- These are the names of stats_arrays, which Brightway uses, and of the release's `uncertainty` column ([A release](#a-release)). The record holds `loc` too, worked out from the amount: ln 0.25 for the lognormal above.
- Blank cells mean no distribution: the line is fixed. A file may leave the four columns out.
- Refused, each naming the line: a cell without a distribution, an unknown distribution (with the closest name), a missing cell the distribution needs or a cell it does not use, a `scale` of 0 or below, an amount outside `minimum` and `maximum`, a lognormal on an amount of 0, and a line whose amount is a formula. A formula's spread comes from its parameters: give the distribution to them.
- A parameter takes a distribution with the same keys, around its `value`. A parameter that is a formula cannot have one.
- An override row takes the same columns. A row that keeps the line's amount and unit and gives no distribution changes only its supplier: the line keeps the original's distribution.
- A scenario that sets a line's amount or unit fixes it, and one that sets a parameter fixes the parameter. A scenario that changes only the supplier keeps the line's distribution.
- `headwater diff` lists a change of distribution as a changed field: `uncertainty none → lognormal scale 0.1`. It moves no score.

#### Overrides

An override file is a CSV in `overrides/`. The file name is free, for example `overrides/wind-power.csv`. The process id is in each row, because ids contain colons, which Windows cannot have in a file name.

```
process,line,role,flow,amount,unit,supplier,comment
toy:electricity-production,e02,emission,toy:co2,0.3,kg,,Wind contract: 0.3 kg CO2 per kWh
```

- Each row is the whole new line, as written in a process file, distribution columns included ([Uncertainty](#uncertainty)). A blank supplier means no supplier.
- A row may change the amount (a number or a formula), the unit and the supplier of a line. The `role` and `flow` must be those of the background line. They are written so a reviewer sees what the line is, and an override that changes them is an error.
- A row changes a line that exists. It cannot add or remove a line, or change the reference line. To remove an input, set its amount to 0.
- A line is overridden once, in one row of one file.
- An override changes a process of the background. A process of the database is changed in its own file.
- Validation checks each row against the background: the process exists, the line exists and has the same role and flow, the unit converts, and the supplier makes the flow. An unknown process or line is an error with a suggestion. `database.validate` and `database.calc` read every line of the process, from the folder or from the release's exchanges table. `validate` given the records of a release, which hold only the reference line of each process, gives a row naming another line the warning `line-not-checked`, and checks it only for its own flow, unit and supplier.

The file `overrides/wind-power.csv` above, on the toy background, gives the T-shirt 1.09205. The row `toy:fabric-production,e02,input,toy:electricity,2.5,kWh,toy:electricity-production,` is a local override of the fabric at 2.5 kWh per kg. On its own it gives 1.125825, and as a second row beside the wind-power one 1.025825.

#### Cut-offs

```
process,line,flow,criterion,share,reason,who,when
tshirt:tshirt,e04,tshirt:packaging-tape,mass,0.0385,No tape in the background: 0.01 kg of 0.26 kg of mass input,leo,2026-10-11
```

- The line is an `input` or `waste_to_treatment` line of a process of the database, and its supplier is blank in the process file. The `flow` is that line's flow.
- `criterion` is one of the criteria of `cut_off` in `database.yaml`. A `cut_offs.csv` with rows needs that `cut_off`, and is refused without it. A `cut_off` with no rows is a warning: it says nothing.
- `share` is between 0 and 1, the author's estimate of the share of that criterion. `reason`, `who` and `when` are required. `when` is an ISO date or time. A time with a zone is stored in UTC without one.
- The cumulative share is not written: headwater works it out by criterion, in the order of the lines, and checks it against `max_cumulative`. The row above needs a `max_cumulative` of at least 0.0385 in `database.yaml`.
- A line is cut off once. The `cut_offs.csv` of a database with no cut-offs may hold only the header, or be left out, and its `database.yaml` has no `cut_off`.

Reading a database turns each row into an issue with the status `cut_off`, and marks the line as cut off, so a calculation skips it.

#### Flows with no factor

Example: a dyeing process emits 0.002 kg of COD to river, and EF 3.1 gives COD no factor in any indicator. Brightway counts it 0 and says nothing. headwater refuses the database, in `validate` and in `calc`, with the same `no-factor` error naming the flow and the database's processes that emit it. Once a person has checked that 0 is right, one row of `no_factor.csv` says so:

```
flow,flow_name,method,reason,who,when
tshirt:cod,"COD, Chemical Oxygen Demand",toy-climate,not characterised in toy-climate,Sam,2026-10-12
```

- `flow` is the flow's id, as a process file gives it, and `flow_name`, which is optional, is its name: validation checks the two still match, as for a process file. `method` is the method's id.
- `reason`, `who` and `when` are required, as in `cut_offs.csv`: `when` is an ISO date or time, and a time with a zone is stored in UTC without one.
- With the row, the flow counts 0, as in Brightway, and `result.not_characterised` lists it as the database's own, with the reason.
- A row holds for one method, and a flow is written once per method. When the database moves to a new method, each flow the new method does not cover needs a row for that method. A database that uses several methods together needs a row, naming one of them, only for a flow that has no factor in any of them ([Several methods together](#several-methods-together)).
- A row that is not needed is a warning, "row not needed", never an error, so old rows never block a new method. That is a row for a flow that has a factor in the method (in one of the methods used together), for a flow no process of the database emits or takes, or for a method the database does not use. The `flow_name` of such a row is not checked.
- Flows of the background need no row: those the method does not cover are listed on the result and are never an error, because ecoinvent has many.
- The `no_factor.csv` of a database with none may hold only the header, or be left out.
- A study may add rows for its methods, for a flow of any database its products reach, in its own `no_factor.csv` ([A study](#a-study)): the database is then valid for that study only.

The toy T-shirt with 0.002 kg of COD to water, `tshirt:cod`, is refused on toy-climate without the row above, and scores 1.19205 with it.

#### Editing from Python

Example: five calls build the toy T-shirt, and the files they write are `tests/fixtures/toy/tshirt/tshirt`, byte for byte ([calculation.md](calculation.md#building-and-editing-a-database) has every call):

```python
database = hw.Database.new("tshirt", bg, pin="toy-background@fixture", methods=["toy-climate"])
tshirt = database.new_process("tshirt", name="T-shirt", location="GLO", product="T-shirt", unit="unit")
tshirt.new_exchange("input", "toy:cotton-fabric", 0.25, "kg", supplier="toy:fabric-production")
tshirt.new_exchange("input", "toy:electricity", 0.5, "kWh", supplier="toy:electricity-production")
database.save()
```

- **The calls change the database's own files**: what is in git is what the database is ([D4](decisions.md)).
- **Nothing is written until `save()`.** `save` writes the changed files into a copy of the folder, reads the copy as `hw.open` does, and checks it against the background as `validate` does, with the databases it uses. It also checks the databases next to it that use it, since a change to `dyes` is a change of `tshirt-dyed`. Only then are the files moved into place. An edit that makes any of them invalid raises `InvalidDataError` naming the problem, and writes nothing. A file that changed on disk after the editor read it is refused too, so a hand edit is never overwritten. A call that names something that is not there, such as a line or a background process, raises `ValueError` at once.
- **The files look hand-written.** A new file is written as the toy fixtures are: the columns it needs, and a number as short as it reads back (`1`, not `1.0`). The Excel import writes its files the same way. In a file that exists, only the changed rows are written again, and every other row keeps its text, quotes and line endings. A YAML entry is replaced on its own lines, so comments stay. A file nothing changed is not written, and setting a value to the one it has is no change, so a database saved with no change is the same byte for byte.
- **A copy of a background process is a local override** ([Overrides](#overrides)): `database.override("toy:electricity-production").set_exchange("e02", amount=0.3)` writes a row of `overrides/`. The row is the whole line, so its role, flow, unit and supplier come from the background's line. A new row goes to `overrides/<process id with hyphens for colons>.csv`, or the file `file=` names, and a row that exists stays in its file.
- **A new database needs only its folder and its background.** `Database.new(path, bg)` writes `database.yaml` with the folder's name and the release `bg` was read from, and no methods unless `methods` names them. `pin` names the release for a background read from a folder, which has no hash: `pin="toy-background@fixture"`. `name`, `methods`, `uses` and `description` may be given, and `editor.set(...)` changes them later. A field that is not set, or a list that is empty, is not written, so the file says only what is.
- **Nothing cascades.** Removing a line that a cut-off names, or a process that another line buys from, is refused, naming the rows. A flow is not removed with its process.
- Scenarios, `no_factor.csv`, `flow_properties.csv` and `units.csv` are written by hand. The calls leave them as they are.

### A study

```
tshirt-dyed/
  dyes/                       a database
  tshirt/                     a database, uses: [dyes]
  tshirt-footprint/
    study.yaml
    scenarios/                optional: named sets of changes, <name>.yaml
    no_factor.csv             optional: flows of the databases it reaches, acknowledged
```

`study.yaml`:

```yaml
name: tshirt-footprint
goal: Footprint of one T-shirt, internal use, not a public comparison
functional_unit: 1 T-shirt
products:
  - {process: tshirt:tshirt, amount: 1}
boundary: cradle-to-gate
methods: [toy-climate]
cut_off: {criteria: [mass, energy], max_cumulative: 0.03}
```

- `products` lists each product once: the id of the process that makes it, and how much of what it makes, a number above 0. Each is scored for its amount.
- `methods` are the methods the study is scored with, one or more: `[EF-3.1, IPCC-2021]` ([Several methods together](#several-methods-together)). A background loaded with others is refused.
- `cut_off` is the criteria, and the largest share that may be left out in all. A database a product reaches whose `cut_off` allows more is refused, naming it: the toy database of the cut-off case allows 0.05, so a study that allows 0.03 refuses it.
- `background:` pins the release when every product is a process of it, and is refused when a product is in a database.
- Optional: `audience`, `public_comparison`, `data_quality`, `pcr`, `assumptions` and `limitations`, text no calculation reads.
- A study holds no processes: `processes/` or any other file of a database in a study folder is an error, and so is a folder that holds both database.yaml and study.yaml.

A study's own `no_factor.csv` has the columns of a database's ([Flows with no factor](#flows-with-no-factor)). A row names one of the study's methods and a flow of a database the study reaches, and acknowledges it for this study only. A row no database needs is a warning.

The two toy products in `tshirt-dyed/tshirt-and-dye`, 2 T-shirts and 0.5 kg of dye, score 2.485292 and 2.5298.

#### Scenarios

A scenario is the file `scenarios/<name>.yaml` of a study. The name is the file name, letters, digits, dots, underscores and hyphens.

```yaml
description: The grid emits 0.3 kg CO2 per kWh instead of 0.5, for every process that buys electricity.
changes:
  - process: toy:electricity-production
    line: e02
    amount: 0.3
  - process: tshirt:tshirt
    line: e03
    supplier: toy:electricity-production
  - parameter: fabric_kg
    value: 0.30
  - parameter: cut_ratio
    process: tshirt:tshirt
    value: fabric_kg / 3
```

- A **line change** has `process` and `line`, and at least one of `amount` (a number or a formula), `unit` and `supplier`. A change to a process of a database the products reach, their own or one they use, is made for every product that reaches it. A change to a process of the background is global: it changes that process for every process that buys from it, in this scenario. A change to a database next to the study that no product reaches is refused.
- A **parameter change** has `parameter` and a `value` (a number or a formula). `process` names the process of a parameter that belongs to one process. `database` names the database that holds the parameter: it may be left out when one database the study reaches has a parameter of that name, and is needed when several do.
- A formula in a scenario uses the parameters of the database that holds the line, as the scenario changes them. A formula on a background line is refused when the study reaches several databases: write the number.
- A line or a parameter is changed once per scenario. A scenario cannot change a reference line, or a line that an override of a database it reaches also changes.
- Reading the study works out every formula amount of the databases again with the scenario's parameters. A scenario that makes an amount negative, or divides by zero, is an error that names the scenario and the line.
- Validation checks each line change against its process, in a database or in the background, like an override row, with the same warning `line-not-checked` when only the reference line of a release's process is known.

On the toy background, the scenario `clean-grid` (the first change only) gives the T-shirt 0.94205, a scenario that sets `fabric_kg` to 0.30 gives 1.37748, and `cleaner-dye` of `tshirt-footprint`, which changes a line of the used database `dyes`, gives 1.237646.

#### A study in Python

A study can live in memory, with only its products and methods: `hw.Study(products={"tshirt:tshirt": 1}, methods=["toy-climate"], databases="tshirt-dyed")`. `databases` is the folder that holds the database folders of its products; without it every product is looked for in the background. Its scenarios are given as their files say them, `scenarios={"cleaner-dye": {"changes": [{"process": "dyes:reactive-dye", "line": "e03", "amount": 3.5}]}}`. It scores as the same study saved, with the same input hash.

`study.save(path, goal=..., functional_unit=..., boundary=..., cut_off=...)` writes study.yaml and its scenario files into `path`, next to the databases: `path`'s parent must be the `databases` folder. The goal and scope fields are asked for at `save`, since study.yaml requires them. The files are written to a copy, read back as `hw.open` reads them, and moved into place only then.

#### Schemas

`headwater schema FOLDER` writes `FOLDER/.schemas/database.schema.json`, `study.schema.json` and `scenario.schema.json`, from the models that read the files. An editor that reads JSON schemas completes keys and values with them: a first line such as `# yaml-language-server: $schema=../.schemas/study.schema.json` points at one. A study's products and a scenario's processes complete from the process ids of the databases in FOLDER, as suggestions: a process of the release still validates. `headwater validate` is the check; a schema only helps write the file.

### A method

```
methods/toy-climate/
  method.yaml     id, version, variant, flow_list, indicators
  factors.csv     indicator, flow, location, factor
```

A method folder is the contract between a source of factors and headwater. Whatever the source, its reader writes this folder, and everything after the import reads it by flow id.

- `factors.csv` has one row per factor: the indicator's id, the flow's id, a location and the factor, a float64. The location is blank: a factor for one location is refused, as regionalised factors are not in v1.
- `method.yaml` gives the method's id, version, variant and flow list, and each indicator's id, name, category indicator and unit. An indicator with no category indicator has no such key, here, in `release.json` and in the method's hash. An importer writes comments at its top that say where the factors came from and which categories it left out.
- A release holds the same data as Parquet: the method in `release.json`, its factors in `methods/<id>/factors.parquet`.
- Methods used together stay separate folders ([Several methods together](#several-methods-together)).

The toy method `toy-climate` is written by hand. The method of a database's background is imported, as `methods/EF-3.1` is from ecoinvent's workbook.

### A normalisation and weighting set

```
methods/toy-nw/
  set.yaml        id, version, the method it pins (id, version, hash), unit, source
  factors.csv     indicator, normalisation, weight
```

A person writes `factors.csv` from the source of the numbers, one row per indicator of the method:

```
indicator,normalisation,weight
climate-change,10.0,0.6
ozone-formation,0.01,0.4
```

and `headwater method import-set` checks it against the method and writes the folder beside the method's ([workflows.md](workflows.md#command-line)). `set.yaml`:

```yaml
id: toy-nw
version: '1'
method:
  id: toy-impacts
  version: '1'
  hash: bdc4e5667236ebb5e84a94a1ce947966bf780a3126598adaf2ce4248d7cc6832
unit: Pt
source: Made up for the toy, so every number can be checked by hand
```

- A normalised score is the score divided by `normalisation`, as openLCA and JRC give it. SimaPro writes the inverse, 1 / normalisation, and a weight given in percent, such as 20%, is written 0.2.
- A weighted score is the normalised score times `weight`, and the single score is the sum of the weighted scores. Their unit is `unit`. A normalised score has no unit.
- Every row has a normalisation, or none has, and the same for weights, so the single score never adds scores of different kinds. Without normalisation the scores are weighted as they are, as SimaPro allows. Without weights there are normalised scores only, and no `unit`.
- A normalisation is a finite number above 0, and a weight a finite number of 0 or more. An indicator is written once.
- A row for an indicator the method does not have is an error naming it. A method's indicator with no row counts in none of the set's scores, and is listed on every result of the set: EF 3.1's climate sub-indicators are part of its climate change, so its set leaves them out.
- The set pins its method by the method's hash, as an indicators release pins its parent. Loading a set with a method of the same id but other content is an error naming both hashes. Adding a set never changes the method's folder, its hash or a release.
- The set's hash covers every field of `set.yaml` and every row, in any order. A result of the set carries it, and it is part of the result's input hash.
- The indicators a set adds are named by its id: `toy-nw:normalised:climate-change`, `toy-nw:weighted:climate-change` and `toy-nw:single-score`, named `Climate change, normalised (toy-nw)` and so on. They come after the method's, normalised first, then weighted, then the single score, each in the order of the method's indicators.

## Out of scope for v1

Regionalised characterisation factors, consequential system models, time-dependent (dynamic) LCA, a GUI, a web API, and editing without git. Each has a planned slot in [roadmap.md](roadmap.md).
