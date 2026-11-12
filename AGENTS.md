# AGENTS.md

Guidance for anyone, human or AI, writing code in this repository.

## Read first, in this order

1. [README.md](README.md): what the library is for.
2. [docs/design.md](docs/design.md): principles, data model, file formats.
3. [docs/calculation.md](docs/calculation.md): before touching anything under `calc/`.
4. [docs/decisions.md](docs/decisions.md): do not reopen a settled decision in code. Propose a change to the decision first.
5. [docs/roadmap.md](docs/roadmap.md): build only what the current milestone needs.

## Rules that are not negotiable

1. **Ids are stable.** A dataset's id is its source identity plus version (for example `ecoinvent:3.12:cutoff:<activity uuid>_<product uuid>`). Integer matrix indices exist only inside one calculation. Never store them, return them, or key anything on them.
2. **float64 everywhere.** Amounts, factors, scores, arrays on disk and in memory.
3. **Fail loudly.** Unresolved links, unknown units, a study's own flows that the method does not cover, a matrix that cannot be solved, a residual above tolerance: raise an error that names the processes or flows involved. Background flows the method does not cover are listed on the result, never dropped without a trace. Never drop data, never return NaN or a partial number without saying so.
4. **Releases are immutable.** A release is written once, to a temporary path, then published under its content hash. Nothing edits a release in place.
5. **Signs live in one place.** Every exchange has a role (see [docs/design.md](docs/design.md#roles-and-signs)). One function turns roles into matrix signs. No other code decides a sign.
6. **No organisation-specific code in `headwater`.** Company storage, exports and reporting rules go in a separate `headwater-<org>` package.
7. **No licensed data in git.** Dataset names and counts are fine in docs and examples. Amounts, factors, scores, and copies of datasets or files are not, anywhere: code, fixtures, tests, docs, benchmark results. Tests use the toy fixtures. Integration tests read licensed data from the path in `ECOINVENT_DIR` and are skipped when it is not set.
8. **Calculation code is pure.** Code under `calc/` takes arrays and returns arrays. No file access, no network, no global state, no caching. Caching and I/O live in the layer that calls it.
9. **Every result carries its provenance.** Release hashes, method and version, study commit, solver settings and the residual check.
10. **Claims are measured.** Any statement about speed, size or accuracy, in a doc, a docstring or a PR, cites a script in [bench/](bench/) and the machine it ran on. Put the number in [docs/appendix/benchmarks.md](docs/appendix/benchmarks.md) and link to it.

## Tests

- **Toy fixtures with hand-checked numbers.** The T-shirt example in [docs/calculation.md](docs/calculation.md) is the first fixture: 1.19205, 1.25205 with the waste line, 0.13245 for the electricity sensitivity. A change that moves these numbers is a bug until proven otherwise.
- **Integration tests on real data are opt-in, and the ecoinvent ones run on version 3.12 only.** They need `ECOINVENT_DIR`, the 3.12 cut-off folder (APOS imports read `ECOINVENT_APOS_DIR`, the 3.12 APOS folder; ecoSpold1 imports read `ECOSPOLD1_DIR`, a folder of `.xml` files), and parity tests against Brightway also need `HEADWATER_WORK`, where `bench/bw_reference.py` writes Brightway's matrices and scores. Tests never import Brightway. CI does not have licensed data and runs only synthetic fixtures.
- **Compare floats with a tolerance.** Relative 1e-9 for toy fixtures, 1e-6 against other engines. Never compare floats for exact equality.

## Commands

```
uv sync
uv run ruff check . && uv run ruff format --check .
uv run pyright
uv run pytest
```

Python 3.14 for development (`.python-version`); the package supports 3.13+. Tools come from the project's dev dependencies, pinned in `uv.lock`; never rely on globally installed ones.

## Branches, commits and pull requests

- One task, one branch, one pull request. Branch names: `<milestone>/<slug>`, for example `m0/calc-core`.
- Commit messages follow Conventional Commits: `feat(calc): rank-1 score update`.
- Open the pull request ready for review, never as a draft. The body says: the goal in two sentences, what changed, the tests and checks run with their result, and the proof (for calculation code: hand-checked numbers or a comparison against a reference).
- Stay inside the files your task owns. If you need to change another file, say so in the pull request and keep the change minimal.
- CI must pass. Never merge, never enable auto-merge, never force-push to `main`. The owner reviews and merges.
- Plans, task notes and logs never go into the repository.

## Writing

- Plain English, short sentences. One concrete example before the general rule.
- No em dashes. Use commas, colons or full stops.
- State how things are now. What changed and why belongs in the commit or PR, not in the docs or comments.
