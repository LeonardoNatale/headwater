# Workflows

How people use headwater day to day. The "Available" column of the operations table says what exists today; the rest is the intended interface.

## Setup

### Anyone, with no licence

```bash
pip install headwater
headwater demo headwater-demo
cd headwater-demo
headwater release pull releases/toy-background@6864b7dbb65d
headwater calc studies/tshirt-footprint
```

`headwater demo` copies the toy background as a release, four small databases and a study, which ship in the package. The study scores 1.242646 kg CO2-eq. Python 3.13 or later.

### Anyone with an ecoinvent licence

```bash
pip install headwater
headwater release import ~/ecoinvent/3.12_cutoff_ecoSpold02 --version 3.12
headwater method import "$HOME/ecoinvent/LCIA Implementation 3.12.xlsx" \
  --method "EF v3.1" --flow-list ecoinvent-3.12-cutoff@b9428cdd8045
```

The second command maps EF 3.1 onto the release's flows and writes the method folder `methods/EF-3.1`. EF 3.1 has many indicators, so a calculation names the one it wants:

```python
import headwater as hw
bg = hw.load("ecoinvent-3.12-cutoff@b9428cdd8045", method="methods/EF-3.1")  # factorizes once
result = hw.open("tshirt").calc(bg, "tshirt:tshirt", indicator="climate-change")
result.contributions()
result.sensitivity()
```

The same from the command line, on the release the database pins. `calc` prints every indicator, `contributions` needs one:

```bash
headwater calc tshirt --method methods/EF-3.1
headwater contributions tshirt --method methods/EF-3.1 --indicator climate-change
```

The import parses the source once, validates and links it, and writes a release folder into `HEADWATER_HOME` (default `~/.headwater`). `--version` is required, because ecoSpold2 files do not record the database version. Each file names the release it last changed in, and `--version` must be the newest of them: a folder whose files name release 3.11, imported as 3.12, is refused. ecoinvent 3.12 writes release 3.0 in every file, so when no file names a later release, `--version` is taken as given and the import warns `version-unchecked`. `--exclude` lists source files to skip, each with a reason, recorded as data gaps. After that, nothing is imported again: every study on that machine points at the same release.

`notebooks/` walks through this step by step.

### A team sharing releases

One person imports a release and puts its folder where the team can read it: a shared folder or a bucket. Everyone else pulls it into their cache. The last part of the path is the release's folder name, `name@hash`:

```bash
headwater release pull /shared/releases/ecoinvent-3.12-cutoff@b9428cdd8045
headwater release pull gs://acme-lca/releases/ecoinvent-3.12-cutoff@b9428cdd8045
headwater release pull s3://acme-lca/releases/ecoinvent-3.12-cutoff@b9428cdd8045
headwater release pull https://data.example.com/releases/ecoinvent-3.12-cutoff@b9428cdd8045
```

To put a release in a bucket, copy its folder as it is:

```bash
gcloud storage cp -r ~/.headwater/releases/ecoinvent-3.12-cutoff@b9428cdd8045 gs://acme-lca/releases/
aws s3 sync ~/.headwater/releases/ecoinvent-3.12-cutoff@b9428cdd8045 \
  s3://acme-lca/releases/ecoinvent-3.12-cutoff@b9428cdd8045
```

`file://`, `http://` and `https://` work after `pip install headwater`. `gs://` needs `pip install 'headwater[gcs]'` and `s3://` needs `pip install 'headwater[s3]'`; without them the command says which one to install. From Python, `hw.load("gs://acme-lca/releases/ecoinvent-3.12-cutoff@b9428cdd8045", method="methods/EF-3.1")` pulls the release and loads it from the cache.

The pull checks the release before it is cached: its manifest against its own hash, every file against its sha256, and every table's rows against the digests the hash covers. It is written to a hidden folder first and moved into place only when all of it matches. A release that does not match, or a download that stops half way, leaves nothing in the cache and the command exits 1 naming the file or the URL. The 12 characters of hash in the URL are the pin: a release with another hash at that URL is refused. Pulling a release the cache has already changes nothing, and fetches only its small `release.json`. After the first pull, `name@hash` loads from the cache with no network.

A derived release (indicator vectors) needs the release it was solved from. Put both in the same folder of the bucket and pull the derived one: its source release comes with it, unless the cache has it. If the source release is nowhere, the pull stops and names it. One release is pulled per call.

Access to the bucket follows the source's licence: only people and CI jobs licensed for that data can read it. Access uses the cloud's own login, never a password in a file or in the URL. For `gs://` that is `gcloud auth application-default login` or a service account on the machine, for `s3://` an AWS profile (`AWS_PROFILE`, `aws sso login`). A URL with a user name, password, query or fragment is refused.

## Operations

| Operation | Command | What it does | Available |
|---|---|---|---|
| Import a release | `headwater release import` | Parse, validate, link, list issues, freeze. Fails until every issue is linked, a recorded cut-off, or a data gap. | M1 (ecoSpold2) |
| Pull a release | `headwater release pull` | Check a frozen release, from a folder or a URL, and copy it into the local cache by hash. | M1 (folders), M4 (URLs) |
| Validate | `headwater validate <database or study>` | Units and properties, roles and signs, links, overrides, scenarios, cut-offs, scope fields and the pin. Same rules as for releases. Exit 1 on errors. `--json` prints the same as data. | M2 |
| Calculate | `headwater calc <database or study>`, `hw.open(folder).calc(bg, ...)` | Results for every product a database makes, or for each product of a study and its amount, and all indicators of the methods, with provenance. | M1 Python, M2 command line |
| Ranges | `headwater calc <study> --iterations N`, `hw.monte_carlo(bg, items, iterations=N)` | Monte Carlo: the mean, median, standard deviation and percentiles of each score, with products and studies drawn from the same iterations. | M5 |
| Contribution and sensitivity analysis | `headwater contributions <database>`, `headwater where <database>`, `headwater sensitivity <database>`, `result.contributions()`, `result.where()`, `result.sensitivity()` | What it buys, where it is emitted, and which inputs matter most. | M1 Python, M2 command line; `where` M3 |
| Find a process | `headwater find <text> --release <release>`, `bg.find(text)` | The id of a background process, from part of its name, product or location. Needs no study and no method. | M4 |
| Import a normalisation and weighting set | `headwater method import-set <csv> --method <method folder> --id ID --version V --unit Pt --source TEXT` | Check a CSV of normalisation factors and weights against the method, pin the method by hash and write the set folder beside it. | M5 |
| Indicators | `bg.write_indicators()` | Freeze every product's scores into a derived release. | M1, Python only |
| Diff | `headwater diff main <database or study>` | Results between two git refs, with the changes that caused each difference. | M2 |
| Users | `headwater database uses <database>` | The databases that use a database, and the studies whose products reach it, directly or not. | M3 |
| Diff in CI | the `study-diff` workflow | One comment on every pull request that changes a database or a study: one `headwater diff <base> <merged> <folder> --json` for each, which validates it, then compares it with the base (or counts the products of a new database, and calculates a new study), with one background load. Databases come first, then the studies whose products reach them. | M2 |
| Import from Excel | `headwater database import-excel <workbook> --release NAME@HASH --out <folder>` | Convert a Brightway-format inventory into a database folder, link it to a release, and report every line that did not map. | M2 |
| Import from Brightway | `headwater database import-brightway <project> <database> --release NAME@HASH --out <folder>` | Convert a database of a Brightway project into a database folder, from the project's own files, and report every line that did not map. | M4 |
| Upgrade | `headwater upgrade <study> --to NAME@HASH` | Map every supplier to the new release, list what did not map with its reason, flag RoW links and markets that changed, write the study in place and diff it across the release change. | M4 |
| Publish | `headwater publish` | Tag the study and freeze it as a release that other repositories can pin. | M4 |

