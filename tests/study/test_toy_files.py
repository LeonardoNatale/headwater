"""Exit test: the toy files load into records with the expected values."""

from pathlib import Path

import pytest

from headwater.model import (
    CutOffCriterion,
    FlowKind,
    Process,
    ProcessKind,
    Role,
)
from headwater.study import read_database, read_method, read_process_folder, validate
from tests.conftest import toy_database

REL = 1e-9  # AGENTS.md: relative 1e-9 for toy fixtures


def lines(process: Process) -> list[tuple[str, Role, str, str, str | None]]:
    """Everything except the amount, which is compared with a tolerance."""
    return [(e.line_id, e.role, e.flow_id, e.unit, e.supplier_id) for e in process.exchanges]


def amounts(process: Process) -> list[float]:
    return [e.amount for e in process.exchanges]


def test_background_has_the_three_toy_processes(toy_dir: Path) -> None:
    background = read_process_folder(toy_dir / "background", namespace="toy")

    assert sorted(p.id for p in background.processes) == [
        "toy:electricity-production",
        "toy:fabric-production",
        "toy:incineration-textile-waste",
    ]


def test_background_process_details(toy_dir: Path) -> None:
    background = read_process_folder(toy_dir / "background", namespace="toy")

    # The name comes from the YAML file. The id comes from the namespace and the file name.
    assert {p.id: (p.name, p.location, p.kind) for p in background.processes} == {
        "toy:electricity-production": (
            "electricity production",
            "GLO",
            ProcessKind.ordinary_transforming_activity,
        ),
        "toy:fabric-production": (
            "cotton fabric production",
            "GLO",
            ProcessKind.ordinary_transforming_activity,
        ),
        "toy:incineration-textile-waste": (
            "incineration of textile waste",
            "GLO",
            ProcessKind.ordinary_transforming_activity,
        ),
    }
    assert all(p.time_start is None and p.time_end is None for p in background.processes)


def test_background_flows(toy_dir: Path) -> None:
    background = read_process_folder(toy_dir / "background", namespace="toy")
    flows = {f.id: f for f in background.flows}

    assert {i: (f.kind, f.unit) for i, f in flows.items()} == {
        "toy:cotton-fabric": (FlowKind.product, "kg"),
        "toy:electricity": (FlowKind.product, "kWh"),
        "toy:textile-waste": (FlowKind.waste, "kg"),
        "toy:co2": (FlowKind.elementary, "kg"),
        "toy:ch4": (FlowKind.elementary, "kg"),
    }
    assert flows["toy:co2"].name == "Carbon dioxide, fossil"
    assert flows["toy:co2"].compartment == "air"


def test_background_exchanges(toy_dir: Path) -> None:
    background = read_process_folder(toy_dir / "background", namespace="toy")
    processes = {p.id: p for p in background.processes}
    electricity = processes["toy:electricity-production"]
    fabric = processes["toy:fabric-production"]
    incineration = processes["toy:incineration-textile-waste"]

    assert lines(electricity) == [
        ("e01", Role.reference, "toy:electricity", "kWh", None),
        ("e02", Role.emission, "toy:co2", "kg", None),
        ("e03", Role.emission, "toy:ch4", "kg", None),
    ]
    assert amounts(electricity) == pytest.approx([1, 0.5, 0.001], rel=REL)
    assert lines(fabric) == [
        ("e01", Role.reference, "toy:cotton-fabric", "kg", None),
        ("e02", Role.input, "toy:electricity", "kWh", "toy:electricity-production"),
        ("e03", Role.emission, "toy:co2", "kg", None),
        ("e04", Role.emission, "toy:ch4", "kg", None),
    ]
    assert amounts(fabric) == pytest.approx([1, 3, 2.0, 0.004], rel=REL)
    # A treatment process: its reference is the waste it treats, a positive kg.
    assert lines(incineration) == [
        ("e01", Role.reference, "toy:textile-waste", "kg", None),
        ("e02", Role.emission, "toy:co2", "kg", None),
    ]
    assert amounts(incineration) == pytest.approx([1, 1.2], rel=REL)


def test_reference_exchange_of_each_background_process(toy_dir: Path) -> None:
    background = read_process_folder(toy_dir / "background", namespace="toy")

    assert {p.id: p.reference_exchange.flow_id for p in background.processes} == {
        "toy:electricity-production": "toy:electricity",
        "toy:fabric-production": "toy:cotton-fabric",
        "toy:incineration-textile-waste": "toy:textile-waste",
    }


