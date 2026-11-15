# Notebooks

Six notebooks that teach headwater by running it, for someone who knows LCA and writes a little Python. Read 01 to 03 in order. On GitHub they show their outputs, so you can read them without running anything.

| Notebook | What you learn | Data | Needs | Outputs |
|---|---|---|---|---|
| [01-first-footprint](01-first-footprint.ipynb) | A footprint step by step: load a release, calculate, contributions, where, sensitivity, change one amount, add a waste line | the demo's toy T-shirt | | kept, re-run by CI |
| [02-writing-a-database](02-writing-a-database.ipynb) | Every file of a database, one by one: database.yaml, flows, processes and exchanges, overrides, parameters, cut-offs, no_factor, uses; a study next to it, with products, amounts and scenarios; and the mistakes headwater catches | a toy tote bag on the demo's background | | kept, re-run by CI |
| [03-case-study](03-case-study.ipynb) | A product from start to end: validate, calculate, contributions, where, sensitivity, two scenarios, a Monte Carlo range, a design change shown with `headwater diff` | a backpack on [invented data](case-study/README.md) | | kept, re-run by CI |
| [04-working-with-ecoinvent](04-working-with-ecoinvent.ipynb) | Import ecoinvent 3.12 and its methods (EF 3.1, IPCC 2021 carbon indicators), find, footprint, where, contributions, a database on ecoinvent, many products at once | ecoinvent 3.12 cut-off | an ecoinvent licence | removed before commit |
| [05-coming-from-brightway](05-coming-from-brightway.ipynb) | The same tasks in Brightway and in headwater, side by side, and what differs | toy T-shirt | the `brightway` group | kept, refreshed by hand |
| [06-brightway-and-headwater-on-ecoinvent](06-brightway-and-headwater-on-ecoinvent.ipynb) | Notebook 05 on ecoinvent 3.12, with every task timed in both tools | ecoinvent 3.12 cut-off | an ecoinvent licence, the `brightway` group | removed before commit |

Notebooks 01 and 02 copy the demo that `pip install headwater` ships, with `headwater demo`, as the quick start of the [README](../README.md) does. Notebook 03 reads its data from [case-study/](case-study/README.md), next to it. Notebooks 01 to 03 work in a temporary folder and a temporary `HEADWATER_HOME`, so they change nothing in the repository or in your cache.

## Run them in VS Code

1. Clone the repository and build the environment from its root. The `notebooks` group adds Jupyter's kernel and pandas. The `brightway` group adds Brightway, for notebooks 05 and 06 only.

   ```bash
   git clone https://github.com/LeonardoNatale/headwater.git
   cd headwater
   uv sync --group notebooks --group brightway
   ```

2. Open a notebook and pick the kernel `.venv/bin/python` of the repository (Select Kernel, Python Environments).

A plain `uv sync` removes the packages of these groups again, since it installs exactly the default groups. Run the command above again after it.

Without VS Code:

```bash
uv run --group notebooks --group brightway --with jupyterlab jupyter lab notebooks
```

## Notebooks 04 and 06: ecoinvent

They need a licensed ecoinvent 3.12 copy. Set these before you start VS Code or Jupyter:

```bash
export ECOINVENT_DIR="/path/to/ecoinvent 3.12_cutoff_ecoSpold02"      # holds datasets/ and MasterData/
export ECOINVENT_LCIA="/path/to/LCIA Implementation 3.12.xlsx"
export HEADWATER_HOME=~/lca/headwater                                  # optional, default ~/.headwater
export NOTEBOOK_BRIGHTWAY_DIR=~/lca/notebook-brightway                 # optional, notebook 06 only
```

Notebook 04 imports the files once and reuses the release in `HEADWATER_HOME` after that. Notebook 06 imports ecoinvent into Brightway too, its slowest step: with `NOTEBOOK_BRIGHTWAY_DIR` it keeps that folder and imports once; without it, it imports into a new temporary folder on every run. Neither notebook opens your own Brightway folder, and neither logs in to ecoinvent.

Their outputs hold ecoinvent values: clear them before you commit.

```bash
uv run --group notebooks python notebooks/refresh_outputs.py --clear-licensed
```

How CI runs the notebooks, and the rules for editing them, are in [CONTRIBUTING.md](../CONTRIBUTING.md#notebooks).
