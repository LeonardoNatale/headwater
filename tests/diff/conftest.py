"""Fixtures for the diff tests: a writable toy, and a git repository made inside the test."""

import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

import headwater as hw

TOY = Path(__file__).resolve().parent.parent / "fixtures" / "toy"


@pytest.fixture
def toy(tmp_path: Path) -> Path:
    """A writable copy of the toy fixtures."""
    target = tmp_path / "toy"
    shutil.copytree(TOY, target)
    return target


@pytest.fixture(scope="module")
def background() -> hw.Background:
    """The toy background as committed, factorized once. Read only."""
    return hw.load(TOY / "background", method=TOY / "methods" / "toy-climate", namespace="toy")


Git = Callable[..., str]
Commit = Callable[[str], str]


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A repository whose first commit, on `main`, holds the toy T-shirt database in
    `tshirt/tshirt`.

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
    return path


def _git(path: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "-C", str(path), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return done.stdout.strip()


@pytest.fixture
def git(repo: Path) -> Git:
    """Run git in the test repository and return its output."""
    return lambda *args: _git(repo, *args)


@pytest.fixture
def commit(repo: Path) -> Commit:
    """Commit everything in the test repository, and return the new commit's hash."""

    def run(message: str) -> str:
        _git(repo, "add", "--all")
        _git(repo, "commit", "--quiet", "-m", message)
        return _git(repo, "rev-parse", "HEAD")

    return run
