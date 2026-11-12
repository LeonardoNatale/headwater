"""A database scored on its own names its commit in its provenance, and whether its files differ
from it (AGENTS.md rule 9), as a study's product names the study's.

The toy: the database `tshirt` uses `dyes`, and its T-shirt scores 1.242646. The commit and
`changed` are of the database and of the databases it uses, checked at the database's first calc.
"""

import json
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import headwater as hw
import headwater.query.database as database_module
from headwater.cli import app
from headwater.query.result import DatabaseRef
from tests.conftest import open_database, open_study_folder

TOY = Path(__file__).resolve().parent.parent / "fixtures" / "toy"
TOY_ARGS = [
    "--release",
    str(TOY / "background"),
    "--method",
    str(TOY / "methods" / "toy-climate"),
    "--namespace",
    "toy",
]

Git = Callable[..., str]


def background() -> hw.Background:
    return hw.load(TOY / "background", method=TOY / "methods" / "toy-climate", namespace="toy")


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A git repository whose one commit holds the dyed T-shirt's databases and studies."""
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
    shutil.copytree(TOY / "tshirt-dyed", path / "tshirt-dyed")
    subprocess.run(["git", "init", "--quiet", "--initial-branch", "main", str(path)], check=True)
    git(path, "add", "--all")
    git(path, "commit", "--quiet", "-m", "the dyed T-shirt")
    monkeypatch.chdir(path)
    return path


