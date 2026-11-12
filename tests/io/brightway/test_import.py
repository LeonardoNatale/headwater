"""Import a database of the toy Brightway project into a database, and score it."""

import datetime
import json
import math
import os
import pickle
from pathlib import Path

import pytest

import headwater as hw
from headwater.io.brightway import import_brightway
from headwater.io.excel import Status
from headwater.io.excel.report import format_report
from headwater.model import Distribution, Exchange, InvalidDataError, Role, Uncertainty
from headwater.release import Release
from headwater.study import read_database
from tests.conftest import only_process, open_database

from .conftest import Project

REL = 1e-9
TSHIRT = "tshirt:t-shirt-glo"


def score(folder: Path, release: Release) -> float:
    database = open_database(folder)
    return database.calc(hw.load(release.path), only_process(database)).score


def files(folder: Path) -> dict[str, bytes]:
    return {
        p.relative_to(folder).as_posix(): p.read_bytes() for p in folder.rglob("*") if p.is_file()
    }


def lines(folder: Path, process_id: str) -> dict[str, Exchange]:
    (process,) = [p for p in read_database(folder).processes if p.id == process_id]
    return {e.supplier_id or e.flow_id: e for e in process.exchanges}


def test_the_tshirt_scores_1_25205_with_its_waste_line(
    project: Project, studies: Path, toy_release: Release
) -> None:
    done = import_brightway(
        "toy", "tshirt", toy_release, studies / "tshirt", brightway_dir=project.root
    )
    assert done.clean, format_report(done.report)
    assert score(done.folder, toy_release) == pytest.approx(1.25205, rel=REL)
    assert (
        (done.folder / "database.yaml")
        .read_text()
        .startswith("# Imported by headwater from Brightway project toy, database tshirt.\n")
    )


def test_the_tshirt_scores_1_19205_without_its_waste_line(
    project: Project, studies: Path, toy_release: Release
) -> None:
    project.delete_exchange(16)
    done = import_brightway(
        "toy", "tshirt", toy_release, studies / "tshirt", brightway_dir=project.root
    )
    assert done.clean, format_report(done.report)
    assert score(done.folder, toy_release) == pytest.approx(1.19205, rel=REL)


def test_a_line_to_another_database_of_the_project_uses_its_study(
    project: Project, studies: Path, toy_release: Release
) -> None:
    dyes = import_brightway(
        "toy", "dyes", toy_release, studies / "dyes", brightway_dir=project.root
    )
    assert dyes.clean, format_report(dyes.report)
    assert score(dyes.folder, toy_release) == pytest.approx(5.0596, rel=REL)
    dyed = import_brightway(
        "toy", "tshirt-dyed", toy_release, studies / "tshirt-dyed", brightway_dir=project.root
    )
    assert dyed.clean, format_report(dyed.report)
    assert dyed.report.uses == ("dyes",)
    assert read_database(dyed.folder).database.uses == ("dyes",)
    assert score(dyed.folder, toy_release) == pytest.approx(1.242646, rel=REL)


def test_a_line_to_a_database_with_no_study_folder_waits_for_it(
    project: Project, studies: Path, toy_release: Release
) -> None:
    dyed = import_brightway(
        "toy", "tshirt-dyed", toy_release, studies / "tshirt-dyed", brightway_dir=project.root
    )
    assert not dyed.clean
    (waiting,) = [o for o in dyed.report.outcomes if o.status is Status.waiting]
    assert waiting.database == "dyes"
    assert "reactive dye production" in waiting.line


