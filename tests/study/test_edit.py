"""Building and changing a database from Python. The calls change the database's own files, as a
person would by hand, and `save` checks the whole database before it writes anything."""

import difflib
import shutil
from collections.abc import Callable
from datetime import date
from pathlib import Path

import pytest

import headwater as hw
from headwater.model import InvalidDataError
from headwater.study import DatabaseEditor
from tests.conftest import open_database, toy_cases, toy_database

TOY = Path(__file__).resolve().parent.parent / "fixtures" / "toy"
REL = 1e-9


@pytest.fixture(scope="module")
def bg() -> hw.Background:
    return hw.load(TOY / "background", method=TOY / "methods" / "toy-climate", namespace="toy")


@pytest.fixture
def cases(tmp_path: Path) -> Path:
    """A writable copy of the toy fixtures, one folder per case, so a database that uses another
    finds it next to it."""
    target = tmp_path / "toy"
    shutil.copytree(TOY, target)
    return target


def files(folder: Path) -> dict[str, bytes]:
    return {
        p.relative_to(folder).as_posix(): p.read_bytes() for p in folder.rglob("*") if p.is_file()
    }


def changed_lines(before: dict[str, bytes], after: dict[str, bytes]) -> list[str]:
    """Every line removed or added, as `-file: text` and `+file: text`."""
    found: list[str] = []
    for name in sorted(before.keys() | after.keys()):
        old = before.get(name, b"").decode().splitlines()
        new = after.get(name, b"").decode().splitlines()
        for line in difflib.ndiff(old, new):
            if line[:1] in "+-":
                found.append(f"{line[0]}{name}: {line[2:]}")
    return found


def _one_purchase(path: Path, bg: hw.Background) -> DatabaseEditor:
    """A new database `shirt` whose one process buys 0.25 kg of fabric."""
    database = hw.Database.new(path, bg, pin="toy-background@fixture")
    shirt = database.new_process(
        "shirt", name="T-shirt", location="GLO", product="T-shirt", unit="unit"
    )
    shirt.new_exchange("input", "toy:cotton-fabric", 0.25, "kg", supplier="toy:fabric-production")
    return database


# --- building a database -----------------------------------------------------------------------


def test_the_tshirt_built_in_a_few_calls_is_the_fixture_byte_for_byte(
    bg: hw.Background, tmp_path: Path
) -> None:
    database = hw.Database.new(
        tmp_path / "tshirt", bg, pin="toy-background@fixture", methods=["toy-climate"]
    )
    tshirt = database.new_process(
        "tshirt", name="T-shirt", location="GLO", product="T-shirt", unit="unit"
    )
    line = tshirt.new_exchange(
        "input", "toy:cotton-fabric", 0.25, "kg", supplier="toy:fabric-production"
    )
    assert line == "e02"
    tshirt.new_exchange(
        "input", "toy:electricity", 0.5, "kWh", supplier="toy:electricity-production"
    )
    saved = database.save()

    assert files(tmp_path / "tshirt") == files(toy_database(TOY, "tshirt"))
    assert saved.calc(bg, "tshirt:tshirt").score == pytest.approx(1.19205, rel=REL)


def test_a_new_database_needs_only_the_background(bg: hw.Background, tmp_path: Path) -> None:
    saved = _one_purchase(tmp_path / "shirt", bg).save()

    # The name is the folder's, and there are no methods unless they are given.
    assert (tmp_path / "shirt" / "database.yaml").read_text() == (
        "name: shirt\nbackground: toy-background@fixture\n"
    )
    assert saved.validate(bg).problems == ()
    assert saved.calc(bg, "shirt:shirt").score == pytest.approx(0.92715, rel=REL)


def test_a_database_prints_its_processes_background_and_methods(
    bg: hw.Background, tmp_path: Path
) -> None:
    saved = _one_purchase(tmp_path / "shirt", bg).save()

    assert repr(saved) == (
        "Database(shirt: 1 process, background toy-background@fixture, no methods)"
    )
    assert repr(open_database(toy_database(TOY, "dyes"))) == (
        "Database(dyes: 1 process, background toy-background@fixture, no methods)"
    )


