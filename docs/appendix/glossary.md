# Glossary

The words headwater uses, in plain words, each with one example. Most examples use the toy T-shirt of the demo: 0.25 kg of cotton fabric and 0.5 kWh of electricity, 1.19205 kg CO₂-eq ([how it works](../how-it-works.md)). Where Brightway has another word, it is given.

## headwater terms

| Term | Meaning | Example |
|---|---|---|
| Background | The shared database your own processes buy from, with the score of every product solved once when it is loaded. Brightway has no separate word: it is a database. | ecoinvent 3.12 cut-off; the demo's three toy processes |
| Release | A background frozen once: a folder of Parquet files named by the hash of its content, which never changes. Imported with `headwater release import`, shared with `headwater release pull`. | `toy-background@6864b7dbb65d` |
| Pin | The `background: name@hash` line of a `database.yaml`: the release the database was written for. It is refused on any other. | `background: toy-background@6864b7dbb65d` |
| Database | A folder of the processes you model yourself, in git: `database.yaml`, `processes.csv`, `exchanges.csv` and `flows.csv`, plus optional files. Every process of a database is a product. Brightway's foreground database. | the demo's `project/tshirt` |
| Study | A folder next to databases, with a `study.yaml`, that asks a question of them: which products, how much of each, with which methods, and its scenarios. It holds no processes. | the demo's `project/tshirt-footprint`: 1 dyed T-shirt, 1.242646 |
| Scenario | A named what-if of a study: a file `scenarios/<name>.yaml` with a list of changes to lines or parameters, of its databases or of the background. The files of the databases do not change. | `cleaner-dye`: the dye emits 3.5 kg CO₂ per kg, not 4, so the T-shirt is 1.237646 |
| Override | A database's change to one background process, for that database's own purchases only: a CSV file in its `overrides/` folder, one row per changed line. The database gets its own copy of the process. | a wind contract: electricity emits 0.3 kg CO₂ per kWh for the T-shirt's own 0.5 kWh, so 1.09205 |
| Uses | The `uses:` list of a `database.yaml`: the databases next to it that it buys from. Git versions both, so there is no version to pin. | `tshirt-dyed` has `uses: [dyes]` and buys 0.01 kg of `dyes:reactive-dye` |
| Process | Something that turns inputs into an output: one row of `processes.csv`, with its id, name, location and kind. Brightway's activity. | `fabric-production`, cotton fabric production, GLO |
| Process id | The text that names a process for good: the database's name and the row's `id`, or, in a release, the source, version and dataset. Never a row number. | `tshirt:tshirt`, `toy:fabric-production`, `ecoinvent:3.12:cutoff:<activity uuid>_<product uuid>` |
| Line | One row of `exchanges.csv`: an amount of a flow that a process makes, buys or emits, with its own id. Brightway's exchange. | `tshirt,e02,input,toy:cotton-fabric,0.25,kg,toy:fabric-production` |
| Role | What a line means: `reference`, `input`, `emission`, `waste_to_treatment` and a few more. Amounts are positive, and the role decides the sign in the matrix. | `e02` is an `input`, Brightway's technosphere exchange |
| Flow | A thing that moves: a product, a waste or an elementary flow. A database lists its own in `flows.csv`. | `toy:cotton-fabric` (product), `toy:co2` (elementary) |
| Supplier | The process a line buys from, by its id, in the `supplier` column. An input with no supplier is an error, unless it is a cut-off. | `toy:fabric-production` for the fabric |
| Parameter | A named value or formula in `parameters.yaml`, that a line's amount can use instead of a number. | `fabric_kg = bag_area_m2 * fabric_kg_per_m2`, 0.5 × 0.3 = 0.15 kg |
| Cut-off | A line left out on purpose: a row of `cut_offs.csv` with the line, the criterion, its share, the reason, who and when. `cut_off` in `database.yaml` caps the shares in all. | sewing thread, 1.32% of the mass, with `max_cumulative: 0.03` |
| no_factor | A database's own flow that the method does not count, recorded as a row of `no_factor.csv` with the reason, who and when. Then it counts 0 and the result lists it. Without the row it is an error. | nitrous oxide under a method that only counts CO₂ and methane |
| Method | A set of characterisation factors for one or more indicators, with an id and a version. A release holds its methods. | `toy-climate`, EF 3.1 |
| Indicator | One impact category of a method, with its unit. Every score is for one indicator. | `climate-change`, in kg CO₂-eq |
| Normalisation and weighting set | Factors that divide each indicator's score by a reference and weigh it, as SimaPro and openLCA do. | the toy set `toy-nw`: climate change ÷ 10 × 0.6 |
| Single score | The weighted scores of a set added into one number, in Pt. It is one more indicator, so it needs no new solve. | the T-shirt, 0.161523 Pt with `toy-nw` |
| Contributions | What a score is made of: its direct emissions plus amount × stored score of each purchase, as a tree. `contributions` in the API and the command line. | fabric 0.25 × 3.7086 = 0.92715, 77.8% of the T-shirt |
| Where | Which processes emit a score, wherever they are upstream, and in which locations. `where` in the API and the command line. | electricity runs 1.25 kWh for one T-shirt and emits 0.66225, 55.6% |
| Sensitivity, elasticity | How much a score changes per unit (sensitivity) or per 1% (elasticity) change of one line. `sensitivity` ranks every line by elasticity, as ISO 14044 calls the analysis. | 1 more kWh per kg of fabric adds 0.13245 to the T-shirt |
| Provenance | What a result was computed from, and how: the release and its hash, the method, the input hash, the engine version, the solver settings, the residual. `result.provenance`. | `pin: toy-background@6864b7dbb65d` |
| Input hash | A sha256 of every input of a result: the products and amounts, methods, the records of the databases it reaches, the scenario and the background. Two results with the same input hash were computed from the same data. | the T-shirt with 0.25 kg and with 0.30 kg of fabric have different input hashes |
| Issue | Something unresolved in a release or a database, and how it was resolved. | an input with no supplier, resolved as a cut-off |
| Mapping | A reviewed match between two ids, kept in a CSV file, with who and when. | a workbook's line to a release process, or an ecoinvent 3.10 id to its 3.12 id |
| Indicators release | A release that holds the solved score of every product of another release, for one or more methods, and pins it by hash. | the EF 3.1 scores of ecoinvent 3.12 |
| Demo | The toy release, four databases and one study that ship in the package. `headwater demo FOLDER` copies them. | `releases/` and `project/` in the folder |