def test_parameters_formulas_and_distributions_survive(
    project: Project, studies: Path, toy_release: Release
) -> None:
    done = import_brightway(
        "toy", "tshirt", toy_release, studies / "tshirt", brightway_dir=project.root
    )
    study = read_database(done.folder)
    parameters = {p.id: p for p in study.parameters}
    assert sorted(parameters) == ["fabric_kg", "scrap_share", f"{TSHIRT}/waste_kg"]
    assert parameters["scrap_share"].value == 0.2
    assert parameters["scrap_share"].comment == "Share of the fabric cut away"
    assert parameters["scrap_share"].uncertainty == Uncertainty(
        distribution=Distribution.normal, loc=0.2, scale=0.02
    )
    assert parameters["fabric_kg"].value == 0.25
    assert parameters["fabric_kg"].unit == "kg"
    assert parameters[f"{TSHIRT}/waste_kg"].formula == "fabric_kg * scrap_share"

    found = lines(done.folder, TSHIRT)
    fabric = found["toy:fabric-production"]
    assert (fabric.formula, fabric.amount) == ("fabric_kg", 0.25)
    assert fabric.comment == "Fabric in the bill of materials"
    waste = found["toy:incineration-textile-waste"]
    assert waste.role is Role.waste_to_treatment
    assert waste.formula == "-(-waste_kg)"
    assert waste.amount == pytest.approx(0.05, rel=REL)
    electricity = found["toy:electricity-production"]
    assert electricity.formula is None
    assert electricity.uncertainty is not None
    assert electricity.uncertainty.distribution is Distribution.lognormal
    assert electricity.uncertainty.scale == pytest.approx(math.log(1.1), rel=REL)
    assert electricity.uncertainty.loc == pytest.approx(math.log(0.5), rel=REL)


def test_a_database_parameter_in_a_formula_survives(
    project: Project, studies: Path, toy_release: Release
) -> None:
    """Project parameters go to every study of the project, used or not."""
    done = import_brightway(
        "toy", "dyes", toy_release, studies / "dyes", brightway_dir=project.root
    )
    assert sorted((p.id, p.value) for p in read_database(done.folder).parameters) == [
        ("kwh_per_kg", 2.0),
        ("scrap_share", 0.2),
    ]
    electricity = lines(done.folder, "dyes:reactive-dye-production-glo")[
        "toy:electricity-production"
    ]
    assert (electricity.formula, electricity.amount) == ("kwh_per_kg", 2.0)


def test_a_waste_line_turns_its_distribution_with_its_sign(
    project: Project, studies: Path, toy_release: Release
) -> None:
    project.sql("DELETE FROM parameterizedexchange WHERE exchange = 16", file="parameters.db")
    data = project.exchange(16)
    del data["formula"]
    data.update({"uncertainty type": 5, "minimum": -0.07, "loc": -0.05, "maximum": -0.04})
    project.set_exchange(16, data)
    done = import_brightway(
        "toy", "tshirt", toy_release, studies / "tshirt", brightway_dir=project.root
    )
    waste = lines(done.folder, TSHIRT)["toy:incineration-textile-waste"]
    assert waste.amount == pytest.approx(0.05, rel=REL)
    assert waste.uncertainty == Uncertainty(
        distribution=Distribution.triangular, loc=0.05, minimum=0.04, maximum=0.07
    )


def test_a_distribution_that_a_study_cannot_hold_is_reported(
    project: Project, studies: Path, toy_release: Release
) -> None:
    fabric = project.exchange(14)
    fabric.update({"uncertainty type": 2, "loc": math.log(0.25), "scale": 0.1})
    project.set_exchange(14, fabric)
    electricity = project.exchange(15)
    electricity.update({"uncertainty type": 7, "minimum": 0, "maximum": 1})
    project.set_exchange(15, electricity)
    done = import_brightway(
        "toy", "tshirt", toy_release, studies / "tshirt", brightway_dir=project.root
    )
    notes = [f"{n.source}: {n.message}" for n in done.report.notes]
    assert any("exchange 14" in n and "formula" in n for n in notes), notes
    assert any("exchange 15" in n and "type 7" in n for n in notes), notes
    found = lines(done.folder, TSHIRT)
    assert found["toy:fabric-production"].uncertainty is None
    assert found["toy:electricity-production"].uncertainty is None


