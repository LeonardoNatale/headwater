"""A database uses the databases that `uses:` names, folders next to it: reading resolves them."""

from pathlib import Path

import pytest

from headwater.model import InvalidDataError
from headwater.study import read_database
from tests.conftest import toy_database

TOY = Path(__file__).resolve().parent.parent / "fixtures" / "toy"

DYE = "e01,reference,dyes:reactive-dye,1,kg,\ne02,emission,toy:co2,4,kg,"
DYE_FLOW = "dyes:reactive-dye,reactive dye,product,kg,,"
SALT = "e01,reference,chemicals:salt,1,kg,\ne02,emission,toy:co2,0.2,kg,"
SALT_FLOW = "chemicals:salt,sodium chloride,product,kg,,"
SHIRT = "e01,reference,tshirt:tshirt,1,unit,\ne02,input,dyes:reactive-dye,0.01,kg,dyes:reactive-dye"
SHIRT_FLOW = "tshirt:tshirt,T-shirt,product,unit,,"


def _codes(error: pytest.ExceptionInfo[InvalidDataError]) -> list[str]:
    return [p.code for p in error.value.problems]


def test_the_dyed_tshirt_reads_dyes_from_the_folder_next_to_it() -> None:
    folder = read_database(toy_database(TOY, "tshirt-dyed"))

    assert folder.database.uses == ("dyes",)
    assert [u.database.name for u in folder.used] == ["dyes"]
    [dye] = folder.used[0].processes
    assert dye.id == "dyes:reactive-dye"
    # dyes works out its own formulas, with its own parameters
    assert {e.line_id: e.amount for e in dye.exchanges}["e02"] == pytest.approx(2.0, rel=1e-9)


def test_a_database_that_uses_nothing_has_no_used_databases() -> None:
    assert read_database(toy_database(TOY, "tshirt")).used == ()


def test_a_used_database_whose_name_is_not_its_folder_name_is_refused_naming_both(
    tmp_path: Path, write_study
) -> None:
    write_study(tmp_path / "dyes", {"reactive-dye": DYE}, name="colours", flows=[DYE_FLOW])
    shirt = write_study(tmp_path / "tshirt", {"tshirt": SHIRT}, uses=["dyes"], flows=[SHIRT_FLOW])

    with pytest.raises(InvalidDataError) as error:
        read_database(shirt)

    assert _codes(error) == ["used-database-name"]
    message = str(error.value)
    assert "colours" in message and "dyes" in message


def test_a_used_database_that_is_not_there_is_refused(tmp_path: Path, write_study) -> None:
    shirt = write_study(tmp_path / "tshirt", {"tshirt": SHIRT}, uses=["dyes"], flows=[SHIRT_FLOW])

    with pytest.raises(InvalidDataError) as error:
        read_database(shirt)

    assert _codes(error) == ["used-database-missing"]
    assert "dyes" in str(error.value)


def test_a_cycle_between_two_databases_is_refused_naming_both(tmp_path: Path, write_study) -> None:
    write_study(tmp_path / "dyes", {"reactive-dye": DYE}, uses=["chemicals"], flows=[DYE_FLOW])
    write_study(tmp_path / "chemicals", {"salt": SALT}, uses=["dyes"], flows=[SALT_FLOW])

    with pytest.raises(InvalidDataError) as error:
        read_database(tmp_path / "dyes")

    assert _codes(error) == ["uses-cycle"]
    assert "dyes → chemicals → dyes" in str(error.value)


def test_a_cycle_further_down_the_chain_is_refused(tmp_path: Path, write_study) -> None:
    write_study(tmp_path / "dyes", {"reactive-dye": DYE}, uses=["chemicals"], flows=[DYE_FLOW])
    write_study(tmp_path / "chemicals", {"salt": SALT}, uses=["dyes"], flows=[SALT_FLOW])
    shirt = write_study(tmp_path / "tshirt", {"tshirt": SHIRT}, uses=["dyes"], flows=[SHIRT_FLOW])

    with pytest.raises(InvalidDataError) as error:
        read_database(shirt)

    assert _codes(error) == ["uses-cycle"]
    assert "dyes → chemicals → dyes" in str(error.value)


def test_a_database_that_uses_itself_is_refused(tmp_path: Path, write_study) -> None:
    dyes = write_study(tmp_path / "dyes", {"reactive-dye": DYE}, uses=["dyes"], flows=[DYE_FLOW])

    with pytest.raises(InvalidDataError) as error:
        read_database(dyes)

    assert _codes(error) == ["uses-cycle"]
    assert "dyes → dyes" in str(error.value)