def test_no_file_says_basis_null(bg: hw.Background, cases: Path, tmp_path: Path) -> None:
    database = _one_purchase(tmp_path / "shirt", bg)
    database.set(allocation={"rule": "none"})
    database.save()
    editor = open_database(toy_database(cases, "tshirt")).edit(bg)
    editor.set(allocation={"rule": "none"})
    editor.save()

    for folder in (tmp_path / "shirt", toy_database(cases, "tshirt")):
        text = (folder / "database.yaml").read_text()
        assert "allocation: {rule: none}\n" in text and "null" not in text


def test_a_new_database_is_written_only_into_an_empty_folder(
    bg: hw.Background, cases: Path
) -> None:
    with pytest.raises(ValueError, match="already holds files"):
        hw.Database.new(toy_database(cases, "tshirt"), bg, pin="toy-background@fixture")


def test_a_database_field_that_does_not_read_is_refused_at_once(
    bg: hw.Background, tmp_path: Path
) -> None:
    with pytest.raises(InvalidDataError, match="background"):
        hw.Database.new(tmp_path / "x", bg, pin="toy-background")
    assert not (tmp_path / "x").exists()


# --- each edit gives the fixture a person wrote by hand ----------------------------------------

Edit = Callable[[DatabaseEditor], None]


def _waste(s: DatabaseEditor) -> None:
    s.process("tshirt").new_exchange(
        "waste_to_treatment",
        "toy:textile-waste",
        0.05,
        "kg",
        supplier="toy:incineration-textile-waste",
    )


def _no_waste(s: DatabaseEditor) -> None:
    s.process("tshirt:tshirt").remove_exchange("e04")


def _tape(s: DatabaseEditor) -> None:
    s.set(cut_off={"criteria": ["mass", "energy"], "max_cumulative": 0.05})
    tape = s.new_flow("packaging-tape", name="packaging tape", kind="product", unit="kg")
    line = s.process("tshirt").new_exchange("input", tape, 0.01, "kg")
    s.cut_off(
        "tshirt:tshirt",
        line,
        criterion="mass",
        share=0.0384615384615385,
        reason="No supplier for packaging tape in the background: 0.01 kg of 0.26 kg of mass input",
        who="leo",
        when=date(2026, 10, 11),
    )


def _parameter(s: DatabaseEditor) -> None:
    s.set_parameter(
        "fabric_kg",
        0.25,
        unit="kg",
        comment="Fabric in one T-shirt, from the bill of materials",
    )
    s.process("tshirt").set_exchange("e02", amount="fabric_kg")


def _wind(s: DatabaseEditor) -> None:
    s.override("toy:electricity-production", file="own-wind-power").set_exchange(
        "e02", amount=0.3, comment="Wind power: 0.3 kg CO2 per kWh instead of 0.5"
    )


def _no_wind(s: DatabaseEditor) -> None:
    s.override("toy:electricity-production").reset_exchange("e02")


def _mill(s: DatabaseEditor) -> None:
    s.override("toy:fabric-production", file="efficient-weaving").set_exchange(
        "e02", amount=2.5, comment="Measured at the mill"
    )


def _dye(s: DatabaseEditor) -> None:
    s.set(uses=["dyes"])
    s.process("tshirt").new_exchange(
        "input", "dyes:reactive-dye", 0.01, "kg", supplier="dyes:reactive-dye"
    )


