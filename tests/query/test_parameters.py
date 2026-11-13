"""Named parameters: worked out on every calc, with a sensitivity per parameter by the chain rule.

Every sensitivity here is checked against a central finite difference, where the parameter's
value is moved by a scenario and the database is calculated again.
"""

import shutil
from dataclasses import replace
from pathlib import Path

import pytest

import headwater as hw
from headwater.calc import CalculationError
from tests.query.conftest import pinned_study

REL = 1e-9
STEP = 1e-6

PARAMETERS = """\
database:
  fabric_kg: {value: 0.25, unit: kg}
  loss: 0.05
  fabric_gross: fabric_kg * (1 + loss)
  scraps_g: 50
  batch: 2
  unused: 7
processes:
  tshirt:tshirt:
    sew_kwh: 0.5
  toy:fabric-production:
    mill_kwh: 2.5
"""
TSHIRT = """\
process,line,role,flow,amount,unit,supplier
tshirt,e01,reference,tshirt:tshirt,batch,unit,
tshirt,e02,input,toy:cotton-fabric,batch * fabric_gross,kg,toy:fabric-production
tshirt,e03,input,toy:electricity,"batch * max(sew_kwh, 0.1) * 1000",Wh,toy:electricity-production
tshirt,e04,waste_to_treatment,toy:textile-waste,batch * scraps_g,g,toy:incineration-textile-waste
tshirt,e05,emission,toy:co2,batch * 0.01 * exp(loss),kg,
"""
WEAVING = """\
process,line,role,flow,amount,unit,supplier,comment
toy:fabric-production,e02,input,toy:electricity,mill_kwh ** 2 / 2.5,kWh,toy:electricity-production,
"""


@pytest.fixture
def rich(toy: Path) -> Path:
    """A T-shirt whose every line is a formula: a chained parameter, a parameter of the process,
    a reference amount, lines in Wh and g, an emission, and an override of the fabric."""
    database = toy / "rich" / "tshirt"
    shutil.copytree(toy / "tshirt" / "tshirt", database)
    (database / "parameters.yaml").write_text(PARAMETERS)
    (database / "exchanges.csv").write_text(TSHIRT)
    (database / "overrides").mkdir()
    (database / "overrides" / "weaving.csv").write_text(WEAVING)
    return toy


def probe(toy: Path, case: str, background: hw.Background, parameter: str, value: float) -> float:
    """The score with one parameter set to a value, through a study's scenario made for it."""
    study = hw.Study(
        {"tshirt:tshirt": 1},
        ["toy-climate"],
        databases=toy / case,
        scenarios={"probe": {"changes": [{"parameter": parameter, "value": value}]}},
    )
    (result,) = pinned_study(study, background).calc(background, scenario="probe")
    return result.score


def test_every_sensitivity_matches_a_finite_difference(
    either: hw.Background, open_study, rich: Path
) -> None:
    database = open_study("rich", either, root=rich)
    result = database.calc(either, "tshirt:tshirt")

    found = {p.parameter_id: p for p in result.parameters()}

    assert set(found) == {
        "batch",
        "fabric_gross",
        "fabric_kg",
        "loss",
        "scraps_g",
        "unused",
        "tshirt:tshirt/sew_kwh",
        "toy:fabric-production/mill_kwh",
    }
    for parameter_id, parameter in found.items():
        up = probe(rich, "rich", either, parameter_id, parameter.value + STEP)
        down = probe(rich, "rich", either, parameter_id, parameter.value - STEP)
        expected = (up - down) / (2 * STEP)
        assert parameter.sensitivity == pytest.approx(expected, rel=1e-6, abs=1e-9), parameter_id
        assert parameter.elasticity == pytest.approx(
            parameter.sensitivity * parameter.value / result.score, rel=1e-12
        )
    assert found["unused"].sensitivity == 0.0


