## Goal

<!-- Two sentences: what this pull request is for. -->

## What changed

<!-- The files or parts changed, and why. Name any file outside the task's own, with the reason. -->

## Checks

<!-- Each command run and its result. -->

- `uv run ruff check . && uv run ruff format --check .`:
- `uv run lint-imports`:
- `uv run pyright`:
- `uv run pytest`:

## Proof

<!-- For calculation code: hand-checked numbers or a comparison against a reference. The toy scores stay 1.19205, 1.25205 and 0.13245 unless this pull request says why they move. No licensed amounts, factors or scores. -->

## Checklist

Tick what applies, or delete the line. `AGENTS.md` has the rules.

- [ ] A new or changed command: `--json`, an example at the end of its `--help`, a schema in `src/headwater/cli/schemas/` with its hash in `tests/cli/schemas.sha256`, and a line in the skill (rule 11). A changed shape is a new format version.
- [ ] A change to the public Python API: D38 in `docs/decisions.md` updated first (rule 12).
- [ ] A new file format: a decision row in `docs/decisions.md`.
- [ ] A claim about speed, size or accuracy: a script in `bench/` and the number in `docs/appendix/benchmarks.md` (rule 10).
