"""Exit tests of D46's first part (M6, S2a): a folder of processes is a database, read from
database.yaml, and each of its processes is a product scored per unit of what it makes.

Example: `hw.open("tests/fixtures/toy/tshirt/tshirt").calc(bg, "tshirt:tshirt")` is 1.19205. The
toy scores do not move: 1.19205, 1.25205 with the waste line, 0.13245 for the electricity
sensitivity, 1.242646 with the dye. The toy release and method hashes do not move either
(tests/query/test_api.py).
"""

import shutil
from pathlib import Path

import pytest

import headwater as hw
from headwater.model import InvalidDataError
from tests.conftest import open_database

TOY = Path(__file__).resolve().parent / "fixtures" / "toy"
REL = 1e-9  # AGENTS.md: relative 1e-9 for toy fixtures


@pytest.fixture(scope="module")
def bg() -> hw.Background:
    return hw.load(TOY / "background", method=TOY / "methods" / "toy-climate", namespace="toy")


@pytest.fixture
def toy(tmp_path: Path) -> Path:
    target = tmp_path / "toy"
    shutil.copytree(TOY, target)
    return target


def test_open_gives_a_database_scored_by_the_id_of_a_process(bg: hw.Background) -> None:
    database = open_database(TOY / "tshirt" / "tshirt")

    assert isinstance(database, hw.Database)
    assert repr(database) == (
        "Database(tshirt: 1 process, background toy-background@fixture, methods toy-climate)"
    )
    assert database.calc(bg, process_id="tshirt:tshirt").score == pytest.approx(1.19205, rel=REL)
    assert database.calc(bg, "tshirt:tshirt").score == pytest.approx(1.19205, rel=REL)


@pytest.mark.parametrize(
    ("folder", "process_id", "score"),
    [
        ("tshirt/tshirt", "tshirt:tshirt", 1.19205),
        ("tshirt-waste/tshirt", "tshirt:tshirt", 1.25205),
        ("tshirt-dyed/tshirt", "tshirt:tshirt", 1.242646),
        ("tshirt-dyed/dyes", "dyes:reactive-dye", 5.0596),
        ("tshirt-local-electricity/tshirt", "tshirt:tshirt", 1.09205),
    ],
)
def test_the_toy_scores_do_not_move(
    bg: hw.Background, folder: str, process_id: str, score: float
) -> None:
    assert open_database(TOY / folder).calc(bg, process_id).score == pytest.approx(score, rel=REL)


def test_the_sensitivity_to_the_fabric_s_electricity_is_0_13245(bg: hw.Background) -> None:
    levers = open_database(TOY / "tshirt" / "tshirt").calc(bg, "tshirt:tshirt").sensitivity()

    [electricity] = [
        s for s in levers if (s.process_id, s.line_id) == ("toy:fabric-production", "e02")
    ]
    assert electricity.sensitivity == pytest.approx(0.13245, rel=REL)


def test_a_database_with_no_methods_validates_and_scores(bg: hw.Background) -> None:
    dyes = open_database(TOY / "tshirt-dyed" / "dyes")

    assert dyes.folder.database.methods == ()
    assert repr(dyes) == "Database(dyes: 1 process, background toy-background@fixture, no methods)"
    assert dyes.validate(bg).errors == ()
    assert dyes.calc(bg, "dyes:reactive-dye").score == pytest.approx(5.0596, rel=REL)


def test_a_process_the_database_does_not_have_is_named_with_those_it_has(
    bg: hw.Background,
) -> None:
    with pytest.raises(ValueError, match=r"no process 'tshirt:shirt'.*tshirt:tshirt"):
        open_database(TOY / "tshirt" / "tshirt").calc(bg, "tshirt:shirt")


def test_a_question_field_in_database_yaml_is_refused(toy: Path) -> None:
    """What a study asks (goal, functional unit, reference flow) is not a database's."""
    path = toy / "tshirt" / "tshirt" / "database.yaml"
    path.write_text(path.read_text() + "reference_flow: tshirt:tshirt\n")

    with pytest.raises(InvalidDataError) as raised:
        open_database(path.parent)

    assert [p.item for p in raised.value.problems] == ["database.yaml: reference_flow"]