def test_the_hand_checked_sensitivities(either: hw.Background, open_study, rich: Path) -> None:
    result = open_study("rich", either, root=rich).calc(either, "tshirt:tshirt")
    found = {p.parameter_id: p for p in result.parameters()}

    # Per T-shirt: 0.2625 kg of fabric copy at 2.1192 + 2.5 x 0.5298 = 3.4437, 0.5 kWh at
    # 0.5298, 0.05 kg of scraps at 1.2 and 0.01 x e^0.05 kg of CO2.
    score = 0.2625 * 3.4437 + 0.5 * 0.5298 + 0.05 * 1.2 + 0.01 * 2.718281828459045**0.05
    assert result.score == pytest.approx(score, rel=REL)
    # The batch makes 2 T-shirts out of twice everything: the score per T-shirt does not move.
    assert found["batch"].sensitivity == pytest.approx(0.0, abs=1e-12)
    # fabric_kg moves fabric_gross by 1.05, and each kg of fabric adds 3.4437 per T-shirt.
    assert found["fabric_gross"].sensitivity == pytest.approx(3.4437, rel=REL)
    assert found["fabric_kg"].sensitivity == pytest.approx(1.05 * 3.4437, rel=REL)
    # Grams: one more g of scraps is 0.001 kg at 1.2 per kg.
    assert found["scraps_g"].sensitivity == pytest.approx(0.0012, rel=REL)
    assert found["tshirt:tshirt/sew_kwh"].sensitivity == pytest.approx(0.5298, rel=REL)
    # mill_kwh ** 2 / 2.5 has the derivative 2 x 2.5 / 2.5 = 2 kWh per kWh, for 0.2625 kg.
    assert found["toy:fabric-production/mill_kwh"].sensitivity == pytest.approx(
        2 * 0.2625 * 0.5298, rel=REL
    )


def test_parameters_are_ranked_by_elasticity(either: hw.Background, open_study, rich) -> None:
    result = open_study("rich", either, root=rich).calc(either, "tshirt:tshirt")

    parameters = result.parameters()
    sizes = [abs(p.elasticity) for p in parameters]

    assert sizes == sorted(sizes, reverse=True)
    assert parameters[0].parameter_id in ("fabric_gross", "fabric_kg")
    assert result.parameters(top=2) == parameters[:2]
    gross = next(p for p in parameters if p.parameter_id == "fabric_gross")
    assert (gross.formula, gross.unit, gross.scope) == ("fabric_kg * (1 + loss)", None, None)


def test_a_derivative_that_is_not_a_number_is_named(
    either: hw.Background, open_study, toy: Path
) -> None:
    database = toy / "tshirt" / "tshirt"
    (database / "parameters.yaml").write_text("database:\n  extra: 0\n")
    csv = database / "exchanges.csv"
    csv.write_text(csv.read_text().replace(",0.5,kWh", ",0.5 + sqrt(extra),kWh"))
    result = open_study("tshirt", either, root=toy).calc(either, "tshirt:tshirt")

    assert result.score == pytest.approx(1.19205, rel=REL)
    with pytest.raises(CalculationError, match=r"tshirt:tshirt line e03.*sqrt\(extra\)"):
        result.parameters()


def test_parameters_are_worked_out_on_every_calc(
    either: hw.Background, open_study, toy: Path
) -> None:
    # A record changed after the database was read still gives what its formula says.
    database = toy / "tshirt" / "tshirt"
    (database / "parameters.yaml").write_text("database:\n  fabric_kg: 0.25\n")
    csv = database / "exchanges.csv"
    csv.write_text(csv.read_text().replace(",0.25,kg", ",fabric_kg,kg"))
    opened = open_study("tshirt", either, root=toy)
    processes = tuple(
        p.model_copy(
            update={
                "exchanges": tuple(
                    e.model_copy(update={"amount": 99.0}) if e.formula else e for e in p.exchanges
                )
            }
        )
        for p in opened.folder.processes
    )

    stale = hw.Database(replace(opened.folder, processes=processes))

    assert stale.calc(either, "tshirt:tshirt").score == pytest.approx(1.19205, rel=REL)


def test_a_parameter_in_a_change_to_the_background_has_its_sensitivity(
    either: hw.Background, toy: Path
) -> None:
    # The scenario sets fabric's electricity, for everyone, to the parameter mill_kwh = 2.5.
    # One more kWh per kg of fabric adds 0.25 kg x 0.5298.
    database = toy / "tshirt" / "tshirt"
    (database / "parameters.yaml").write_text("database:\n  mill_kwh: 2.5\n")
    changes: list[dict[str, object]] = [
        {"process": "toy:fabric-production", "line": "e02", "amount": "mill_kwh"}
    ]

    def score(extra: float | None) -> hw.Result:
        given = [*changes, *([{"parameter": "mill_kwh", "value": extra}] if extra else [])]
        study = hw.Study(
            {"tshirt:tshirt": 1},
            ["toy-climate"],
            databases=toy / "tshirt",
            scenarios={"mill": {"changes": given}},
        )
        return pinned_study(study, either).calc(either, scenario="mill")[0]

    result = score(None)

    (mill,) = result.parameters()
    assert result.score == pytest.approx(1.125825, rel=REL)
    assert mill.sensitivity == pytest.approx(0.25 * 0.5298, rel=REL)
    up = score(2.5 + STEP).score
    down = score(2.5 - STEP).score
    assert mill.sensitivity == pytest.approx((up - down) / (2 * STEP), rel=1e-6)