def test_a_used_database_pinned_to_another_background_is_refused_naming_both_pins(
    tmp_path: Path, write_study
) -> None:
    write_study(
        tmp_path / "dyes",
        {"reactive-dye": DYE},
        flows=[DYE_FLOW],
        background="toy-background@0123456789ab",
    )
    shirt = write_study(tmp_path / "tshirt", {"tshirt": SHIRT}, uses=["dyes"], flows=[SHIRT_FLOW])

    with pytest.raises(InvalidDataError) as error:
        read_database(shirt)

    assert _codes(error) == ["uses-background"]
    message = str(error.value)
    assert "toy-background@fixture" in message and "toy-background@0123456789ab" in message


def test_a_database_used_through_another_comes_along(tmp_path: Path, write_study) -> None:
    dye = DYE + "\ne03,input,chemicals:salt,0.5,kg,chemicals:salt"
    write_study(tmp_path / "dyes", {"reactive-dye": dye}, uses=["chemicals"], flows=[DYE_FLOW])
    write_study(tmp_path / "chemicals", {"salt": SALT}, flows=[SALT_FLOW])
    shirt = write_study(tmp_path / "tshirt", {"tshirt": SHIRT}, uses=["dyes"], flows=[SHIRT_FLOW])

    folder = read_database(shirt)

    assert [u.database.name for u in folder.used] == ["chemicals", "dyes"]


def test_a_database_used_twice_in_the_chain_is_read_once(tmp_path: Path, write_study) -> None:
    dye = DYE + "\ne03,input,chemicals:salt,0.5,kg,chemicals:salt"
    write_study(tmp_path / "dyes", {"reactive-dye": dye}, uses=["chemicals"], flows=[DYE_FLOW])
    write_study(tmp_path / "chemicals", {"salt": SALT}, flows=[SALT_FLOW])
    shirt = write_study(
        tmp_path / "tshirt", {"tshirt": SHIRT}, uses=["dyes", "chemicals"], flows=[SHIRT_FLOW]
    )

    folder = read_database(shirt)

    assert [u.database.name for u in folder.used] == ["chemicals", "dyes"]


def test_a_pinned_version_in_uses_is_refused_with_the_folder_form(
    tmp_path: Path, write_study
) -> None:
    shirt = write_study(
        tmp_path / "tshirt",
        {"tshirt": SHIRT},
        uses=["textile-chemicals@v3"],
        flows=[SHIRT_FLOW],
    )

    with pytest.raises(InvalidDataError) as error:
        read_database(shirt)

    message = str(error.value)
    assert "textile-chemicals@v3" in message
    assert "folder" in message


def test_a_database_named_twice_in_uses_is_refused(tmp_path: Path, write_study) -> None:
    write_study(tmp_path / "dyes", {"reactive-dye": DYE}, flows=[DYE_FLOW])
    shirt = write_study(
        tmp_path / "tshirt", {"tshirt": SHIRT}, uses=["dyes", "dyes"], flows=[SHIRT_FLOW]
    )

    with pytest.raises(InvalidDataError) as error:
        read_database(shirt)

    assert "dyes" in str(error.value)
    assert "twice" in str(error.value)


def test_a_used_database_that_does_not_read_names_its_folder(tmp_path: Path, write_study) -> None:
    write_study(
        tmp_path / "dyes",
        {"reactive-dye": DYE + "\ne03,emission,toy:co2,lots,kg,"},
        flows=[DYE_FLOW],
    )
    shirt = write_study(tmp_path / "tshirt", {"tshirt": SHIRT}, uses=["dyes"], flows=[SHIRT_FLOW])

    with pytest.raises(InvalidDataError) as error:
        read_database(shirt)

    locations = [p.location or "" for p in error.value.problems]
    assert locations and all(loc.startswith("../dyes/") for loc in locations)


def test_a_used_database_is_found_from_a_relative_path(
    tmp_path: Path, write_study, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_study(tmp_path / "dyes", {"reactive-dye": DYE}, flows=[DYE_FLOW])
    write_study(tmp_path / "tshirt", {"tshirt": SHIRT}, uses=["dyes"], flows=[SHIRT_FLOW])
    monkeypatch.chdir(tmp_path / "tshirt")

    assert [u.database.name for u in read_database(".").used] == ["dyes"]