@pytest.mark.parametrize(
    ("start", "edit", "expected", "score"),
    [
        ("tshirt", _waste, "tshirt-waste", 1.25205),
        ("tshirt-waste", _no_waste, "tshirt", 1.19205),
        ("tshirt", _tape, "tshirt-cut-off", 1.19205),
        ("tshirt", _parameter, "tshirt-parameter", 1.19205),
        ("tshirt", _wind, "tshirt-local-electricity", 1.09205),
        ("tshirt-local-electricity", _no_wind, "tshirt", 1.19205),
        ("tshirt", _mill, "tshirt-local-fabric", 1.125825),
        ("tshirt", _dye, "tshirt-dyed", 1.242646),
    ],
)
def test_an_edit_writes_the_files_a_person_wrote_by_hand(
    bg: hw.Background, cases: Path, start: str, edit: Edit, expected: str, score: float
) -> None:
    # A database next to the expected one, such as dyes, goes next to the one edited too.
    for other in (TOY / expected).iterdir():
        if other.name != "tshirt":
            shutil.copytree(other, cases / start / other.name)
    path = toy_database(cases, start)
    editor = open_database(path).edit(bg)
    edit(editor)
    saved = editor.save()

    want = files(toy_database(TOY, expected))
    got = files(path)
    assert got == want
    if not any(name.startswith("overrides/") for name in want):
        assert not (path / "overrides").exists()
    assert saved.calc(bg, "tshirt:tshirt").score == pytest.approx(score, rel=REL)


def test_an_override_made_through_the_api_scores_the_local_override(
    bg: hw.Background, cases: Path
) -> None:
    path = toy_database(cases, "tshirt")
    editor = open_database(path).edit(bg)
    _wind(editor)
    database = editor.save()
    assert database.calc(bg, "tshirt:tshirt").score == pytest.approx(1.09205, rel=REL)
    assert (path / "overrides" / "own-wind-power.csv").read_bytes() == (
        toy_database(TOY, "tshirt-local-electricity") / "overrides" / "own-wind-power.csv"
    ).read_bytes()


# --- read and save: nothing moves that was not changed -----------------------------------------


@pytest.mark.parametrize("case", toy_cases(TOY))
def test_every_toy_database_read_and_saved_is_unchanged_byte_for_byte(
    bg: hw.Background, cases: Path, case: str
) -> None:
    before = files(cases)
    folder = open_database(toy_database(cases, case)).folder
    database = folder.database
    editor = open_database(toy_database(cases, case)).edit(bg)
    # Every field and line written again with the value it has: nothing changes.
    editor.set(
        name=database.name,
        background=str(database.background),
        uses=list(database.uses),
        methods=list(database.methods),
        description=database.description,
        allocation=(
            database.allocation.model_dump(mode="json", exclude_none=True)
            if database.allocation
            else None
        ),
        end_of_life=database.end_of_life,
        cut_off=database.cut_off.model_dump(mode="json") if database.cut_off else None,
    )
    for process in folder.processes:
        p = editor.process(process.id)
        for e in process.exchanges:
            p.set_exchange(e.line_id, amount=e.formula or e.amount, unit=e.unit)
    for parameter in folder.parameters:
        editor.set_parameter(
            parameter.name,
            parameter.formula or parameter.value or 0,
            process_id=parameter.scope,
        )
    editor.save()
    assert files(cases) == before


def test_an_edit_changes_its_own_row_and_nothing_else(bg: hw.Background, cases: Path) -> None:
    before = files(cases)
    editor = open_database(toy_database(cases, "tshirt-parameter")).edit(bg)
    editor.process("tshirt").set_exchange("e03", amount=0.6)
    database = editor.save()

    assert changed_lines(before, files(cases)) == [
        "-tshirt-parameter/tshirt/processes/tshirt.csv: "
        "e03,input,toy:electricity,0.5,kWh,toy:electricity-production",
        "+tshirt-parameter/tshirt/processes/tshirt.csv: "
        "e03,input,toy:electricity,0.6,kWh,toy:electricity-production",
    ]
    # the parameter and the formula line survive: fabric_kg still drives line e02
    assert database.calc(bg, "tshirt:tshirt").score == pytest.approx(
        0.25 * 3.7086 + 0.6 * 0.5298, rel=REL
    )