## LCA terms

| Term | Meaning | Example |
|---|---|---|
| Life cycle assessment (LCA) | Adding up the environmental impacts of a product across its supply chain | the CO₂ of a T-shirt, from cotton farming to the factory gate |
| Product (reference flow) | What a process makes, in one unit | 1 kg cotton fabric |
| Elementary flow | Something taken from or released to nature | CO₂ to air, water from a river |
| Foreground | What a practitioner models themselves: in headwater, databases | the T-shirt factory |
| Market | A dataset that mixes the suppliers of one product in one region, with transport and losses | market for electricity, low voltage, India |
| GLO, RoW | Global; "rest of world", meaning the world minus the regions that have their own dataset | |
| System model | How a database handles recycling and multi-output processes. Cut-off, APOS and consequential are separate releases. | ecoinvent 3.12 cut-off |
| Allocation | Splitting the impacts of a process that makes several products | a cotton gin making fibre and seed |
| Substitution (avoided product) | Crediting a process for a product it displaces | exported electricity replacing grid power |
| Characterisation factor | How much one flow counts in one indicator | 1 kg methane = 29.8 kg CO₂-eq |
| Functional unit | What a study's result is per | 1 T-shirt |
| System boundary | Which life cycle stages are included | cradle-to-gate |
| Data gap | An input with no data available, reported as such | |
| Pedigree matrix | ecoinvent's five data-quality scores per exchange, used to widen uncertainty | |
| DQR | PEF's data quality rating per dataset | |
| PEF | The EU's Product Environmental Footprint method | |

## Math terms

| Term | Meaning | Example |
|---|---|---|
| Technosphere matrix (A) | One column per process, one row per product. The reference amount is on the diagonal; inputs are negative. | fabric's column: 1 kg fabric, −3 kWh electricity |
| Biosphere matrix (B) | One column per process, one row per elementary flow. | fabric's column: 2.0 kg CO₂, 0.004 kg methane |
| Supply (s) | How much each process has to run to make what you asked for. | 1.25 kWh of electricity for one T-shirt |
| Score vector (x) | The score per unit of every product, for one indicator. | electricity 0.5298, fabric 3.7086 |
| Direct emissions (h) | A process's own emissions, already converted to one indicator. | fabric 2.0 + 0.004 × 29.8 = 2.1192 |
| Forward solve | Solving `A s = f` for one product at a time. | gives `s`, for `where` |
| Transposed solve (adjoint) | Solving `Aᵀ x = h` for one indicator at a time, which gives every product's score at once. | gives `x`, for every score |
| Factorization (LU) | Splitting A into two triangular matrices once, so later solves take milliseconds. | |
| Pivoting | Reordering rows during factorization for numerical safety. Slower; often not needed for LCA matrices. | |
| Scaling (equilibration) | Dividing rows and columns so their largest entries are 1, before factorizing. | |
| Residual | How far a computed result is from satisfying the equations: the accuracy check. A result above the tolerance is an error. | |
| Rank-1 update (Sherman-Morrison) | Updating every score after one column of A changes, with one extra solve instead of a new factorization. Rank k (Woodbury) for k columns. | a scenario that changes one process |
| Strongly connected group | A set of processes where each is, directly or indirectly, a supplier of every other: a loop. | electricity needs steel, steel needs electricity |
| Monte Carlo | Running the calculation many times with uncertain amounts drawn at random, to see the spread of results. Every item of a run shares the same draws. | 1,000 draws of the backpack of notebook 03 |
