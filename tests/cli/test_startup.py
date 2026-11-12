"""Start-up: a command loads the libraries it needs and no others.

`headwater --help` and `headwater find` load no solver, database or file reader as long as nothing
at the top of a module they load imports numpy, scipy, pyarrow's dataset module, DuckDB, lxml or
openpyxl. These tests run in a fresh interpreter, because what an earlier test imported is
already in `sys.modules`. The start-up times are in docs/appendix/benchmarks.md.
"""

import importlib
import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from headwater.cli import COMMANDS, load
from headwater.release import Release

Invoke = Callable[..., tuple[int, str]]

TOY = Path(__file__).resolve().parent.parent / "fixtures" / "toy"

HEAVY = ("numpy", "scipy", "pyarrow", "duckdb", "lxml", "openpyxl", "pandas")
"""What a command that only reads files and prints should not load."""

# `release` and `method` list the formats that can be read in their help, so they import the
# readers. `uses` is registered by the study group, not by a COMMANDS entry.
_MODULES = {entry.target.partition(":")[0] for entry in COMMANDS.values()}
COMMAND_MODULES = sorted(
    (_MODULES | {"headwater.cli.uses"}) - {"headwater.cli.release", "headwater.cli.method"}
)

LOAD_AND_REPORT = """
import json, sys
{code}
print(json.dumps(sorted(set(sys.modules) & set({names!r}))))
"""


def loaded(code: str, *names: str) -> list[str]:
    """Run `code` in a new interpreter and return which of `names` are then imported."""
    script = LOAD_AND_REPORT.format(code=code, names=list(names))
    done = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.splitlines()[-1])


def run_command(*args: str) -> str:
    """The Python of `headwater <args>`, for `loaded`, which leaves the exit code alone."""
    return (
        f"sys.argv = ['headwater', *{list(args)!r}]\n"
        "from headwater.cli import main\n"
        "try:\n    main()\nexcept SystemExit:\n    pass\n"
    )


def find_loads_no_solver_and_no_database(*release_args: str) -> None:
    code = run_command("find", "electricity", *release_args)
    assert loaded(code, "scipy", "duckdb", "pyarrow.dataset", "pandas") == []


def test_importing_the_command_line_loads_no_library() -> None:
    names = (*HEAVY, "pydantic", "yaml", "headwater.model", "headwater.query")
    assert loaded("import headwater.cli", *names) == []


def test_a_command_module_loads_no_numeric_or_file_format_library() -> None:
    # One interpreter, a module after another: the assertion names the first one that fails.
    code = f"""
import importlib
for module in {COMMAND_MODULES!r}:
    importlib.import_module(module)
    heavy = sorted(set(sys.modules) & set({HEAVY!r}))
    assert not heavy, (module, heavy)
"""
    loaded(code)


def test_help_loads_no_command() -> None:
    names = (*HEAVY, *COMMAND_MODULES)
    assert loaded(run_command("--help"), *names) == []


def test_find_in_a_folder_loads_no_solver_and_no_database() -> None:
    find_loads_no_solver_and_no_database("--release", str(TOY / "background"), "--namespace", "toy")


def test_find_in_a_release_loads_no_solver_and_no_database(toy_release: Release) -> None:
    # The `home` fixture set HEADWATER_HOME in this process, and the new interpreter inherits it.
    find_loads_no_solver_and_no_database("--release", f"{toy_release.name}@{toy_release.hash[:12]}")


def test_the_help_line_of_each_command_is_the_one_its_own_help_starts_with() -> None:
    for name, entry in COMMANDS.items():
        command = load(name)
        first = (command.help or "").strip().split("\n\n")[0]
        assert entry.help == " ".join(first.split()), name


def test_help_lists_every_command_in_order(invoke: Invoke) -> None:
    code, output = invoke("--help")
    assert code == 0
    positions = [output.index(f"│ {name} ") for name in COMMANDS]
    assert positions == sorted(positions)
    text = " ".join(output.replace("│", " ").split())
    for entry in COMMANDS.values():
        assert entry.help in text


def test_a_command_that_does_not_exist_is_refused_like_any_other(invoke: Invoke) -> None:
    code, output = invoke("nonsense")
    assert code == 2
    assert "No such command" in output


def test_a_mistyped_command_suggests_the_closest(invoke: Invoke) -> None:
    code, output = invoke("calx")
    assert code == 2
    assert "Did you mean 'calc'?" in output


PACKAGES = ["headwater", "headwater.query", "headwater.release"]
"""The packages whose `__init__` exports names lazily."""


@pytest.mark.parametrize("name", PACKAGES)
def test_every_exported_name_loads_on_first_use(name: str) -> None:
    package = importlib.import_module(name)
    for export in package.__all__:
        assert export in dir(package)
        getattr(package, export)


@pytest.mark.parametrize("name", PACKAGES)
def test_a_name_that_is_not_exported_is_an_attribute_error(name: str) -> None:
    package = importlib.import_module(name)
    missing = "nothing"
    with pytest.raises(AttributeError, match="no attribute 'nothing'"):
        getattr(package, missing)
