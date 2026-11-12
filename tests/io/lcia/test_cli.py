"""`headwater method import`, on the fixture workbook and the ecoSpold2 fixture release."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from headwater.cli import app
from headwater.release import Release, pull
from headwater.study import read_method

runner = CliRunner()


def _import(workbook: Path, flow_list: str, *extra: str) -> tuple[int, str]:
    result = runner.invoke(
        app,
        [
            "method",
            "import",
            str(workbook),
            "--method",
            "EF v3.1",
            "--flow-list",
            flow_list,
            *extra,
        ],
        catch_exceptions=False,
    )
    return result.exit_code, result.output


def test_import_writes_the_method_folder_and_reports_per_indicator(
    workbook: Path, release: Release, tmp_path: Path
) -> None:
    out = tmp_path / "methods" / "EF-3.1"

    code, output = _import(workbook, str(release.path), "--out", str(out))

    assert code == 0, output
    method = read_method(out)
    assert (method.id, method.variant, len(method.factors)) == ("EF-3.1", "EF v3.1", 5)
    assert "Read 9 factors of EF v3.1" in output
    assert "ecoinvent-3.10" in output
    lines = output.splitlines()
    # indicator, factors in the workbook, mapped, copied, not in the flow list, flows with no
    # factor
    assert any(line.split() == ["climate-change", "5", "3", "0", "2", "1"] for line in lines), (
        output
    )
    assert any(line.split() == ["climate-change-fossil", "3", "2", "0", "1", "2"] for line in lines)
    assert any(
        line.split() == ["ecotoxicity-freshwater", "1", "0", "0", "1", "4"] for line in lines
    )
    assert "Dinitrogen monoxide (air/urban air close to ground)" in output
    assert "Zinc (soil/agricultural)" in output
    assert "Cadmium (soil/agricultural)" in output
    assert "left out" not in output
    assert f"Wrote method EF-3.1 to {out}" in output


def test_chosen_categories_are_imported_and_the_others_named(
    workbook: Path, release: Release, tmp_path: Path
) -> None:
    out = tmp_path / "methods" / "EF-3.1"

    code, output = _import(
        workbook,
        str(release.path),
        "--out",
        str(out),
        "--category",
        "climate change: fossil",
        "--category",
        "ecotoxicity: freshwater",
    )

    assert code == 0, output
    assert [i.id for i in read_method(out).indicators] == [
        "climate-change-fossil",
        "ecotoxicity-freshwater",
    ]
    assert "Read 4 factors of EF v3.1" in output
    assert "1 category of EF v3.1 left out: climate change" in output


def test_the_flow_list_can_be_a_cached_release(
    workbook: Path, release: Release, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HEADWATER_HOME", str(tmp_path / "home"))
    pull(release.path)
    monkeypatch.chdir(tmp_path)

    code, output = _import(workbook, release.path.name)

    assert code == 0, output
    assert read_method(tmp_path / "methods" / "EF-3.1").id == "EF-3.1"


def test_errors_are_reported_without_a_traceback(
    workbook: Path, release: Release, tmp_path: Path
) -> None:
    result = runner.invoke(
        app,
        ["method", "import", str(workbook), "--method", "EF v9", "--flow-list", str(release.path)],
        catch_exceptions=False,
    )

    assert result.exit_code == 1
    assert "method EF v9" in result.output
    assert "Traceback" not in result.output


def test_a_flow_list_that_is_neither_a_folder_nor_a_release_is_an_error(
    workbook: Path, tmp_path: Path
) -> None:
    code, output = _import(workbook, str(tmp_path / "nowhere"))

    assert code == 1
    assert "nowhere" in output


def test_a_folder_that_cannot_be_written_is_an_error(
    workbook: Path, release: Release, tmp_path: Path
) -> None:
    blocker = tmp_path / "a-file"
    blocker.write_text("not a folder", encoding="utf-8")

    code, output = _import(workbook, str(release.path), "--out", str(blocker / "EF-3.1"))

    assert code == 1
    assert "a-file" in output
    assert "Traceback" not in output


def test_the_format_can_be_given(workbook: Path, release: Release, tmp_path: Path) -> None:
    code, output = _import(
        workbook, str(release.path), "--format", "ecoinvent-lcia", "--out", str(tmp_path / "m")
    )

    assert code == 0, output


def test_a_workbook_of_another_version_maps_only_with_a_flow_mapping(
    workbooks, release: Release, tmp_path: Path
) -> None:
    path = workbooks.write(read_me=[["Database Version", "3.12, test fixture"]])
    mapping = tmp_path / "flows.csv"
    mapping.write_text(
        "source_name,source_compartment,target,who,when\n"
        '"Carbon dioxide, fossil",air/urban air close to ground,'
        "ecoinvent:flow:e0000000-0000-4000-8000-000000000001,leo,2026-10-24\n",
        encoding="utf-8",
    )

    refused, without = _import(path, str(release.path), "--out", str(tmp_path / "a"))
    code, output = _import(
        path, str(release.path), "--out", str(tmp_path / "b"), "--flow-mapping", str(mapping)
    )

    assert refused == 1 and "flow list" in without
    assert code == 0, output
    assert (
        "The rows of flows.csv decided the flows they name."
        in (tmp_path / "b" / "method.yaml").read_text()
    )
