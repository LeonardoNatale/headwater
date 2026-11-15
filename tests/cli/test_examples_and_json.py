"""Every command takes `--json` and prints one JSON document, and the example at the end of each
`--help` runs (D49).

The examples run in a copy of the demo, committed to git, with its release pulled. The seven that
need a source the demo does not hold name a toy file, which `toy_files` copies from
tests/fixtures into the copy first.
"""

import csv
import json
import shlex
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import openpyxl
import pytest
from typer.testing import CliRunner

from headwater.cli import COMMANDS, EXAMPLES, app
from headwater.cli._json import GROUPS
from tests.cli.conftest import ANSI
from tests.upgrade.conftest import build_toy, short

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"

ORDER = list(EXAMPLES)
"""The examples in the order they run: `method import` reads the release `release import`
writes, and `diff` the edit made before it."""

NO_JSON = {"skill show"}
"""The one command without `--json`: its output is the skill."""


def run(*args: str) -> tuple[int, str, str]:
    result = CliRunner().invoke(app, list(args), catch_exceptions=False)
    return result.exit_code, ANSI.sub("", result.stdout), ANSI.sub("", result.stderr)


def git(folder: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=folder,
        check=True,
        capture_output=True,
    )


def toy_files(demo: Path) -> None:
    """The toy files the examples of the import commands and of `upgrade` name."""
    shutil.copytree(FIXTURES / "formats" / "olca-jsonld" / "toy", demo / "olca-toy")
    shutil.copytree(FIXTURES / "toy" / "methods" / "toy-impacts", demo / "toy-impacts")
    shutil.copyfile(FIXTURES / "toy" / "methods" / "toy-nw" / "factors.csv", demo / "toy-nw.csv")
    shutil.copytree(FIXTURES / "old-layout" / "tshirt-dyed", demo / "old-tshirt-dyed")
    shutil.copytree(FIXTURES / "brightway", demo / "brightway")
    book = openpyxl.Workbook()
    book.remove(book.worksheets[0])
    sheet = book.create_sheet("inventory")
    with (FIXTURES / "excel" / "tshirt.csv").open(encoding="utf-8", newline="") as file:
        for row in csv.reader(file):
            sheet.append([_cell(text) for text in row])
    book.save(demo / "tshirt.xlsx")
    toy1, toy2 = build_toy("1"), build_toy("2")
    shutil.copytree(FIXTURES / "upgrade" / "studies" / "tshirt", demo / "upgrade" / "tshirt")
    mapping = "upgrade-mapping.csv"
    shutil.copyfile(FIXTURES / "upgrade" / mapping, demo / "upgrade" / mapping)
    pin = demo / "upgrade" / "tshirt" / "database.yaml"
    pin.write_text(pin.read_text().replace("toy-1@PIN", short(toy1)))
    assert short(toy2) in EXAMPLES["upgrade"].line, "the example names the toy-2 the test builds"


def _cell(text: str) -> object:
    if not text:
        return None
    for kind in (int, float):
        try:
            return kind(text)
        except ValueError:
            pass
    return text


