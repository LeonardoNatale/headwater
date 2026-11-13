"""A change to a database that another one uses is a difference of the database that uses it.

The dyed T-shirt buys 0.01 kg of reactive dye from the database `dyes`. Raising the dye's CO2 from
4 to 4.5 kg per kg moves the T-shirt by 0.01 × 0.5 = 0.005, from 1.242646 to 1.247646.
"""

import shutil
from dataclasses import replace
from pathlib import Path

import pytest

import headwater as hw
from headwater.diff import diff_databases, differences, read_at
from headwater.model import InvalidDataError

TOY = Path(__file__).resolve().parent.parent / "fixtures" / "toy"
REL = 1e-9
DYE_CSV = "tshirt-dyed/dyes/exchanges.csv"


def edit(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    assert old in text, f"{old!r} not found in {path}"
    path.write_text(text.replace(old, new, 1))


@pytest.fixture
def dyed(repo: Path, commit) -> Path:
    """The repository with `dyes` and the dyed T-shirt next to each other, committed on main."""
    shutil.copytree(TOY / "tshirt-dyed", repo / "tshirt-dyed")
    commit("the dyed T-shirt and its dyes")
    return repo


def test_a_study_read_at_a_ref_brings_the_studies_it_uses_at_that_ref(dyed: Path, commit) -> None:
    edit(dyed / DYE_CSV, "toy:co2,4,", "toy:co2,4.5,")
    commit("more CO2 in the dye")

    def co2(ref: str | None) -> float:
        [dyes] = read_at(dyed / "tshirt-dyed" / "tshirt", ref).database.used
        [dye] = dyes.processes
        return {e.line_id: e.amount for e in dye.exchanges}["e03"]

    assert co2("HEAD~1") == pytest.approx(4.0, rel=REL)
    assert co2("HEAD") == pytest.approx(4.5, rel=REL)
    edit(dyed / DYE_CSV, "toy:co2,4.5,", "toy:co2,5,")
    assert co2(None) == pytest.approx(5.0, rel=REL)


def test_a_used_study_missing_at_a_ref_is_refused(dyed: Path, commit) -> None:
    shutil.rmtree(dyed / "tshirt-dyed" / "dyes")
    commit("no more dyes")

    with pytest.raises(InvalidDataError, match="dyes"):
        read_at(dyed / "tshirt-dyed" / "tshirt", "HEAD")


def test_a_changed_line_of_the_used_study_is_named_by_its_full_id(dyed: Path) -> None:
    old = read_at(dyed / "tshirt-dyed" / "tshirt", "main").database
    edit(dyed / DYE_CSV, "toy:co2,4,", "toy:co2,4.5,")
    new = read_at(dyed / "tshirt-dyed" / "tshirt").database

    [found] = differences(old, new)

    assert found.id == "line dyes:reactive-dye line e03"
    assert found.item == "dyes:reactive-dye line e03"
    assert found.database == "dyes"
    assert found.description == "amount 4 → 4.5 kg"


def test_the_diff_of_the_user_ranks_the_line_of_the_used_study(
    dyed: Path, background: hw.Background
) -> None:
    old = read_at(dyed / "tshirt-dyed" / "tshirt", "main").database
    edit(dyed / DYE_CSV, "toy:co2,4,", "toy:co2,4.5,")
    new = read_at(dyed / "tshirt-dyed" / "tshirt").database

    [row] = diff_databases(old, new, background).rows

    assert row.old == pytest.approx(1.242646, rel=REL)
    assert row.new == pytest.approx(1.247646, rel=REL)
    [driver] = row.drivers
    assert driver.difference.item == "dyes:reactive-dye line e03"
    assert driver.contribution == pytest.approx(0.005, rel=REL)
    assert row.interaction == pytest.approx(0.0, abs=1e-12)


def test_a_parameter_of_the_used_study_is_named_with_its_study(
    dyed: Path, background: hw.Background
) -> None:
    old = read_at(dyed / "tshirt-dyed" / "tshirt", "main").database
    edit(dyed / "tshirt-dyed" / "dyes" / "parameters.yaml", "value: 2", "value: 3")
    new = read_at(dyed / "tshirt-dyed" / "tshirt").database

    [row] = diff_databases(old, new, background).rows

    [driver] = row.drivers
    assert driver.difference.item == "kwh_per_kg in dyes"
    assert driver.difference.affects == ("dyes:reactive-dye line e02",)
    # 0.01 kg of dye × 1 more kWh per kg × 0.5298
    assert driver.contribution == pytest.approx(0.005298, rel=REL)


def test_a_parameter_of_the_same_name_in_both_studies_gives_two_differences(
    dyed: Path, background: hw.Background
) -> None:
    (dyed / "tshirt-dyed" / "tshirt" / "parameters.yaml").write_text("database:\n  kwh_per_kg: 1\n")
    old = read_at(dyed / "tshirt-dyed" / "tshirt").database
    edit(dyed / "tshirt-dyed" / "dyes" / "parameters.yaml", "value: 2", "value: 3")
    (dyed / "tshirt-dyed" / "tshirt" / "parameters.yaml").write_text("database:\n  kwh_per_kg: 5\n")
    new = read_at(dyed / "tshirt-dyed" / "tshirt").database

    found = differences(old, new)

    assert sorted(d.id for d in found) == ["parameter kwh_per_kg", "parameter kwh_per_kg in dyes"]
    [row] = diff_databases(old, new, background, differences=found).rows
    effects = {d.difference.id: d.contribution for d in row.drivers}
    assert effects["parameter kwh_per_kg in dyes"] == pytest.approx(0.005298, rel=REL)
    assert effects["parameter kwh_per_kg"] == pytest.approx(0.0, abs=1e-12)


def test_a_line_that_starts_buying_from_a_used_study_brings_it_along(
    dyed: Path, background: hw.Background
) -> None:
    old = read_at(dyed / "tshirt" / "tshirt", "main").database
    new = read_at(dyed / "tshirt-dyed" / "tshirt").database
    new = replace(new, path=old.path)

    [row] = diff_databases(old, new, background).rows

    by_id = {d.difference.id: d.contribution for d in row.drivers}
    assert by_id["line tshirt:tshirt line e04"] == pytest.approx(0.050596, rel=REL)
    assert row.interaction == pytest.approx(0.0, abs=1e-12)


def test_anything_but_a_line_of_a_used_study_is_named_with_its_study(dyed: Path) -> None:
    (dyed / "tshirt-dyed" / "dyes" / "parameters.yaml").write_text(
        "database:\n  kwh_per_kg: 2\n  a: 1\n"
    )
    old = read_at(dyed / "tshirt-dyed" / "tshirt").database
    (dyed / "tshirt-dyed" / "dyes" / "parameters.yaml").write_text(
        "database:\n  kwh_per_kg: 2\n  a: 2\n"
    )
    edit(
        dyed / "tshirt-dyed" / "dyes" / "flows.csv",
        "reactive dye,product",
        "reactive dye 2,product",
    )
    new = read_at(dyed / "tshirt-dyed" / "tshirt").database

    found = {d.id: d.item for d in differences(old, new)}

    # the id is `<kind> <item>`: a parameter named `a` is not replaced inside `parameter`
    assert found == {
        "parameter a in dyes": "a in dyes",
        "flow dyes:reactive-dye in dyes": "dyes:reactive-dye in dyes",
    }
