---
name: add-command
description: Add a command to the headwater CLI or change one, with every piece the tests require (the function, COMMANDS, the help example, --json and its format, the JSON schema and its hash, a line in the user skill, docs). Use when adding, renaming or changing a `headwater` command or its `--json` output.
---

# Add or change a headwater command

Example used below: a new command `headwater flows <folder> --json` that prints `headwater-flows/1`. A command of a group, such as `database uses`, works the same way inside its group's `typer.Typer`.

Read [AGENTS.md](../../../AGENTS.md) first. Rule 11 is this checklist; the tests named in each step fail when a piece is missing.

## Checklist

1. The command function and its entry in `COMMANDS`.
2. Its example in `EXAMPLES`, the last line of its `--help`.
3. `--json`, its format constant, and failures through `fail`.
4. The JSON schema of the format and its hash.
5. A line in the user skill.
6. Tests of its behaviour.
7. Docs, and a decision row if it adds a file format.

## 1. The command function and COMMANDS

- Write `flows_command` in a module of its own, `src/headwater/cli/flows.py`. Its docstring's first paragraph is the help line; its other paragraphs say what it does, its exit codes (0 done, 1 the data has a problem, 2 the command could not run as given) and what it refuses.
- Add `"flows": Entry("headwater.cli.flows:flows_command", "<the first paragraph, on one line>")` to `COMMANDS` in [src/headwater/cli/__init__.py](../../../src/headwater/cli/__init__.py), in the place `--help` should list it.
- Import numpy, scipy, pyarrow, DuckDB and the file readers inside the function, never at the top of the module: `--help` must load none of them ([tests/cli/test_startup.py](../../../tests/cli/test_startup.py)).
- The command is a thin layer over the Python API in `query/`. A new public function or method there changes the public API: update D38 in [docs/decisions.md](../../../docs/decisions.md) and the lists in [tests/query/test_api.py](../../../tests/query/test_api.py) first (rule 12).
- No other module may import `cli/` (rule 13, `uv run lint-imports`).

## 2. The example in EXAMPLES

- Add `"flows": Example("headwater flows project/tshirt-footprint --json", DEMO)` to `EXAMPLES` in the same file. It must run in the folder of `headwater demo` with the release pulled, and take `--json`.
- An example that needs a file the demo lacks names a toy file; `toy_files` in [tests/cli/test_examples_and_json.py](../../../tests/cli/test_examples_and_json.py) copies it from `tests/fixtures` first. Never use licensed data.
- The test runs every example in order and checks that each `--help` ends with its example.

## 3. --json, the format, and failures

- Take `as_json: JsonOption = False` from [src/headwater/cli/_common.py](../../../src/headwater/cli/_common.py).
- Define the format next to the command, with the rule in its docstring:

  ```python
  FORMAT = "headwater-flows/1"
  """The `format` of `flows --json`. Within version 1 keys may be added, none is renamed or
  removed."""
  ```

- Print one document with `echo({"format": FORMAT, ...})` from [src/headwater/cli/_json.py](../../../src/headwater/cli/_json.py): indented, no NaN.
- Report a failure with `fail(error, code=...)`: under `--json` it prints a `headwater-error/1` document, without it a message on standard error.
- Changing the output of an existing command: adding a key keeps the version. Renaming, removing a key or changing its type is a new version (`headwater-flows/2`) and a new schema file; the old file stays.

## 4. The schema and its hash

- Write `src/headwater/cli/schemas/headwater-flows-1.schema.json`. Copy the shape of a schema of the same kind, such as `headwater-where-1.schema.json`: JSON Schema 2020-12, `title` the format, `"format": {"const": "headwater-flows/1"}`, every key the code prints in `properties`, the keys always printed in `required`, nullable values as `["string", "null"]`, every array with `items`. Never set `additionalProperties: false`.
- Take types from the code, not from one output: a value null in the demo may be a string elsewhere.
- A document of another format inside it: `{"$ref": "headwater-validate-2.schema.json"}`.
- Run `uv run pytest tests/cli/test_schemas.py`: it fails with the line to add to [tests/cli/schemas.sha256](../../../tests/cli/schemas.sha256). Add it once the format is final. From then on the schema never changes, except its descriptions.
- If the command prints another shape for other options (a database and a study, a scenario), add that command line to `VARIANTS` in [tests/cli/test_examples_and_json.py](../../../tests/cli/test_examples_and_json.py). Every `--json` run of the whole test suite is checked against its schema too ([tests/conftest.py](../../../tests/conftest.py)).

## 5. A line in the user skill

- Add a line naming `headwater flows` with its main options to [src/headwater/.agents/skills/headwater/SKILL.md](../../../src/headwater/.agents/skills/headwater/SKILL.md), in the section it belongs to. Every option it writes must exist, and the file stays under 200 lines ([tests/cli/test_skill.py](../../../tests/cli/test_skill.py)).
- `skill show` is the one command the skill need not name.

## 6. Tests

- A test file `tests/cli/test_flows_cli.py` with the toy fixtures (`toy`, `toy_args`, `toy_release` in [tests/cli/conftest.py](../../../tests/cli/conftest.py)): the output, each exit code, each error and what it names.
- Compare floats with a relative tolerance of 1e-9. The toy scores stay 1.19205, 1.25205 and 0.13245.

## 7. Docs

- Its paragraph in the Command line section of [docs/workflows.md](../../../docs/workflows.md), and its row in the table of formats under "Using headwater with an AI agent".
- A new file format, read or written: a decision row in [docs/decisions.md](../../../docs/decisions.md).
- A claim about speed: a script in `bench/` and the number in [docs/appendix/benchmarks.md](../../../docs/appendix/benchmarks.md) (rule 10).

## Check before the pull request

```bash
uv run ruff check . && uv run ruff format --check .
uv run lint-imports
uv run pyright
uv run pytest tests/cli/test_examples_and_json.py tests/cli/test_schemas.py tests/cli/test_startup.py tests/cli/test_skill.py tests/query/test_api.py
uv run pytest
```

Then open the pull request with the body of [.github/pull_request_template.md](../../../.github/pull_request_template.md) and tick the first line of its checklist.
