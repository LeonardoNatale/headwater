from pathlib import Path

import pytest

from headwater.model import InvalidDataError, Role
from headwater.study import build_catalog, read_database, read_process_folder


def test_file_that_is_not_a_process_csv_is_not_ignored(toy: Path) -> None:
    (toy / "tshirt/tshirt/processes/dyeing.cvs").write_text("line,role\n")

    with pytest.raises(InvalidDataError) as raised:
        read_database(toy / "tshirt/tshirt")

    (problem,) = raised.value.problems
    assert problem.code == "unknown-file"
    assert "dyeing.cvs" in problem.item


def test_hidden_files_in_processes_are_ignored(toy: Path) -> None:
    (toy / "tshirt/tshirt/processes/.DS_Store").write_text("x")

    assert [p.id for p in read_database(toy / "tshirt/tshirt").processes] == ["tshirt:tshirt"]


def test_process_ids_use_the_namespace_and_the_file_name(toy: Path) -> None:
    background = read_process_folder(toy / "background", namespace="other")

    assert sorted(p.id for p in background.processes)[0] == "other:electricity-production"


def test_namespace_defaults_to_the_folder_name(toy: Path) -> None:
    background = read_process_folder(toy / "background")

    assert background.namespace == "background"
    assert "background:fabric-production" in {p.id for p in background.processes}


def test_byte_order_mark_and_windows_line_endings_are_accepted(toy: Path) -> None:
    path = toy / "tshirt/tshirt/processes/tshirt.csv"
    path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes().replace(b"\n", b"\r\n"))

    folder = read_database(toy / "tshirt/tshirt")

    assert [e.line_id for e in folder.processes[0].exchanges] == ["e01", "e02", "e03"]


def test_catalog_over_a_study_and_its_background(toy_dir: Path) -> None:
    background = read_process_folder(toy_dir / "background", namespace="toy")
    study = read_database(toy_dir / "tshirt-waste" / "tshirt")

    catalog = build_catalog(study, background)

    process = catalog.process("tshirt:tshirt")
    assert process is not None
    # Every amount of the toy files is already in the reference unit of its flow.
    for exchange in process.exchanges:
        assert catalog.convert(exchange) == pytest.approx(exchange.amount, rel=1e-9)
    assert catalog.process("toy:fabric-production") is not None
    assert catalog.flow("toy:co2") is not None
    assert [e.role for e in process.exchanges][-1] is Role.waste_to_treatment


def test_treatment_is_derived_from_the_reference_flow_being_a_waste(toy_dir: Path) -> None:
    background = read_process_folder(toy_dir / "background", namespace="toy")
    catalog = build_catalog(background)

    treatment = {p.id: catalog.is_treatment(p) for p in background.processes}

    assert treatment == {
        "toy:electricity-production": False,
        "toy:fabric-production": False,
        "toy:incineration-textile-waste": True,
    }


def test_a_yml_file_is_not_a_process_file(toy: Path) -> None:
    (toy / "tshirt/tshirt/processes/tshirt.yml").write_text("name: x\n")

    with pytest.raises(InvalidDataError) as raised:
        read_database(toy / "tshirt/tshirt")

    (problem,) = raised.value.problems
    assert problem.code == "unknown-file"
    assert "tshirt.yml" in problem.item
    assert ".yaml" in problem.message
