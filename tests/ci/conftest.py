"""Fixtures for the CI comment tests: the toy studies in a git repository, the modules of the
composite action, and the `headwater` command to run on them, in this process or as a real one.

The repository and the release cache are made once for the session and copied for each test."""

import contextlib
import importlib.util
import shutil
import subprocess
import sys
import traceback
from collections.abc import Callable, Sequence
from pathlib import Path
from types import ModuleType

import pytest
from typer.testing import CliRunner

from headwater.release import pull
from tests.conftest import toy_database

ROOT = Path(__file__).resolve().parents[2]
DEMO = ROOT / "src" / "headwater" / "demo"
TOY = ROOT / "tests" / "fixtures" / "toy"
ACTION = ROOT / ".github" / "actions" / "study-diff"
RELEASE = next((DEMO / "releases").glob("toy-background@*"))

Git = Callable[..., str]
Commit = Callable[[str], str]


def load(name: str) -> ModuleType:
    """A script of the action by file name, which is not a package."""
    path = ACTION / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"study_diff_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def collect() -> ModuleType:
    return load("collect")


@pytest.fixture(scope="session")
def render() -> ModuleType:
    return load("render")


@pytest.fixture(scope="session")
def comment() -> ModuleType:
    return load("comment")


def in_process(
    command: Sequence[str], args: Sequence[str], repo: Path
) -> subprocess.CompletedProcess[str]:
    """What `collect._run` does, without a process: the command line of this checkout, called
    with Typer's test runner from inside `repo`. It returns what a process would have, so the
    action cannot tell the difference. A crash gives the exit code 1 and the traceback."""
    from headwater.cli import app

    with contextlib.chdir(repo):
        result = CliRunner().invoke(app, list(args))
    error = result.stderr
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        error += "".join(traceback.format_exception(result.exception))
    return subprocess.CompletedProcess([*command, *args], result.exit_code, result.stdout, error)


@pytest.fixture
def headwater(collect: ModuleType, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The command the action runs, called in this process: a real process imports NumPy, SciPy,
    DuckDB and PyArrow every time it starts. The tests that are about the process itself use
    `headwater_process`."""
    monkeypatch.setattr(collect, "_run", in_process)
    return ["headwater"]


@pytest.fixture
def headwater_process() -> list[str]:
    """The command the action runs, as a real process: the entry point of this checkout."""
    return [sys.executable, "-c", "from headwater.cli import main; main()"]


@pytest.fixture(scope="session")
def home_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A HEADWATER_HOME that holds the release the demo studies pin, made once. Read only."""
    path = tmp_path_factory.mktemp("home")
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("HEADWATER_HOME", str(path))
        pull(RELEASE)
    return path


@pytest.fixture(autouse=True)
def home(home_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Every test reads releases from `home_dir`, unless it points HEADWATER_HOME elsewhere."""
    monkeypatch.setenv("HEADWATER_HOME", str(home_dir))
    return home_dir


def _git(path: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "-C", str(path), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return done.stdout.strip()


def _git_reads_nothing_else(patch: pytest.MonkeyPatch, config: Path) -> None:
    """Git reads no user or system configuration, and commits are made by a fixed author, so the
    tests do not depend on the machine."""
    config.write_text("")
    for name, value in {
        "GIT_CONFIG_GLOBAL": str(config),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }.items():
        patch.setenv(name, value)
    patch.delenv("GIT_DIR", raising=False)
    patch.delenv("GIT_WORK_TREE", raising=False)


@pytest.fixture(scope="session")
def repo_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The repository `repo` copies, made once: its first commit, on `main`, holds the toy
    databases in `studies/`."""
    root = tmp_path_factory.mktemp("template")
    path = root / "repo"
    path.mkdir()
    # The toy studies, pinned to the demo's release. Not the demo studies themselves: a pull
    # request may change those, and these tests need known amounts.
    for name in ("tshirt", "tshirt-local-electricity"):
        shutil.copytree(TOY / name, path / "studies" / name)
        database = toy_database(path / "studies", name) / "database.yaml"
        database.write_text(database.read_text().replace("toy-background@fixture", RELEASE.name))
    (path / "README.md").write_text("A studies repository.\n")
    with pytest.MonkeyPatch.context() as patch:
        _git_reads_nothing_else(patch, root / "gitconfig")
        subprocess.run(
            ["git", "init", "--quiet", "--initial-branch", "main", str(path)], check=True
        )
        _git(path, "add", "--all")
        _git(path, "commit", "--quiet", "-m", "the toy studies")
    return path


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, repo_template: Path) -> Path:
    """A copy of the template repository, which the test may change and commit to. The command
    runs from inside it, as the action does."""
    _git_reads_nothing_else(monkeypatch, tmp_path / "gitconfig")
    path = tmp_path / "repo"
    shutil.copytree(repo_template, path, symlinks=True)
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
