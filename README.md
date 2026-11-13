# headwater

A Python library for life cycle assessment (LCA).

Background databases like ecoinvent are imported once per version into immutable Parquet files, with every product's impact scores already solved. Studies are plain text files in git. Calculations run inside your Python process with DuckDB and SciPy. There is no server and no database to install.

**Status: alpha.** File formats and the Python API may change before 1.0. headwater imports ecoinvent 3.12, cut-off and APOS, and other sources (ecoSpold1, SimaPro CSV, openLCA JSON-LD) into frozen releases, and pulls them from a folder or a bucket URL. Databases, two CSV tables (`processes.csv` and `exchanges.csv`) beside a `database.yaml`, written by hand or converted from a Brightway Excel workbook or a database of a Brightway project, and the studies that ask a question of them, calculate, explain, give single scores and Monte Carlo ranges, upgrade to a new release and show their changes as a diff on pull requests. Not there yet: publishing a database as a release, and EF 3.1 single scores with JRC's factors ([roadmap](https://github.com/LeonardoNatale/headwater/blob/main/docs/roadmap.md)).

## Install

```bash
pip install headwater
```

Python 3.13 or later. Extras: `headwater[pandas]` for tables as DataFrames, `headwater[gcs]` and `headwater[s3]` to pull releases from Google Cloud Storage or S3.

## Quick start

The demo is a toy: it needs no licence and no download beyond the package.

```bash
pip install headwater
headwater demo headwater-demo
cd headwater-demo
headwater release pull releases/toy-background@6864b7dbb65d
headwater calc studies/tshirt-footprint
```

`headwater demo` copies a toy background, frozen as a release, four small databases and a study into `headwater-demo`. `release pull` checks the release and copies it into your cache, `~/.headwater` or `$HEADWATER_HOME`. `calc` scores the study, one dyed T-shirt: 1.24265 kg CO₂-eq, with the data versions and the solver checks behind it.

The same in Python, in that folder:

```python
import headwater as hw

background = hw.load("toy-background@6864b7dbb65d")
[result] = hw.open("studies/tshirt-footprint").calc(background)
print(f"{result.score:.6f}")  # 1.242646
```

