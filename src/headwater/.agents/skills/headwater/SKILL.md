---
name: headwater
description: Life cycle assessment (LCA) with the headwater command line. Score a database or a study (a carbon footprint in kg CO2-eq, and other indicators), explain a score (contributions, where it is emitted, sensitivity), total each flow such as water or energy (inventory), change a line and validate it, compare two versions in git, find a background process, import from Excel or Brightway. Use it when a folder holds database.yaml or study.yaml with processes.csv and exchanges.csv, when a release is named like name@hash, or when the user asks about a footprint or an LCA made with headwater.
license: Apache-2.0
metadata:
  headwater-version: "0.4.0"
---

# headwater

headwater calculates life cycle assessment (LCA) scores, such as a carbon footprint in kg CO2-eq. A database or a study is plain files in git. The background is a frozen release. Every command below takes `--json` and then prints one JSON document.

## First steps

```bash
headwater --version
headwater demo headwater-demo
cd headwater-demo
headwater release pull releases/toy-background@6864b7dbb65d
headwater calc project/tshirt-footprint --json
```

The demo is a toy, with no licence: a T-shirt of cotton fabric, electricity and dye. Its README.md says what each folder is. This is the skill of headwater 0.4.0. If `headwater --version` prints another version, the skill is an old copy: run `headwater skill install --force`.

## What the files are

- A database is a folder with `database.yaml` (its name, the release it pins in `background:`, its `methods`), `processes.csv` (one row per process), `exchanges.csv` (one row per line: `process,line,role,flow,amount,unit,supplier`) and `flows.csv`. Example: `project/tshirt-dyed`.
- A study is a folder with `study.yaml`, which names products of the databases next to it and their amounts, and optional `scenarios/`. Example: `project/tshirt-footprint`.
- A release is a frozen background with its methods, named `name@hash`, kept in a cache (`releases/` in `HEADWATER_HOME`, `~/.headwater` by default). `headwater release pull` puts one there, and `headwater release list --json` lists what the cache holds.
- A process id is `<database>:<process>`, such as `tshirt-dyed:tshirt`, or a background process such as `toy:electricity-production`. A line is its process and its line id: `tshirt-dyed:tshirt line e02`.

## Score a database or a study

```bash
headwater calc project/tshirt-footprint --json
headwater calc project/tshirt-footprint --scenario cleaner-dye --json
headwater calc project/tshirt-dyed --product tshirt-dyed:tshirt --json
```

Read `results[].score`, with `results[].indicator.unit`. A study scores each of its products for its amount. A database scores one unit of each product, or those `--product` names. `--indicator` picks indicators of a method that has several.

## Explain a score

- `headwater contributions <folder> --json`: what the score buys, level by level, in `results[].tree` (each node has `score`, `share` of the whole, `direct` and `children`), then the lines that move it most, in `results[].sensitivity`.
- `headwater where <folder> --json`: which processes emit the score, anywhere upstream, and in which locations.
- `headwater sensitivity <folder> --top 10 --json`: the lines a person can change, largest elasticity first. The elasticity is the % change of the score per 1% change of the line. `sensitivity` is the change of the score per one more unit of the line.

- `headwater inventory <folder> --json`: the total of each elementary flow over the whole life cycle, with no method, in `results[].flows`. `--flow water --compartment "natural resource"` keeps the water taken and adds its total in `results[].sums`. It also takes a background process id with `--release`.

Each takes a database or a study, `--product`, and for a study `--scenario`. Example: on the demo, `headwater contributions project/tshirt-footprint --json` gives 1.24265 kg CO2-eq, and the cotton fabric, line e02 of `tshirt-dyed:tshirt`, is 74.6% of it.

## Change a line, then validate

A database's lines are the rows of its `exchanges.csv`. To change an amount, edit its row, then validate and calculate again. Example: 0.3 kg of fabric instead of 0.25.

```bash
# project/tshirt-dyed/exchanges.csv, the row of line e02:
# tshirt,e02,input,toy:cotton-fabric,0.3,kg,toy:fabric-production
headwater validate project/tshirt-footprint --json
headwater calc project/tshirt-footprint --json
```

From Python, the editor writes the same files and checks the whole database before it saves:

```python
import headwater as hw

bg = hw.load("toy-background@6864b7dbb65d")
editor = hw.open("project/tshirt-dyed").edit(bg)
editor.process("tshirt").set_exchange("e02", amount=0.3)
editor.save()
```

Validate after every edit. With `--json`, `valid` is false when there are `errors`, and each error has a `code`, the `item` it is about and a `message` that says what to change.

## Compare two versions

In git, `headwater diff HEAD project/tshirt-footprint --json` scores the folder at a ref and in the working tree, and ranks the lines that changed by their effect on each score. `headwater diff main my-branch project/tshirt-footprint --json` compares two refs. Commit before an edit, so there is something to compare with. `headwater database uses project/dyes --json` lists the databases and studies a change to `dyes` reaches.

## Find a background process

Never guess an id. Search the release:

```bash
headwater find electricity --release toy-background@6864b7dbb65d --json
```

`matches[].process_id` is the supplier to put on a line, and `matches[].flow_id` the flow it makes. `--location`, `--product` and `--kind` narrow the search. A database's own processes and flows are in its `processes.csv` and `flows.csv`.

## Import and convert

- A Brightway-format Excel workbook: `headwater database import-excel book.xlsx --release name@hash --out project/name --json`.
- A database of a Brightway project: `headwater database import-brightway <project> <database> --release name@hash --out project/name --json`.
- A folder from before 0.2.0, with `processes/` and one file per process: `headwater database convert <folder> --json`.
- A source into a release: `headwater release import <source> --json`. An impact method: `headwater method import`. Moving databases to a newer release: `headwater upgrade`.

An import that exits 1 has written the database, but some lines need a decision: `to_decide` lists each, with the reason and what it might be.

## JSON and exit codes

Every command but `skill show` takes `--json`. Standard output then holds one JSON document with a `format` key, such as `headwater-calc/3`. A failure is a document too: `format` is `headwater-error/1`, with the `exit_code` and `errors`, each with `code`, `item`, `message`, `location` and `text`.

Exit codes: 0 done; 1 the data has a problem (validation errors, a line not mapped, a calculation refused); 2 the command could not run as given (a wrong option, a missing folder or release). Every `--help` ends with an example that runs.

## Rules

- A number comes from a command's output, never from memory, from a README or from working it out by hand. Give it with its unit and 5 significant figures, as the text output prints it: 1.24265 kg CO2-eq, not 1.24. `--json` holds the full float64, such as 1.4280760000000003: round it, do not cut it short.
- Never invent an id, a flow or a release. Use `headwater find` for background processes, and read `processes.csv` and `flows.csv` for a database's own.
- An error names what to fix. Read its `code` and `message`, fix that, and run the command again.
- Validate after every edit, before you report a score.
- Ids are stable. Do not rename a process or a line: results, diffs and other databases name them.

## Docs

The docs of headwater 0.4.0:

- [README](https://github.com/LeonardoNatale/headwater/blob/v0.4.0/README.md)
- [The command line](https://github.com/LeonardoNatale/headwater/blob/v0.4.0/docs/workflows.md#command-line)
- [The Python API](https://github.com/LeonardoNatale/headwater/blob/v0.4.0/docs/calculation.md#python-api)
- [The file formats](https://github.com/LeonardoNatale/headwater/blob/v0.4.0/docs/design.md#file-formats)
- [Decisions](https://github.com/LeonardoNatale/headwater/blob/v0.4.0/docs/decisions.md)