def git(path: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "-C", str(path), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return done.stdout.strip()


def test_a_database_in_git_names_its_commit(repo: Path) -> None:
    head = git(repo, "rev-parse", "HEAD")

    result = open_database(repo / "tshirt-dyed" / "tshirt").calc(background(), "tshirt:tshirt")

    assert result.score == pytest.approx(1.242646, rel=1e-9)
    assert result.provenance.database == DatabaseRef(name="tshirt", commit=head, changed=False)
    assert result.provenance.study is None


def test_a_change_to_a_database_it_uses_is_a_change(repo: Path) -> None:
    dye = repo / "tshirt-dyed" / "dyes" / "processes" / "reactive-dye.csv"
    dye.write_text(dye.read_text().replace("toy:co2,4,", "toy:co2,3.5,"))

    result = open_database(repo / "tshirt-dyed" / "tshirt").calc(background(), "tshirt:tshirt")

    assert result.provenance.database is not None
    assert result.provenance.database.commit == git(repo, "rev-parse", "HEAD")
    assert result.provenance.database.changed


def test_the_files_are_checked_at_the_first_calc(repo: Path) -> None:
    """A database is read when it is opened, so its first calc says what it was scored from: a
    file changed after it does not change what later results of the same database say."""
    opened = open_database(repo / "tshirt-dyed" / "tshirt")
    first = opened.calc(background(), "tshirt:tshirt")
    yaml = repo / "tshirt-dyed" / "tshirt" / "database.yaml"
    yaml.write_text(yaml.read_text() + "# a note\n")

    later = opened.calc(background(), "tshirt:tshirt")

    assert first.provenance.database == later.provenance.database
    assert later.provenance.database is not None and not later.provenance.database.changed
    reopened = open_database(repo / "tshirt-dyed" / "tshirt").calc(background(), "tshirt:tshirt")
    assert reopened.provenance.database is not None and reopened.provenance.database.changed


def test_a_database_outside_git_has_no_commit(tmp_path: Path) -> None:
    shutil.copytree(TOY / "tshirt-dyed", tmp_path / "tshirt-dyed")

    result = open_database(tmp_path / "tshirt-dyed" / "tshirt").calc(background(), "tshirt:tshirt")

    assert result.provenance.database == DatabaseRef(name="tshirt", commit=None, changed=False)


def test_the_commit_is_not_part_of_the_input_hash(repo: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    shutil.copytree(TOY / "tshirt-dyed", outside)

    inside = open_database(repo / "tshirt-dyed" / "tshirt").calc(background(), "tshirt:tshirt")
    alone = open_database(outside / "tshirt").calc(background(), "tshirt:tshirt")

    assert inside.provenance.input_hash == alone.provenance.input_hash


def test_a_product_of_a_study_names_the_study_not_the_database(repo: Path) -> None:
    [result] = open_study_folder(repo / "tshirt-dyed" / "tshirt-footprint").calc(background())

    assert result.provenance.database is None
    assert result.provenance.study is not None
    assert result.provenance.study.commit == git(repo, "rev-parse", "HEAD")


def invoke(*args: str | Path) -> tuple[int, str]:
    result = CliRunner().invoke(app, [str(a) for a in args], catch_exceptions=False)
    return result.exit_code, result.stdout


def test_calc_prints_the_database_and_its_commit(repo: Path) -> None:
    head = git(repo, "rev-parse", "HEAD")

    code, output = invoke("calc", "tshirt-dyed/tshirt", *TOY_ARGS)
    assert code == 0, output
    assert f"  database: commit {head}\n" in output

    code, output = invoke("calc", "tshirt-dyed/tshirt", *TOY_ARGS, "--json")
    assert code == 0, output
    data = json.loads(output)
    assert data["provenance"]["database"] == {"name": "tshirt", "commit": head, "changed": False}


def test_calc_says_when_the_files_differ_from_the_commit(repo: Path) -> None:
    yaml = repo / "tshirt-dyed" / "tshirt" / "database.yaml"
    yaml.write_text(yaml.read_text() + "# a note\n")

    code, output = invoke("calc", "tshirt-dyed/tshirt", *TOY_ARGS)

    assert code == 0, output
    assert f"  database: commit {git(repo, 'rev-parse', 'HEAD')}, with changes\n" in output


def test_calc_outside_git_says_so(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    shutil.copytree(TOY / "tshirt-dyed", tmp_path / "tshirt-dyed")
    monkeypatch.chdir(tmp_path)

    code, output = invoke("calc", "tshirt-dyed/tshirt", *TOY_ARGS)

    assert code == 0, output
    assert "  database: not in git\n" in output


DIFF_KEYS = {
    "format", "path", "kind", "created", "old", "new", "engine_version", "solver", "background",
    "method", "methods", "tolerance", "old_background", "products", "results", "differences",
    "processes",
    "validation",
}  # fmt: skip
"""The keys of `headwater diff --json` (headwater-diff/2) as they were before a database result
had a commit: the diff does not carry it."""


def refs(data: Any) -> list[Any]:
    """Every object in `data` shaped as a database and its commit, `{name, commit, changed}`."""
    if isinstance(data, dict):
        found = [data] if {"name", "commit", "changed"} <= set(data) else []
        return found + [r for value in data.values() for r in refs(value)]
    if isinstance(data, list):
        return [r for value in data for r in refs(value)]
    return []


def test_a_diff_asks_git_for_no_database_state_and_its_json_is_unchanged(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[Any] = []
    real = database_module.git_state

    def counted(paths: Any) -> Any:
        asked.append(paths)
        return real(paths)

    monkeypatch.setattr(database_module, "git_state", counted)
    dye = repo / "tshirt-dyed" / "dyes" / "processes" / "reactive-dye.csv"
    dye.write_text(dye.read_text().replace("toy:co2,4,", "toy:co2,3.5,"))
    git(repo, "commit", "--quiet", "-am", "a cleaner dye")

    for folder in ("tshirt-dyed/dyes", "tshirt-dyed/tshirt"):
        code, output = invoke("diff", "HEAD~1", "HEAD", folder, *TOY_ARGS, "--json")
        assert code == 0, output
        data = json.loads(output)
        assert set(data) == DIFF_KEYS
        assert not any(refs(data)), "a database and its commit, in the diff's JSON"

    assert asked == []


def test_a_used_database_outside_the_repository_is_a_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`tshirt` is in a repository and `dyes`, which it uses, is next to it but outside: the
    commit alone does not give the dye back."""
    for name, value in {
        "GIT_CONFIG_GLOBAL": str(tmp_path / "none"),
        "GIT_CONFIG_NOSYSTEM": "1",
    }.items():
        monkeypatch.setenv(name, value)
    shutil.copytree(TOY / "tshirt-dyed", tmp_path / "tshirt-dyed")
    inner = tmp_path / "tshirt-dyed" / "tshirt"
    subprocess.run(["git", "init", "--quiet", str(inner)], check=True)
    git(inner, "add", "--all")
    git(
        inner, "-c", "user.name=T", "-c", "user.email=t@example.com", "commit", "--quiet", "-m", "x"
    )

    result = open_database(inner).calc(background(), "tshirt:tshirt")

    assert result.provenance.database is not None
    assert result.provenance.database.commit == git(inner, "rev-parse", "HEAD")
    assert result.provenance.database.changed