@pytest.fixture(scope="module")
def demo(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """A copy of the demo, its release pulled into a HEADWATER_HOME of its own, committed to git,
    then line e02 of the T-shirt set to 0.3 kg so that `diff` has a change to show."""
    root = tmp_path_factory.mktemp("examples")
    patch = pytest.MonkeyPatch()
    patch.setenv("HEADWATER_HOME", str(root / "home"))
    patch.setenv("HOME", str(root / "user"))
    patch.setenv("NO_COLOR", "1")
    patch.setenv("COLUMNS", "200")
    patch.setenv("TERMINAL_WIDTH", "200")
    patch.delenv("HEADWATER_METHOD_FOLDERS", raising=False)
    folder = root / "demo"
    code, _, err = run("demo", str(folder))
    assert code == 0, err
    patch.chdir(folder)
    code, _, err = run("release", "pull", "releases/toy-background@6864b7dbb65d")
    assert code == 0, err
    toy_files(folder)
    git(folder, "init", "-q")
    git(folder, "add", "-A")
    git(folder, "commit", "-qm", "demo")
    lines = folder / "project" / "tshirt-dyed" / "exchanges.csv"
    text = lines.read_text()
    assert "tshirt,e02,input,toy:cotton-fabric,0.25," in text
    lines.write_text(text.replace("cotton-fabric,0.25,", "cotton-fabric,0.3,"))
    yield folder
    patch.undo()


@pytest.fixture(scope="module")
def ran(demo: Path) -> dict[str, tuple[int, str, str]]:
    """Each example run once, in order, as written: exit code, standard output and error."""
    found: dict[str, tuple[int, str, str]] = {}
    for name in ORDER:
        words = shlex.split(EXAMPLES[name].line)
        assert words[0] == "headwater"
        found[name] = run(*words[1:])
    return found


def commands() -> list[str]:
    """Every command, a group's by its two words: `database import-excel`."""
    from headwater.cli import load

    names: list[str] = []
    for name in COMMANDS:
        if name in GROUPS:
            group = load(name)
            names += [f"{name} {word}" for word in getattr(group, "commands", {})]
        else:
            names.append(name)
    return names


def test_every_command_has_an_example() -> None:
    assert sorted(EXAMPLES) == sorted(commands())


@pytest.mark.parametrize("name", ORDER)
def test_the_help_of_each_command_ends_with_its_example(name: str) -> None:
    code, out, _ = run(*name.split(), "--help")

    assert code == 0
    tail = " ".join(out.split()[-len(EXAMPLES[name].line.split()) - 20 :])
    assert " ".join(EXAMPLES[name].line.split()) in tail
    assert out.rstrip().endswith(EXAMPLES[name].line.split()[-1])


@pytest.mark.parametrize("name", ORDER)
def test_each_example_runs_on_the_demo(name: str, ran: dict[str, tuple[int, str, str]]) -> None:
    code, out, err = ran[name]

    assert code == 0, out + err


@pytest.mark.parametrize("name", [n for n in ORDER if n not in NO_JSON])
def test_every_command_takes_json_and_prints_one_document(
    name: str, ran: dict[str, tuple[int, str, str]]
) -> None:
    assert "--json" in shlex.split(EXAMPLES[name].line) or name == "release pull"
    code, out, err = ran[name] if name != "release pull" else _pull_json()

    assert code == 0, err
    data = json.loads(out)
    assert data["format"].startswith("headwater-")


def _pull_json() -> tuple[int, str, str]:
    return run("release", "pull", "releases/toy-background@6864b7dbb65d", "--json")


@pytest.mark.parametrize("name", [n for n in commands() if n not in NO_JSON])
def test_every_command_lists_json_in_its_help(name: str) -> None:
    _, out, _ = run(*name.split(), "--help")

    assert "--json" in out


def test_the_contributions_of_the_study_are_those_of_its_database(
    demo: Path, ran: dict[str, tuple[int, str, str]]
) -> None:
    study = json.loads(ran["contributions"][1])
    code, out, err = run("contributions", "project/tshirt-dyed", "--json")
    database = json.loads(out)

    assert code == 0, err
    [mine], [theirs] = study["results"], database["results"]
    assert study["kind"] == "study" and database["kind"] == "database"
    assert mine["product"] == {"id": "tshirt-dyed:tshirt", "amount": 1.0}
    assert mine["tree"] == theirs["tree"]
    assert mine["score"] == pytest.approx(1.428076, rel=1e-9)  # the demo copy has 0.3 kg fabric


def test_a_usage_error_under_json_is_an_error_document(demo: Path) -> None:
    code, out, err = run("calc", "project/tshirt-footprint", "--indicator", "nope", "--json")

    assert code == 2
    data = json.loads(out)
    assert data["format"] == "headwater-error/1"
    assert (data["command"], data["exit_code"]) == ("calc", 2)
    [problem] = data["errors"]
    assert problem["code"] == "usage" and problem["item"] == "--indicator"
    assert "nope" in problem["message"] and "nope" in err


def test_without_json_a_failure_prints_nothing_to_standard_output(demo: Path) -> None:
    code, out, err = run("calc", "project/no-such-folder")

    assert code == 1
    assert out == ""
    assert "no-such-folder" in err