A database made with headwater 0.1.0 holds `processes/`: convert it once with `headwater database convert <folder>` ([workflows.md](https://github.com/LeonardoNatale/headwater/blob/main/docs/workflows.md#converting-the-layout-before-020)).

Next: `headwater --help`, the folder's README.md, and [Setup](https://github.com/LeonardoNatale/headwater/blob/main/docs/workflows.md#setup) to import ecoinvent.

## One example

A toy T-shirt factory buys 0.25 kg of cotton fabric and 0.5 kWh of electricity per T-shirt. The background database already knows the score of each thing it buys:

| Bought | Amount | Stored score (kg CO₂-eq per unit) | Contribution |
|---|---|---|---|
| cotton fabric | 0.25 kg | 3.7086 | 0.92715 |
| electricity | 0.5 kWh | 0.5298 | 0.26490 |
| **T-shirt** | | | **1.19205** |

Change the fabric to 0.30 kg and the answer is 1.37748, with no work on the background. The same idea scales to ecoinvent (23,522 products) and to tens of thousands of SKUs. [docs/calculation.md](https://github.com/LeonardoNatale/headwater/blob/main/docs/calculation.md) explains the math on this example.

## Goal

- **Correct numbers, or an error.** Unlinked inputs, unknown units and numerically unsafe results stop the work with a list of what is wrong. A flow the method does not cover is listed on the result. In a database's own processes it is an error until a row of the database's `no_factor.csv` says why it counts 0. Nothing is dropped silently.
- **Explainable numbers.** For any result: what it buys, where emissions physically happen, and which inputs matter most.
- **Reproducible numbers.** Every result records the exact data versions, method, code commit and solver settings that produced it.
- **Easy setup.** `pip install headwater`, a folder, and git. Background data is imported once and reused, never re-imported per project.
- **Fast enough to stop thinking about speed.** Editing a database, scoring a portfolio of SKUs and exploring scenarios happen in milliseconds once the background is loaded.
- **A general library.** Nothing in it is specific to one company. Organisation-specific code (storage, exports, reporting rules) lives in separate packages.

## What we found

We studied Brightway, the leading open-source Python LCA framework, by reading its code, reproducing its failures and timing it on ecoinvent 3.10. Its calculation core is sound. Its data layer is where things go wrong: integer ids that change on every rewrite, writes that delete and reinsert, data spread over seven kinds of files, and failures that return a wrong number instead of an error. We also checked our own design against LCA practice (ecoinvent conventions, ISO 14044/14067, PEF, GHG Protocol) and corrected it. Details and evidence: [docs/findings.md](https://github.com/LeonardoNatale/headwater/blob/main/docs/findings.md). All measurements: [docs/appendix/benchmarks.md](https://github.com/LeonardoNatale/headwater/blob/main/docs/appendix/benchmarks.md).

## What we build

```
headwater            the library: data model, importers, validation, releases,
                     calculation, database files, CLI and Python API
headwater-<org>      optional, private, per organisation: storage access,
                     exports to internal systems, reporting rules
```

## Stack

| Part | Choice |
|---|---|
| Language | Python 3.13+ (developed on 3.14) |
| Background data | Parquet files (zstd), immutable, named by content hash |
| Query engine | DuckDB, in-process, in memory |
| Calculation | NumPy and SciPy sparse (SuperLU), solver pluggable |
| Studies and history | YAML and CSV files in git |
| Records and validation | Pydantic |
| Parsing ecoSpold2 | lxml |
| Tooling | uv, ruff, pyright, pytest |

No Postgres and no server in v1. Reasons in [docs/design.md](https://github.com/LeonardoNatale/headwater/blob/main/docs/design.md#stack).

## Docs

| Read | When |
|---|---|
| [AGENTS.md](https://github.com/LeonardoNatale/headwater/blob/main/AGENTS.md) | Before writing any code. Rules that are not negotiable. |
| [docs/findings.md](https://github.com/LeonardoNatale/headwater/blob/main/docs/findings.md) | You want to know why the design is the way it is. |
| [docs/design.md](https://github.com/LeonardoNatale/headwater/blob/main/docs/design.md) | Principles, architecture, data model, file formats. |
| [docs/calculation.md](https://github.com/LeonardoNatale/headwater/blob/main/docs/calculation.md) | How results are computed, explained and updated. |
| [docs/workflows.md](https://github.com/LeonardoNatale/headwater/blob/main/docs/workflows.md) | Setup, commands, collaboration with git, CI. |
| [notebooks/](https://github.com/LeonardoNatale/headwater/tree/main/notebooks) | You want to learn by running code, in a clone of the repository: getting started, a first footprint, writing a database, ecoinvent 3.12, and the same tasks in Brightway and headwater side by side. |
| [docs/roadmap.md](https://github.com/LeonardoNatale/headwater/blob/main/docs/roadmap.md) | What to build in which order, and the test that closes each step. |
| [docs/decisions.md](https://github.com/LeonardoNatale/headwater/blob/main/docs/decisions.md) | What is settled, and what is still open. |
| [docs/appendix/benchmarks.md](https://github.com/LeonardoNatale/headwater/blob/main/docs/appendix/benchmarks.md) | Every measured number, with its scope and script. |
| [docs/appendix/glossary.md](https://github.com/LeonardoNatale/headwater/blob/main/docs/appendix/glossary.md) | LCA and math terms in plain words. |
| [docs/appendix/sources.md](https://github.com/LeonardoNatale/headwater/blob/main/docs/appendix/sources.md) | Links. |
| [bench/](https://github.com/LeonardoNatale/headwater/tree/main/bench) | The scripts behind the measurements. |

## Licence

Apache-2.0: [LICENSE](https://github.com/LeonardoNatale/headwater/blob/main/LICENSE). The demo and the test fixtures are toy data. No licensed data is in the package or the repository.
