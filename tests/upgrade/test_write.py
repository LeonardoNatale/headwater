"""Writing an upgrade in place: only the cells that name the old release change, so the git diff
of the pull request is what a reviewer needs to read, and nothing more."""

from collections.abc import Callable
from pathlib import Path

import pytest

import headwater as hw
from headwater.study import read_database
from headwater.upgrade import UpgradeError, plan
from tests.conftest import open_database

Edit = Callable[[Path, str, str], None]
REL = 1e-9

UPGRADED_TSHIRT = (
    "line,role,flow,amount,unit,supplier,flow_name,comment\n"
    "e01,reference,tshirt:tshirt,1,unit,,T-shirt,\n"
    "e02,input,toy:cotton-fabric,0.25,kg,toy:2:fabric-b,cotton fabric,"
    '"bought in RoW, ""as usual"""\n'
    "e03,input,toy:electricity,0.5,kWh,toy:2:market-elec,electricity,\n"
    "e04,input,toy:dye,0.01,kg,toy:2:dye-r,reactive dye,\n"
    'e05,emission,toy:ch4,0.001,kg,,"Methane, fossil",\n'
    "e06,input,toy:tape,0.01,kg,toy:2:tape-rer,tape,\n"
)
"""The fixture's T-shirt once upgraded with the reviewed rows: suppliers, and the flow name of
the renamed dye, change. The quoted comment of e02 keeps its text."""


def snapshot(path: Path) -> dict[Path, bytes]:
    return {p.relative_to(path): p.read_bytes() for p in path.rglob("*") if p.is_file()}


def test_only_suppliers_renamed_flows_and_the_pin_change(
    studies: Path, to: str, mapping: Path
) -> None:
    before = (studies / "tshirt" / "database.yaml").read_text()

    written = plan(studies / "tshirt", to=to, mapping=mapping).write()

    assert (studies / "tshirt" / "processes" / "tshirt.csv").read_text() == UPGRADED_TSHIRT
    pin = before.split("background: ")[1].split()[0]
    assert (studies / "tshirt" / "database.yaml").read_text() == before.replace(pin, to)
    assert sorted(p.relative_to(studies).as_posix() for p in written) == [
        "tshirt/database.yaml",
        "tshirt/processes/tshirt.csv",
    ]


def test_the_upgraded_database_validates_and_scores_on_the_new_release(
    studies: Path, to: str, mapping: Path
) -> None:
    plan(studies / "tshirt", to=to, mapping=mapping).write()

    background = hw.load(to)
    study = open_database(studies / "tshirt")
    assert not study.validate(background).errors
    # 0.25 × 2.75 + 0.5 × 0.25 + 0.01 × 3 + 0.01 × 0.8 + 0.001 × 27
    assert study.calc(background, "tshirt:tshirt").score == pytest.approx(0.8775, rel=REL)


def test_nothing_is_written_while_a_supplier_is_unmapped(studies: Path, to: str) -> None:
    before = snapshot(studies)
    found = plan(studies / "tshirt", to=to)

    with pytest.raises(UpgradeError) as caught:
        found.write()

    assert "toy:1:tape" in str(caught.value)
    assert snapshot(studies) == before


def test_writing_into_another_folder_leaves_the_database_alone(
    studies: Path, to: str, mapping: Path, tmp_path: Path
) -> None:
    before = snapshot(studies)

    written = plan(studies / "tshirt", to=to, mapping=mapping).write(into=tmp_path / "check")

    assert snapshot(studies) == before
    copy = tmp_path / "check" / "tshirt"
    assert (copy / "processes" / "tshirt.csv").read_text() == UPGRADED_TSHIRT
    assert all(p.is_relative_to(tmp_path / "check") for p in written)
    assert read_database(copy).database.background.name == "toy-2"