def test_a_parameter_changes_in_place_and_keeps_its_unit_and_comment(
    bg: hw.Background, cases: Path
) -> None:
    before = files(cases)
    editor = open_database(toy_database(cases, "tshirt-parameter")).edit(bg)
    editor.set_parameter("fabric_kg", 0.30)
    database = editor.save()

    assert changed_lines(before, files(cases)) == [
        "-tshirt-parameter/tshirt/parameters.yaml:     value: 0.25",
        "+tshirt-parameter/tshirt/parameters.yaml:     value: 0.3",
    ]
    assert database.calc(bg, "tshirt:tshirt").score == pytest.approx(1.37748, rel=REL)


def test_comments_in_yaml_files_are_kept(bg: hw.Background, cases: Path) -> None:
    root = toy_database(cases, "tshirt-parameter")
    (root / "parameters.yaml").write_text(
        "# From the bill of materials, 2026\n"
        "database:\n"
        "  fabric_kg: 0.25   # weighed on 10 shirts\n"
        "  loss: 0.05\n"
    )
    database_yaml = root / "database.yaml"
    database_yaml.write_text(
        database_yaml.read_text() + "description: T-shirt\n# reviewed by leo\n"
    )
    editor = open_database(root).edit(bg)
    editor.set_parameter("loss", "fabric_kg / 5")
    editor.set_parameter("extra", 2, unit="kg")
    editor.set(description="T-shirt, size M")
    editor.save()

    assert (root / "parameters.yaml").read_text() == (
        "# From the bill of materials, 2026\n"
        "database:\n"
        "  fabric_kg: 0.25   # weighed on 10 shirts\n"
        "  loss: fabric_kg / 5\n"
        "  extra:\n"
        "    value: 2\n"
        "    unit: kg\n"
    )
    text = database_yaml.read_text()
    assert "description: T-shirt, size M\n" in text
    assert text.endswith("# reviewed by leo\n")


def test_a_parameter_is_removed_and_its_file_goes_with_the_last_one(
    bg: hw.Background, cases: Path
) -> None:
    path = toy_database(cases, "tshirt-parameter")
    editor = open_database(path).edit(bg)
    editor.process("tshirt").set_exchange("e02", amount=0.25)
    editor.remove_parameter("fabric_kg")
    editor.save()
    assert files(path) == files(toy_database(TOY, "tshirt"))


# --- invalid edits -----------------------------------------------------------------------------


def _no_temporary_folders(case: Path) -> None:
    """A save writes into a copy next to the database, in the folder of its case: none is left."""
    assert sorted(p.name for p in case.iterdir()) == sorted(
        p.name for p in (TOY / case.name).iterdir()
    )


def test_an_invalid_edit_raises_naming_it_and_writes_nothing(
    bg: hw.Background, cases: Path
) -> None:
    before = files(cases)
    editor = open_database(toy_database(cases, "tshirt")).edit(bg)
    editor.process("tshirt").new_exchange(
        "input", "toy:cotton-fabric", 0.1, "kg", supplier="toy:no-such-process"
    )
    with pytest.raises(InvalidDataError, match="toy:no-such-process"):
        editor.save()
    assert files(cases) == before
    _no_temporary_folders(cases / "tshirt")


def test_a_formula_with_an_unknown_parameter_is_refused_at_save(
    bg: hw.Background, cases: Path
) -> None:
    before = files(cases)
    editor = open_database(toy_database(cases, "tshirt-parameter")).edit(bg)
    editor.process("tshirt").set_exchange("e03", amount="fabric_kgg * 2")
    with pytest.raises(InvalidDataError, match="fabric_kgg"):
        editor.save()
    assert files(cases) == before
    _no_temporary_folders(cases / "tshirt-parameter")


def test_a_cut_off_above_the_limit_of_the_database_is_refused(
    bg: hw.Background, cases: Path
) -> None:
    before = files(cases)
    editor = open_database(toy_database(cases, "tshirt-cut-off")).edit(bg)
    editor.set(cut_off={"criteria": ["mass", "energy"], "max_cumulative": 0.03})
    with pytest.raises(InvalidDataError, match="max_cumulative|cumulative"):
        editor.save()
    assert files(cases) == before


