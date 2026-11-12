"""Editing a database on a background read from a release: the pin and an override's lines come
from the release."""

from pathlib import Path

import pytest

import headwater as hw


def test_a_new_database_on_a_release_pins_it_and_an_override_reads_its_lines(
    release_background: hw.Background, tmp_path: Path
) -> None:
    release = release_background.release
    assert release is not None
    database = hw.Database.new(tmp_path / "tshirt", release_background, methods=["toy-climate"])
    tshirt = database.new_process(
        "tshirt", name="T-shirt", location="GLO", product="T-shirt", unit="unit"
    )
    tshirt.new_exchange("input", "toy:cotton-fabric", 0.25, "kg", supplier="toy:fabric-production")
    tshirt.new_exchange(
        "input", "toy:electricity", 0.5, "kWh", supplier="toy:electricity-production"
    )
    database.override("toy:electricity-production").set_exchange("e02", amount=0.3)
    saved = database.save()

    text = (tmp_path / "tshirt" / "database.yaml").read_text()
    assert f"background: {release.name}@{release.hash[:12]}\n" in text
    assert "methods: [toy-climate]\n" in text
    assert (tmp_path / "tshirt" / "overrides" / "toy-electricity-production.csv").read_text() == (
        "process,line,role,flow,amount,unit,supplier\n"
        "toy:electricity-production,e02,emission,toy:co2,0.3,kg,\n"
    )
    assert saved.calc(release_background, "tshirt:tshirt").score == pytest.approx(1.09205, rel=1e-9)
