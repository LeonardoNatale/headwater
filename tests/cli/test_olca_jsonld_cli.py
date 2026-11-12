"""An openLCA JSON-LD package read twice by the command line: its processes into a release,
then one of its methods into a method folder."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from headwater.cli import app

TOY = Path(__file__).resolve().parents[1] / "fixtures" / "formats" / "olca-jsonld" / "toy"

runner = CliRunner()


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "home"
    monkeypatch.setenv("HEADWATER_HOME", str(path))
    return path


def _run(*args: str) -> tuple[int, str]:
    result = runner.invoke(app, list(args), catch_exceptions=False)
    return result.exit_code, result.output


def test_a_package_imports_into_a_release_then_its_method_into_a_folder(
    home: Path, tmp_path: Path
) -> None:
    code, output = _run(
        "release", "import", str(TOY), "--source", "toy", "--version", "1.0", "--licence", "CC0"
    )
    assert code == 0, output
    [release] = (home / "releases").iterdir()
    assert release.name.startswith("toy-1.0@")

    code, output = _run(
        "method",
        "import",
        str(TOY),
        "--method",
        "toy climate v1",
        "--flow-list",
        str(release),
        "--out",
        str(tmp_path / "toy-climate"),
    )

    assert code == 0, output
    assert "Read 2 factors of toy climate v1" in output
    assert (tmp_path / "toy-climate" / "factors.csv").is_file()


def test_a_package_without_its_source_is_refused_naming_the_flags(home: Path) -> None:
    code, output = _run("release", "import", str(TOY), "--version", "1.0")

    assert code == 1
    assert "--source" in output and "--licence" in output
    assert "Traceback" not in output