def test_importing_twice_gives_the_same_files_and_ids(tmp_path: Path, toy_release: Release) -> None:
    one = Project(tmp_path / "one" / "brightway")
    two = Project(tmp_path / "two" / "somewhere else" / "brightway")
    first = import_brightway(
        "toy", "tshirt", toy_release, tmp_path / "one" / "tshirt", brightway_dir=one.root
    )
    second = import_brightway(
        "toy", "tshirt", toy_release, tmp_path / "two" / "tshirt", brightway_dir=two.root
    )
    assert files(first.folder) == files(second.folder)
    assert [p.id for p in first.conversion.processes] == [TSHIRT]
    again = import_brightway(
        "toy", "tshirt", toy_release, tmp_path / "one" / "tshirt", brightway_dir=one.root
    )
    assert again.replaced
    assert files(again.folder) == files(second.folder)


def test_a_key_in_no_database_is_an_error_naming_the_exchange(
    project: Project, studies: Path, toy_release: Release
) -> None:
    project.sql(
        "DELETE FROM activitydataset WHERE database = 'toy-background' AND code = 'cotton-fabric'"
    )
    with pytest.raises(InvalidDataError) as error:
        import_brightway(
            "toy", "tshirt", toy_release, studies / "tshirt", brightway_dir=project.root
        )
    message = str(error.value)
    assert "exchange 14" in message
    assert "('toy-background', 'cotton-fabric')" in message
    assert not (studies / "tshirt").exists()


def test_a_key_in_a_database_the_project_does_not_have_is_an_error(
    project: Project, studies: Path, toy_release: Release
) -> None:
    project.sql("UPDATE exchangedataset SET input_database = 'nowhere' WHERE id = 15")
    with pytest.raises(InvalidDataError) as error:
        import_brightway(
            "toy", "tshirt", toy_release, studies / "tshirt", brightway_dir=project.root
        )
    message = str(error.value)
    assert "exchange 15" in message and "nowhere" in message
    assert not (studies / "tshirt").exists()


def test_a_pickled_object_that_is_not_plain_data_is_refused(
    project: Project, studies: Path, toy_release: Release
) -> None:
    data = project.exchange(15)
    data["comment"] = datetime.date(2026, 1, 1)
    project.set_exchange(15, data)
    with pytest.raises(InvalidDataError) as error:
        import_brightway(
            "toy", "tshirt", toy_release, studies / "tshirt", brightway_dir=project.root
        )
    message = str(error.value)
    assert "datetime.date" in message
    assert "tshirt/tshirt" in message and "exchange 15" in message


def test_the_project_is_only_read(project: Project, studies: Path, toy_release: Release) -> None:
    before = files(project.root)
    paths = sorted(project.root.rglob("*"), reverse=True)
    for path in paths:
        path.chmod(0o555 if path.is_dir() else 0o444)
    project.root.chmod(0o555)
    try:
        done = import_brightway(
            "toy", "tshirt", toy_release, studies / "tshirt", brightway_dir=project.root
        )
    finally:
        project.root.chmod(0o755)
        for path in paths:
            path.chmod(0o755 if path.is_dir() else 0o644)
    assert done.clean
    assert files(project.root) == before


