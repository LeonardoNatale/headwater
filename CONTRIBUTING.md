# Contributing

This page is for people who change headwater: its code, its docs or its notebooks. To use headwater, start with the [README](README.md).

## Read first

| Read | When |
|---|---|
| [AGENTS.md](AGENTS.md) | Before writing any code, by hand or with an AI agent. The rules that are not negotiable. |
| [docs/design.md](docs/design.md) | Principles, architecture, data model, file formats. |
| [docs/calculation.md](docs/calculation.md) | Before touching anything under `calc/`. |
| [docs/decisions.md](docs/decisions.md) | What is settled, and what is still open. Propose a change to a decision before you change the code. |
| [docs/roadmap.md](docs/roadmap.md) | What to build in which order, and the test that closes each step. |
| [docs/findings.md](docs/findings.md) | Why the design is the way it is. |
| [docs/appendix/benchmarks.md](docs/appendix/benchmarks.md) | Every measured number, with its scope and script. |
| [docs/appendix/sources.md](docs/appendix/sources.md) | Links. |

## Working in a clone

```bash
git clone https://github.com/LeonardoNatale/headwater.git
cd headwater
uv sync
uv run ruff check . && uv run ruff format --check .
uv run pyright
uv run pytest
```

Python 3.14 for development (`.python-version`); the package supports 3.13 and later. Tools come from the project's dev dependencies, pinned in `uv.lock`. Never rely on globally installed ones.

Two more dependency groups:

- `notebooks`: Jupyter's kernel and pandas, for the notebooks.
- `brightway`: Brightway (bw2data 4.5.4, bw2calc 2.3, bw2io 0.9.14, peewee 3.19.0), for notebooks 05 and 06 only.

A plain `uv sync` removes the packages of these groups again, since it installs exactly the default groups. Add them with `uv sync --group notebooks --group brightway`.

Integration tests on real data are opt-in. They read a licensed ecoinvent 3.12 copy from the path in `ECOINVENT_DIR` and are skipped when it is not set ([AGENTS.md](AGENTS.md#tests)). CI has no licensed data.

## Goal

- **Correct numbers, or an error.** Unlinked inputs, unknown units and numerically unsafe results stop the work with a list of what is wrong. A flow the method does not cover is listed on the result. In a database's own processes it is an error until a row of the database's `no_factor.csv` says why it counts 0. Nothing is dropped silently.
- **Explainable numbers.** For any result: what it buys, where emissions physically happen, and which inputs matter most.
- **Reproducible numbers.** Every result records the exact data versions, method, code commit and solver settings that produced it.
- **Easy setup.** `pip install headwater`, a folder, and git. Background data is imported once and reused, never re-imported per project.
- **Fast enough to stop thinking about speed.** Editing a database, scoring a portfolio of SKUs and exploring scenarios happen in milliseconds once the background is loaded.
- **A general library.** Nothing in it is specific to one company. Organisation-specific code (storage, exports, reporting rules) lives in separate packages.

## What we found

We studied Brightway, the leading open-source Python LCA framework, by reading its code, reproducing its failures and timing it on ecoinvent 3.10. Its calculation core is sound. Its data layer is where things go wrong: integer ids that change on every rewrite, writes that delete and reinsert, data spread over seven kinds of files, and failures that return a wrong number instead of an error. We also checked our own design against LCA practice (ecoinvent conventions, ISO 14044/14067, PEF, GHG Protocol) and corrected it. Details and evidence: [docs/findings.md](docs/findings.md). All measurements: [docs/appendix/benchmarks.md](docs/appendix/benchmarks.md).

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
| Databases, studies and history | YAML and CSV files in git |
| Records and validation | Pydantic |
| Parsing ecoSpold2 | lxml |
| Tooling | uv, ruff, pyright, pytest |

No Postgres and no server in v1. Reasons in [docs/design.md](docs/design.md#stack).

## Notebooks

The notebooks are read on GitHub with their outputs, so the outputs of the toy notebooks (01, 02, 03 and 05) are committed and kept current. Notebooks 04 and 06 run on ecoinvent: their outputs are never committed.

### How CI runs them

```bash
uv run --locked --group notebooks pytest --nbmake notebooks
```

This runs every notebook and fails on any error. Notebooks 04 and 06 are skipped unless `ECOINVENT_DIR` and `ECOINVENT_LCIA` are set, and 05 and 06 when Brightway is not installed ([notebooks/conftest.py](notebooks/conftest.py)). CI does not install Brightway, so it runs 01, 02 and 03. The toy notebooks check their hand-checked values with an `assert`, so a result that moves fails CI. The same command runs [notebooks/test_names.py](notebooks/test_names.py), which checks that every headwater function, argument and command the notebooks name exists, in their text and in the code of 04, 05 and 06.

```bash
uv run --locked --group notebooks python notebooks/refresh_outputs.py --check
```

This runs the toy notebooks again and fails when what they print is not what the files hold, so the kept outputs cannot go stale. Without Brightway it leaves 05 out and says so. It also fails when 04 or 06 holds an output.

### Notebook 05: run it by hand

CI does not run notebook 05, so nothing refreshes its kept outputs for you. After a change to headwater's API or to its printed output, and before a release, run it with Brightway installed:

```bash
uv sync --group notebooks --group brightway
uv run --group notebooks --group brightway pytest --nbmake notebooks/05-coming-from-brightway.ipynb
uv run --group notebooks --group brightway python notebooks/refresh_outputs.py --check 05-coming-from-brightway.ipynb
```

If the check says the outputs are stale, refresh them and commit the notebook:

```bash
uv run --group notebooks --group brightway python notebooks/refresh_outputs.py 05-coming-from-brightway.ipynb
```

### Editing a notebook

- Print rounded numbers (`:.5f`). Never print a path, a time, a memory address or a hash that changes between runs. A notebook that commits to git fixes the author and the dates, so the commit hash is the same on every run (03 does).
- Check the key results with an `assert` against a hand-checked value, so CI fails when a number moves. Compare floats with a tolerance: 1e-9 relative for headwater, 1e-6 against Brightway.
- Brightway prints log lines with the time and progress bars. Notebook 05's set-up cell shows how to turn them off.
- Precede every code cell with one or two sentences: what it does, and what to look at in its output.
- After a change to a toy notebook, run `uv run ruff format notebooks`, then refresh the kept outputs from the repository root: `uv run --locked --group notebooks --group brightway python notebooks/refresh_outputs.py`, or name the notebooks to refresh only those. Without the `brightway` group, 05 is left out. A notebook that fails is not written.
- Never commit outputs of notebooks 04 and 06. They hold ecoinvent values. `uv run --group notebooks python notebooks/refresh_outputs.py --clear-licensed` clears them.
- The case study's data in [notebooks/case-study](notebooks/case-study/README.md) is invented. Keep it so: no amount or factor from ecoinvent or any other licensed source.

## Benchmarks

Any statement about speed, size or accuracy, in a doc, a docstring or a pull request, cites a script in [bench/](bench/README.md) and the machine it ran on, with the number in [docs/appendix/benchmarks.md](docs/appendix/benchmarks.md) ([AGENTS.md](AGENTS.md#rules-that-are-not-negotiable), rule 10).

## Pull requests

One task, one branch, one pull request, ready for review. Commit messages follow Conventional Commits, and CI must pass. The owner reviews and merges. The details are in [AGENTS.md](AGENTS.md#branches-commits-and-pull-requests).
