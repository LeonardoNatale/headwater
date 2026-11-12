"""`headwater release import` and `headwater release pull`, on the ecoSpold2 fixture source."""

import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from headwater.cli import app
from headwater.release import Release

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "ecospold2"
SOURCE = FIXTURES / "cutoff"
EMPTY_FILE = "a0000000-0000-4000-8000-000000000008_b0000000-0000-4000-8000-000000000003.spold"

runner = CliRunner()


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "home"
    monkeypatch.setenv("HEADWATER_HOME", str(path))
    return path


def _import(*extra: str) -> tuple[int, str]:
    result = runner.invoke(
        app,
        ["release", "import", str(SOURCE), "--version", "3.10", "--workers", "1", *extra],
        catch_exceptions=False,
    )
    return result.exit_code, result.output


def test_import_writes_a_release_into_headwater_home(home: Path) -> None:
    code, output = _import("--exclude", str(SOURCE / "exclude.csv"))

    assert code == 0, output
    [folder] = (home / "releases").iterdir()
    assert folder.name.startswith("ecoinvent-3.10-cutoff@")
    assert folder.name in output
    assert Release.open(folder).name == "ecoinvent-3.10-cutoff"
    assert "data gap" in output
    assert "market for natural gas" in output
    assert EMPTY_FILE in output


def test_import_takes_a_name(home: Path) -> None:
    code, output = _import("--exclude", str(SOURCE / "exclude.csv"), "--name", "ei-toy")

    assert code == 0, output
    [folder] = (home / "releases").iterdir()
    assert folder.name.startswith("ei-toy@")


def test_import_with_a_bad_name_fails_without_a_traceback(home: Path) -> None:
    code, output = _import("--exclude", str(SOURCE / "exclude.csv"), "--name", "ei toy")

    assert code == 1
    assert "release ei toy" in output
    assert "Traceback" not in output


def test_import_of_a_source_that_names_no_release_warns_that_the_version_is_unchecked(
    home: Path, tmp_path: Path
) -> None:
    source = tmp_path / "source"
    shutil.copytree(SOURCE, source)
    for path in (source / "datasets").glob("*.spold"):
        text = path.read_text(encoding="utf-8")
        path.write_text(text.replace('minorRelease="10"', 'minorRelease="0"'), encoding="utf-8")

    result = runner.invoke(
        app,
        ["release", "import", str(source), "--version", "3.12", "--workers", "1"]
        + ["--exclude", str(source / "exclude.csv")],
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    [folder] = (home / "releases").iterdir()
    assert folder.name.startswith("ecoinvent-3.12-cutoff@")
    assert "version 3.12: " in result.output
    assert "cannot be checked" in result.output


def test_import_without_the_exclude_file_fails_naming_the_empty_file(home: Path) -> None:
    code, output = _import()

    assert code == 1
    assert EMPTY_FILE in output
    assert "Traceback" not in output
    assert not (home / "releases").exists() or not any((home / "releases").iterdir())


def test_pull_copies_a_release_folder_into_the_cache(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code, output = _import("--exclude", str(SOURCE / "exclude.csv"))
    assert code == 0, output
    [built] = (home / "releases").iterdir()
    other = tmp_path / "other-home"
    monkeypatch.setenv("HEADWATER_HOME", str(other))

    result = runner.invoke(app, ["release", "pull", str(built)], catch_exceptions=False)

    assert result.exit_code == 0, result.output
    assert (other / "releases" / built.name).is_dir()
    assert built.name in result.output


def test_pull_of_a_folder_that_is_not_a_release_fails_with_its_problem(
    home: Path, tmp_path: Path
) -> None:
    result = runner.invoke(app, ["release", "pull", str(tmp_path)], catch_exceptions=False)

    assert result.exit_code == 1
    assert "release.json" in result.output
    assert "Traceback" not in result.output


def test_help_lists_the_release_commands() -> None:
    result = runner.invoke(app, ["release", "--help"])

    assert result.exit_code == 0
    assert "import" in result.output and "pull" in result.output


def test_import_takes_the_format_by_name(home: Path) -> None:
    code, output = _import("--exclude", str(SOURCE / "exclude.csv"), "--format", "ecospold2")

    assert code == 0, output
    assert "Wrote release ecoinvent-3.10-cutoff@" in output


def test_import_of_a_method_format_is_refused(home: Path) -> None:
    code, output = _import("--format", "ecoinvent-lcia")

    assert code == 1
    assert "format ecoinvent-lcia: does not read an inventory" in output
    assert "ecospold2" in output


def test_import_of_a_path_no_format_recognises_asks_for_the_format(
    home: Path, tmp_path: Path
) -> None:
    result = runner.invoke(app, ["release", "import", str(tmp_path)], catch_exceptions=False)

    assert result.exit_code == 1
    assert "--format" in result.output and "ecospold2" in result.output
    assert "Traceback" not in result.output


def test_a_supplier_mapping_makes_a_missing_dataset_a_data_gap(home: Path, tmp_path: Path) -> None:
    source = tmp_path / "source"
    shutil.copytree(SOURCE, source)
    missing = "a0000000-0000-4000-8000-000000000010_b0000000-0000-4000-8000-000000000002.spold"
    shutil.copy(FIXTURES / "broken" / missing, source / "datasets" / missing)
    supplier = (
        "ecoinvent:3.10:cutoff:a0000000-0000-4000-8000-000000000099"
        "_b0000000-0000-4000-8000-000000000003"
    )
    mapping = tmp_path / "suppliers.csv"
    mapping.write_text(
        f"supplier_id,supplier,reason,who,when\n{supplier},,Not in the download,leo,2026-10-24\n",
        encoding="utf-8",
    )
    args = ["release", "import", str(source), "--version", "3.10", "--workers", "1"]
    args += ["--exclude", str(source / "exclude.csv")]

    refused = runner.invoke(app, args, catch_exceptions=False)
    done = runner.invoke(app, [*args, "--supplier-mapping", str(mapping)], catch_exceptions=False)

    assert refused.exit_code == 1
    assert "which is not in the source" in refused.output
    assert done.exit_code == 0, done.output
    assert "Not in the download" in done.output


@pytest.mark.parametrize("flag", ["--source", "--licence"])
def test_a_source_or_licence_flag_is_refused_by_a_format_that_does_not_take_it(
    home: Path, flag: str
) -> None:
    code, output = _import("--exclude", str(SOURCE / "exclude.csv"), flag, "toy")

    assert code == 1
    assert f"{flag}: does not apply to format ecospold2" in output


SIMAPRO_TOY = FIXTURES.parent / "formats" / "simapro-csv" / "toy" / "toy.csv"


def test_a_simapro_export_is_detected_and_imported_with_its_source_and_licence(
    home: Path,
) -> None:
    args = ["release", "import", str(SIMAPRO_TOY), "--version", "1.0", "--source", "toy"]

    result = runner.invoke(app, [*args, "--licence", "CC0-1.0"], catch_exceptions=False)
    missing = runner.invoke(app, args, catch_exceptions=False)

    assert result.exit_code == 0, result.output
    [folder] = (home / "releases").iterdir()
    assert folder.name.startswith("toy-1.0@")
    assert missing.exit_code == 1
    assert "--licence" in missing.output
