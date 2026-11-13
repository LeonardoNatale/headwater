"""A study whose method its release does not hold, as the Excel import writes one: the action
takes method folders, and each fills a method that a study names by its id.

The demo release holds `toy-climate` only. The toy T-shirt workbook is imported with the method
folder `toy-impacts`, so its database.yaml says `methods: [toy-impacts]`.
"""

import csv
import json
import shutil
from pathlib import Path
from types import ModuleType

import openpyxl
import pytest

from headwater.io.excel import import_excel
from headwater.release import Release

from .conftest import RELEASE, ROOT, TOY, Commit, Git

IMPACTS = TOY / "methods" / "toy-impacts"
STUDY = "studies/tshirt-excel"


def workbook(path: Path) -> Path:
    """The toy T-shirt sheet of the Excel import's tests, as an .xlsx."""
    book = openpyxl.Workbook()
    book.remove(book.worksheets[0])
    sheet = book.create_sheet("inventory")
    with (ROOT / "tests" / "fixtures" / "excel" / "tshirt.csv").open(newline="") as handle:
        for row in csv.reader(handle):
            sheet.append([_cell(text) for text in row])
    book.save(path)
    return path


def _cell(text: str) -> str | int | float | None:
    if not text:
        return None
    for kind in (int, float):
        try:
            return kind(text)
        except ValueError:
            pass
    return text


@pytest.fixture
def imported(repo: Path, git: Git, commit: Commit, tmp_path: Path) -> Path:
    """The workbook imported on `main`, then a branch where its electricity goes from 0.5 to
    0.6 kWh. Returns the study folder."""
    folder = repo / STUDY
    import_excel(workbook(tmp_path / "tshirt.xlsx"), Release.open(RELEASE), folder, method=IMPACTS)
    assert "methods: [toy-impacts]\n" in (folder / "database.yaml").read_text()
    commit("the T-shirt, imported from Excel")
    git("switch", "--quiet", "--create", "more-power")
    csv_file = folder / "exchanges.csv"
    text = csv_file.read_text()
    assert ",0.5,kWh," in text
    csv_file.write_text(text.replace(",0.5,kWh,", ",0.6,kWh,"))
    commit("more electricity")
    return folder


def test_a_study_from_the_excel_import_is_compared_with_its_method_folder(
    collect: ModuleType, render: ModuleType, headwater: list[str], imported: Path
) -> None:
    report = collect.collect_report(
        imported.parents[1], headwater, "main", "HEAD", methods=[str(IMPACTS)]
    )

    [study] = report["folders"]
    assert study["ok"], study["diff_error"]
    diff = study["diff"]
    assert diff["methods"] == ["toy-impacts"]
    assert len(diff["results"]) == 3  # the three indicators of toy-impacts
    body = render.render(report)
    assert "method `toy-impacts 1`" in body
    assert body.count("`toy-foreground:t-shirt-glo line e03`") == 1
    assert "Photochemical ozone formation" in body


def test_without_the_method_folder_the_study_fails_and_says_which_method(
    collect: ModuleType, headwater: list[str], imported: Path
) -> None:
    report = collect.collect_report(imported.parents[1], headwater, "main", "HEAD")

    [study] = report["folders"]
    assert not study["ok"]
    assert "toy-impacts" in study["diff_error"]


def test_a_folder_fills_only_a_method_the_release_does_not_hold(
    collect: ModuleType, headwater: list[str], imported: Path, tmp_path: Path, commit: Commit
) -> None:
    repo = imported.parents[1]
    tshirt = repo / "studies" / "tshirt" / "tshirt" / "exchanges.csv"
    tshirt.write_text(tshirt.read_text().replace(",0.25,", ",0.22,"))
    commit("lighter T-shirt too")
    # two folders with the id of the method the release holds, one with other factors: neither
    # is used, so they are not two candidates either
    climate = tmp_path / "methods" / "toy-climate"
    shutil.copytree(TOY / "methods" / "toy-climate", climate)
    factors = climate / "factors.csv"
    factors.write_text(factors.read_text().replace("toy:co2,,1", "toy:co2,,2"))
    twin = tmp_path / "twin" / "toy-climate"
    shutil.copytree(TOY / "methods" / "toy-climate", twin)

    report = collect.collect_report(
        repo, headwater, "main", "HEAD", methods=[str(IMPACTS), str(climate), str(twin)]
    )

    by_path = {s["path"]: s for s in report["folders"]}
    assert all(s["ok"] for s in by_path.values()), report
    tshirt_diff = by_path["studies/tshirt/tshirt"]["diff"]
    assert tshirt_diff["methods"] == ["toy-climate"]
    # the release's toy-climate: the T-shirt still scores 1.19205 before the change
    assert tshirt_diff["results"][0]["old"] == pytest.approx(1.19205, rel=1e-9)
    assert by_path[STUDY]["diff"]["methods"] == ["toy-impacts"]


def test_two_folders_with_the_same_id_and_different_methods_are_refused_naming_both(
    collect: ModuleType, headwater: list[str], imported: Path, tmp_path: Path
) -> None:
    twin = tmp_path / "other" / "toy-impacts"
    shutil.copytree(IMPACTS, twin)
    factors = twin / "factors.csv"
    lines = factors.read_text().splitlines()
    lines[1] = lines[1].rsplit(",", 1)[0] + ",2"  # another factor, so another method
    factors.write_text("\n".join(lines) + "\n")

    report = collect.collect_report(
        imported.parents[1], headwater, "main", "HEAD", methods=[str(IMPACTS), str(twin)]
    )

    [study] = report["folders"]
    assert not study["ok"]
    assert str(IMPACTS) in study["diff_error"] and str(twin) in study["diff_error"]


def test_main_passes_the_method_folders_given_one_per_line(
    collect: ModuleType, headwater: list[str], imported: Path, tmp_path: Path
) -> None:
    out = tmp_path / "report.json"

    code = collect.main(
        [
            "--repo",
            str(imported.parents[1]),
            "--out",
            str(out),
            "--base",
            "main",
            "--methods",
            f"\n{IMPACTS}\n",
        ]
    )

    assert code == 0
    [study] = json.loads(out.read_text())["folders"]
    assert study["ok"], study["diff_error"]