def test_a_folder_of_the_old_format_says_what_it_needs(toy: Path) -> None:
    folder = toy / "tshirt" / "tshirt"
    (folder / "database.yaml").rename(folder / "study.yaml")

    with pytest.raises(InvalidDataError) as raised:
        open_database(folder)

    [problem] = raised.value.problems
    assert problem.item == "database.yaml"
    assert "holds study.yaml" in problem.message and "database.yaml" in problem.message


# --- input hashes: changed once by D46 -----------------------------------------------------

# Before D46 the T-shirt's input hash was
# 61d08237751248aead99485a1b765521a21385fc5c641ece7aba0200b7b94099, and the dyed T-shirt's
# 176ef6edff7c6f9df3db0a7875d4f2cb979ba21347589b9764e6ca5f7424d59a: study.yaml, with its
# question fields, was hashed. Now the fields database.yaml sets are, with the process scored.
TSHIRT = "b5697b05239fd21e05b47b090e7e466061a88c091d2eeb82c1996c86fc8221c0"
DYED = "1b1827644f9298b1f9a89aabfc242e78104a14b78d181df4cb1bfb4d6089fe04"


def test_the_input_hashes_of_the_toy_after_d46(bg: hw.Background) -> None:
    tshirt = open_database(TOY / "tshirt" / "tshirt").calc(bg, "tshirt:tshirt")
    dyed = open_database(TOY / "tshirt-dyed" / "tshirt").calc(bg, "tshirt:tshirt")

    assert tshirt.provenance.input_hash == TSHIRT
    assert dyed.provenance.input_hash == DYED


def test_the_same_inputs_in_another_folder_hash_alike(bg: hw.Background) -> None:
    """The T-shirt of the grid case is the T-shirt, next to a study that holds the grid
    scenario: the same inputs, so the same hash."""
    grid = open_database(TOY / "tshirt-grid-scenario" / "tshirt")

    assert grid.calc(bg, "tshirt:tshirt").provenance.input_hash == TSHIRT


def test_each_product_of_a_database_has_its_own_input_hash(bg: hw.Background, toy: Path) -> None:
    folder = toy / "tshirt" / "tshirt"
    (folder / "processes" / "bag.csv").write_text(
        "line,role,flow,amount,unit,supplier\n"
        "e01,reference,tshirt:bag,1,unit,\n"
        "e02,input,toy:cotton-fabric,0.1,kg,toy:fabric-production\n"
    )
    (folder / "processes" / "bag.yaml").write_text(
        "name: bag\nlocation: GLO\nkind: ordinary_transforming_activity\n"
    )
    with (folder / "flows.csv").open("a") as flows:
        flows.write("tshirt:bag,bag,product,unit,,\n")
    database = open_database(folder)

    shirt = database.calc(bg, "tshirt:tshirt")
    bag = database.calc(bg, "tshirt:bag")

    assert shirt.score == pytest.approx(1.19205, rel=REL)
    assert bag.score == pytest.approx(0.1 * 3.7086, rel=REL)
    assert shirt.provenance.input_hash != bag.provenance.input_hash


def test_a_diff_of_a_product_only_the_new_side_makes_has_no_old_hash(
    bg: hw.Background, toy: Path
) -> None:
    from headwater.diff import diff_databases
    from headwater.study import read_database

    folder = toy / "tshirt" / "tshirt"
    old = read_database(folder)
    (folder / "processes" / "bag.csv").write_text(
        "line,role,flow,amount,unit,supplier\n"
        "e01,reference,tshirt:bag,1,unit,\n"
        "e02,input,toy:cotton-fabric,0.1,kg,toy:fabric-production\n"
    )
    (folder / "processes" / "bag.yaml").write_text(
        "name: bag\nlocation: GLO\nkind: ordinary_transforming_activity\n"
    )
    with (folder / "flows.csv").open("a") as flows:
        flows.write("tshirt:bag,bag,product,unit,,\n")

    diff = diff_databases(old, read_database(folder), bg, products=["tshirt:bag"])

    assert (diff.old_input_hash, diff.old_residual) == (None, None)
    [row] = diff.rows
    assert (row.old, row.new) == (None, pytest.approx(0.1 * 3.7086, rel=REL))
