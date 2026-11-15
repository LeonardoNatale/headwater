"""The case study of notebook 03: a backpack on an invented background, frozen as a release next to
the notebook. These tests keep the release, the pin and the hand-checked score in step."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

import headwater as hw
from headwater.release import Release, pull
from tests.conftest import open_database

CASE_STUDY = Path(__file__).resolve().parents[2] / "notebooks" / "case-study"
RELEASE = next((CASE_STUDY / "releases").glob("invented-background@*"))
REL = 1e-9


def _build_script() -> ModuleType:
    path = Path(__file__).resolve().parent / "build_demo_release.py"
    spec = importlib.util.spec_from_file_location("case_study_build_release", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_the_committed_release_is_what_the_build_script_writes(tmp_path: Path) -> None:
    rebuilt = _build_script().build_case_study_release(tmp_path)

    assert rebuilt.path.name == RELEASE.name, (
        "The invented background or the release format changed. Run "
        "`uv run python tests/ci/build_demo_release.py`, replace the folder in "
        "notebooks/case-study/releases/, and pin the backpack database to the new name."
    )
    committed = Release.open(RELEASE)
    committed.verify()
    assert committed.hash == rebuilt.hash


def test_the_backpack_database_pins_the_committed_release() -> None:
    pin = open_database(CASE_STUDY / "project" / "backpack").folder.database.background

    assert pin is not None
    assert f"{pin.name}@{pin.version}" == RELEASE.name


def test_the_backpack_has_the_hand_checked_score(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """11.454848 kg CO2-eq, worked out by hand in notebook 03."""
    monkeypatch.setenv("HEADWATER_HOME", str(tmp_path))
    pull(RELEASE)
    study = hw.open(CASE_STUDY / "project" / "backpack-footprint")
    assert isinstance(study, hw.Study)
    background = hw.load(RELEASE.name)

    [base] = study.calc(background)
    [portugal] = study.calc(background, scenario="factory-in-portugal")
    [recycled] = study.calc(background, scenario="recycled-polyester")

    assert base.score == pytest.approx(11.454848, rel=REL)
    assert portugal.score == pytest.approx(10.957088, rel=REL)
    assert recycled.score == pytest.approx(10.0589744, rel=REL)
