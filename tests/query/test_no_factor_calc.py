"""A flow the study emits that the method has no factor for: calc refuses it as validate does,
until a row of no_factor.csv acknowledges it. Then it counts 0 and the result lists it.

The fixture `tshirt-no-factor` is the toy T-shirt with 0.002 kg of COD to water, which
toy-climate does not cover, and the row for it.
"""

from collections.abc import Callable
from pathlib import Path

import pytest

import headwater as hw
from headwater.model import Flow, FlowKind, InvalidDataError
from tests.conftest import add_lines, toy_database

REL = 1e-9  # AGENTS.md: relative 1e-9 for toy fixtures

STUDY = "tshirt-no-factor"
HEADER = "flow,flow_name,method,reason,who,when\n"
COD = (
    'tshirt:cod,"COD, Chemical Oxygen Demand",toy-climate,'
    "not characterised in toy-climate,Sam,2026-10-12\n"
)
CO2 = "toy:co2,,toy-climate,thought so,Sam,2026-10-12\n"

OpenStudy = Callable[..., hw.Database]
LoadToy = Callable[[Path], hw.Background]


def rows(toy: Path, *lines: str) -> None:
    (toy_database(toy, STUDY) / "no_factor.csv").write_text(HEADER + "".join(lines))


def test_without_the_row_calc_refuses_with_the_errors_of_validate(
    toy: Path, either: hw.Background, open_study: OpenStudy
) -> None:
    rows(toy)
    study = open_study(STUDY, either, root=toy)

    report = study.validate(either)
    with pytest.raises(InvalidDataError) as raised:
        study.calc(either, "tshirt:tshirt")

    assert [(p.code, p.item) for p in raised.value.problems] == [("no-factor", "flow tshirt:cod")]
    assert raised.value.problems == report.errors
    assert "tshirt:tshirt" in str(raised.value)


def test_with_the_row_the_flow_counts_0_and_is_listed_with_its_reason(
    either: hw.Background, open_study: OpenStudy
) -> None:
    result = open_study(STUDY, either).calc(either, "tshirt:tshirt")

    assert result.score == pytest.approx(1.19205, rel=REL)
    assert result.forward_score == pytest.approx(1.19205, rel=REL)
    (flow,) = result.not_characterised
    assert flow.flow_id == "tshirt:cod"
    assert flow.name == "COD, Chemical Oxygen Demand"
    assert flow.processes == ("tshirt:tshirt",)
    assert flow.in_database
    assert flow.reason == "not characterised in toy-climate"
    assert repr(result).endswith(", 1 flow not characterised)")


def test_a_row_that_is_not_needed_does_not_stop_calc(
    toy: Path, either: hw.Background, open_study: OpenStudy
) -> None:
    rows(toy, COD, CO2)
    study = open_study(STUDY, either, root=toy)

    assert [p.code for p in study.validate(either).warnings] == ["no-factor-not-needed"]
    assert study.calc(either, "tshirt:tshirt").score == pytest.approx(1.19205, rel=REL)


@pytest.mark.parametrize(
    ("lines", "valid"),
    [
        pytest.param((COD,), True, id="row"),
        pytest.param((), False, id="no row"),
        pytest.param((COD, CO2), True, id="row not needed"),
        pytest.param((CO2,), False, id="only a row not needed"),
    ],
)
def test_validate_passes_exactly_when_calc_does_not_refuse(
    toy: Path, either: hw.Background, open_study: OpenStudy, lines: tuple[str, ...], valid: bool
) -> None:
    rows(toy, *lines)
    study = open_study(STUDY, either, root=toy)

    report = study.validate(either)

    assert (report.errors == ()) is valid
    if valid:
        study.calc(either, "tshirt:tshirt")
    else:
        with pytest.raises(InvalidDataError) as raised:
            study.calc(either, "tshirt:tshirt")
        assert raised.value.problems == report.errors


def test_a_flow_the_study_and_the_background_emit_is_listed_once_as_the_studys_own(
    toy: Path, load_toy: LoadToy, open_study: OpenStudy
) -> None:
    with (toy / "background" / "flows.csv").open("a") as f:
        f.write('tshirt:cod,"COD, Chemical Oxygen Demand",elementary,kg,water,\n')
    add_lines(toy / "background", "electricity-production", "e09,emission,tshirt:cod,0.0001,kg,")
    background = load_toy(toy)

    result = open_study(STUDY, background, root=toy).calc(background, "tshirt:tshirt")

    assert result.score == pytest.approx(1.19205, rel=REL)
    (flow,) = result.not_characterised
    assert flow.processes == ("toy:electricity-production", "tshirt:tshirt")
    assert flow.in_database
    assert flow.reason == "not characterised in toy-climate"


def test_a_background_flow_is_listed_as_not_the_studys_own(
    toy: Path, load_toy: LoadToy, open_study: OpenStudy
) -> None:
    with (toy / "background" / "flows.csv").open("a") as f:
        f.write("toy:n2o,Dinitrogen monoxide,elementary,kg,air,10024-97-2\n")
    add_lines(toy / "background", "electricity-production", "e09,emission,toy:n2o,0.0001,kg,")
    background = load_toy(toy)

    result = open_study(STUDY, background, root=toy).calc(background, "tshirt:tshirt")

    assert [(f.flow_id, f.in_database, f.reason) for f in result.not_characterised] == [
        ("toy:n2o", False, None),
        ("tshirt:cod", True, "not characterised in toy-climate"),
    ]


N2O = Flow(id="toy:n2o", name="Dinitrogen monoxide", kind=FlowKind.elementary, unit="kg")


def test_a_study_flow_that_only_the_release_defines_is_refused_then_listed(
    toy: Path, make_release, records, open_study: OpenStudy
) -> None:
    # The study's line names a flow of the background release, not of its own flows.csv: the
    # shape of an ecoinvent flow in a real study.
    background = hw.load(make_release(records.with_flows(N2O)).path, method="toy-climate")
    add_lines(toy_database(toy, STUDY), "tshirt", "e05,emission,toy:n2o,0.0001,kg,")
    study = open_study(STUDY, background, root=toy)

    report = study.validate(background)
    with pytest.raises(InvalidDataError) as raised:
        study.calc(background, "tshirt:tshirt")

    assert [(p.code, p.item) for p in report.errors] == [("no-factor", "flow toy:n2o")]
    assert raised.value.problems == report.errors

    rows(toy, COD, "toy:n2o,Dinitrogen monoxide,toy-climate,not in toy-climate,Sam,2026-10-12\n")
    result = open_study(STUDY, background, root=toy).calc(background, "tshirt:tshirt")

    assert result.score == pytest.approx(1.19205, rel=REL)
    assert [(f.flow_id, f.name, f.in_database, f.reason) for f in result.not_characterised] == [
        ("toy:n2o", "Dinitrogen monoxide", True, "not in toy-climate"),
        ("tshirt:cod", "COD, Chemical Oxygen Demand", True, "not characterised in toy-climate"),
    ]