def test_method_toy_climate(toy_dir: Path) -> None:
    method = read_method(toy_dir / "methods" / "toy-climate")

    assert (method.id, method.version, method.variant, method.flow_list) == (
        "toy-climate",
        "1",
        None,
        "toy",
    )
    assert [(i.id, i.unit) for i in method.indicators] == [("climate-change", "kg CO2-eq")]
    assert [(f.indicator, f.flow_id, f.location) for f in method.factors] == [
        ("climate-change", "toy:co2", None),
        ("climate-change", "toy:ch4", None),
    ]
    assert [f.factor for f in method.factors] == pytest.approx([1, 29.8], rel=REL)


def test_direct_emissions_of_each_process_match_the_hand_checked_numbers(toy_dir: Path) -> None:
    """h in docs/calculation.md: 0.5298, 2.1192 and 1.2. Plain arithmetic, no matrix signs."""
    background = read_process_folder(toy_dir / "background", namespace="toy")
    method = read_method(toy_dir / "methods" / "toy-climate")
    factor = {f.flow_id: f.factor for f in method.factors}

    h = {
        p.id: sum(e.amount * factor[e.flow_id] for e in p.exchanges if e.role is Role.emission)
        for p in background.processes
    }

    assert h["toy:electricity-production"] == pytest.approx(0.5298, rel=REL)
    assert h["toy:fabric-production"] == pytest.approx(2.1192, rel=REL)
    assert h["toy:incineration-textile-waste"] == pytest.approx(1.2, rel=REL)


def test_tshirt_database_record(toy_dir: Path) -> None:
    folder = read_database(toy_dir / "tshirt" / "tshirt")
    database = folder.database

    assert database.name == "tshirt"
    assert (database.background.name, database.background.version) == ("toy-background", "fixture")
    assert database.methods == ("toy-climate",)
    assert database.uses == ()
    assert database.description is None
    assert database.cut_off is None  # needed only with rows in cut_offs.csv


def test_the_cut_off_database_has_its_limit(toy_dir: Path) -> None:
    cut_off = read_database(toy_dir / "tshirt-cut-off" / "tshirt").database.cut_off

    assert cut_off is not None
    assert cut_off.criteria == (CutOffCriterion.mass, CutOffCriterion.energy)
    assert cut_off.max_cumulative == pytest.approx(0.05, rel=REL)


def test_tshirt_database_process(toy_dir: Path) -> None:
    folder = read_database(toy_dir / "tshirt" / "tshirt")

    assert [p.id for p in folder.processes] == ["tshirt:tshirt"]
    process = folder.processes[0]
    assert (process.name, process.location, process.kind) == (
        "T-shirt",
        "GLO",
        ProcessKind.ordinary_transforming_activity,
    )
    assert lines(folder.processes[0]) == [
        ("e01", Role.reference, "tshirt:tshirt", "unit", None),
        ("e02", Role.input, "toy:cotton-fabric", "kg", "toy:fabric-production"),
        ("e03", Role.input, "toy:electricity", "kWh", "toy:electricity-production"),
    ]
    assert amounts(folder.processes[0]) == pytest.approx([1, 0.25, 0.5], rel=REL)
    assert [(f.id, f.kind, f.unit) for f in folder.flows] == [
        ("tshirt:tshirt", FlowKind.product, "unit")
    ]


def test_waste_variant_is_the_same_database_plus_one_line(toy_dir: Path) -> None:
    plain = read_database(toy_dir / "tshirt" / "tshirt")
    waste = read_database(toy_dir / "tshirt-waste" / "tshirt")

    # Same goal, scope and flows: the files are identical.
    for name in ("database.yaml", "flows.csv", "processes.csv"):
        assert (toy_dir / "tshirt/tshirt" / name).read_text() == (
            toy_dir / "tshirt-waste/tshirt" / name
        ).read_text()
    assert waste.database.name == plain.database.name == "tshirt"
    assert [p.id for p in waste.processes] == [p.id for p in plain.processes] == ["tshirt:tshirt"]
    assert lines(waste.processes[0])[:3] == lines(plain.processes[0])
    assert amounts(waste.processes[0])[:3] == pytest.approx(amounts(plain.processes[0]), rel=REL)
    assert lines(waste.processes[0])[3:] == [
        (
            "e04",
            Role.waste_to_treatment,
            "toy:textile-waste",
            "kg",
            "toy:incineration-textile-waste",
        )
    ]
    assert amounts(waste.processes[0])[3:] == pytest.approx([0.05], rel=REL)


@pytest.mark.parametrize("variant", ["tshirt", "tshirt-waste"])
def test_toy_files_pass_validation_with_no_errors_and_no_warnings(
    toy_dir: Path, variant: str
) -> None:
    background = read_process_folder(toy_dir / "background", namespace="toy")
    method = read_method(toy_dir / "methods" / "toy-climate")
    database = read_database(toy_database(toy_dir, variant))

    assert validate(database, background=background, method=method).problems == ()
    assert validate(background, method=method).problems == ()