def test_a_line_a_cut_off_names_cannot_be_removed(bg: hw.Background, cases: Path) -> None:
    editor = open_database(toy_database(cases, "tshirt-cut-off")).edit(bg)
    with pytest.raises(ValueError, match=r"cut_offs\.csv:2"):
        editor.process("tshirt").remove_exchange("e04")


def test_unknown_names_are_refused_at_once(bg: hw.Background, cases: Path) -> None:
    editor = open_database(toy_database(cases, "tshirt")).edit(bg)
    with pytest.raises(ValueError, match="e02"):
        editor.process("tshirt").set_exchange("e2", amount=1)
    with pytest.raises(ValueError, match="tshirt"):
        editor.process("tshrt")
    with pytest.raises(ValueError, match="toy:electricity-production"):
        editor.override("toy:electricity-prod")
    with pytest.raises(ValueError, match="reference"):
        editor.override("toy:electricity-production").set_exchange("e01", amount=2)
    with pytest.raises(ValueError, match="process of the database"):
        editor.override("tshirt:tshirt")
    with pytest.raises(ValueError, match="role"):
        editor.process("tshirt").new_exchange("buy", "toy:electricity", 1, "kWh")


def test_an_editor_saves_again_after_a_save(bg: hw.Background, cases: Path) -> None:
    path = toy_database(cases, "tshirt")
    editor = open_database(path).edit(bg)
    _waste(editor)
    editor.save()
    editor.process("tshirt").remove_exchange("e04")
    editor.save()
    assert files(path) == files(toy_database(TOY, "tshirt"))


# --- what else a person wrote stays ------------------------------------------------------------


def test_a_file_with_a_bom_and_windows_line_endings_keeps_them(
    bg: hw.Background, cases: Path
) -> None:
    path = toy_database(cases, "tshirt") / "processes" / "tshirt.csv"
    path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes().replace(b"\n", b"\r\n"))
    editor = open_database(toy_database(cases, "tshirt")).edit(bg)
    editor.process("tshirt").set_exchange("e03", amount=0.6)
    editor.process("tshirt").new_exchange("emission", "toy:co2", 0.01, "kg", comment="Ironing")
    editor.save()
    assert path.read_bytes() == (
        b"\xef\xbb\xbfline,role,flow,amount,unit,supplier,comment\r\n"
        b"e01,reference,tshirt:tshirt,1,unit,,\r\n"
        b"e02,input,toy:cotton-fabric,0.25,kg,toy:fabric-production,\r\n"
        b"e03,input,toy:electricity,0.6,kWh,toy:electricity-production,\r\n"
        b"e04,emission,toy:co2,0.01,kg,,Ironing\r\n"
    )


def test_a_parameter_of_one_process_goes_under_its_id(bg: hw.Background, cases: Path) -> None:
    path = toy_database(cases, "tshirt-parameter")
    editor = open_database(path).edit(bg)
    editor.set_parameter("kwh", 0.5, process_id="tshirt:tshirt", unit="kWh")
    editor.process("tshirt").set_exchange("e03", amount="kwh * 2")
    database = editor.save()
    assert (
        (path / "parameters.yaml")
        .read_text()
        .endswith("processes:\n  tshirt:tshirt:\n    kwh:\n      value: 0.5\n      unit: kWh\n")
    )
    assert database.calc(bg, "tshirt:tshirt").score == pytest.approx(0.92715 + 0.5298, rel=REL)
    editor.remove_parameter("kwh", process_id="tshirt:tshirt")
    editor.process("tshirt").set_exchange("e03", amount=0.5)
    editor.save()
    assert files(path) == files(toy_database(TOY, "tshirt-parameter"))


