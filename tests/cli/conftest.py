"""Fixtures for the command tests: the toy as a folder or as a release, and a git repository."""

import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner

from headwater.model import Pin
from headwater.release import Release, ReleaseInfo, build
from headwater.study import read_method, read_process_folder
from tests.conftest import add_process

TOY = Path(__file__).resolve().parent.parent / "fixtures" / "toy"

Git = Callable[..., str]
Commit = Callable[[str], str]
Invoke = Callable[..., tuple[int, str]]
ReleaseStudy = Callable[..., Path]

ANSI = re.compile(r"\x1b\[[0-9;]*m")
"""Styling that rich puts in usage errors, even on a plain terminal: it also highlights options."""

INFO = ReleaseInfo(
    name="toy-background",
    source="toy",
    version="1",
    system_model="cutoff",
    licence="CC0-1.0",
    flow_list="toy",
)


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty HEADWATER_HOME."""
    path = tmp_path / "home"
    monkeypatch.setenv("HEADWATER_HOME", str(path))
    return path


@pytest.fixture
def toy(tmp_path: Path) -> Path:
    """A writable copy of the toy fixtures."""
    target = tmp_path / "toy"
    shutil.copytree(TOY, target)
    return target


@pytest.fixture
def toy_args() -> list[str]:
    """The options that point a command at the toy background folder and its method."""
    return [
        "--release",
        str(TOY / "background"),
        "--method",
        str(TOY / "methods" / "toy-climate"),
        "--namespace",
        "toy",
    ]


@pytest.fixture
def toy_release(home: Path) -> Release:
    """The toy background as a release in the cache."""
    folder = read_process_folder(TOY / "background", namespace="toy")
    return build(
        INFO,
        processes=folder.processes,
        flows=folder.flows,
        flow_properties=folder.flow_properties,
        methods=(read_method(TOY / "methods" / "toy-climate"),),
    )


@pytest.fixture
def pin_to(toy: Path) -> Callable[[Release], str]:
    """Rewrite the pin of the toy T-shirt database to a release, and return the pin."""

    def pin(release: Release) -> str:
        text = str(Pin(name=release.name, version=release.hash[:12]))
        path = toy / "tshirt" / "tshirt" / "database.yaml"
        path.write_text(path.read_text().replace("toy-background@fixture", text))
        return text

    return pin


@pytest.fixture(autouse=True)
def plain_wide_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Usage errors are drawn in a box. Without colour and with a wide terminal, a message is not
    split or styled, whatever the machine or CI sets."""
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setenv("TERMINAL_WIDTH", "200")
    monkeypatch.delenv("FORCE_COLOR", raising=False)


@pytest.fixture(autouse=True)
def no_method_folders(monkeypatch: pytest.MonkeyPatch) -> None:
    """The method folders of the machine are not those of a test."""
    monkeypatch.delenv("HEADWATER_METHOD_FOLDERS", raising=False)


@pytest.fixture
def invoke() -> Invoke:
    """Run the command line and return the exit code and what it printed, errors included."""
    from headwater.cli import app

    runner = CliRunner()

    def run(*args: str | Path) -> tuple[int, str]:
        result = runner.invoke(app, [str(a) for a in args], catch_exceptions=False)
        return result.exit_code, ANSI.sub("", result.output)

    return run


@pytest.fixture
def invoke_stdout() -> Invoke:
    """As `invoke`, but only what went to standard output."""
    from headwater.cli import app

    runner = CliRunner()

    def run(*args: str | Path) -> tuple[int, str]:
        result = runner.invoke(app, [str(a) for a in args], catch_exceptions=False)
        return result.exit_code, ANSI.sub("", result.stdout)

    return run


def edit(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    assert old in text, f"{old!r} not found in {path}"
    path.write_text(text.replace(old, new, 1))


def copy_process(database: Path, code: str, to: str) -> None:
    """A second process with the details and the lines of the process `code`, under the id `to`:
    it makes the same flow."""
    rows = (database / "processes.csv").read_text().splitlines()
    [details] = [row.split(",", 1)[1] for row in rows if row.startswith(f"{code},")]
    table = (database / "exchanges.csv").read_text().splitlines()
    lines = [row.split(",", 1)[1] for row in table if row.startswith(f"{code},")]
    add_process(database, to, "\n".join(lines), details=details)


def _git(path: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "-C", str(path), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return done.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A repository whose first commit, on `main`, holds the toy T-shirt database in
    `tshirt/tshirt`. The command runs from inside it.

    Git reads no user or system configuration here, and commits are made by a fixed author, so
    the tests do not depend on the machine.
    """
    nothing = tmp_path / "gitconfig"
    nothing.write_text("")
    for name, value in {
        "GIT_CONFIG_GLOBAL": str(nothing),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("GIT_DIR", raising=False)
    monkeypatch.delenv("GIT_WORK_TREE", raising=False)
    path = tmp_path / "repo"
    shutil.copytree(TOY / "tshirt" / "tshirt", path / "tshirt" / "tshirt")
    subprocess.run(["git", "init", "--quiet", "--initial-branch", "main", str(path)], check=True)
    _git(path, "add", "--all")
    _git(path, "commit", "--quiet", "-m", "the T-shirt database")
    monkeypatch.chdir(path)
    return path


@pytest.fixture
def git(repo: Path) -> Git:
    return lambda *args: _git(repo, *args)


@pytest.fixture
def commit(repo: Path) -> Commit:
    """Commit everything in the test repository, and return the new commit's hash."""

    def run(message: str) -> str:
        _git(repo, "add", "--all")
        _git(repo, "commit", "--quiet", "-m", message)
        return _git(repo, "rev-parse", "HEAD")

    return run
