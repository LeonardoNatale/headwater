"""`headwater release import` on the ecoSpold1 toy: the format is detected, and the options that
name the source are required."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from headwater.cli import app
from headwater.release import Release

TOY = Path(__file__).resolve().parents[2] / "fixtures" / "formats" / "ecospold1" / "toy"

runner = CliRunner()


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "home"
    monkeypatch.setenv("HEADWATER_HOME", str(path))
    return path


def test_the_toy_imports_with_the_format_detected(home: Path) -> None:
    result = runner.invoke(
        app,
        [
            "release",
            "import",
            str(TOY),
            "--version",
            "1",
            "--source",
            "toy",
            "--licence",
            "CC0-1.0",
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    [folder] = (home / "releases").iterdir()
    assert folder.name.startswith("toy-1@")
    release = Release.open(folder)
    assert (release.info.source, release.info.licence) == ("toy", "CC0-1.0")
    assert sorted(p.name for p in release.processes()) == [
        "cotton fabric production",
        "electricity production",
        "incineration of textile waste",
    ]


def test_the_source_and_the_licence_are_required(home: Path) -> None:
    result = runner.invoke(
        app,
        ["release", "import", str(TOY), "--format", "ecospold1", "--version", "1"],
        catch_exceptions=False,
    )

    assert result.exit_code == 1
    assert "--source" in result.output and "--licence" in result.output
    assert not (home / "releases").exists() or not list((home / "releases").iterdir())