def test_brightway2_dir_is_where_the_projects_are_by_default(
    project: Project, studies: Path, toy_release: Release, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BRIGHTWAY2_DIR", str(project.root))
    done = import_brightway("toy", "tshirt", toy_release, studies / "tshirt")
    assert done.clean


def test_a_project_that_is_not_there_is_an_error_naming_the_ones_that_are(
    project: Project, studies: Path, toy_release: Release
) -> None:
    with pytest.raises(InvalidDataError) as error:
        import_brightway(
            "tyo", "tshirt", toy_release, studies / "tshirt", brightway_dir=project.root
        )
    assert "default, toy" in str(error.value)


def test_a_database_that_is_not_there_is_an_error_naming_the_ones_that_are(
    project: Project, studies: Path, toy_release: Release
) -> None:
    with pytest.raises(InvalidDataError) as error:
        import_brightway(
            "toy", "shirt", toy_release, studies / "tshirt", brightway_dir=project.root
        )
    assert "dyes, toy-background, toy-biosphere, tshirt, tshirt-dyed" in str(error.value)


def test_a_database_stored_another_way_is_refused(
    project: Project, studies: Path, toy_release: Release
) -> None:
    path = project.folder / "databases.json"
    databases = json.loads(path.read_text())
    databases["tshirt"]["backend"] = "iotable"
    path.write_text(json.dumps(databases))
    with pytest.raises(InvalidDataError) as error:
        import_brightway(
            "toy", "tshirt", toy_release, studies / "tshirt", brightway_dir=project.root
        )
    assert "iotable" in str(error.value)


def test_tables_of_another_layout_are_refused(
    project: Project, studies: Path, toy_release: Release
) -> None:
    project.sql("ALTER TABLE exchangedataset RENAME COLUMN type TO kind")
    with pytest.raises(InvalidDataError) as error:
        import_brightway(
            "toy", "tshirt", toy_release, studies / "tshirt", brightway_dir=project.root
        )
    message = str(error.value)
    assert "exchangedataset" in message and "type" in message


def _add_activity(project: Project, code: str, name: str, first_exchange: int) -> None:
    data = {
        "name": name,
        "reference product": name,
        "unit": "unit",
        "location": "GLO",
        "type": "processwithreferenceproduct",
        "database": "tshirt",
        "code": code,
    }
    project.sql(
        "INSERT INTO activitydataset (data, code, database, location, name, product, type) "
        "VALUES (?, ?, 'tshirt', 'GLO', ?, ?, 'processwithreferenceproduct')",
        pickle.dumps(data),
        code,
        name,
        name,
    )
    for n, (key, amount, kind) in enumerate(
        [
            (("tshirt", code), 1.0, "production"),
            (("toy-background", "electricity"), 0.1, "technosphere"),
        ]
    ):
        exchange = {"input": key, "amount": amount, "type": kind, "output": ("tshirt", code)}
        project.sql(
            "INSERT INTO exchangedataset "
            "(id, data, input_code, input_database, output_code, output_database, type) "
            "VALUES (?, ?, ?, ?, ?, 'tshirt', ?)",
            first_exchange + n,
            pickle.dumps(exchange),
            key[1],
            key[0],
            code,
            kind,
        )


def test_several_top_level_activities_are_all_processes_of_the_database(
    project: Project, studies: Path, toy_release: Release
) -> None:
    _add_activity(project, "tote", "tote bag", 100)
    done = import_brightway(
        "toy", "tshirt", toy_release, studies / "tshirt", brightway_dir=project.root
    )
    assert done.clean, format_report(done.report)
    assert sorted(p.id for p in read_database(done.folder).processes) == [
        "tshirt:t-shirt-glo",
        "tshirt:tote-bag-glo",
    ]
    assert "reference_flow" not in (done.folder / "database.yaml").read_text()


CUT_A = "twins:cut-fabric-glo"
CUT_B = "twins:cut-fabric-glo-2"
TSHIRT_A = "twins:t-shirt-a-glo"
TSHIRT_B = "twins:t-shirt-b-glo"


def test_twins_link_each_line_to_the_activity_of_its_key(
    project: Project, studies: Path, toy_release: Release
) -> None:
    """`cut-a` and `cut-b` are both `fabric cutting`, cut fabric, GLO. T-shirt A buys 0.25 kg from
    `cut-a`, 1 kg of fabric per kg: 0.25 × 3.7086 + 0.5 × 0.5298 = 1.19205. T-shirt B buys it
    from `cut-b`, 1.2 kg per kg: 0.3 × 3.7086 + 0.2649 = 1.37748."""
    done = import_brightway(
        "toy", "twins", toy_release, studies / "twins", brightway_dir=project.root
    )
    assert done.clean, format_report(done.report)
    assert [p.id for p in done.conversion.processes] == [CUT_A, CUT_B, TSHIRT_A, TSHIRT_B]
    assert lines(done.folder, CUT_A)["toy:fabric-production"].amount == 1.0
    assert lines(done.folder, CUT_B)["toy:fabric-production"].amount == 1.2
    a, b = lines(done.folder, TSHIRT_A), lines(done.folder, TSHIRT_B)
    assert a[CUT_A].amount == b[CUT_B].amount == 0.25
    assert CUT_B not in a and CUT_A not in b
    study = open_database(done.folder)
    background = hw.load(toy_release.path)
    assert study.calc(background, TSHIRT_A).score == pytest.approx(1.19205, rel=REL)
    assert study.calc(background, TSHIRT_B).score == pytest.approx(1.37748, rel=REL)


def test_twins_keep_their_ids_whatever_order_brightway_wrote_them_in(
    tmp_path: Path, toy_release: Release
) -> None:
    """Activities are read in the order of their codes, so `cut-a` is numbered before `cut-b`
    even when its row comes last."""
    first = Project(tmp_path / "first" / "brightway")
    moved = Project(tmp_path / "moved" / "brightway")
    moved.sql(
        "UPDATE activitydataset SET id = (SELECT max(id) + 1 FROM activitydataset) "
        "WHERE database = 'twins' AND code = 'cut-a'"
    )
    one = import_brightway(
        "toy", "twins", toy_release, tmp_path / "first" / "twins", brightway_dir=first.root
    )
    two = import_brightway(
        "toy", "twins", toy_release, tmp_path / "moved" / "twins", brightway_dir=moved.root
    )
    again = import_brightway(
        "toy", "twins", toy_release, tmp_path / "first" / "twins", brightway_dir=first.root
    )
    assert [p.id for p in two.conversion.processes] == [CUT_A, CUT_B, TSHIRT_A, TSHIRT_B]
    assert files(one.folder) == files(two.folder) == files(again.folder)


def test_a_line_whose_twin_was_not_converted_is_not_linked_to_the_other(
    project: Project, studies: Path, toy_release: Release
) -> None:
    production = project.exchange(21)  # cut-a makes 1 kg
    production["amount"] = 0.0
    project.set_exchange(21, production)
    done = import_brightway(
        "toy", "twins", toy_release, studies / "twins", brightway_dir=project.root
    )
    # cut-b is now the only `fabric cutting`, so it takes the id of its name (D40)
    alone = "twins:fabric-cutting-glo"
    assert [p.id for p in done.conversion.processes] == [alone, TSHIRT_A, TSHIRT_B]
    (outcome,) = [o for o in done.report.outcomes if o.source == "exchange 26 of twins/tshirt-a"]
    assert outcome.status is Status.unmapped
    assert outcome.code == "activity-not-converted"
    assert alone not in lines(done.folder, TSHIRT_A)
    assert lines(done.folder, TSHIRT_B)[alone].amount == 0.25


def test_the_project_folder_is_named_as_bw2data_names_it(project: Project) -> None:
    """The names bw_processing 1.0's `safe_filename` gives."""
    from headwater.io.brightway.project import folder_name

    assert folder_name("toy", full_hash=False) == "toy.10016b6e"
    assert folder_name("toy", full_hash=True) == "toy.10016b6ed5a5b09be08133fa2d282636"
    assert folder_name("default", full_hash=False) == "default.c21f969b"
    assert folder_name("Mon projet été", full_hash=False) == "Mon-projet-ete.99ecb7d2"
    assert folder_name("a/b: c", full_hash=False) == "ab-c.b13743fd"
    assert os.path.isdir(project.root / folder_name("toy", full_hash=False))
