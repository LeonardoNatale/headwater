"""Building, loading and calculating a release warns nothing, on the newest duckdb as on the locked
one. duckdb 2 warns when the result of `execute` is read as Arrow, so headwater reads it from
`sql` instead."""

import importlib.util
import sys
import warnings
from pathlib import Path
from types import ModuleType

import pytest

import headwater as hw
from tests.conftest import open_study_folder

ROOT = Path(__file__).resolve().parent.parent
STUDY = ROOT / "src" / "headwater" / "demo" / "studies" / "tshirt-footprint"


def _build_script() -> ModuleType:
    path = ROOT / "tests" / "ci" / "build_demo_release.py"
    spec = importlib.util.spec_from_file_location("demo_build_release_no_warning", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_a_release_builds_loads_and_calculates_without_a_warning(tmp_path: Path) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        release = _build_script().build_toy_release(tmp_path)
        release.verify()
        background = hw.load(release.path)
        [result] = open_study_folder(STUDY).calc(background)

    assert result.score == pytest.approx(1.242646, rel=1e-9)
