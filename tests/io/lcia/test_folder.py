"""Writing an imported method as a method folder, reading it back, and scoring a release with it."""

from pathlib import Path

import pytest

import headwater as hw
from headwater.io.lcia import MethodImport, import_method, read_workbook, write_method
from headwater.model import InvalidDataError
from headwater.release import Release, method_hash
from headwater.study import read_method


@pytest.fixture
def imported(workbook: Path, release: Release) -> MethodImport:
    return import_method(read_workbook(workbook, "EF v3.1"), release)


def test_the_folder_reads_back_as_the_same_method(imported: MethodImport, tmp_path: Path) -> None:
    folder = write_method(imported, tmp_path / "EF-3.1")

    assert sorted(p.name for p in folder.iterdir()) == ["factors.csv", "method.yaml"]
    assert read_method(folder) == imported.method


def test_factors_are_written_sorted_by_indicator_and_flow(
    imported: MethodImport, tmp_path: Path
) -> None:
    folder = write_method(imported, tmp_path / "EF-3.1")

    flow = "ecoinvent:flow:e0000000-0000-4000-8000-00000000000"
    assert (folder / "factors.csv").read_text(encoding="utf-8").splitlines() == [
        "indicator,flow,location,factor",
        f"climate-change,{flow}1,,1.0",
        f"climate-change,{flow}3,,0.0",
        f"climate-change,{flow}4,,29.8",
        f"climate-change-fossil,{flow}1,,1.0",
        f"climate-change-fossil,{flow}4,,29.8",
    ]


def test_method_yaml_says_where_it_came_from(imported: MethodImport, tmp_path: Path) -> None:
    folder = write_method(imported, tmp_path / "EF-3.1")

    text = (folder / "method.yaml").read_text(encoding="utf-8")
    assert text.startswith(
        "# Imported by headwater from LCIA Implementation 1.xlsx, method EF v3.1"
    )
    assert "name, compartment and subcompartment" in text
    assert "left out" not in text


def test_method_yaml_names_the_categories_left_out(
    workbook: Path, release: Release, tmp_path: Path
) -> None:
    book = read_workbook(workbook, "EF v3.1", categories=["climate change: fossil"])
    folder = write_method(import_method(book, release), tmp_path / "EF-3.1")

    text = (folder / "method.yaml").read_text(encoding="utf-8")
    assert "# Categories of EF v3.1 left out: climate change, ecotoxicity: freshwater.\n" in text
    assert [i.id for i in read_method(folder).indicators] == ["climate-change-fossil"]


def test_the_category_indicator_is_kept_in_the_folder_the_background_and_a_release(
    imported: MethodImport, release: Release, tmp_path: Path
) -> None:
    folder = write_method(imported, tmp_path / "EF-3.1")
    expected = {
        "climate-change": "global warming potential (GWP100)",
        "climate-change-fossil": "global warming potential (GWP100)",
        "ecotoxicity-freshwater": "comparative toxic unit for ecosystems (CTUe)",
    }

    text = (folder / "method.yaml").read_text(encoding="utf-8")
    background = hw.load(release.path, method=folder)
    written = background.write_indicators(into=tmp_path / "derived")

    assert (
        "- id: climate-change\n"
        "  name: climate change\n"
        "  category_indicator: global warming potential (GWP100)\n"
        "  unit: kg CO2-Eq\n"
    ) in text
    assert {i.id: i.category_indicator for i in background.indicators} == expected
    [entry] = written.method_entries
    assert {i.id: i.category_indicator for i in entry.indicators} == expected
    assert entry.hash == method_hash(imported.method)


def test_a_line_break_in_a_name_stays_in_the_comment(
    workbooks, release: Release, tmp_path: Path
) -> None:
    name = "EF v3.1\nid: other"
    cfs = [[name if r[0] == "EF v3.1" else r[0], *r[1:]] for r in workbooks.sheet("cfs")]
    indicators = [
        [name if r[0] == "EF v3.1" else r[0], *r[1:]] for r in workbooks.sheet("indicators")
    ]
    imported = import_method(
        read_workbook(workbooks.write(cfs=cfs, indicators=indicators), name), release
    )

    folder = write_method(imported, tmp_path / "EF-3.1")

    assert read_method(folder) == imported.method
    assert "# Imported by headwater from LCIA Implementation 1.xlsx, method EF v3.1 id: other" in (
        (folder / "method.yaml").read_text(encoding="utf-8")
    )


def test_an_existing_folder_with_files_is_not_overwritten(
    imported: MethodImport, tmp_path: Path
) -> None:
    folder = tmp_path / "EF-3.1"
    folder.mkdir()
    (folder / "notes.txt").write_text("keep me", encoding="utf-8")

    with pytest.raises(InvalidDataError) as error:
        write_method(imported, folder)

    assert [p.code for p in error.value.problems] == ["folder-not-empty"]
    assert [p.name for p in folder.iterdir()] == ["notes.txt"]


def test_an_empty_existing_folder_is_used(imported: MethodImport, tmp_path: Path) -> None:
    folder = tmp_path / "EF-3.1"
    folder.mkdir()

    write_method(imported, folder)

    assert read_method(folder) == imported.method
    assert not [p.name for p in tmp_path.iterdir() if p.name.startswith(".tmp-")]


def test_the_method_scores_the_release_it_was_mapped_to(
    imported: MethodImport, release: Release, tmp_path: Path, fx
) -> None:
    folder = write_method(imported, tmp_path / "EF-3.1")

    background = hw.load(release.path, method=folder)

    assert background.method == imported.method
    [cadmium] = background.not_characterised
    assert (cadmium.flow_id, cadmium.name) == (fx.elementary(2), "Cadmium")
    assert cadmium.processes == (fx.pid(1, 1),)


def test_an_indicators_release_is_not_a_flow_list(
    imported: MethodImport, workbook: Path, release: Release, tmp_path: Path
) -> None:
    background = hw.load(release.path, method=write_method(imported, tmp_path / "EF-3.1"))
    scores = background.write_indicators(into=tmp_path / "derived")

    with pytest.raises(InvalidDataError) as error:
        import_method(read_workbook(workbook, "EF v3.1"), scores)

    assert [p.code for p in error.value.problems] == ["indicators-release"]
