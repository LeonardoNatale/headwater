# headwater

A Python library and command line for life cycle assessment (LCA). A background database such as ecoinvent is imported once, with the score of every product already solved. Your own data is a few CSV tables in git, and a footprint takes milliseconds, with no server and no database to install.

**Status: alpha.** File formats and the Python API may change before 1.0 ([roadmap](https://github.com/LeonardoNatale/headwater/blob/main/docs/roadmap.md)).

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
headwater calc project/tshirt-footprint
```

`headwater demo` copies a toy background, frozen as a release, four small databases and a study into `headwater-demo`. Each database names the release it was written for, `background: toy-background@6864b7dbb65d` in its `database.yaml`, and headwater looks that name up in your release cache: the folder `HEADWATER_HOME` names, `~/.headwater` by default. `release pull` checks the release's hash and copies it there, once. Every database and study on your machine then uses it, and the hash means the same data gives the same numbers on any machine. `calc` scores the study, one dyed T-shirt: 1.24265 kg CO₂-eq, with the data versions and the solver checks behind it.

The same in Python, in that folder:

```python
import headwater as hw

background = hw.load("toy-background@6864b7dbb65d")
[result] = hw.open("project/tshirt-footprint").calc(background)
print(f"{result.score:.6f}")  # 1.242646
```

`headwater --help` lists every command, each with an example on the demo.

## Learn

- [How headwater works](https://github.com/LeonardoNatale/headwater/blob/main/docs/how-it-works.md): the three ideas behind it, on one T-shirt. Two pages.
- [Glossary](https://github.com/LeonardoNatale/headwater/blob/main/docs/appendix/glossary.md): every term, with an example.
- Three notebooks, to read on GitHub with their outputs, or to run in a clone of the repository ([how](https://github.com/LeonardoNatale/headwater/blob/main/notebooks/README.md)):
  - [01-first-footprint](https://github.com/LeonardoNatale/headwater/blob/main/notebooks/01-first-footprint.ipynb): the demo's T-shirt, step by step: load, calculate, what it is made of, where it is emitted, which lines matter most.
  - [02-writing-a-database](https://github.com/LeonardoNatale/headwater/blob/main/notebooks/02-writing-a-database.ipynb): a database written file by file, a study with scenarios, and the mistakes headwater catches.
  - [03-case-study](https://github.com/LeonardoNatale/headwater/blob/main/notebooks/03-case-study.ipynb): a backpack on invented data, from its footprint to two scenarios, a Monte Carlo range and a design change in git.

More notebooks cover ecoinvent 3.12 and the same tasks in Brightway.

## Using headwater with an AI agent

headwater ships a skill: a short guide that Claude Code, or another AI agent, reads before it uses headwater. It sits in the package as the [Library Skills](https://github.com/tiangolo/library-skills) convention asks, as FastAPI's does. Link it into your project once:

```bash
pip install headwater
headwater demo headwater-demo && cd headwater-demo
headwater skill install
```

Then ask Claude "what drives the footprint of project/tshirt-footprint?". It runs `headwater contributions project/tshirt-footprint --json` and answers from the output. The links in `.claude/skills` and `.agents/skills` serve Claude Code, Codex, Copilot and Cursor, and follow upgrades of headwater. `uv tool run library-skills --claude` makes the same links, for every library of a project that ships a skill. `headwater skill show` prints the skill. Every command but `skill show` takes `--json`. Details in [workflows.md](https://github.com/LeonardoNatale/headwater/blob/main/docs/workflows.md#using-headwater-with-an-ai-agent).

## Why headwater

- **An error, never a wrong number.** Unlinked inputs, unknown units and numerically unsafe results stop the work with a list of what is wrong. A flow the method does not count is listed on the result, never dropped.
- **Every number explained.** What a product buys, where its emissions happen, which lines move it most, and why it changed between two commits.
- **Every number reproducible.** A result records the release, the method, the solver settings and the accuracy check behind it.
- **Fast once loaded.** On a synthetic background the size of ecoinvent, a study scores in 42 ms ([benchmarks](https://github.com/LeonardoNatale/headwater/blob/main/docs/appendix/benchmarks.md#study-queries-on-a-release)) and 50,000 SKUs in 55 ms ([benchmarks](https://github.com/LeonardoNatale/headwater/blob/main/docs/appendix/benchmarks.md#calculation-from-a-release)).

We studied Brightway, the leading open-source Python LCA framework, before we built headwater: [docs/findings.md](https://github.com/LeonardoNatale/headwater/blob/main/docs/findings.md) says what we found.

One example. A toy T-shirt factory buys 0.25 kg of cotton fabric and 0.5 kWh of electricity per T-shirt. The background already knows the score of each thing it buys:

| Bought | Amount | Stored score (kg CO₂-eq per unit) | Contribution |
|---|---|---|---|
| cotton fabric | 0.25 kg | 3.7086 | 0.92715 |
| electricity | 0.5 kWh | 0.5298 | 0.26490 |
| **T-shirt** | | | **1.19205** |

Change the fabric to 0.30 kg and the answer is 1.37748, with no work on the background. [docs/calculation.md](https://github.com/LeonardoNatale/headwater/blob/main/docs/calculation.md) explains the math on this example.

## Docs

- [docs/workflows.md](https://github.com/LeonardoNatale/headwater/blob/main/docs/workflows.md): setup with ecoinvent, every command, working with git, the results diff in CI.
- [docs/design.md](https://github.com/LeonardoNatale/headwater/blob/main/docs/design.md): principles, data model and file formats.
- [docs/calculation.md](https://github.com/LeonardoNatale/headwater/blob/main/docs/calculation.md): how results are computed, explained and updated, and the Python API.

A database made with headwater 0.1.0 holds `processes/`: convert it once with `headwater database convert <folder>` ([workflows.md](https://github.com/LeonardoNatale/headwater/blob/main/docs/workflows.md#converting-the-layout-before-020)).

## Contributing

Working in a clone, the rules for code, the roadmap and the decisions: [CONTRIBUTING.md](https://github.com/LeonardoNatale/headwater/blob/main/CONTRIBUTING.md).

## Licence

Apache-2.0: [LICENSE](https://github.com/LeonardoNatale/headwater/blob/main/LICENSE). The demo, the case study and the test fixtures are toy or invented data. No licensed data is in the package or the repository.
