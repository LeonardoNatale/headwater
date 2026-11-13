"""A database buys from the processes of the databases it uses, on the same background.

The dyed T-shirt is the T-shirt of docs/calculation.md plus 0.01 kg of reactive dye from the
database `dyes`, in the same case folder: `tshirt-dyed/tshirt` uses `tshirt-dyed/dyes`. The dye
takes 2 kWh of electricity and emits 4 kg of CO2 per kg, so it scores 2 × 0.5298 + 4 = 5.0596 per
kg, and the T-shirt 1.19205 + 0.01 × 5.0596 = 1.242646.
"""

import shutil
from pathlib import Path

import pytest

import headwater as hw
from headwater.model import InvalidDataError, Report
from tests.conftest import add_lines, open_database, open_study_folder

TOY = Path(__file__).resolve().parent.parent / "fixtures" / "toy"
REL = 1e-9
DYED = 1.242646


@pytest.fixture(scope="module")
def background() -> hw.Background:
    return hw.load(TOY / "background", method=TOY / "methods" / "toy-climate", namespace="toy")


@pytest.fixture
def dyed(tmp_path: Path) -> Path:
    """A writable copy of the dyed T-shirt's case: the databases `tshirt` and `dyes`, side by
    side as committed."""
    target = tmp_path / "tshirt-dyed"
    shutil.copytree(TOY / "tshirt-dyed", target)
    return target


