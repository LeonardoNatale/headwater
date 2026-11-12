# Glossary

Terms used in these docs, in plain words.

## LCA terms

| Term | Meaning | Example |
|---|---|---|
| Life cycle assessment (LCA) | Adding up the environmental impacts of a product across its supply chain | the CO₂ of a T-shirt, from cotton farming to the factory gate |
| Process (activity) | Something that turns inputs into an output | fabric production |
| Product (reference flow) | What a process makes, in one unit | 1 kg cotton fabric |
| Exchange | One line of a process: an amount of a flow going in or out | 3 kWh electricity in |
| Elementary flow | Something taken from or released to nature | CO₂ to air, water from a river |
| Background | Large shared databases a study builds on | ecoinvent |
| Foreground / study | What a practitioner models themselves | the T-shirt factory |
| Market | A dataset that mixes the suppliers of one product in one region, with transport and losses | market for electricity, low voltage, India |
| GLO, RoW | Global; "rest of world", meaning the world minus the regions that have their own dataset | |
| System model | How a database handles recycling and multi-output processes. Cut-off, APOS and consequential are separate databases. | ecoinvent 3.12 cut-off |
| Allocation | Splitting the impacts of a process that makes several products | a cotton gin making fibre and seed |
| Substitution (avoided product) | Crediting a process for a product it displaces | exported electricity replacing grid power |
| Characterisation factor | How much one flow counts in one impact category | 1 kg methane = 29.8 kg CO₂-eq |
| Impact method | A set of characterisation factors for several categories | EF 3.1 |
| Indicator | One category or sub-category of a method | climate change, fossil |
| Functional unit | What the study's result is per | 1 T-shirt |
| System boundary | Which life cycle stages are included | cradle-to-gate |
| Cut-off | An input left out on purpose, with a recorded reason and share | packaging tape under 0.1% of mass |
| Data gap | An input with no data available, reported as such | |
| Pedigree matrix | ecoinvent's five data-quality scores per exchange, used to widen uncertainty | |
| DQR | PEF's data quality rating per dataset | |
| PEF | The EU's Product Environmental Footprint method | |

## Math terms

| Term | Meaning |
|---|---|
| Technosphere matrix (A) | One column per process, one row per product. The reference amount is on the diagonal; inputs are negative. |
| Biosphere matrix (B) | One column per process, one row per elementary flow. |
| Supply (s) | How much each process has to run to make what you asked for. |
| Score vector (x) | The score per unit of every product, for one indicator. |
| Direct emissions (h) | A process's own emissions, already converted to one indicator. |
| Forward solve | Solving `A s = f` for one product at a time. |
| Transposed solve (adjoint) | Solving `Aᵀ x = h` for one indicator at a time, which gives every product's score at once. |
| Factorization (LU) | Splitting A into two triangular matrices once, so later solves take milliseconds. |
| Pivoting | Reordering rows during factorization for numerical safety. Slower; often not needed for LCA matrices. |
| Scaling (equilibration) | Dividing rows and columns so their largest entries are 1, before factorizing. |
| Residual | How far a computed result is from satisfying the equations. The accuracy check. |
| Sensitivity, elasticity | How much the score changes per unit (sensitivity) or per 1% (elasticity) change of one input. |
| Rank-1 update (Sherman-Morrison) | Updating every score after one column of A changes, with one extra solve instead of a new factorization. Rank k (Woodbury) for k columns. |
| Strongly connected group | A set of processes where each is, directly or indirectly, a supplier of every other: a loop. |
| Monte Carlo | Running the calculation many times with uncertain inputs drawn at random, to see the spread of results. |

## headwater terms

| Term | Meaning |
|---|---|
| Release | A frozen, hashed set of Parquet files, such as a background database. Brightway's nearest word is a database. A study frozen into one, for other repositories to pin, comes in M4. |
| Indicators release | A release that holds the solved score of every product of a release, for one or more methods, and pins that release by hash. |
| Pin | The `background: name@hash` line of a study: the release it was written for. A study is refused on any other. |
| Study | A folder of YAML and CSV files in git, modelling one product system. Brightway's nearest word is a foreground database. |
| Process id | The text that names a process for good, such as `ecoinvent:3.12:cutoff:<activity uuid>_<product uuid>` in an ecoinvent release, or `<study>:<file name>` in a study. |
| Line | One exchange of a process, one row of its CSV file, with a line id such as `e02`. |
| Role | What a line means (reference, input, waste to treatment, ...), from which its sign is derived. |
| Sensitivity analysis | Every line ranked by how much it moves a score, by its sensitivity and its elasticity: `sensitivity` in the API and the command line, as ISO 14044 calls it. |
| Contribution analysis | What a score is made of: direct emissions plus amount × score of each purchase, as a tree: `contributions` in the API and the command line. |
| Provenance | What a result was computed from and how: release and method hashes, solver settings, the residual. |
| Issue | Something unresolved in a release or study: no supplier, unknown unit, missing factor. |
| Mapping | A reviewed match between two ids, for example between ecoinvent versions. |
| Override | A study's change to one background process, stored as changed lines only. |
| Scenario | A named set of changes applied on top of a background, without merging them. |
