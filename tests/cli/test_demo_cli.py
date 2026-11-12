"""`headwater demo FOLDER`: the demo that ships in the package, copied where a pip user runs it."""

from collections.abc import Callable
from pathlib import Path

import pytest

import headwater as hw
from headwater.release import Release
from tests.conftest import open_study_folder

Invoke = Callable[..., tuple[int, str]]

DEMO = Path(__file__).resolve().parents[2] / "src" / "headwater" / "demo"
RELEASE = "toy-background@6864b7dbb65d"


def files(folder: Path) -> dict[str, bytes]:
    return {
        p.relative_to(folder).as_posix(): p.read_bytes()
        for p in sorted(folder.rglob("*"))
        if p.is_file()
    }


def test_demo_copies_the_release_the_databases_and_the_study(
    invoke: Invoke, tmp_path: Path
) -> None:
    target = tmp_path / "headwater-demo"

    code, output = invoke("demo", target)

    assert code == 0, output
    assert files(target) == files(DEMO)
    assert sorted(p.name for p in (target / "studies").iterdir()) == [
        "dyes",
        "tshirt",
        "tshirt-dyed",
        "tshirt-footprint",
        "tshirt-local-electricity",
    ]
    assert [p.name for p in (target / "releases").iterdir()] == [RELEASE]
    assert str(target) in output
    assert f"headwater release pull {Path('releases') / RELEASE}" in output


def test_the_copied_demo_scores_the_study(invoke: Invoke, tmp_path: Path) -> None:
    invoke("demo", tmp_path / "demo")
    release = Release.open(tmp_path / "demo" / "releases" / RELEASE)
    release.verify()

    background = hw.load(release.path)
    [result] = open_study_folder(tmp_path / "demo" / "studies" / "tshirt-footprint").calc(
        background
    )

    assert result.score == pytest.approx(1.242646, rel=1e-9)


def test_demo_fills_an_empty_folder(invoke: Invoke, tmp_path: Path) -> None:
    code, output = invoke("demo", tmp_path)

    assert code == 0, output
    assert (tmp_path / "studies" / "tshirt-footprint" / "study.yaml").is_file()


def test_demo_fills_a_new_git_repository(invoke: Invoke, tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()

    code, output = invoke("demo", tmp_path)

    assert code == 0, output
    assert (tmp_path / ".git").is_dir()
    assert (tmp_path / "studies" / "tshirt-footprint" / "study.yaml").is_file()


def test_demo_refuses_a_folder_that_holds_files(invoke: Invoke, tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("mine")

    code, output = invoke("demo", tmp_path)

    assert code == 1
    assert "not empty" in output and str(tmp_path) in output
    assert [p.name for p in tmp_path.iterdir()] == ["notes.txt"]


def test_demo_refuses_a_file(invoke: Invoke, tmp_path: Path) -> None:
    path = tmp_path / "a-file"
    path.write_text("")

    code, output = invoke("demo", path)

    assert code == 2
    assert "not a folder" in output
