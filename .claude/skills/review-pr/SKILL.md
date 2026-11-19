---
name: review-pr
description: Review a headwater pull request or local diff against the rules of AGENTS.md and the checklist of the pull request template, and report each finding with the rule it breaks. Use when asked to review a headwater PR, a branch or uncommitted changes, or to check a change before opening a PR.
---

# Review a headwater pull request

Check a diff against [AGENTS.md](../../../AGENTS.md) and the checklist of [.github/pull_request_template.md](../../../.github/pull_request_template.md). Report findings; do not fix them unless asked.

## Get the diff

- A pull request: `gh pr diff <number> --repo LeonardoNatale/headwater` and `gh pr view <number> --repo LeonardoNatale/headwater` for its body.
- A branch: `git diff main...HEAD`. Uncommitted changes: `git diff HEAD`.
- Read the whole diff before judging any line. Read the files around a change when the diff alone does not show the rule is kept.

## Run the checks

```bash
uv run ruff check . && uv run ruff format --check .
uv run lint-imports
uv run pyright
uv run pytest
```

A failing check is a finding: give the command and the first lines of its output. The machine checks cover rules 5, 11, 12 and 13 in part; the rest needs reading.

## Check each rule

For each rule of AGENTS.md, what to look for in the diff:

1. Ids are stable: no matrix index stored, returned or used as a key outside one calculation.
2. float64: no float32, no `dtype` left to default where amounts, factors or scores are stored.
3. Fail loudly: no `except` that drops data, no NaN or partial number returned without saying so, errors name the processes or flows.
4. Releases are immutable: nothing edits a release in place; writes go to a temporary path, then publish by hash.
5. Signs in one place: no code outside `calc/signs.py` decides a matrix sign from a role.
6. No organisation-specific code: no company name, storage or reporting rule.
7. No licensed data: no ecoinvent amount, factor or score in code, fixtures, tests, docs or the PR body. Names and counts are fine.
8. Calculation code is pure: nothing under `calc/` reads files, uses the network, keeps global state or caches.
9. Provenance: a new result carries release hashes, method and version, commit, solver settings and the residual.
10. Claims are measured: a statement about speed, size or accuracy cites a `bench/` script, the machine, and a row of `docs/appendix/benchmarks.md`.
11. Commands: a new or changed command has `--json`, a help example, a schema with its hash, and a line in the user skill. A schema listed in `tests/cli/schemas.sha256` changed only in descriptions; a changed shape is a new format version in a new file. Use the skill [add-command](../add-command/SKILL.md) as the list.
12. Public API: a name added to or removed from `headwater`, `Background`, `Database`, `Study` or `Result` comes with D38 updated in `docs/decisions.md`.
13. Layers: `calc/` imports only `headwater.model`, numpy, scipy and the standard library; `model/` imports no other part of headwater; nothing imports `cli/`. A contract in `pyproject.toml` weakened or an `ignore_imports` added is a finding.

Then the other sections of AGENTS.md:

- Tests: floats compared with a tolerance (1e-9 relative on toy fixtures, 1e-6 against other engines), never `==`. The toy scores 1.19205, 1.25205 and 0.13245 unchanged unless the PR says why. Integration tests skipped when their environment variable is not set.
- A settled decision in `docs/decisions.md` reopened in code without the decision changed first.
- Writing: plain English, short sentences, no em dashes, docs state how things are now.
- Scope: files outside the task's own named in the PR body. No plans, task notes or logs in the repository.

## Check the PR checklist

- The body has the goal in two sentences, what changed, each check with its result, and the proof.
- Each line of the checklist that applies is ticked and true: a command (rule 11), the public API (rule 12), a new file format (a decision row), a claim (rule 10).
- Branch `<milestone>/<slug>`, Conventional Commits, ready for review, not a draft.

## Report

One finding per line, the most serious first, each with its rule:

```
rule 7: tests/io/test_x.py:42: an ecoinvent factor in a fixture. Use the toy fixture.
rule 11: src/headwater/cli/flows.py: no schema for headwater-flows/1. Add src/headwater/cli/schemas/headwater-flows-1.schema.json and its hash.
checklist: the body gives no proof for the change to calc/solve.py. Add the hand-checked toy numbers.
```

Say `rule N` for AGENTS.md, `checklist` for the template, `check` for a failing command. End with the checks run and their result. When nothing is wrong, say so in one line and list the checks run.

## What not to flag

- Style that ruff accepts.
- A choice a decision in `docs/decisions.md` settles.
- Code the diff does not touch, unless the change breaks it.