def _edit(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    assert old in text, f"{old!r} not found in {path}"
    path.write_text(text.replace(old, new, 1))


def _codes(report: Report) -> list[str]:
    return [p.code for p in report.errors]


def test_the_dyed_tshirt_includes_the_dye_burden(background: hw.Background) -> None:
    result = open_database(TOY / "tshirt-dyed" / "tshirt").calc(background, "tshirt:tshirt")

    assert result.score == pytest.approx(DYED, rel=REL)
    assert result.forward_score == pytest.approx(DYED, rel=REL)
    assert result.provenance.used == ("dyes",)


def test_the_dyed_tshirt_validates(background: hw.Background) -> None:
    report = open_database(TOY / "tshirt-dyed" / "tshirt").validate(background)

    assert report.problems == ()


def test_the_library_scores_on_its_own(background: hw.Background) -> None:
    result = open_database(TOY / "tshirt-dyed" / "dyes").calc(background, "dyes:reactive-dye")

    assert result.score == pytest.approx(5.0596, rel=REL)


def test_explain_names_the_dye_by_its_full_id(background: hw.Background) -> None:
    root = (
        open_database(TOY / "tshirt-dyed" / "tshirt")
        .calc(background, "tshirt:tshirt")
        .contributions()
    )

    dye = next(c for c in root.children if c.process_id == "dyes:reactive-dye")
    assert dye.amount == pytest.approx(0.01, rel=REL)
    assert dye.score == pytest.approx(0.050596, rel=REL)
    assert dye.direct == pytest.approx(0.04, rel=REL)
    assert dye.database == "dyes"
    assert "0.01 kg of reactive dye production (dyes:reactive-dye): 0.05060 (4.1%)" in str(root)


def test_where_counts_the_electricity_the_dye_takes(background: hw.Background) -> None:
    where = open_database(TOY / "tshirt-dyed" / "tshirt").calc(background, "tshirt:tshirt").where()

    by_id = {e.process_id: e for e in where.processes}
    # 0.5 for the T-shirt, 0.75 for its fabric, 0.02 for its dye
    assert by_id["toy:electricity-production"].amount == pytest.approx(1.27, rel=REL)
    assert by_id["toy:electricity-production"].score == pytest.approx(0.672846, rel=REL)
    assert by_id["dyes:reactive-dye"].score == pytest.approx(0.04, rel=REL)
    assert "(dyes:reactive-dye, GLO): 0.04000" in str(where)


def test_levers_name_the_dye_line_by_its_full_id(background: hw.Background) -> None:
    levers = (
        open_database(TOY / "tshirt-dyed" / "tshirt")
        .calc(background, "tshirt:tshirt")
        .sensitivity()
    )

    co2 = next(lv for lv in levers if (lv.process_id, lv.line_id) == ("dyes:reactive-dye", "e03"))
    assert co2.sensitivity == pytest.approx(0.01, rel=REL)
    assert co2.elasticity == pytest.approx(0.04 / DYED, rel=REL)


def test_the_used_database_keeps_its_own_parameters(background: hw.Background, dyed: Path) -> None:
    (dyed / "tshirt" / "parameters.yaml").write_text("database:\n  kwh_per_kg: 9\n")

    result = open_database(dyed / "tshirt").calc(background, "tshirt:tshirt")

    assert result.score == pytest.approx(DYED, rel=REL)


def test_the_parameters_of_a_result_are_those_of_the_database_itself(
    background: hw.Background,
) -> None:
    result = open_database(TOY / "tshirt-dyed" / "tshirt").calc(background, "tshirt:tshirt")

    assert result.parameters() == ()


def test_an_override_of_the_used_database_changes_only_its_own_purchases(
    background: hw.Background, dyed: Path
) -> None:
    (dyed / "dyes" / "overrides").mkdir()
    shutil.copy(
        TOY / "tshirt-local-electricity" / "tshirt" / "overrides" / "own-wind-power.csv",
        dyed / "dyes" / "overrides" / "wind.csv",
    )

    result = open_database(dyed / "tshirt").calc(background, "tshirt:tshirt")

    # the dye's 2 kWh at 0.3298, the T-shirt's own 0.5 kWh still at 0.5298
    assert result.score == pytest.approx(1.19205 + 0.01 * (4 + 2 * 0.3298), rel=REL)
    root = result.contributions(min_share=0)
    dye = next(c for c in root.children if c.process_id == "dyes:reactive-dye")
    [copy] = dye.children
    assert copy.process_id == "dyes:copy:toy:electricity-production"
    assert copy.copy_of == "toy:electricity-production"


def test_an_override_of_the_using_database_does_not_reach_the_used_one(
    background: hw.Background, dyed: Path
) -> None:
    shutil.copytree(
        TOY / "tshirt-local-electricity" / "tshirt" / "overrides", dyed / "tshirt" / "overrides"
    )

    result = open_database(dyed / "tshirt").calc(background, "tshirt:tshirt")

    assert result.score == pytest.approx(1.09205 + 0.01 * 5.0596, rel=REL)


def test_a_global_change_in_a_scenario_reaches_the_used_database(
    background: hw.Background, dyed: Path
) -> None:
    shutil.copy(
        TOY / "tshirt-grid-scenario" / "tshirt-footprint" / "scenarios" / "clean-grid.yaml",
        dyed / "tshirt-footprint" / "scenarios" / "clean-grid.yaml",
    )

    [result] = open_study_folder(dyed / "tshirt-footprint").calc(background, scenario="clean-grid")

    assert result.score == pytest.approx(0.94205 + 0.01 * (4 + 2 * 0.3298), rel=REL)


def test_a_database_holds_no_scenarios_even_a_used_one(dyed: Path) -> None:
    (dyed / "dyes" / "scenarios").mkdir()
    (dyed / "dyes" / "scenarios" / "dirty.yaml").write_text(
        "description: More CO2\nchanges:\n  - process: dyes:reactive-dye\n    line: e03\n"
        "    amount: 40\n"
    )

    with pytest.raises(InvalidDataError) as raised:
        open_database(dyed / "tshirt")

    assert [p.code for p in raised.value.problems] == ["database-scenarios"]


def test_a_scenario_of_a_study_can_change_a_line_of_a_used_database(
    background: hw.Background, dyed: Path
) -> None:
    (dyed / "tshirt-footprint" / "scenarios" / "dirty.yaml").write_text(
        "description: More CO2\nchanges:\n  - process: dyes:reactive-dye\n    line: e03\n"
        "    amount: 40\n"
    )
    study = open_study_folder(dyed / "tshirt-footprint")

    assert _codes(study.validate(background)) == []
    [result] = study.calc(background, scenario="dirty")

    assert result.score == pytest.approx(1.19205 + 0.01 * (40 + 2 * 0.5298), rel=REL)
    [dye_only] = study.calc(background)
    assert dye_only.score == pytest.approx(DYED, rel=REL)


def test_an_override_cannot_change_a_process_of_a_used_database(
    background: hw.Background, dyed: Path
) -> None:
    (dyed / "tshirt" / "overrides").mkdir()
    (dyed / "tshirt" / "overrides" / "dye.csv").write_text(
        "process,line,role,flow,amount,unit,supplier\n"
        "dyes:reactive-dye,e03,emission,toy:co2,40,kg,\n"
    )

    report = open_database(dyed / "tshirt").validate(background)

    assert _codes(report) == ["override-used-database"]
    assert "dyes:reactive-dye line e03" in str(report.errors[0])


def test_a_supplier_from_a_database_not_in_uses_is_refused(
    background: hw.Background, dyed: Path
) -> None:
    _edit(dyed / "tshirt" / "database.yaml", "uses: [dyes]", "uses: []")
    database = open_database(dyed / "tshirt")

    report = database.validate(background)

    # The dye's flow is unknown too: it is defined in dyes, which the database does not use.
    assert _codes(report) == ["unknown-flow", "supplier-not-in-uses"]
    refused = report.errors[1]
    assert refused.item == "tshirt:tshirt line e04"
    assert "add dyes to uses" in refused.message
    with pytest.raises(InvalidDataError):
        database.calc(background, "tshirt:tshirt")


def test_a_database_used_through_another_is_calculated_too(
    background: hw.Background, dyed: Path, write_study
) -> None:
    write_study(
        dyed / "chemicals",
        {"salt": "e01,reference,chemicals:salt,1,kg,\ne02,emission,toy:co2,0.2,kg,"},
        flows=["chemicals:salt,sodium chloride,product,kg,,"],
    )
    _edit(
        dyed / "dyes" / "database.yaml",
        "background: toy-background@fixture\n",
        "background: toy-background@fixture\nuses: [chemicals]\n",
    )
    add_lines(dyed / "dyes", "reactive-dye", "e04,input,chemicals:salt,0.5,kg,chemicals:salt")

    result = open_database(dyed / "tshirt").calc(background, "tshirt:tshirt")

    # the dye takes 0.5 kg of salt at 0.2: 5.0596 + 0.1 per kg of dye
    assert result.score == pytest.approx(1.19205 + 0.01 * 5.1596, rel=REL)


def test_a_line_must_list_the_database_it_buys_from_even_when_it_comes_along(
    background: hw.Background, dyed: Path, write_study
) -> None:
    write_study(
        dyed / "chemicals",
        {"salt": "e01,reference,chemicals:salt,1,kg,\ne02,emission,toy:co2,0.2,kg,"},
        flows=["chemicals:salt,sodium chloride,product,kg,,"],
    )
    _edit(
        dyed / "dyes" / "database.yaml",
        "background: toy-background@fixture\n",
        "background: toy-background@fixture\nuses: [chemicals]\n",
    )
    add_lines(dyed / "tshirt", "tshirt", "e05,input,chemicals:salt,0.001,kg,chemicals:salt")

    report = open_database(dyed / "tshirt").validate(background)

    assert _codes(report) == ["supplier-not-in-uses"]
    assert report.errors[0].item == "tshirt:tshirt line e05"


def test_a_flow_of_the_used_database_with_no_factor_needs_its_own_row(
    background: hw.Background, dyed: Path
) -> None:
    with (dyed / "dyes" / "flows.csv").open("a") as f:
        f.write('dyes:cod,"COD, Chemical Oxygen Demand",elementary,kg,water,\n')
    add_lines(dyed / "dyes", "reactive-dye", "e04,emission,dyes:cod,0.05,kg,")

    report = open_database(dyed / "tshirt").validate(background)

    assert _codes(report) == ["no-factor"]
    assert report.errors[0].location == "../dyes/no_factor.csv"

    (dyed / "dyes" / "no_factor.csv").write_text(
        "flow,method,reason,who,when\n"
        "dyes:cod,toy-climate,not characterised in toy-climate,Sam,2026-10-24\n"
    )
    result = open_database(dyed / "tshirt").calc(background, "tshirt:tshirt")

    assert result.score == pytest.approx(DYED, rel=REL)
    [cod] = [n for n in result.not_characterised if n.flow_id == "dyes:cod"]
    assert cod.in_database and cod.reason == "not characterised in toy-climate"
    assert cod.processes == ("dyes:reactive-dye",)


def test_an_error_in_a_used_database_names_its_folder(
    background: hw.Background, dyed: Path
) -> None:
    _edit(dyed / "dyes" / "exchanges.csv", "kwh_per_kg,kWh", "kwh_per_kg,kg")

    report = open_database(dyed / "tshirt").validate(background)

    assert report.errors
    assert all(p.item.startswith("dyes:reactive-dye") for p in report.errors)
    assert all((p.location or "").startswith("../dyes") for p in report.errors)


def test_a_change_to_the_used_database_moves_the_input_hash(
    background: hw.Background, dyed: Path
) -> None:
    before = open_database(dyed / "tshirt").calc(background, "tshirt:tshirt").provenance.input_hash
    _edit(dyed / "dyes" / "exchanges.csv", "toy:co2,4,", "toy:co2,4.5,")

    after = open_database(dyed / "tshirt").calc(background, "tshirt:tshirt")

    assert after.provenance.input_hash != before
    assert after.score == pytest.approx(DYED + 0.005, rel=REL)


def test_the_cut_offs_of_a_used_database_travel_with_it(
    background: hw.Background, dyed: Path
) -> None:
    with (dyed / "dyes" / "flows.csv").open("a") as f:
        f.write("dyes:fixative,fixative,product,kg,,\n")
    add_lines(dyed / "dyes", "reactive-dye", "e04,input,dyes:fixative,0.001,kg,")
    (dyed / "dyes" / "cut_offs.csv").write_text(
        "process,line,flow,criterion,share,reason,who,when\n"
        "dyes:reactive-dye,e04,dyes:fixative,mass,0.001,No fixative data,leo,2026-10-24\n"
    )
    with (dyed / "dyes" / "database.yaml").open("a") as f:
        f.write("cut_off: {criteria: [mass, energy], max_cumulative: 0.03}\n")

    result = open_database(dyed / "tshirt").calc(background, "tshirt:tshirt")

    assert result.score == pytest.approx(DYED, rel=REL)
    assert result.provenance.cut_offs == ()
    assert [(i.process_id, i.line_id) for i in result.provenance.used_cut_offs] == [
        ("dyes:reactive-dye", "e04")
    ]
