"""`headwater release list`: the releases in the cache, with the name@hash a database pins, their
kind and their size, so a person sees where the cache is and what it holds."""

import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from headwater.cli import app

DEMO = Path(__file__).resolve().parents[2] / "src" / "headwater" / "demo"
TOY = "toy-background@6864b7dbb65d"

runner = CliRunner()


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "home"
    monkeypatch.setenv("HEADWATER_HOME", str(path))
    return path


def run(*args: str) -> tuple[int, str]:
    result = runner.invoke(app, ["release", *args], catch_exceptions=False)
    return result.exit_code, result.stdout


def test_an_empty_cache_says_where_it_is_and_how_to_fill_it(home: Path) -> None:
    code, out = run("list")

    assert code == 0
    assert out.strip() == (
        f"No releases in {home / 'releases'}. Pull one with: headwater release pull <folder>"
    )


def test_a_pulled_release_is_listed_with_its_pin_kind_and_size(home: Path) -> None:
    assert run("pull", str(DEMO / "releases" / TOY))[0] == 0

    code, out = run("list")
    data = json.loads(run("list", "--json")[1])

    assert code == 0
    assert out.splitlines()[0] == f"Releases in {home / 'releases'}:"
    assert out.splitlines()[1].split()[:2] == [TOY, "source"]
    assert data["format"] == "headwater-release-list/1"
    assert data["folder"] == str(home / "releases")
    [listed] = data["releases"]
    assert listed["release"] == TOY
    assert listed["hash"].startswith(TOY.split("@")[1]) and len(listed["hash"]) == 64
    assert listed["kind"] == "source"
    assert listed["bytes"] == sum(
        f.stat().st_size for f in (DEMO / "releases" / TOY).rglob("*") if f.is_file()
    )


def test_a_folder_that_is_not_a_release_is_listed_with_the_reason(home: Path) -> None:
    (home / "releases" / "broken@000000000000").mkdir(parents=True)

    code, out = run("list", "--json")

    assert code == 0
    [listed] = json.loads(out)["releases"]
    assert listed["folder"] == "broken@000000000000"
    assert "release" not in listed and listed["error"]


def test_a_release_folder_under_another_name_is_listed_with_the_reason(home: Path) -> None:
    shutil.copytree(DEMO / "releases" / TOY, home / "releases" / "renamed@000000000000")

    [listed] = json.loads(run("list", "--json")[1])["releases"]

    assert listed["folder"] == "renamed@000000000000"
    assert "another name" in listed["error"]
