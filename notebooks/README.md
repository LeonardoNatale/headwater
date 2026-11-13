# Notebooks

Six notebooks that teach headwater by running it, for someone who knows LCA, has used Brightway a little and writes a little Python. Read them in order.

| Notebook | What you learn | Data | Needs | Outputs |
|---|---|---|---|---|
| [00-getting-started](00-getting-started.ipynb) | What headwater is, the words it uses against Brightway's, every way to load data, the main functions with what they take and return, the types you meet, errors | toy | | kept, re-run by CI |
| [01-first-footprint](01-first-footprint.ipynb) | A footprint step by step: load, calculate, contributions, where, sensitivity, change one amount | toy T-shirt | | kept, re-run by CI |
| [02-writing-a-database](02-writing-a-database.ipynb) | Every file of a database, one by one: database.yaml, flows, processes and exchanges, overrides, parameters, cut-offs, no_factor, uses; a study next to it, with products, amounts and scenarios; and the mistakes headwater catches | toy tote bag | | kept, re-run by CI |
| [03-working-with-ecoinvent](03-working-with-ecoinvent.ipynb) | Import ecoinvent 3.12 and its methods (EF 3.1, IPCC 2021 carbon indicators), find, footprint, where, contributions, a database on ecoinvent, many products at once | ecoinvent 3.12 cut-off | an ecoinvent licence | removed before commit |
| [04-coming-from-brightway](04-coming-from-brightway.ipynb) | The same tasks in Brightway and in headwater, side by side, and what differs | toy T-shirt | the `brightway` group | kept, refreshed by hand |
| [05-brightway-and-headwater-on-ecoinvent](05-brightway-and-headwater-on-ecoinvent.ipynb) | Notebook 04 on ecoinvent 3.12, with every task timed in both tools | ecoinvent 3.12 cut-off | an ecoinvent licence, the `brightway` group | removed before commit |

## Run them in VS Code

1. Build the environment from the repository root. The `notebooks` group adds Jupyter's kernel and pandas. The `brightway` group adds Brightway (bw2data 4.5.4, bw2calc 2.3, bw2io 0.9.14, peewee 3.19.0), for notebooks 04 and 05 only.

   ```bash
   uv sync --group notebooks --group brightway
   ```

2. Open a notebook and pick the kernel `.venv/bin/python` of the repository (Select Kernel, Python Environments).

A plain `uv sync` removes the packages of these groups again, since it installs exactly the default groups. Run the command above again after it.

Without VS Code:

```bash
uv run --group notebooks --group brightway --with jupyterlab jupyter lab notebooks
```

## Notebooks 03 and 05: ecoinvent

They need a licensed ecoinvent 3.12 copy. Set these before you start VS Code or Jupyter:

```bash
export ECOINVENT_DIR="/path/to/ecoinvent 3.12_cutoff_ecoSpold02"      # holds datasets/ and MasterData/
export ECOINVENT_LCIA="/path/to/LCIA Implementation 3.12.xlsx"
export HEADWATER_HOME=~/lca/headwater                                  # optional, default ~/.headwater
export NOTEBOOK_BRIGHTWAY_DIR=~/lca/notebook-brightway                 # optional, notebook 05 only
```

Notebook 03 imports the files once and reuses the release in `HEADWATER_HOME` after that. Notebook 05 imports ecoinvent into Brightway too, its slowest step: with `NOTEBOOK_BRIGHTWAY_DIR` it keeps that folder and imports once; without it, it imports into a new temporary folder on every run. Neither notebook opens your own Brightway folder, and neither logs in to ecoinvent.

Their outputs hold ecoinvent values: clear them before you commit.

```bash
uv run --group notebooks python notebooks/refresh_outputs.py --clear-licensed
```

## How CI runs them

```bash
uv run --locked --group notebooks pytest --nbmake notebooks
```

This runs every notebook and fails on any error. Notebooks 03 and 05 are skipped unless `ECOINVENT_DIR` and `ECOINVENT_LCIA` are set, and 04 and 05 when Brightway is not installed ([conftest.py](conftest.py)). CI does not install Brightway, so it runs 00, 01 and 02. The toy notebooks check the hand-checked values of [docs/calculation.md](../docs/calculation.md) with an `assert`, so a result that moves fails CI. The same command runs [test_names.py](test_names.py), which checks that every headwater function, argument and command the notebooks name exists, in their text and in the code of 03, 04 and 05.

```bash
uv run --locked --group notebooks python notebooks/refresh_outputs.py --check
```

This runs the toy notebooks again and fails when what they print is not what the files hold, so the kept outputs cannot go stale. Without Brightway it leaves 04 out and says so. It also fails when 03 or 05 holds an output.

## Notebook 04: run it by hand

CI does not run notebook 04, so nothing refreshes its kept outputs for you. After a change to headwater's API or to its printed output, and before a release, run it with Brightway installed:

```bash
uv sync --group notebooks --group brightway
uv run --group notebooks --group brightway pytest --nbmake notebooks/04-coming-from-brightway.ipynb
uv run --group notebooks --group brightway python notebooks/refresh_outputs.py --check 04-coming-from-brightway.ipynb
```

If the check says the outputs are stale, refresh them and commit the notebook:

```bash
uv run --group notebooks --group brightway python notebooks/refresh_outputs.py 04-coming-from-brightway.ipynb
```

## Editing a notebook

- Print rounded numbers (`:.5f`). Never print a path, a time, a memory address or a hash that changes between runs.
- Check the key results with an `assert` against a hand-checked value, so CI fails when a number moves. Compare floats with a tolerance: 1e-9 relative for headwater, 1e-6 against Brightway.
- Brightway prints log lines with the time and progress bars. Notebook 04's set-up cell shows how to turn them off.
- Precede every code cell with one or two sentences: what it does, and what to look at in its output.
- After a change to a toy notebook, run `uv run ruff format notebooks`, then refresh the kept outputs from the repository root: `uv run --locked --group notebooks --group brightway python notebooks/refresh_outputs.py`, or name the notebooks to refresh only those. Without the `brightway` group, 04 is left out. A notebook that fails is not written.
- Never commit outputs of notebooks 03 and 05. They hold ecoinvent values.