def test_a_process_is_added_and_removed_with_its_two_files(bg: hw.Background, cases: Path) -> None:
    path = toy_database(cases, "tshirt")
    editor = open_database(path).edit(bg)
    bag = editor.new_process(
        "bag", name="cotton bag", location="GLO", product="cotton bag", unit="unit"
    )
    bag.new_exchange("input", "toy:cotton-fabric", 0.1, "kg", supplier="toy:fabric-production")
    editor.process("tshirt").new_exchange("input", "tshirt:bag", 1, "unit", supplier=bag.id)
    with pytest.raises(ValueError, match=r"processes/tshirt\.csv:5"):
        editor.remove_process("bag")
    database = editor.save()
    assert database.calc(bg, "tshirt:tshirt").score == pytest.approx(
        1.19205 + 0.1 * 3.7086, rel=REL
    )
    assert (path / "processes" / "bag.yaml").read_text() == (
        "name: cotton bag\nlocation: GLO\nkind: ordinary_transforming_activity\n"
    )

    editor.process("tshirt").remove_exchange("e04")
    editor.remove_process("tshirt:bag")
    editor.save()
    flows = (path / "flows.csv").read_text()
    assert flows.endswith("tshirt:bag,cotton bag,product,unit,,\n")  # a flow is not removed
    assert sorted(files(path)) == sorted(files(toy_database(TOY, "tshirt")))


# --- what the review found ---------------------------------------------------------------------


def test_a_blank_line_between_parameters_keeps_them_together(
    bg: hw.Background, cases: Path
) -> None:
    root = toy_database(cases, "tshirt-parameter")
    (root / "parameters.yaml").write_text(
        "database:\n  a: 1\n\n  fabric_kg: 0.25\n\n  b: 2\nprocesses:\n  tshirt:tshirt:\n    c: 3\n"
    )
    editor = open_database(root).edit(bg)
    editor.remove_parameter("a")
    editor.set_parameter("b", 5)
    editor.save()
    assert (root / "parameters.yaml").read_text() == (
        "database:\n\n  fabric_kg: 0.25\n\n  b: 5\nprocesses:\n  tshirt:tshirt:\n    c: 3\n"
    )


def test_an_override_file_stays_in_overrides(bg: hw.Background, cases: Path) -> None:
    editor = open_database(toy_database(cases, "tshirt")).edit(bg)
    with pytest.raises(ValueError, match="overrides/"):
        editor.override("toy:electricity-production", file="../wind")


def test_a_file_changed_on_disk_after_it_was_read_is_not_overwritten(
    bg: hw.Background, cases: Path
) -> None:
    path = toy_database(cases, "tshirt") / "processes" / "tshirt.csv"
    editor = open_database(toy_database(cases, "tshirt")).edit(bg)
    editor.process("tshirt").set_exchange("e03", amount=0.6)
    path.write_text(path.read_text().replace("0.25", "0.3"))  # a hand edit meanwhile
    with pytest.raises(InvalidDataError, match="changed on disk"):
        editor.save()
    assert ",0.3,kg," in path.read_text() and ",0.5,kWh," in path.read_text()


def test_an_edit_that_breaks_a_database_using_this_one_is_refused(
    bg: hw.Background, cases: Path
) -> None:
    before = files(cases)
    editor = open_database(toy_database(cases, "dyes")).edit(bg)
    editor.new_process(  # so that dyes still has a process
        "acid-dye", name="acid dye production", location="GLO", product="acid dye", unit="kg"
    )
    editor.remove_process("reactive-dye")  # the dyed T-shirt next to it buys it
    with pytest.raises(InvalidDataError, match=r"\.\./tshirt\b"):
        editor.save()
    assert files(cases) == before


def test_a_process_date_is_written_as_a_date(bg: hw.Background, cases: Path) -> None:
    path = toy_database(cases, "tshirt")
    editor = open_database(path).edit(bg)
    editor.process("tshirt").set(time_start=date(2024, 1, 1))
    editor.save()
    assert (path / "processes" / "tshirt.yaml").read_text() == (
        "name: T-shirt\nlocation: GLO\nkind: ordinary_transforming_activity\n"
        "time_start: 2024-01-01\n"
    )
