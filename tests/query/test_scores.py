"""`Study.scores`: the score of a study for every indicator at once, as `calc` gives each one.

Numbers are those of `calc` (docs/calculation.md): the T-shirt is 0.25 × 3.7086 + 0.5 × 0.5298 =
1.19205 kg CO2-eq, and 0.25 × 0.007 + 0.5 × 0.001 = 0.00225 kg CH4 for the second indicator.
"""

import shutil
from pathlib import Path

import pytest

import headwater as hw
from headwater.model import InvalidDataError
from headwater.query import database as study_module
from tests.conftest import only_process, open_database, toy_cases, toy_database

TOY = Path(__file__).resolve().parent.parent / "fixtures" / "toy"
REL = 1e-12
STUDIES = toy_cases(TOY)


@pytest.fixture(scope="module")
def two_indicators(tmp_path_factory: pytest.TempPathFactory) -> hw.Background:
    """The toy background with a second indicator, methane, next to climate change."""
    method = tmp_path_factory.mktemp("method") / "two-indicators"
    shutil.copytree(TOY / "methods" / "toy-climate", method)
    path = method / "method.yaml"
    path.write_text(
        path.read_text().replace(
            "    unit: kg CO2-eq\n",
            "    unit: kg CO2-eq\n  - id: methane\n    name: Methane\n    unit: kg CH4\n",
        )
    )
    with (method / "factors.csv").open("a") as handle:
        handle.write("methane,toy:ch4,,1\n")
    return hw.load(TOY / "background", method=method, namespace="toy")


def test_the_tshirt_scores_every_indicator_in_one_call(
    two_indicators: hw.Background,
) -> None:
    scores = open_database(TOY / "tshirt" / "tshirt").scores(two_indicators, "tshirt:tshirt")

    assert list(scores) == ["climate-change", "methane"]
    assert scores["climate-change"] == pytest.approx(1.19205, rel=REL)
    assert scores["methane"] == pytest.approx(0.00225, rel=REL)


@pytest.mark.parametrize("name", STUDIES)
def test_each_score_is_what_calc_gives_for_that_indicator(
    name: str, two_indicators: hw.Background
) -> None:
    """Overrides, parameters, cut-offs, waste and acknowledged flows all take the same road."""
    study = open_database(toy_database(TOY, name))

    scores = study.scores(two_indicators, only_process(study))

    for indicator, score in scores.items():
        expected = study.calc(two_indicators, only_process(study), indicator=indicator).score
        assert score == pytest.approx(expected, rel=REL, abs=1e-15), (name, indicator)


def test_a_study_that_does_not_validate_is_refused_as_calc_refuses_it(
    toy: Path, two_indicators: hw.Background
) -> None:
    csv = toy / "tshirt" / "tshirt" / "exchanges.csv"
    csv.write_text(csv.read_text().replace("toy:fabric-production", "toy:nowhere"))
    study = open_database(toy / "tshirt" / "tshirt")

    with pytest.raises(InvalidDataError) as calc_error:
        study.calc(two_indicators, "tshirt:tshirt", indicator="methane")
    with pytest.raises(InvalidDataError) as scores_error:
        study.scores(two_indicators, "tshirt:tshirt")

    assert str(scores_error.value) == str(calc_error.value)


def test_a_pin_that_is_not_the_release_given_is_refused(
    release_background: hw.Background,
) -> None:
    """The toy studies pin the label `toy-background@fixture`, which names no release."""
    study = open_database(TOY / "tshirt" / "tshirt")

    with pytest.raises(InvalidDataError) as error:
        study.scores(release_background, "tshirt:tshirt")

    assert [p.code for p in error.value.problems] == ["background-pin-mismatch"]


def test_the_study_is_assembled_once_whatever_the_number_of_indicators(
    two_indicators: hw.Background, monkeypatch: pytest.MonkeyPatch
) -> None:
    built: list[int] = []
    real = study_module.assemble

    def counting(*args, **kwargs):  # type: ignore[no-untyped-def]
        built.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(study_module, "assemble", counting)

    open_database(TOY / "tshirt" / "tshirt").scores(two_indicators, "tshirt:tshirt")

    assert len(built) == 1


def test_scores_do_not_solve_with_the_background(
    two_indicators: hw.Background, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The stored scores of the background are enough: nothing is solved against its matrix."""
    monkeypatch.setattr(study_module, "solve_study", None)

    scores = open_database(TOY / "tshirt" / "tshirt").scores(two_indicators, "tshirt:tshirt")

    assert scores["climate-change"] == pytest.approx(1.19205, rel=REL)