Calculate, contributions and sensitivity are also available from Python ([calculation.md](calculation.md#python-api)). The diff is `headwater.diff.diff_databases` for a database and `headwater.diff.diff_study` for a study, and the upgrade `headwater.upgrade.plan`.

## Command line

`validate`, `calc`, `contributions`, `where`, `sensitivity` and `diff` take a database folder, the one that holds `database.yaml`, and read the databases it uses from the folders next to it ([design.md](design.md#databases-it-uses)). `validate`, `calc` and `diff` also take a study folder, the one that holds `study.yaml`, and read the databases of its products from the folders next to it ([design.md](design.md#studies)); the other commands say they take a database. `find` takes a release instead. They are thin: each calls the Python API and prints what it returns.

A product of a database is named by the id of the process that makes it, as in study.yaml and in Python: `--product tshirt:bag-sewing`. Every process is a product, scored per unit of what it makes, so two processes that make one flow are two products. An id that is the flow a process makes is refused, exit code 2, with the process to give: `tshirt:bag is the flow of the process tshirt:bag-sewing: give its id, tshirt:bag-sewing.`

**The background.** By default it is the release the database pins, `background: name@hash` in `database.yaml`, read from `HEADWATER_HOME`, with the methods the database names in `methods`, or the release's one method when it names none. A pin must name a release: its name and at least 12 characters of its hash. Options change that:

| Option | Meaning |
|---|---|
| `--release` | The background to use instead: `name@hash`, a release folder, or a folder of process files |
| `--method` | A method id the release holds, or a method folder. It replaces the methods the database names. A folder of process files needs a method folder. |
| `--method-folder` | A method folder, once per folder, for a method the database names by its id and its release does not hold (see below) |
| `--namespace` | For a folder of process files, the prefix of its process ids, for example `toy` |
| `--indicator` | An indicator of the method, by id. `calc` and `diff` take it once per indicator and print them in the method's order; by default, all of them. `contributions`, `where` and `sensitivity` take one, and need it when the method has several. |
| `--set` | A normalisation and weighting set folder, once per set, for `calc`, `contributions`, `where`, `sensitivity` and `diff`. Its normalised and weighted scores and its single score are indicators after the method's, such as `toy-nw:single-score`. `diff` refuses it across a release change. |

A release that is not in the cache, a pin that names no release, and a pin that is not the release given each stop with a message that says what to pass or write. Releases are pulled with `headwater release pull`.

**A method the release does not hold.** A database can name a method by its id that its release does not hold: the Excel and Brightway imports write `methods: [EF-3.1]` when given the folder of EF 3.1, and an ecoinvent release holds no method. Every command that reads a database then takes the folder with `--method-folder`: `headwater validate knitting --method-folder methods/EF-3.1`, and the same for `calc`, `contributions`, `where`, `sensitivity` and `diff`. A method folder holds `method.yaml` and `factors.csv`, as `headwater method import` writes it. A folder fills only a method the database names and its release does not hold, so one list of folders serves every database of a repository, and a folder whose id the database does not name is not used. Of two folders with the same id, those mapped to the release's flow list are kept, and when they hold different methods the command stops and names both, with their hashes. Without the folder, the command stops and says what to pass: `database knitting names method EF-3.1, which release ecoinvent-3.12-cutoff@b9428cdd8045 does not hold (it holds none). Pass --method-folder with the method's folder, or list it in HEADWATER_METHOD_FOLDERS: a folder that holds method.yaml and factors.csv, as headwater method import writes it.` `--method` instead replaces the methods the database names.

To give the folders once, list them in the environment variable `HEADWATER_METHOD_FOLDERS`, separated as in `PATH` (`:` on Linux and macOS): `export HEADWATER_METHOD_FOLDERS=~/methods/EF-3.1:~/methods/IPCC-2021`. Every command that reads a database then uses them by the same rule, after the folders `--method-folder` gives, so a folder on the command line is used first. Each method that comes from a folder is named on standard error, with what gave the folder: `Method EF-3.1: from the folder /home/me/methods/EF-3.1, given by HEADWATER_METHOD_FOLDERS.` Results name the method by its id, version and hash.

**`headwater validate <database>`** prints every error and warning, then one line: `Database tshirt: valid.` or `Database tshirt: 2 errors, 1 warning.` It exits 1 when there is an error. It runs the checks `calc` makes (`Database.validate`), so a database that validates is one `calc` will not refuse. `--json` prints one object instead, `format` `headwater-validate/2`: `path` (the folder as given), `kind` (`database` or `study`, or null for a folder that does not read), `valid`, `background` (`release` as `name@full hash`, or null, and `folder`, or null), `method` (`id` and `version`, or null for no method or for several used together), `methods` (the list of ids, or null) and `errors` and `warnings`, each a list of `code`, `item`, `message`, `location` (or null) and `text` (the line the command prints after `error: `). A folder that does not read has errors and no `background` or `method`. Within version 2 keys may be added; none is renamed or removed. When the background cannot be loaded nothing is printed to standard output, and the message goes to standard error with exit code 1.

**`headwater calc <database>`** prints one score per indicator for each product of the database, and the provenance: input hash, method hash, engine version, background, pin, the databases it uses, `database: commit <hash>` (with `, with changes` when the database or a database it uses differs from that commit, or `not in git`), solver settings, condition, residual and the flows no indicator covers. The commit and whether the files differ are checked at the database's first calc. A database holds no scenarios, so `--scenario` with a database is a usage error. `--iterations N` then runs Monte Carlo on the background and on the database's own lines and parameters ([calculation.md](calculation.md#uncertainty)), and prints, per indicator, the mean, median, standard deviation and 2.5 and 97.5 percentiles, then the lines of the background, the lines of the database and the parameters drawn, the draws of the opposite sign, the largest condition, the residual, the generator and how markets are drawn. `--seed S` (0 by default) picks the draws, and `--workers W` runs the iterations in W processes (1 by default). The same seed gives the same draws in every run, and for a study's product with and without `--scenario`, so ranges are paired. On ecoinvent 3.12 with EF 3.1 an iteration takes about 3.6 s ([benchmarks](appendix/benchmarks.md#monte-carlo-on-ecoinvent-312)). The list of flows no indicator covers shows the first 20 flows, the database's own first, each with its first 20 processes, and a count of the rest: one flow of ecoinvent is used by a thousand processes.

**Every product.** A database imported from a workbook often makes many products, one per activity, and each is scored per unit of what it makes. `calc` scores every product of the database, in the order of the database. `--product ID`, once per product, scores those products instead, by the id of the process that makes each, as for `diff`. `--all-products` is the same as no `--product`. Each product is named by the process that makes it, what it makes and the id of the process, and says what its score is for. Example: the T-shirt database also makes a bag, by the process `bag sewing`, `tshirt:bag-sewing`, from 2 kWh of electricity:

```
$ headwater calc tshirt
Database tshirt (tshirt)
Background: toy-background@6864b7dbb65d, method toy-climate 1

T-shirt (tshirt:tshirt), per unit
  Climate change: 1.19205 kg CO2-eq

bag sewing | bag (tshirt:bag-sewing), per kg
  Climate change:  1.0596 kg CO2-eq

Provenance
  method hash: c2180f15db23...
...
  residual: 0.00e+00, the largest of the 2 products
  not characterised flows: 0
  each product's input hash and residual: in --json
```

A process with the name of its product is not named twice: `T-shirt (tshirt:tshirt)`. The first product is calculated in full, with `database.calc(bg, process_id)`. The others are scored as `headwater diff` scores them, with `database.scores(bg, process_id)`: the same checks and numbers, each solve's residual checked, and no solve against the background's matrix. With several products the provenance is the database's: the method, the background, the solver, the conditions and the largest residual of all the products, and each product's own input hash and residual are in `--json`. Two processes that make one flow are two products, each scored. An id that is no process of the database is an error, exit code 2, that names the process when the id is the flow it makes, and lists the products otherwise. With one product, the provenance is that product's. `--iterations` works on one product, so with several it is a usage error, exit code 2: give one `--product`.

**`--csv`** prints the scores as a table, one row per product and indicator, with the columns `process_id` (the product's id), `process`, `product` (what it makes), `per` (what the score is for, `1 kg`, or for a study the product's amount, `0.5 kg`), `indicator_id`, `indicator`, `score_unit` and `score`, each score written so that it reads back as the same float64. It is what a spreadsheet or pandas reads: `headwater calc knitting --csv > scores.csv`.

**`--json`** prints one object, `format` `headwater-calc/3`. Within version 3 keys may be added; none is renamed or removed. `--json` and `--csv` print nothing for `--iterations`, which is a usage error with them.

| Key | Holds |
|---|---|
| `format`, `path`, `kind`, `name` | `"headwater-calc/3"`, the database path as given, `"database"`, the database's name |
| `background`, `method`, `methods`, `engine_version`, `solver`, `scenario` | As for `diff`: `release` and `folder`; `id`, `version` and `hash`, null for several methods used together; the ids of the methods; the engine's version; how the background was factorized; the scenario, or null |
| `products` | Every product scored, the first one first: `id` (the id of the process that makes it, which `--product` takes), `name` (what it makes), `process` (the name of the process), `flow` (the id of the flow it makes), `unit` (what one score is for), `per` (`1 kg`), `input_hash` (the hash of the inputs of its scores), `residual` (the largest residual of the solves behind its scores) and `cancels` (the indicators whose score cancels, for the first product, which is calculated in full; null for the others) |
| `results` | One per product and indicator, product by product, indicators in the method's order: `product` (`id`, `name`, `process`), `indicator` (`id`, `name`, `unit`) and `score` |
| `provenance` | The database's: `method_hash`, `pin`, `uses`, `condition` (the background's), `database_condition` (the database block's), `residual` (the largest of all the products), `not_characterised` (`id`, `name`, `processes` and the `reason` of the database's own row, or null) and `database` (`name`, `commit`, null outside git, and `changed`) |

For a study, `--json` has the same keys, with `kind` `"study"`, `name` the study's and `scenario` the one `--scenario` names. Each product also has its `amount` in the study, `per` is that amount (`0.5 kg`), each score is for that amount, `input_hash` is the study's for that product, and `cancels` is a list for every product, since each is calculated in full. In `provenance`, `uses` lists the databases the study reaches, `condition`, `database_condition` and `residual` are the largest of its products, `not_characterised` holds the flows of every product, and `study` (`name`, `saved`, `commit` and `changed`) takes the place of `database`. `--csv` has the columns of a database's. On the toy, `headwater calc tshirt-footprint --json` scores 1 T-shirt 1.242646, and 1.237646 with `--scenario cleaner-dye`.

**`headwater contributions <database> [--product ID] [--depth N] [--min-share X] [--sensitivity N]`** prints the contribution tree of one product, the database's one product or the one `--product` names by the id of its process (a database with several needs it, exit code 2 otherwise), of [calculation.md](calculation.md#python-api), with the same defaults, then the top lines of the sensitivity analysis, 5 by default. `--sensitivity 0` leaves them out. A score of exactly 0 has a tree and no sensitivity, and says why.

**`headwater where <database> [--product ID] [--top N]`** prints, after the same header as `contributions`, which processes emit the score and in which locations, as `result.where()` does in [calculation.md](calculation.md#python-api): the 10 largest of each by default, and a `rest` line for the others.

**`headwater sensitivity <database> [--product ID] [--top N] [--include-reference]`** prints, after the same header, the sensitivity analysis: the lines a person can change, largest elasticity first, in the format of `contributions`: 10 by default. `--include-reference` lists each process's reference line too. A score of exactly 0 has no sensitivity, and says why.

**`headwater method import-set CSV --method FOLDER --id ID --version V --source TEXT [--unit UNIT] [--out FOLDER]`** writes a normalisation and weighting set ([design.md](design.md#a-normalisation-and-weighting-set)) from a CSV a person writes, `indicator,normalisation,weight`, one row per indicator of the method. It checks every row against the method folder, pins the method by its hash, and writes `set.yaml` and `factors.csv` into `--out`, by default the folder `ID` beside the method's. The method's folder is not changed. A row for an indicator the method does not have, a weight with no `--unit` and the other mistakes of [design.md](design.md#a-normalisation-and-weighting-set) stop it with exit code 1, and nothing is written. For the toy:

```
$ headwater method import-set toy-nw.csv --method methods/toy-impacts --id toy-nw --version 1 \
    --unit Pt --source "Made up for the toy, so every number can be checked by hand"
Read 2 rows from toy-nw.csv: normalised and weighted, in Pt.
Set toy-nw 1 adds 5 indicators to method toy-impacts 1, pinned by its hash bdc4e5667236.
Indicators of toy-impacts with no row, left out: climate-change-fossil
Wrote methods/toy-nw
```

Then `headwater calc <database> --set methods/toy-nw` prints the set's scores after the method's, and names the set, its hash and what it leaves out in the provenance:

```
  Climate change, normalised (toy-nw):                0.119205
  Photochemical ozone formation, normalised (toy-nw):    0.225
  Climate change, weighted (toy-nw):                  0.071523 Pt
  Photochemical ozone formation, weighted (toy-nw):       0.09 Pt
  Single score (toy-nw):                              0.161523 Pt
...
  set: toy-nw 1, hash c4c59c2225fe..., for method toy-impacts 1; left out of the set: climate-change-fossil
```

`contributions`, `where` and `sensitivity` take `--indicator toy-nw:single-score` as any other indicator.

**`headwater calc <study>`** prints the study, the background, then each product with its amount, `2 × tshirt:tshirt`, and its scores for that amount, then the provenance of the first: input hash, method hash, engine, background, the scenario and `study: commit <hash>`, with `, with changes` when the study or a database it reaches differs from that commit, or `not saved`. `--scenario NAME` calculates in one of the study's scenarios, and `--iterations N` runs Monte Carlo on a study of one product. `--json` and `--csv` print it as for a database, with each product's amount (above). `--product` and `--all-products` are for a database. `headwater validate <study>` checks the study and every database its products reach, with its scenarios, and ends with `Study <name>: valid.`

**`headwater schema [FOLDER]`** writes the JSON schemas of database.yaml, study.yaml and scenario files into `FOLDER/.schemas` ([design.md](design.md#schemas)), with the process ids of the databases in FOLDER as the suggested products. Point an editor at one with a first line such as `# yaml-language-server: $schema=../.schemas/study.schema.json`.

**`headwater find TEXT --release NAME@HASH [--location RER] [--product WORDS] [--kind KIND] [--limit 20] [--json]`** prints the id of a background process from part of its name or product, best match first, one line each: the id, `name | product | location`, the unit and the rule that matched. It takes no study and no method, so it solves nothing: it reads the release's process and flow tables. `--release` is a release (`name@hash`, or its folder) or a folder of process files, and `--namespace` is the prefix of its ids, as for the other commands. For the toy:

```
$ headwater find electricity --release toy-background@3f9a1c2d4e5f
toy:electricity-production  electricity production | electricity | GLO  kWh  (name starts with)
```

The rules and their order are those of `bg.find` in [calculation.md](calculation.md#finding-a-process). `--location` is a whole code and `--kind` a process kind, and an unknown one stops with exit code 2, listing what exists. `--product` keeps the processes whose product has every word. At most `--limit` lines are printed; when more match, a note on standard error says so. Standard output holds the matches only, so `headwater find ... --limit 1` can feed a script. No match is not a failure: the exit code is 0 and a note on standard error says it. `--json` prints one object, `format` `headwater-find/1`, with `matches`: a list with, for each match, the keys `process_id`, `flow_id`, `name`, `reference_product`, `location`, `unit`, `kind` and `why` (an empty list for no match). `process_id` is the supplier a database's line names. `flow_id` is the id of the process's reference flow, the flow to pass to `new_exchange` for that line ([calculation.md](calculation.md#finding-a-process)). Within version 1 keys may be added; none is renamed or removed. A release that is not in the cache stops with exit code 1 and says how to pull it.

**`headwater database uses DATABASE... [--ref REF] [--json]`** lists the databases that use each database and the studies whose products reach it, directly or not, and the way each one goes. Only the `database.yaml` and `study.yaml` files of the folders next to the database are read, on disk or at `REF`. The database need not exist: a database removed can still have users. With `tshirt-dyed` using `dyes`, `dyes` using `chemicals`, and the study `tshirt-footprint` scoring `tshirt-dyed:tshirt`:

```
studies/chemicals is reached by:
  studies/dyes (database, uses chemicals)
  studies/tshirt-dyed (database, uses dyes, which uses chemicals)
  studies/tshirt-footprint (study, products in tshirt-dyed, which uses dyes, which uses chemicals)
```

Databases come first, then studies. A file that does not read is listed as such, and reaches nothing. `--json` prints one object, `format` `headwater-uses/2`: `ref`, and `databases`, one per database given, each with `path` (the path as given), `users` (each with `path`, the folder of the user, `kind`, `database` or `study`, and `through`, the databases it goes through, from the one its own `uses` names, or the one its products are in, down to the database: `["dyes", "chemicals"]`) and `errors` (each with `path`, `file` and `message`). Within version 2 keys may be added; none is renamed or removed. The exit code is 0, and 2 for an unknown ref.

**`headwater diff REF [REF2] FOLDER`** reads a database or a study at `REF` and at `REF2` (default: the working tree, uncommitted changes included) and calculates both on one background. It reads the files from git, so your checkout, index and branches are not touched. For each indicator it prints the old and new score, the change and the % change, for every product of a database, or each product of a study for its amount (see below). Then it lists the lines that differ, ranked by their effect on the score:

```
Database studies/tshirt: origin/main (1a2b3c4d5e6f) → HEAD (9f8e7d6c5b4a)
Background: toy-background@3f9a1c2d4e5f, method toy-climate 1

T-shirt (tshirt:tshirt)
  Climate change: 1.19205 → 1.08079 kg CO2-eq, change -0.111258 (-9.3%)

Lines that differ, ranked by their effect on Climate change (kg CO2-eq):
  -0.111258  tshirt:tshirt line e02 (cotton fabric): amount 0.25 → 0.22 kg
```

Scores are in the method's order. When several changed, each line that differs is listed once, in the order of the files, named by its line id (`e02`), or numbered when two lines share an id. A table then gives what each line did alone to each score that changed: one column per line, in the same order, at most 5, then one for the other lines together and one for the interaction when a score needs it. With more than 5 lines, the 5 with a column are those with the largest share of any score's change, the share being the size of a line's effect over the sizes of all of them: a line that is all of the change of one indicator keeps its column, however small it is on the others. With several products, each has its own table. With EF 3.1, a supplier swap and an amount change give 25 score lines, 2 lines that differ, and a table of 25 rows:

```
Lines that differ:
  e02  autoclave:autoclave line e02 (electricity, medium voltage): supplier market group for electricity, medium voltage (GLO) → market for electricity, medium voltage (VN)
  e07  autoclave:autoclave line e07 (transport, freight, sea, bulk carrier for dry goods, heavy fuel oil): amount 10 → 12 tkm

Effect of each line alone on each score that changed:
                                    e02      e07
  acidification (mol H+-Eq):    +0.0012   +2e-05
  climate change (kg CO2-Eq):     +0.19    +0.01
```

The numbers are placeholders. A supplier is named by its name and location, from the release, or from the database for a process of its own; two suppliers with the same name and location keep their ids after it. The ids stay in `--json`.

**Every product of a database.** The diff scores every process of the database, each a product, on either side. Example: besides the T-shirt, the database makes a bag, by the process `bag sewing`, `tshirt:bag-sewing`, from 2 kWh of electricity. The T-shirt does not buy the bag. Raising the bag's electricity to 3 kWh moves the bag and not the T-shirt, and the diff says so:

```
bag sewing | bag (tshirt:bag-sewing)
  Climate change: 1.0596 → 1.5894 kg CO2-eq, change +0.5298 (+50.0%)

1 other product, no change

Lines that differ:
  e02  tshirt:bag-sewing line e02 (electricity): amount 2 → 3 kWh

Effect of each line alone on each score of bag sewing | bag (tshirt:bag-sewing) that changed:
                                   e02
  Climate change (kg CO2-eq):  +0.5298
```

A product is named by the process that makes it, what it makes and the id of the process, in the new database, or in the old one for a product it removed: `bag sewing | bag (tshirt:bag-sewing)`, and `T-shirt (tshirt:tshirt)` when the process has the name of its product. The comment names it the same way, without the id. No product comes first. A product is listed only when a score moved, or when only one side has it (`new product`, or `removed product`, with its scores `only in` the side that has them). They come in the order of the database, and the rest are counted: `4 other products, no change`. Each line that differs is listed once. Each product has its own table, with the columns of the lines that moved that product: at most 5, those with the largest share of that product's change, then the others together and the interaction. A product that has the name of another is followed by its id in the comment. A database of one product reads as shown above, whatever the options.

**A new database** is one line, with its products counted and not scored, since all its products would be new: with 40 products and 25 indicators that is 1,000 scores with nothing to compare with ([benchmarks](appendix/benchmarks.md#diff-of-every-product)). `headwater calc` scores them:

```
Database bags: main (1a2b3c4d5e6f) → working tree
Background: toy-background@6864b7dbb65d, method toy-climate 1

New database: 2 products, not scored. `headwater calc bags` scores them.
```

**`--product ID`**, once per product of a database, scores only those products, by the id of the process that makes each: the `products` ids of `--json`, such as `tshirt:bag-sewing`. A product neither side has is an error, exit code 2, that names the process when the id is the flow it makes, and lists the products otherwise. It is for a database of hundreds of products, where only some are of interest. On a new database it scores the products it names.

**A study** lists each of its products, in the order of `products:`, scored for its amount: `2 × T-shirt (tshirt:tshirt)`. A product whose scores did not move is one line, `no change`, and none is counted away: a study has few products. Example: the study `tshirt-and-dye` scores 2 T-shirts, which buy 0.01 kg of dye each, and 0.5 kg of dye. A pull request lowers the dye's CO2 from 4 to 3.5 kg per kg:

```
Study tshirt-and-dye: main (1a2b3c4d5e6f) → working tree
Background: toy-background@6864b7dbb65d, method toy-climate 1

2 × T-shirt (tshirt:tshirt)
  Climate change: 2.48529 → 2.47529 kg CO2-eq, change -0.01 (-0.4%)

0.5 × reactive dye production | reactive dye (dyes:reactive-dye)
  Climate change:  2.5298 →  2.2798 kg CO2-eq, change -0.25 (-9.9%)

Lines that differ:
  e03  dyes:reactive-dye line e03 (Carbon dioxide, fossil): amount 4 → 3.5 kg
```

What differs is found in each database the products reach, each read at both refs, named with its database, and in the study itself: the amount of a product (`product tshirt:tshirt (T-shirt): amount 1 → 2`, whose effect alone is the change of the amount times what one unit scored), its `no_factor.csv` rows, its scenarios and the fields of study.yaml. Each effect is that of one unit, times the old amount. A study diff scores the base case only: a changed scenario file is listed under `Other changes`, and not scored, as one line of what changed in it, each change as a line change of a database reads: `scenario cleaner-dye: dyes:reactive-dye line e03 amount 3.5 → 3; description "…3.5…" → "…3…"`, with `added` or `removed` and what it sets for a change only one side has. A product only one side has is a `new product` or `removed product`. A new study is calculated alone.

The effect of a line is what the score would be if only that line had changed in the old database, minus the old score. Lines in series multiply, so the effects of several changes need not add up to the change. What is left is the line `interaction between the changes`, so that every list adds up. Example: the T-shirt buys 0.25 kg of a fabric the study makes itself. Raising the T-shirt's amount to 0.5 kg alone adds 0.89735, raising the fabric's electricity from 3 to 6 kWh alone adds 0.39735, and together they add 1.69205: the 0.39735 more is the interaction. A change that cannot be applied to the old database on its own, such as a line that buys from a process only the new database has, is brought along with what it needs. If that is not enough, its effect shows as `n/a` and is part of the interaction.

What differs in a database, found by line id: lines of its processes (added, removed, or changed amount, unit, supplier, flow or role), processes added, removed or renamed, parameters, override lines, flow, flow property and unit definitions. The comment of a line, an override line or a parameter is never part of what differs on it: an edited comment is listed once under `Comments changed`, as `tshirt:tshirt line e02 (cotton fabric): comment "-" → "Weighed at the factory"`, and is not calculated. The same is found in each database it uses, read at both refs: a line of a used database is named by its full id, `dyes:reactive-dye line e03`, and anything else with its database, `parameter kwh_per_kg in dyes`. A line whose amount moved only because a parameter changed is listed under the parameter. An override row is listed as `override of <background process> line <id>`, in the terms of `overrides/*.csv`; the local copy of the process that a calculation makes from it is never listed. What a score does not depend on is listed apart, under `Other changes`: cut-offs, the rows of `no_factor.csv` (found by flow and method: added, removed, or a changed reason, who or when) and the fields of `database.yaml` (`database description: ...`). A row is listed as `no_factor.csv row <flow> (<flow name>): for method <method>: reason <old> → <new>`, so a changed reason, which moves the input hash and no score, is explained. `--top N` lists at most N lines (default 10): when more differ, the N with the largest share of a score's change. Two scores are the same when they differ by less than 1e-9 of the larger one.

When the two refs pin different releases, as after `headwater upgrade`, each side is calculated on the release it pins, and each changed score says how much of the change is the release's and how much the folder's: `change -0.4098 (-34.0%): release -0.3273, database -0.0825`. The lines the release moved are listed first, each with what one unit of its supplier scored before and after, then the lines that differ, as above. The lines the release moved are listed for the first score that changed ([Upgrading](#upgrading-to-a-new-release)). `--method` is the method of the new side and `--old-method` that of the old one, each mapped to its release's flow list; by default both are the methods the folder names, from each release. A study's products in a database are split the same way, each for its amount. `--release` puts both sides on one background, as before.

**`--method-folder PATH`**, once per folder, is for a database that names a method its release does not hold, by its id, as for the other commands ([above](#command-line)). Example: the database says `methods: [EF-3.1]` and pins `ecoinvent-3.12-cutoff@b9428cdd8045`, which holds no method; `--method-folder methods/EF-3.1` scores it with that folder's EF 3.1. Across a release change, the folders serve both sides.

The folder at `REF2` is validated first, as `headwater validate` does, with the background the diff loads, so the background is loaded once for both. A folder with errors is not calculated: the errors are listed as `validate` lists them, with the same exit code, 1. A folder that is not at `REF`, or that holds neither `database.yaml` nor `study.yaml` there (the format before D46), is new, since the pull request adds it: a new database is counted, as above, and a new study is calculated alone, the header saying `New study: it does not exist at REF, so there is nothing to compare with`, and each score `only in` the new side. A folder that is a database at one ref and a study at the other is not compared, exit code 2.

The exit code is 0 whether or not anything differs, 1 when the folder has validation errors, and 2 when the diff cannot be made: an unknown ref, a folder that does not read or calculate at `REF` (the message names the ref), a background that cannot be loaded. A folder that does not read at `REF2` is a validation error, not a failed diff. Failures go to standard error, so `--json` prints nothing on standard output when it exits 2.

**`--json`** prints one object, `format` `headwater-diff/2`. Within version 2 keys may be added; none is renamed or removed. When the folder has validation errors, the object has `format`, `path`, `kind` and `validation` only, and the exit code is 1.

| Key | Holds |
|---|---|
| `format`, `path`, `kind`, `tolerance` | `"headwater-diff/2"`, the folder as given, `database` or `study`, 1e-9 |
| `created` | True when the folder is not at `REF`. `old.input_hash` and `old.residual` are then null, every `results` score has `old` null, and `differences` is empty. A new database has no `results`, and null `new.input_hash` and `new.residual` |
| `old`, `new` | `ref` (null for the working tree), `commit` (full hash, or null), `input_hash`, `residual` |
| `engine_version`, `solver` | The engine's version, and how the background was factorized: `solver`, `ordering`, `diag_pivot_thresh`, `scaling`, `tolerance`, `condition_limit` |
| `background` | `release` (`name@full hash`, or null) and `folder` (or null) |
| `old_background` | Across a release change, the old side's `release`, `folder` (null) and `method_hash`; null when both sides are on one background |
| `method` | `id`, `version`, `hash`; null when several methods are used together, since their joined id is not written to a file |
| `methods` | The ids of the methods used, a list (one id for one method) |
| `products` | Every product, in the order of the database, or of the study's `products:`: `id` (the id of the process that makes it, which `--product` takes), `name` (what it makes), `process` (the name of the process: of the new side, or of the old one for a removed product), `status` (`both`, `new` when only the new side has it, `removed` when only the old one did) and `amount`, for a study, `old` and `new`, null on a side that does not have the product, and null for a database |
| `results` | One per product and indicator, indicator by indicator in the method's order: `product` (`id`, `name`, `process`), `indicator` (`id`, `name`, `unit`), `old`, `new`, `change`, `percent`, `changed`, `drivers` (ranked `difference` ids with their `contribution`, null for `n/a`), `interaction`, `background` and `moved`. A score missing on one side is null, and so is `percent` when the old score is 0. Across a release change, `background` is what the release changed and `moved` its lines, the largest effect first: `process`, `line` (null for an override's copy), `flow`, `unit`, `old_supplier`, `new_supplier`, `old_value` and `new_value` (what one unit of the supplier scores, or the factor of an emission), `effect` and `how` (`same-id` or `same-dataset`, the rule that moved the supplier; `new-study`, the supplier the new database buys from when no rule moves it; `no-successor`, a line the new database no longer has; `factor`, an emission; `override`, a copy an override makes). On one background, `background` is null and `moved` empty; `drivers` and `interaction` then explain the whole change, and across a release change the folder's part. A study's scores, effects and lines moved are for its amounts. |
| `validation` | The object of `validate --json` for the folder at `REF2`: `valid`, `errors`, `warnings`, and the background and method used |
| `differences` | One per difference: `id`, `kind`, `change` (`added`, `removed`, `changed`), `item`, `description` (with the ids of suppliers and flows), `text` (the same, each supplier named in words: `supplier electricity production (GLO) → solar electricity (FR)`), `fields` (`name`, `old`, `new`, with ids), `affects` (lines a changed parameter moves), `scored`, `flow` (`id` and `name` of the flow the difference is about, `name` null when it is not known, or null for a difference with no flow, such as a parameter), `database` (the database the difference is in when it is one the database uses, or for a study any database its products reach, such as `dyes`; null for the folder's own). A study's own differences are of the kinds `product` (an amount), `no_factor`, `scenario` and `study` (a field of study.yaml); a database's fields are of the kind `database`. An edited comment is its own difference, `kind` `comment`, not scored, whose `item` is the line (`tshirt:tshirt line e02`), the override line (`override of toy:electricity-production line e02`) or the parameter (`parameter fabric_kg`) |
| `processes` | The `name` and `location` of every supplier the lines of either side buy from, by id, from its release or from its database |

## Importing from Excel

Brightway inventories in Excel (`.xlsx`) convert into a database folder, so nobody retypes them.

```bash
headwater database import-excel inventory.xlsx --release toy-background@3f9a1c2d4e5f \
    --out studies/tshirt --method methods/toy-climate [--mapping mapping.csv] [--name tshirt]
```

`--method` is given once per method to use several together, as for `calc`: `--method EF-3.1 --method IPCC-2021` writes `methods: [EF-3.1, IPCC-2021]` in database.yaml. A method folder is written by its id, `methods: [EF-3.1]`. With no `--method`, the import validates with the release's one method and writes no `methods`. When the release does not hold that id, the commands that read the database need the folder ([Command line](#command-line)), and the import's last line says so with the command to run: `headwater validate studies/tshirt --method-folder methods/EF-3.1`.

**Example.** The line `market for steel | steel | RER`, 2 `kilogram`, becomes the line `input`, 2 `kg`, supplier `ecoinvent:3.12:cutoff:<ids>`. One activity becomes one pair of files in `processes/`, its id is `<database>:<name>-<location>`, such as `tshirt:t-shirt-glo`, or `<database>:<product>-<location>` when several activities share a name and location, such as the steps of one process ([design.md](design.md#excel-import) has the rule), and the database gets `flows.csv`, `parameters.yaml` and a `database.yaml`.

**What it reads.** The layout of `bw2io.ExcelImporter`: one `Database` row, `Project parameters` and `Database parameters` tables, and an `Activity` block for each activity, with metadata rows, an optional `Parameters` table and an `Exchanges` table. A sheet whose first cell is `skip` is not read, and a first row `cutoff <n>` keeps the first n columns. Values are the ones Excel stored.

**How a line is linked.** Nothing is guessed.

- A line matches a process of the release by exact name, reference product and location, with no case folding. A line with no reference product or no location matches on what it gives, and only when one process fits. That link is accepted, but it is counted apart in the report (`linked by name only`), listed to review with its supplier, and kept in the import's record, `.headwater-import.json`, under `notes`: `{"process": "toy-foreground:t-shirt-glo", "line": "e02", "supplier": "toy:fabric-production", "note": "linked without the workbook's reference product: the only process that fits"}`. The line's comment is the workbook's alone.
- A line whose `database` is the workbook's own links to one of its activities. A name that starts like the release's source (`ecoinvent-3.12-cutoff` for an ecoinvent release) means the release. A blank database is looked up in the workbook, then in the release, and refused if both match. Any other name, such as `Textile Chemicals`, needs the mapping file.
- A role comes from what the supplier makes, never from the sign. A negative amount to a treatment (a supplier that makes a waste) is `waste_to_treatment`, in positive kg. A Brightway `substitution` is `avoided_product`. A negative amount to any other product is a credit the workbook does not classify, so it is reported, and the report says how to fix it: set the line's type to `substitution` in the workbook and import again, or change its role to `avoided_product` in the CSV by hand. The sign never decides the role.
- A flow of nature matches by name and `categories` (`air::urban air close to ground`). Brightway drops the subcompartment `unspecified`, so `water` also means `water/unspecified`. A flow in `natural resource` is a `resource`, one in air, water or soil an `emission`.
- A unit is the unit of the table that Brightway's name means (`kilogram` is `kg`). It must be of the same quantity as the supplier's flow: grams are converted, mass is not turned into energy.

**Formulas.** The `formula` column and the parameter tables become `parameters.yaml` and formula amounts, in the syntax of [Parameters and formulas](design.md#parameters-and-formulas). Brightway writes waste sent for treatment as a negative amount, so its formula is written with the sign turned. A formula headwater cannot express (`^`, `sin(x)`, a name that is not a parameter) is reported. The line keeps the number Excel stored, and the formula goes in the line's comment. A formula whose value differs from the amount next to it is noted: headwater uses the formula's.

**Uncertainty.** The columns `uncertainty type`, `loc`, `scale`, `minimum` and `maximum`, on a line or on a row of a parameter table, become its distribution, in the four distribution columns of a process file or the same keys of `parameters.yaml` ([design.md](design.md#uncertainty)). Example: the fabric line, 0.25 `kilogram` with `uncertainty type` 2, `loc` -1.38629 (ln 0.25) and `scale` 0.0953102 (ln 1.1), becomes `lognormal` with `scale` 0.0953102. The waste line, -0.05 `kilogram` with `uncertainty type` 4 between -0.06 and -0.04, becomes 0.05 kg `uniform` between 0.04 and 0.06: the bounds turn with the sign. Lognormal, normal, uniform and triangular (stats_arrays types 2 to 5) are copied, centred on the amount. Text that is a number is read as the number, as bw2io reads it, and `undefined` or `no uncertainty` means none. With no `loc`, Brightway centres a distribution on the amount itself, so a normal or a triangular is copied, and a lognormal is not. A distribution a database cannot hold is reported at its row and not copied: another type, a `loc` that is not the amount (or its logarithm, for a lognormal), one on a line or parameter whose amount is a formula, one on a line that is not linked yet, and a spread with no `uncertainty type`.

**What did not map is still written.** A line with no supplier keeps the workbook's own words: its flow is named after the reference product, and its comment says `UNMAPPED (reason)`, then the line as the workbook has it (`electricity production | electricity | RER, 0.5 kilowatt hour`) and why. So a mapping row can be written without opening the workbook again, and `headwater validate` refuses the database until a person decides. Each row of the workbook has one outcome in the report: `mapped`, `unmapped`, `waiting` or `unexpressible`. Identical lines are reported once, with all their places in the workbook (`inventory!A71`) and the processes that might have been meant. Lines that wait for a database folder that is not there yet are grouped under `Waiting for database <folder>`.

```
Not mapped: 2 lines, 1 to decide
- [not-in-release] electricity production | electricity | RER  (2 lines, in 2 activities)
    ... is not a process of release toy-background. If it was renamed or moved, decide it in the mapping file.
    maybe: toy:electricity-production (electricity production | electricity | GLO)
    at inventory!A17, again!A8
```

**The mapping file** holds what a person decided, so it is reviewed, kept in git and reused. `who` and `when` (an ISO date or time, as `2026-10-12`) are required on every row, like a row of `cut_offs.csv`, and the file is read like it: one row is one line.

```
type,name,reference_product,location,categories,target,who,when,comment
technosphere,electricity production,electricity,RER,,toy:electricity-production,leo,2026-10-12,the GLO mix stands for RER
biosphere,Water,,,water::fresh,ecoinvent:flow:<uuid>,leo,2026-10-12,
database,Textile Chemicals,,,,textile-chemicals,leo,2026-10-12,shared database
```

A row with fewer or more cells than the header is an error, and so is a cell that holds a line break (Alt+Enter in Excel). The message names the file and the line: `file mapping.csv: a value spans several lines. One row is one line, so a quote is probably missing or extra. [mapping.csv:3]`. A file with a `date` column in place of `when` is refused: `unknown column 'date'. It is now called 'when'.`

A `technosphere` row sends the line with that name, product and location (as the workbook writes them) to a release process. A `biosphere` row does the same for a flow of nature. A `database` row says what a database of the workbook is: `release`, `own`, or a database of yours, by the name of its folder next to the database written (`textile-chemicals` for `--out studies/widget`, so `studies/textile-chemicals`). When that folder holds a database, its lines link to its processes by exact name, reference product and location, as lines of the release do, and the database is added to `uses:`. A database in it that is not named like its folder, or that does not pin the release as the database written does (`name@` and 12 characters of the hash), is refused before anything is written. When it does not yet, its lines are `waiting`: written with no supplier, and listed, and `uses:` does not name it, so the database still reads. Import again once the folder is there. A pinned form such as `textile-chemicals@v3` is refused, with the folder name to write. A row that no line needed is listed.

**database.yaml** says what a workbook says about the database: its `name`, the release its lines link to (`background`), the databases next to it that it buys from (`uses`), and `methods` when `--method` names them. There is nothing to fill in: a database holds no goal or functional unit, and every activity is a product, scored per unit of what it makes. Columns headwater does not use are counted in the report, not kept.

The import ends with what validation found, `Validation: 0 errors.`, then each error and warning, except those of the lines the report already lists as not mapped, which it counts: `Validation: 1 error. 1 of them is listed above.`

**Importing again.** The loop is: import, read the report, add rows to the mapping file, import again into the same folder. The lines the mapping decides are filled in, and what you wrote in database.yaml stays.

Example. After the first import you add `description: Knitting, gate-to-gate, per kg of fabric` and a comment to database.yaml, and import again. The description and the comment are as you left them. If nothing the import sets changed, the file is not changed by one byte.

The rule has two parts, one for the process files and one for database.yaml.

- **Process files.** The first import records a hash of each file it wrote in the hidden `.headwater-import.json`. A second import replaces those files, and only those, when none was edited since: it also removes the files of an activity that left the workbook. If a person edited one of them, the import refuses, names the file and writes nothing, so an edit is never overwritten: keep it in the workbook or the mapping file, or import into another folder. Files a person added (`cut_offs.csv`, `overrides/`, `scenarios/`, notes) are never touched, so check that their line ids still fit. A folder that holds files and no record of an import is refused.
- **database.yaml.** It is merged, not replaced, and an edit never stops the import. `name`, `background` and `uses` are the import's every time: the processes it writes depend on them. `uses` is taken out when the database no longer uses another. `methods` follows the import only while you have not changed it: the record keeps what the import wrote, so it can tell. A `methods` of yours that the import would have set differently is listed in the report: `database.yaml keeps methods: [EF-3.1, IPCC-2021], which a person wrote. This import would have written methods: [EF-3.1].` Only the line of a field that must change is replaced. Other fields, comments and their order are not moved. A field you deleted is written again. A database.yaml that is not valid YAML stops the import before it writes anything. So does a layout it cannot edit by line, when a field must change: a key in quotes, the whole file in braces, or a comment at the left margin inside a list. The message says `database.yaml: cannot be updated in place (...). The import changes a field by its line, `field: value` at the start of a line. Write the fields that way, or remove the file to have it written again.`

**The exit code** is 0 when every line mapped and the database validates and can be calculated, 1 when something needs a decision (the database is still written), and 2 when nothing was written: a workbook or mapping file that is wrong, a folder with files that an import did not write, or with process files that were edited since, a database.yaml that is not YAML or that the import cannot edit by line, an unknown release or method. A flow the method has no factor for makes the exit code 1: `validate` and `calc` refuse a database that emits one until its `no_factor.csv` acknowledges the flow ([design.md](design.md#flows-with-no-factor)).

## Importing from a Brightway project

A database of a Brightway project converts into a headwater database folder in one command. bw2data is not needed: the project's files are read directly, and never written.

```bash
headwater database import-brightway toy tshirt --release toy-background@3f9a1c2d4e5f \
    --out studies/tshirt --method methods/toy-climate [--brightway-dir <folder>] [--mapping mapping.csv] [--name tshirt]
```

`--brightway-dir` is the folder that holds Brightway's projects, the one bw2data calls `BRIGHTWAY2_DIR`. Without it the command reads `BRIGHTWAY2_DIR`, then the folder bw2data uses by default (`~/Library/Application Support/Brightway3` on a Mac).

**Example.** In the project `toy`, the database `tshirt` has the activity `T-shirt`, GLO, whose exchange 15 comes from `('toy-background', 'electricity')`. That node is `electricity production`, product `electricity`, in GLO. The exchange becomes the line `input`, 0.5 `kWh`, supplier `toy:electricity-production`, the process of the release with that name, product and location. The database scores 1.25205, as it does in Brightway.

**How a line is linked.** Each exchange holds the key of the node it comes from, `(database, code)`. The import reads that node, and links the line with the node's own name, reference product, location, unit and categories, by the rules of the Excel import ([Importing from Excel](#importing-from-excel)): a node of the database imported is one of its activities, a node of a database whose name starts like the release's source (`ecoinvent-3.12-cutoff`) is a process of the release, and a flow of nature matches by name and categories. A node of another database of the project, such as `dyes`, is a process of the database of that name in the folder next to `--out` (`studies/dyes`), and the database uses it. Import `dyes` there first: until then its lines wait, as in the Excel import. A background with another name, `ei_3.12` say, takes a row of the mapping file: `database,ei_3.12,,,,release,<who>,<when>`.

A line to an activity of the database itself links to the activity whose key it holds, even when another one has the same name, reference product and location. Example: in the toy's database `twins`, `cut-a` and `cut-b` are both `fabric cutting`, cut fabric, GLO, with 1 and 1.2 kg of fabric per kg. T-shirt A buys from `cut-a` and T-shirt B from `cut-b`, and they score 1.19205 and 1.37748, as in Brightway. Activities are read in the order of their codes, so the second of two with the same name, location and reference product is `-2` on every import, whatever order Brightway wrote them in: `twins:cut-fabric-glo` and `twins:cut-fabric-glo-2`, named after the cut fabric they make, since their names repeat. A line to another database of the project is linked by name, reference product and location, so two activities alike there are ambiguous, as in the Excel import.

**What it copies.** Project and database parameters go to `database:` in `parameters.yaml`, activity parameters to their process. An exchange's formula is copied when Brightway uses it, that is when the exchange is in a parameter group: a waste line's formula with its sign turned, `-(-waste_kg)`. Distributions are copied on lines and on parameters as in the Excel import, by the same mapping: lognormal, normal, uniform and triangular, centred on the amount, a waste line's bounds turned with its sign, and anything else reported and not copied. Lines are in Brightway's order, the production line first. Ids, mapping file, `database.yaml`, importing again and the exit codes are those of the Excel import.

**What stops it.** Nothing is written, and the exit code is 2, for a project or database that is not there, a database that Brightway stores another way than in SQLite, tables that are not those of bw2data 4.5.4 to 4.7, a record that holds an object of a class (the error names the class and the record: reading one could run code), an exchange that comes from a node no database of the project has (the error names the exchange and the key: Brightway could not calculate it either), and separate product nodes.

## Upgrading to a new release

`headwater upgrade` moves databases and studies from the release they pin to another, writes the change in place, and shows what moved. On a branch, so the pull request shows it:

```bash
headwater upgrade studies/tshirt --to toy-2@bd46d2b98059 --mapping upgrades/toy-1-toy-2.csv
```

```
Upgrade tshirt: toy-1@b24ebd7d98cc → toy-2@bd46d2b98059

Suppliers, by the rule that mapped them:
  mapping row      2 suppliers, 2 lines
  same id          1 supplier, 1 line
  correspondence   0 suppliers, 0 lines
  same dataset     1 supplier, 1 line
  not mapped       0 suppliers, 0 lines

Decided by a mapping row:
  dye production | dye | GLO (toy:1:dye) → reactive dye production | reactive dye | GLO (toy:2:dye-r), by leo on 2026-10-25 (toy-1-toy-2.csv:2)
  ...

To review (2):
  [RoW] cotton fabric production | cotton fabric | RoW (toy:1:fabric-a → toy:2:fabric-b): RoW leaves out the places with a dataset of their own: toy-2@bd46d2b98059 adds IN. At tshirt:tshirt line e02
  [market] market for electricity | electricity | GLO (toy:1:market-elec → toy:2:market-elec): buys from 1 new supplier: electricity production, wind | electricity | GLO, largest share moved: electricity production | electricity | GLO, 100% → 50%. At tshirt:tshirt line e03

Written: tshirt/processes/tshirt.csv, tshirt/database.yaml
```

Then the diff across the release change, as `headwater diff` prints it:

```
T-shirt (tshirt:tshirt)
  Climate change: 1.2048 → 0.8775 kg CO2-eq, change -0.3273 (-27.2%): release -0.3273, database +0

Release change, line by line, on Climate change (kg CO2-eq):
  -0.1875  tshirt:tshirt line e02 (cotton fabric): 3.5 → 2.75 per kg, same dataset, cotton fabric production (RoW), toy:1:fabric-a → toy:2:fabric-b
   -0.125  tshirt:tshirt line e03 (electricity): 0.5 → 0.25 per kWh, same id, market for electricity (GLO), toy:1:market-elec → toy:2:market-elec
    -0.01  tshirt:tshirt line e04 (reactive dye): 4 → 3 per kg, as in the new database, dye production (GLO) → reactive dye production (GLO)
  -0.0028  tshirt:tshirt line e05 (Methane, fossil): factor 29.8 → 27
   -0.002  tshirt:tshirt line e06 (tape): 1 → 0.8 per kg, as in the new database, tape production (GLO) → tape production (RER)
```

How each supplier is mapped, what is flagged and how the diff splits a score are in [design.md](design.md#upgrades).

**The loop.** Run it, read what did not map, write a row for each in the mapping file, run it again. A row says what a person decided, with who and when, in the format of the supplier mapping, keyed by the old process's id or by its name, product, location and unit:

```
supplier_id,name,product,location,unit,supplier,reason,who,when,comment
,dye production,dye,GLO,kg,toy:2:dye-r,,leo,2026-10-25,renamed reactive dye production
```

A supplier no rule maps is listed with its reason, the processes it might be, which are never applied, the lines that buy from it, and a row to fill in:

```
Not mapped: 1 supplier, 1 line
- [not-in-release] tape production | tape | GLO (toy:1:tape): no process of toy-2@bd46d2b98059 is tape production | tape | GLO, with the same product id, and no rule maps it
    maybe: toy:2:tape-rer (tape production | tape | RER)
    at tshirt:tshirt line e06
    row: toy:1:tape,,,,,<supplier>,,<who>,<when>,
```

**Studies.** A study given moves with the databases its products are in. Its scenarios move too: a change to a line of the old release names the successor and its line, found by role and flow as for an override, and a change to a parameter of a process of the old release names the successor. Example: the scenario `clean-grid` of the study `tshirt-footprint` sets line e02 of `toy:1:elec`, its CO2, to 0.3; after the upgrade it sets line a2 of `toy:2:elec`. A study of release products, which pins the release itself, moves its products and its pin: 0.25 kg of `toy:1:fabric-a`, pinned to toy-1, becomes 0.25 kg of `toy:2:fabric-b`, pinned to toy-2.

```
headwater upgrade studies/tshirt-footprint studies/fabric --to toy-2@bd46d2b98059 --mapping upgrades/toy-1-toy-2.csv
...
Written: fabric/study.yaml, tshirt-footprint/scenarios/clean-grid.yaml, tshirt/database.yaml, tshirt/processes/tshirt.csv
...
Study tshirt-footprint: valid.
Study fabric: valid.
```

A scenario line with no single match in the successor stops the upgrade, and the message names the study, the scenario file, the process and the line: `study tshirt-footprint scenarios/clean-grid.yaml: toy:1:elec line e03: no line of toy:2:elec is an emission of toy:n2o. Change or remove the row.` Each study is validated on the new release, and only the databases are diffed.

**Options.** Several folders can be given: they move together, with every database they use and every database a study's products are in. A database next to them that uses one of them must be among them, and so must a study next to them whose products reach one and that names an id of the old release, in its scenarios or its products. `--method` is the method of the new release and `--old-method` that of the old one, each mapped to its own flow list; by default both are the methods the databases name, from each release. Every product of each database is diffed. `--indicator` and `--top` are those of `diff`. `--check` writes nothing, and prints the same report and diff.

**The exit code** is 0 when the folders are written (or would be, with `--check`), 1 when a supplier did not map or something else stops the upgrade (nothing is written), or when an upgraded folder does not validate on the new release (it is written: fix it in place), and 2 when the upgrade cannot run: a release not in the cache, folders that pin different releases or already pin the target, a mapping file that is wrong.

**`--json`** prints one object, `format` `headwater-upgrade/2`: `databases` (the databases that move) and `studies` (the studies given), `from` and `to` (the pins), `check`, `complete`, `counts` (suppliers and lines per rule: `mapping`, `same-id`, `correspondence`, `same-dataset`, `unmapped`), `matches` (each with `old`, `new` and `automatic` datasets, `rule`, `code`, `reason`, `decision` with `who`, `when`, `source` and `comment`, `suggestions` and `lines`), `flags` (`kind`, `old`, `new`, `message`, `added`, `removed`, `lines`), `problems`, `warnings`, `written` (the files changed, from the folder that holds the databases and studies), `validation` (one `validate --json` object per database diffed and per study) and `diffs` (one `diff --json` object per database diffed). Within version 2 keys may be added; none is renamed or removed.

## Several people editing

Databases and studies are text files in git, so people work like on code.

**Example.** Two people work on the T-shirt database. One lowers the fabric from 0.25 to 0.22 kg, the other replaces the electricity supplier, each on a branch. Each pull request shows the changed lines, and CI comments:

```
Database studies/tshirt: origin/main (1a2b3c4d5e6f) → HEAD (9f8e7d6c5b4a)
Background: toy-background@3f9a1c2d4e5f, method toy-climate 1

T-shirt (tshirt:tshirt)
  Climate change: 1.19205 → 1.08079 kg CO2-eq, change -0.111258 (-9.3%)

Lines that differ, ranked by their effect on Climate change (kg CO2-eq):
  -0.111258  tshirt:tshirt line e02 (cotton fabric): amount 0.25 → 0.22 kg
```

That is the output of `headwater diff origin/main HEAD studies/tshirt`.

Processes that many databases buy are a database too, in a folder next to them. A dyed T-shirt says `uses: [dyes]` and buys from `dyes:reactive-dye` ([design.md](design.md#databases-it-uses)). There is no version to pin: git versions both, so a pull request that changes a line of `dyes` shows its effect on every database that uses it, and on every study whose products reach it, in the same comment. Freezing a database into a release that other repositories pin comes later (M4).

Rules that keep merges clean:

- One pair of files per process (a YAML for its details, a CSV for its exchanges), one exchange per line, sorted by line id. Two people editing different processes never touch the same file. The same exchange edited twice is a normal one-line conflict.
- No big shared file that everyone appends to.
- Changes to a background process are overrides containing only the changed lines, never a full copy. This also keeps licensed data out of git.

## CI

A pull request that changes a database or a study gets one comment that shows what changed in the results and why. It lists each database that changed or uses one that changed, then each study whose products reach one. Example: a pull request lowers line e03 of `dyes:reactive-dye`, the dye's CO2, from 4 to 3.5 kg per kg. The database `tshirt` buys 0.01 kg of dye per T-shirt, and the study `tshirt-footprint` scores 1 T-shirt. The comment reads:

````markdown
## Results diff for changed databases and studies

Base `main` (`1a2b3c4d5e6f`), head `merge of #12` (`9f8e7d6c5b4a`). Each database and study is calculated on the release it pins.

### Database `studies/dyes`

Background `toy-background@6864b7dbb65d`, method `toy-climate 1`.

| | Lines that differ |
|---|---|
| e03 | `dyes:reactive-dye line e03` (Carbon dioxide, fossil): amount 4 → 3.5 kg |

A line's column is the change that line alone makes.

| Product | Indicator | Old | New | Change | % | e03 |
|---|---|---:|---:|---:|---:|---:|
| reactive dye production \| reactive dye | Climate change (kg CO2-eq) | 5.0596 | 4.5596 | -0.5 | -9.9% | -0.5 |

### Database `studies/tshirt`

Uses `dyes`, which changed.

Background `toy-background@6864b7dbb65d`, method `toy-climate 1`.

| | Lines that differ |
|---|---|
| e03 | `dyes:reactive-dye line e03` (Carbon dioxide, fossil): amount 4 → 3.5 kg |

A line's column is the change that line alone makes.

| Product | Indicator | Old | New | Change | % | e03 |
|---|---|---:|---:|---:|---:|---:|
| T-shirt | Climate change (kg CO2-eq) | 1.24265 | 1.23765 | -0.005 | -0.4% | -0.005 |

### Study `studies/tshirt-footprint`

Its products reach `tshirt`, which uses `dyes`, which changed.

Background `toy-background@6864b7dbb65d`, method `toy-climate 1`.

| | Lines that differ |
|---|---|
| e03 | `dyes:reactive-dye line e03` (Carbon dioxide, fossil): amount 4 → 3.5 kg |

A line's column is the change that line alone makes.

| Product | Indicator | Old | New | Change | % | e03 |
|---|---|---:|---:|---:|---:|---:|
| 1 × T-shirt | Climate change (kg CO2-eq) | 1.24265 | 1.23765 | -0.005 | -0.4% | -0.005 |
````

Each line that differs is listed once, in the order of the files, with what changed, a supplier named by its name and location. The scores that changed follow, in the method's order, each line with a column, as in `headwater diff`: at most 5, chosen by the largest share of any score's change, then one for the other lines together, and one for the interaction when a score needs it. The first 5 scores that changed are open, the others folded below, then the scores that did not change. Comments that changed are listed apart, once each. A database scored with EF 3.1, with a supplier swap and an amount change, shows 2 lines that differ and the first 5 of its 25 scores, climate change among them, with 20 folded, then the 2 comments if they changed too.

**A database of several products** has a block for each product with a score that moved, or that only one side has, the first block open, and a line for the rest (`4 other products, no change.`). Each block has the columns of the lines that moved that product. **A new database** is one line: `New database: 40 products, not scored. headwater calc studies/knitting scores them.`

**A study** lists each of its products in the order of `products:`, named with its amount, `2 × T-shirt`: a block for each product that moved, and one line for each that did not (`0.5 × reactive dye production | reactive dye: no change.`). None is counted away: a study has few products. A changed amount is listed with the lines, `product tshirt:tshirt (T-shirt): amount 1 → 2`, with its column. A changed scenario file is listed under `Other changes`: the comment scores no scenario. A new study is scored for its products.

There is one comment per pull request. A new push updates it in place: it is found by a hidden marker on its first line and by its author, the Actions bot. When nothing has changed any more, it is updated to say so. The full text, up to 900,000 characters, goes to the job summary, which GitHub limits to 1 MiB. A comment is cut at 60,000 characters (GitHub's limit is 65,536): first with fewer lines listed, fewer columns and fewer open scores (3, then 1 of each), and, with several products, only that many scores for each product after the first, the rest counted, then at a line, with a note.

What the job does:

1. **Check out the pull request merged into its base**, with full history. A folder is calculated as it will be after the merge, not only as written on the branch. The base is the first parent of that merge commit. Require branches to be up to date before merging, because two clean merges can still combine into a wrong model.
2. **Find the changed databases and studies.** A database is a folder that holds `database.yaml`, and a study one that holds `study.yaml`. It has changed when a file in it differs from the base. A folder that exists only in the pull request is new, and one that exists only in the base is removed. Files that belong to neither are ignored. A database that uses a changed, new or removed database, directly or not, is compared too, and so is a study whose products reach one, found by one run of `headwater database uses` on the merged commit. The comment says why: "Uses `dyes`, which changed." or "Its products reach `tshirt`, which uses `dyes`, which changed." A file that does not read is listed in the comment, since whether its folder reaches a changed database is not known. A pull request that changes no database and no study gets no comment, and a comment it already has is updated.
3. **Validate and compare**, one run of `headwater diff <base> <merged> <folder> --json` for each changed or new folder, so the background is loaded once for both. The folder is validated, then diffed against the base. A new database is counted, and a new study is calculated alone, with a note that there is nothing to compare with. A removed folder is listed. Databases come first, then studies, each in the order of their paths. The job reads the JSON that `diff` prints, its `validation` object included, not text. `diff` scores every product of a database, so a change to any of them shows.
4. **Post or update the comment**, and fail the job when a database or a study does not validate or its results cannot be compared. The comment lists the errors or the message in both cases, and says the results were not compared.

Scores are the same when they differ by less than 1e-9 of the larger one, as in `headwater diff`. A folder whose pin changed, an upgrade, is calculated on each release, so the job needs both releases in its cache. The comment shows its scores; what the release changed, line by line, is in the output of `headwater diff` and its JSON, and not yet in the comment.

When the databases and studies that reach the changed ones cannot be found, the comment says so, and the job fails.

On merge to the main branch, tagging and publishing are not built yet (M4).

### Using it in a repository of databases and studies

The reusable workflow is [study-diff.yml](../.github/workflows/study-diff.yml), and it runs the composite action [study-diff](../.github/actions/study-diff/action.yml). The calling workflow must grant the job `contents: read` and `pull-requests: write`, which is all the token gets, and pin the workflow and `headwater-ref` to one commit:

```yaml
on: pull_request
jobs:
  diff:
    permissions:
      contents: read
      pull-requests: write
    uses: LeonardoNatale/headwater/.github/workflows/study-diff.yml@<commit sha>
    with:
      headwater-ref: <the same commit sha>
      releases: /mnt/releases      # or headwater-home: /mnt/headwater-home
      methods: /mnt/methods/EF-3.1 # only for methods the releases do not hold
    secrets:
      headwater-token: ${{ secrets.HEADWATER_READ_TOKEN }}
```

| Input | Meaning |
|---|---|
| `headwater-ref` | A commit of the headwater repository. The workflow installs headwater from its lock file at that commit and runs the action from it. Required. |
| `headwater-repository` | The headwater repository. Default `LeonardoNatale/headwater`. |
| `studies-path` | Only databases and studies inside this folder. Default: the whole repository. |
| `releases` | Releases to pull into the cache before anything runs, one path per line. A path is a release folder (`name@hash`) or a folder that holds release folders. |
| `headwater-home` | Use this folder as `HEADWATER_HOME`, for releases that are already in a cache folder. |
| `methods` | Method folders, one path per line, for the methods that databases and studies name by id and their releases do not hold. Each run of `headwater diff` gets them as `--method-folder`. |
| `headwater-token` (secret) | Reads the headwater repository, while it is private. |

**Releases.** The databases pin a release, and the job needs it in its cache. The repository can hold a small release in a folder and name it in `releases`. A licensed release stays outside git: it sits in a folder the job can read, such as a mounted bucket on a self-hosted runner, and is named in `releases` (pulled into the cache) or `headwater-home` (used where it is). Access follows the licence of the data. The workflow reads releases from folders only. For a release in a bucket, pull it in an earlier step with `headwater release pull <URL>`, with the `gcs` or `s3` extra and the cloud's own login ([A team sharing releases](#a-team-sharing-releases)), and name that cache in `headwater-home`.

**Methods.** A database imported from Excel with a method folder, such as `methods/EF-3.1` from ecoinvent's LCIA workbook, names a method its release does not hold, by its id. The job needs that folder, and gets it as it gets a release: a method of licensed factors stays outside git, in a folder the job can read, such as the mounted bucket that holds the releases, and is named in `methods` (`methods: /mnt/methods/EF-3.1`). A method of your own can be a folder of the repository. Each `headwater diff` gets the folders as `--method-folder`: a folder fills only a method a database or a study names and its release does not hold.

**Limits.**

- A pull request from a fork gets a read-only token, so it gets the job summary and no comment.
- The pull request's own code runs in the job, with the token above. That is fine while everyone who can open a pull request is trusted. Do not switch to `pull_request_target` to get around the fork limit: it would run that code with the base repository's secrets.
- Only changes inside a database or a study folder are found, and the databases and studies that reach a changed database. A database uses only the folders next to it, and a study's products are in the folders next to it, so they are among them.
- Calling the workflow from another repository needs that repository to be allowed to use this private one, and the token above. It is not exercised yet; the demo calls it from this repository.

### The demo

[src/headwater/demo](../src/headwater/demo/README.md) is a repository in miniature, shipped in the package (`headwater demo FOLDER` copies it): four toy databases, one of them `dyes` that another uses, the study `tshirt-footprint` of the dyed T-shirt, and the toy background as a frozen release, which the databases pin. A pull request that lowers the dye's CO2 gets a comment that lists `dyes`, then `tshirt-dyed`, then the study. The workflow [study-diff-demo.yml](../.github/workflows/study-diff-demo.yml) runs on pull requests that touch it, through the reusable workflow. The library's own CI runs only on toy fixtures, so no licensed data is needed.

The attribution is the costly part. The diff makes each difference alone to the old database and scores it for every indicator at once, from the stored scores of the background ([benchmarks](appendix/benchmarks.md#diff-attribution)). Loading the background costs more than the attribution, so each folder loads it once ([benchmarks](appendix/benchmarks.md#diff-attribution)).

## Notebooks

Notebooks stay thin. The model lives in the database files, where it can be reviewed and recalculated by CI. A notebook loads a database, calculates, and plots:

```python
import headwater as hw
bg = hw.load("ecoinvent-3.12-cutoff@b9428cdd8045", method="methods/EF-3.1")
hw.open("tshirt").calc(bg, "tshirt:tshirt", indicator="climate-change")
```

The six notebooks in [notebooks/](../notebooks/README.md) say what each needs and which ones CI runs.

## Later: people who do not use git

When people who do not use git need to edit (for example through a web page), there are two paths, to be chosen then:

- A service opens branches and pull requests for them, so review stays the same.
- DuckLake with a Postgres catalog as a shared editable store, with snapshots and a change feed. It has no primary or foreign keys, so headwater's validation does that job.
