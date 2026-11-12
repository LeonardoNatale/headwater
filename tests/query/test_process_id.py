"""Scoring a process of a database, `calc(bg, process_id)`, as Brightway's `LCA({act: 1})`: one
unit of what that process makes. database.yaml is not touched."""

from pathlib import Path

import pytest

import headwater as hw
from tests.conftest import open_database, toy_cases, toy_database

TIGHT = 1e-12


@pytest.fixture
def toy_with_sewing(toy: Path, background: hw.Background) -> Path:
    """A copy of the toy where the T-shirt buys from a second process of its database: sewing,
    which takes 0.1 kWh."""
    editor = open_database(toy / "tshirt" / "tshirt").edit(background)
    sewing = editor.new_process(
        "sewing", name="sewing", location="GLO", product="sewing", unit="unit"
    )
    sewing.new_exchange(
        "input", "toy:electricity", 0.1, "kWh", supplier="toy:electricity-production"
    )
    editor.process("tshirt").new_exchange(
        "input", "tshirt:sewing", 1, "unit", supplier="tshirt:sewing"
    )
    editor.save()
    return toy


def test_each_process_scores_the_same_by_calc_and_by_scores(
    toy_with_sewing: Path, background: hw.Background
) -> None:
    checked = 0
    for case in toy_cases(toy_with_sewing):
        folder = toy_database(toy_with_sewing, case)
        database = open_database(folder)
        before = (folder / "database.yaml").read_bytes()
        for process in database.folder.processes:
            scores = database.scores(background, process.id)
            assert list(scores) == [i.id for i in background.indicators]
            for indicator, score in scores.items():
                got = database.calc(background, process_id=process.id, indicator=indicator)
                assert got.process_id == process.id
                assert score == pytest.approx(got.score, rel=TIGHT, abs=TIGHT)
            checked += 1
        assert (folder / "database.yaml").read_bytes() == before
    assert checked == 11  # ten toy databases of one process, and the T-shirt's sewing


def test_each_process_of_a_database_has_its_own_input_hash(
    toy_with_sewing: Path, background: hw.Background
) -> None:
    database = open_database(toy_with_sewing / "tshirt" / "tshirt")

    sewing = database.calc(background, "tshirt:sewing").provenance.input_hash
    tshirt = database.calc(background, "tshirt:tshirt").provenance.input_hash

    assert sewing != tshirt


def test_sewing_is_its_own_number_and_the_tshirt_adds_it(
    toy_with_sewing: Path, background: hw.Background
) -> None:
    database = open_database(toy_with_sewing / "tshirt" / "tshirt")
    # 0.1 kWh x 0.5298 per kWh; the T-shirt adds it to its 1.19205.
    assert database.calc(background, process_id="tshirt:sewing").score == pytest.approx(0.05298)
    assert database.calc(background, "tshirt:tshirt").score == pytest.approx(1.24503)


def test_a_process_the_database_does_not_have_is_refused_naming_its_processes(
    toy_with_sewing: Path, background: hw.Background
) -> None:
    database = open_database(toy_with_sewing / "tshirt" / "tshirt")
    with pytest.raises(ValueError, match="tshirt:sewin.*Did you mean 'tshirt:sewing'"):
        database.calc(background, process_id="tshirt:sewin")
    with pytest.raises(ValueError, match="toy:fabric-production"):
        database.scores(background, process_id="toy:fabric-production")


def test_two_processes_that_make_the_same_flow_are_each_scored_by_their_id(
    toy_with_sewing: Path, background: hw.Background
) -> None:
    # sewing-b makes tshirt:sewing too, in RER, with 0.2 kWh: 0.2 x 0.5298.
    editor = open_database(toy_with_sewing / "tshirt" / "tshirt").edit(background)
    twin = editor.new_process(
        "sewing-b", name="sewing", location="RER", flow="tshirt:sewing", unit="unit"
    )
    twin.new_exchange("input", "toy:electricity", 0.2, "kWh", supplier="toy:electricity-production")
    database = editor.save()

    assert database.calc(background, "tshirt:sewing").score == pytest.approx(0.05298)
    assert database.calc(background, "tshirt:sewing-b").score == pytest.approx(0.10596)
    # The T-shirt names its supplier, so it still buys the first sewing.
    assert database.calc(background, "tshirt:tshirt").score == pytest.approx(1.24503)
