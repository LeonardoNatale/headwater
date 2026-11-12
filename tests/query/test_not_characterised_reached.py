"""A result lists only the uncovered flows its study reaches, computed on demand. The
background keeps the full list (tests/query/test_not_characterised.py has the basics)."""

import shutil
from pathlib import Path

import pytest

import headwater as hw
from headwater.calc.solve import Factorization
from headwater.model import Flow, FlowKind, InvalidDataError
from tests.conftest import open_database

TOY = Path(__file__).resolve().parent.parent / "fixtures" / "toy"
REL = 1e-9
N2O = Flow(
    id="toy:n2o",
    name="Dinitrogen monoxide",
    kind=FlowKind.elementary,
    unit="kg",
    compartment="air",
    cas="10024-97-2",
)


def n2o_on_incineration_only(records):
    """The method has no factor for N2O, and only the incineration process emits it."""
    return records.with_flows(N2O).with_new_line(
        "toy:incineration-textile-waste",
        "e03",
        role="emission",
        flow_id=N2O.id,
        amount=0.001,
        unit="kg",
    )


def n2o_on_incineration_and_fabric(records):
    return n2o_on_incineration_only(records).with_new_line(
        "toy:fabric-production", "e05", role="emission", flow_id=N2O.id, amount=0.002, unit="kg"
    )


def as_tuples(found: tuple[hw.NotCharacterised, ...]) -> list[tuple[str, str, tuple[str, ...]]]:
    return [(f.flow_id, f.name, f.processes) for f in found]


def test_a_result_lists_only_the_flows_the_study_reaches(make_release, records, calc) -> None:
    background = hw.load(make_release(n2o_on_incineration_only(records)).path, method="toy-climate")
    uncovered = [("toy:n2o", "Dinitrogen monoxide", ("toy:incineration-textile-waste",))]

    plain = calc("tshirt", background)
    waste = calc("tshirt-waste", background)

    # The plain T-shirt sends nothing to incineration, so it never reaches the flow.
    assert plain.not_characterised == ()
    assert "not characterised" not in repr(plain)
    assert as_tuples(waste.not_characterised) == uncovered
    assert repr(waste).endswith("1 flow not characterised)")
    # It counts 0 in both scores.
    assert plain.score == pytest.approx(1.19205, rel=REL)
    assert waste.score == pytest.approx(1.25205, rel=REL)
    # The background keeps the full list, whatever the study.
    assert as_tuples(background.not_characterised) == uncovered


def test_the_processes_of_an_entry_are_the_ones_the_study_reaches(
    make_release, records, calc
) -> None:
    background = hw.load(
        make_release(n2o_on_incineration_and_fabric(records)).path, method="toy-climate"
    )

    plain = calc("tshirt", background)
    waste = calc("tshirt-waste", background)

    # The T-shirt reaches fabric production, not incineration. The waste study reaches both.
    assert as_tuples(plain.not_characterised) == [
        ("toy:n2o", "Dinitrogen monoxide", ("toy:fabric-production",))
    ]
    assert as_tuples(waste.not_characterised) == [
        (
            "toy:n2o",
            "Dinitrogen monoxide",
            ("toy:fabric-production", "toy:incineration-textile-waste"),
        )
    ]
    assert as_tuples(background.not_characterised) == as_tuples(waste.not_characterised)


def test_the_list_is_computed_on_demand_from_the_supply_already_solved(
    make_release, records, monkeypatch: pytest.MonkeyPatch, calc
) -> None:
    background = hw.load(make_release(n2o_on_incineration_only(records)).path, method="toy-climate")
    result = calc("tshirt-waste", background)

    def no_more_solves(*args: object, **kwargs: object) -> None:
        raise AssertionError("the list needed a new solve")

    monkeypatch.setattr(Factorization, "solve", no_more_solves)

    assert as_tuples(result.not_characterised) == [
        ("toy:n2o", "Dinitrogen monoxide", ("toy:incineration-textile-waste",))
    ]
    assert result.not_characterised is result.not_characterised  # computed once


def test_the_toy_folder_behaves_the_same_way(toy: Path) -> None:
    with (toy / "background" / "flows.csv").open("a") as f:
        f.write("toy:n2o,Dinitrogen monoxide,elementary,kg,air,10024-97-2\n")
    with (toy / "background" / "processes" / "incineration-textile-waste.csv").open("a") as f:
        f.write("e03,emission,toy:n2o,0.001,kg,\n")
    background = hw.load(
        toy / "background", method=toy / "methods" / "toy-climate", namespace="toy"
    )

    plain = open_database(toy / "tshirt" / "tshirt").calc(background, "tshirt:tshirt")
    waste = open_database(toy / "tshirt-waste" / "tshirt").calc(background, "tshirt:tshirt")

    assert plain.not_characterised == ()
    assert as_tuples(waste.not_characterised) == [
        ("toy:n2o", "Dinitrogen monoxide", ("toy:incineration-textile-waste",))
    ]
    assert as_tuples(background.not_characterised) == as_tuples(waste.not_characterised)


def test_a_flow_the_study_emits_itself_with_no_factor_is_still_an_error_on_a_release(
    make_release, records, tmp_path: Path, calc
) -> None:
    background = hw.load(make_release(records).path, method="toy-climate")
    root = tmp_path / "toy"
    shutil.copytree(TOY, root)
    study_dir = root / "tshirt" / "tshirt"
    with (study_dir / "flows.csv").open("a") as f:
        f.write("tshirt:n2o,Dinitrogen monoxide,elementary,kg,air,10024-97-2\n")
    with (study_dir / "processes" / "tshirt.csv").open("a") as f:
        f.write("e04,emission,tshirt:n2o,0.001,kg,\n")

    with pytest.raises(InvalidDataError, match="flow tshirt:n2o") as info:
        calc("tshirt", background, root=root)

    assert [(p.code, p.item) for p in info.value.problems] == [("no-factor", "flow tshirt:n2o")]
    assert "tshirt:tshirt" in str(info.value)