def test_an_override_moves_to_the_new_process_and_finds_its_line_by_role_flow_and_supplier(
    studies: Path, to: str, mapping: Path
) -> None:
    folder = studies / "tshirt"
    (folder / "overrides").mkdir()
    (folder / "overrides" / "losses.csv").write_text(
        "process,line,role,flow,amount,unit,supplier,comment\n"
        "toy:1:market-elec,e02,input,toy:electricity,loss,kWh,toy:1:elec,10% lost\n"
    )
    (folder / "parameters.yaml").write_text(
        "processes:\n  toy:1:market-elec:  # the grid market\n    loss: 1.1\n"
    )

    found = plan(folder, to=to, mapping=mapping)
    found.write()

    # toy-2's market buys electricity twice, from elec (a2) and wind (a3): the successor of
    # the overridden line's supplier decides
    assert (folder / "overrides" / "losses.csv").read_text() == (
        "process,line,role,flow,amount,unit,supplier,comment\n"
        "toy:2:market-elec,a2,input,toy:electricity,loss,kWh,toy:2:elec,10% lost\n"
    )
    assert (folder / "parameters.yaml").read_text() == (
        "processes:\n  toy:2:market-elec:  # the grid market\n    loss: 1.1\n"
    )
    by_old = {m.old.id: m for m in found.matches}
    assert by_old["toy:1:elec"].lines == ("override of toy:1:market-elec line e02",)
    assert by_old["toy:1:market-elec"].lines == (
        "tshirt:tshirt line e03",
        "override of toy:1:market-elec line e02",
        "parameters of toy:1:market-elec",
    )
    background = hw.load(to)
    assert not open_database(folder).validate(background).errors


def test_an_override_line_with_no_line_to_move_to_stops_the_upgrade(
    studies: Path, to: str, mapping: Path
) -> None:
    folder = studies / "tshirt"
    (folder / "overrides").mkdir()
    # toy-1's electricity emits N2O, toy-2's does not: line e03 has nowhere to go
    (folder / "overrides" / "grid.csv").write_text(
        "process,line,role,flow,amount,unit,supplier,comment\n"
        "toy:1:fabric-a,e03,emission,toy:co2,1.5,kg,,\n"
        "toy:1:elec,e03,emission,toy:n2o,0,kg,,no more N2O\n"
    )

    found = plan(folder, to=to, mapping=mapping)

    assert not found.complete
    by_code = {p.code: p for p in found.problems}
    assert set(by_code) == {"override-line", "flow-not-in-release"}
    line = by_code["override-line"]
    assert line.item == "override of toy:1:elec line e03"
    assert "no line of toy:2:elec is an emission of toy:n2o" in line.message
    assert found.moves.lines[("toy:1:fabric-a", "e03")] == "a3"


def test_a_study_next_to_the_database_that_names_no_release_id_is_not_written(
    studies: Path, to: str, mapping: Path
) -> None:
    """A study whose scenarios change only lines of its databases has nothing to move, so it can
    stay out of the upgrade (tests/upgrade/test_studies.py has one that cannot)."""
    study = studies / "tshirt-footprint"
    (study / "scenarios").mkdir(parents=True)
    (study / "study.yaml").write_text(
        "name: tshirt-footprint\ngoal: Test\nfunctional_unit: 1 T-shirt\n"
        "products:\n  - {process: tshirt:tshirt, amount: 1}\nboundary: cradle-to-gate\n"
        "methods: [toy-climate]\ncut_off: {criteria: [mass], max_cumulative: 0.03}\n"
    )
    text = "changes:\n  - process: tshirt:tshirt\n    line: e03\n    amount: 0.4\n"
    (study / "scenarios" / "less-power.yaml").write_text(text)
    before = snapshot(study)

    found = plan(studies / "tshirt", to=to, mapping=mapping)
    written = found.write()

    assert found.problems == ()
    assert snapshot(study) == before
    assert all(study not in p.parents for p in written)


def test_a_flow_the_new_release_lacks_stops_the_upgrade(
    studies: Path, to: str, mapping: Path, edit: Edit
) -> None:
    folder = studies / "tshirt"
    # toy-1 has dinitrogen monoxide, toy-2 does not
    edit(folder / "processes" / "tshirt.csv", "e05,emission,toy:ch4", "e05,emission,toy:n2o")

    found = plan(folder, to=to, mapping=mapping)

    assert [p.code for p in found.problems] == ["flow-not-in-release"]
    assert "toy:n2o" in found.problems[0].item


