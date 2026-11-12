"""A result and an indicators release say how many scores are exactly 0 by structure."""

import json
from pathlib import Path

import pytest

import headwater as hw
from headwater.release import Release
from tests.conftest import open_database

TOY = Path(__file__).resolve().parent.parent / "fixtures" / "toy"

# The T-shirt study pins method toy-climate: this one keeps the id and adds an indicator.
METHOD = """id: toy-climate
version: "1"
flow_list: toy
indicators:
  - id: climate-change
    name: Climate change
    unit: kg CO2-eq
  - id: methane
    name: Methane
    unit: kg CO2-eq
"""

# Incineration emits CO2 and no methane: its methane score is exactly 0, with nothing upstream.
FACTORS = """indicator,flow,location,factor
climate-change,toy:co2,,1
climate-change,toy:ch4,,29.8
methane,toy:ch4,,29.8
"""


@pytest.fixture
def method(tmp_path: Path) -> Path:
    folder = tmp_path / "toy-climate"
    folder.mkdir()
    (folder / "method.yaml").write_text(METHOD)
    (folder / "factors.csv").write_text(FACTORS)
    return folder


@pytest.fixture
def background(method: Path) -> hw.Background:
    return hw.load(TOY / "background", method=method, namespace="toy")


def test_a_result_counts_the_exact_zeros_of_its_indicator(background: hw.Background) -> None:
    study = open_database(TOY / "tshirt" / "tshirt")

    climate = study.calc(background, "tshirt:tshirt", indicator="climate-change")
    methane = study.calc(background, "tshirt:tshirt", indicator="methane")

    assert climate.provenance.exact_zeros == 0
    assert methane.provenance.exact_zeros == 1
    assert climate.score == pytest.approx(1.19205, rel=1e-9)


def test_an_indicators_release_counts_exact_zeros_per_indicator(
    release: Release, method: Path, tmp_path: Path
) -> None:
    background = hw.load(release.path, method=method)

    derived = background.write_indicators(into=tmp_path / "derived")

    provenance = json.loads((derived.path / "release.json").read_text())["provenance"]
    assert provenance["exact_zeros"] == {"climate-change": 0, "methane": 1}