def test_a_database_and_the_databases_it_uses_move_together(
    studies: Path, to: str, write_study: Callable[..., Path], toy1_pin: str
) -> None:
    write_study(
        studies / "dyes",
        {"reactive-dye": "e01,reference,dyes:reactive-dye,1,kg,\ne02,input,toy:dye,1,kg,toy:1:dye"},
        flows=["dyes:reactive-dye,reactive dye mix,product,kg,,"],
        background=toy1_pin,
    )
    write_study(
        studies / "tshirt-dyed",
        {
            "tshirt": "e01,reference,tshirt-dyed:tshirt,1,unit,\n"
            "e02,input,dyes:reactive-dye,0.01,kg,dyes:reactive-dye\n"
            "e03,input,toy:electricity,0.5,kWh,toy:1:market-elec"
        },
        flows=["tshirt-dyed:tshirt,T-shirt,product,unit,,"],
        uses=["dyes"],
        background=toy1_pin,
    )
    rows = studies / "rows.csv"
    rows.write_text(
        "supplier_id,name,product,location,unit,supplier,reason,who,when,comment\n"
        "toy:1:dye,,,,,toy:2:dye-r,,test,2026-10-25,\n"
    )

    found = plan(studies / "tshirt-dyed", to=to, mapping=rows)
    written = found.write()

    assert sorted(p.relative_to(studies).as_posix() for p in written) == [
        "dyes/database.yaml",
        "dyes/processes/reactive-dye.csv",
        "tshirt-dyed/database.yaml",
        "tshirt-dyed/processes/tshirt.csv",
    ]
    assert open_database(studies / "tshirt-dyed").calc(hw.load(to), "tshirt-dyed:tshirt").score > 0


def test_a_database_that_uses_a_moved_database_and_stays_behind_stops_the_upgrade(
    studies: Path, to: str, write_study: Callable[..., Path], toy1_pin: str
) -> None:
    write_study(
        studies / "dyes",
        {"reactive-dye": "e01,reference,dyes:reactive-dye,1,kg,\ne02,input,toy:dye,1,kg,toy:1:dye"},
        flows=["dyes:reactive-dye,reactive dye mix,product,kg,,"],
        background=toy1_pin,
    )
    write_study(
        studies / "scarf",
        {
            "scarf": "e01,reference,scarf:scarf,1,unit,\n"
            "e02,input,dyes:reactive-dye,0.02,kg,dyes:reactive-dye"
        },
        flows=["scarf:scarf,scarf,product,unit,,"],
        uses=["dyes"],
        background=toy1_pin,
    )
    rows = studies / "rows.csv"
    rows.write_text(
        "supplier_id,name,product,location,unit,supplier,reason,who,when,comment\n"
        "toy:1:dye,,,,,toy:2:dye-r,,test,2026-10-25,\n"
    )

    alone = plan(studies / "dyes", to=to, mapping=rows)
    together = plan([studies / "dyes", studies / "scarf"], to=to, mapping=rows)

    [problem] = alone.problems
    assert problem.code == "user-left-behind"
    assert "scarf" in problem.message
    assert together.complete


@pytest.fixture
def toy1_pin(studies: Path) -> str:
    text = (studies / "tshirt" / "database.yaml").read_text()
    return text.split("background: ")[1].split()[0]


def test_a_lines_distribution_survives_the_upgrade(studies: Path, to: str, mapping: Path) -> None:
    csv = studies / "tshirt" / "processes" / "tshirt.csv"
    rows = csv.read_text().splitlines()
    csv.write_text(
        "\n".join(
            [rows[0] + ",distribution,scale,minimum,maximum"]
            + [r + (",lognormal,0.1,," if r.startswith("e02") else ",,,,") for r in rows[1:]]
        )
        + "\n"
    )

    plan(studies / "tshirt", to=to, mapping=mapping).write()

    upgraded = csv.read_text().splitlines()
    assert upgraded[0].endswith(",distribution,scale,minimum,maximum")
    assert next(r for r in upgraded if r.startswith("e02")).endswith(",lognormal,0.1,,")
    (process,) = read_database(studies / "tshirt").processes
    line = next(e for e in process.exchanges if e.line_id == "e02")
    assert line.supplier_id == "toy:2:fabric-b"
    assert line.uncertainty is not None
    assert line.uncertainty.scale == 0.1
