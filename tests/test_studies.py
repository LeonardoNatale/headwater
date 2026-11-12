"""Exit tests of D46's second part (M6, S2b): a study names products of databases with amounts,
asks its question of them, and holds no processes.

Example: the study `tshirt-footprint`, next to the databases `tshirt` and `dyes`, scores one
T-shirt at 1.242646, and 1.237646 in its scenario `cleaner-dye`, which sets line e03 of
`dyes:reactive-dye` from 4 to 3.5: a scenario reaches a line of a database the product uses.
"""

import json
import shutil
import subprocess
from pathlib import Path

import jsonschema
import pytest
import yaml
from typer.testing import CliRunner

import headwater as hw
from headwater.cli import app
from headwater.model import InvalidDataError
from tests.conftest import open_database, open_study_folder

TOY = Path(__file__).resolve().parent / "fixtures" / "toy"
DYED = TOY / "tshirt-dyed"
REL = 1e-9  # AGENTS.md: relative 1e-9 for toy fixtures

CLEANER_DYE = {
    "description": "The dye plant emits 3.5 kg CO2 per kg of dye, not 4.",
    "changes": [{"process": "dyes:reactive-dye", "line": "e03", "amount": 3.5}],
}
SCOPE = {
    "goal": "Footprint of one T-shirt, internal use, not a public comparison",
    "functional_unit": "1 T-shirt",
    "boundary": "cradle-to-gate",
    "cut_off": {"criteria": ["mass", "energy"], "max_cumulative": 0.03},
}


@pytest.fixture(scope="module")
def bg() -> hw.Background:
    return hw.load(TOY / "background", method=TOY / "methods" / "toy-climate", namespace="toy")


@pytest.fixture
def toy(tmp_path: Path) -> Path:
    target = tmp_path / "toy"
    shutil.copytree(TOY, target)
    return target


def scores(results: tuple[hw.Result, ...]) -> dict[str, float]:
    return {r.process_id: r.score for r in results}


# --- a study and its products ----------------------------------------------------------------


def test_open_gives_a_study_that_scores_its_product_and_its_scenario(bg: hw.Background) -> None:
    study = open_study_folder(DYED / "tshirt-footprint")

    assert isinstance(hw.open(DYED / "tshirt-footprint"), hw.Study)
    assert repr(study) == (
        "Study(tshirt-footprint: 1 product, methods toy-climate, scenarios cleaner-dye)"
    )
    [base] = study.calc(bg)
    [cleaner] = study.calc(bg, scenario="cleaner-dye")
    assert base.score == pytest.approx(1.242646, rel=REL)
    assert cleaner.score == pytest.approx(1.237646, rel=REL)
    assert cleaner.provenance.scenario == "cleaner-dye"


def test_each_product_scores_its_amount(bg: hw.Background) -> None:
    """2 T-shirts at 1.242646 and 0.5 kg of dye at 5.0596 per kg. The amount is scored in the
    system, so a contribution tree adds up to the score, not to the score per unit."""
    results = open_study_folder(DYED / "tshirt-and-dye").calc(bg)

    assert scores(results) == {
        "tshirt:tshirt": pytest.approx(2 * 1.242646, rel=REL),
        "dyes:reactive-dye": pytest.approx(0.5 * 5.0596, rel=REL),
    }
    assert [r.amount for r in results] == [2.0, 0.5]
    for result in results:
        assert result.contributions().score == pytest.approx(result.score, rel=REL)
        assert result.forward_score == pytest.approx(result.score, rel=REL)


def test_scores_gives_every_indicator_of_every_product(bg: hw.Background) -> None:
    study = open_study_folder(DYED / "tshirt-and-dye")

    assert study.scores(bg) == {
        "tshirt:tshirt": {"climate-change": pytest.approx(2.485292, rel=REL)},
        "dyes:reactive-dye": {"climate-change": pytest.approx(2.5298, rel=REL)},
    }


def test_the_scenarios_of_the_toy_move_to_studies(bg: hw.Background) -> None:
    """The grid scenario changes a background line for everything, the parameter scenario a
    parameter of the database: the numbers of D23 and D24 stay."""
    grid = open_study_folder(TOY / "tshirt-grid-scenario" / "tshirt-footprint")
    heavier = open_study_folder(TOY / "tshirt-parameter" / "tshirt-footprint")

    assert grid.calc(bg)[0].score == pytest.approx(1.19205, rel=REL)
    assert grid.calc(bg, scenario="clean-grid")[0].score == pytest.approx(0.94205, rel=REL)
    assert heavier.calc(bg, scenario="more-fabric")[0].score == pytest.approx(1.37748, rel=REL)


def test_a_scenario_that_reaches_no_product_is_refused(toy: Path, bg: hw.Background) -> None:
    folder = toy / "tshirt" / "tshirt-footprint"
    folder.mkdir()
    (folder / "study.yaml").write_text((DYED / "tshirt-footprint" / "study.yaml").read_text())
    (folder / "scenarios").mkdir()
    (folder / "scenarios" / "cleaner-dye.yaml").write_text(yaml.safe_dump(CLEANER_DYE))

    with pytest.raises(InvalidDataError) as raised:
        open_study_folder(folder).validate(bg).raise_if_errors()

    assert "dyes:reactive-dye" in str(raised.value)


def test_a_parameter_two_databases_hold_needs_its_database(toy: Path, bg: hw.Background) -> None:
    """`kwh_per_kg` of dyes, and one of the same name added to the T-shirt: the scenario must say
    which. With `database: dyes` it changes the dye, 2 to 3 kWh per kg: +0.01 × 0.5298."""
    (toy / "tshirt-dyed" / "tshirt" / "parameters.yaml").write_text("database:\n  kwh_per_kg: 1\n")
    change = {"parameter": "kwh_per_kg", "value": 3}

    with pytest.raises(InvalidDataError, match="scenario-ambiguous-parameter|say which"):
        in_memory(toy, {"changes": [change]})
    study = in_memory(toy, {"changes": [{**change, "database": "dyes"}]})

    [result] = study.calc(bg, scenario="x")
    assert result.score == pytest.approx(1.242646 + 0.01 * 0.5298, rel=REL)


def test_a_change_to_a_database_no_product_reaches_is_refused(toy: Path) -> None:
    """The T-shirt alone reaches no database `dyes`, though it sits next to the study."""
    shutil.copytree(toy / "tshirt" / "tshirt", toy / "tshirt-dyed" / "plain")
    (toy / "tshirt-dyed" / "plain" / "database.yaml").write_text(
        "name: plain\nbackground: toy-background@fixture\nmethods: [toy-climate]\n"
    )
    for name in ("tshirt.csv", "tshirt.yaml"):
        path = toy / "tshirt-dyed" / "plain" / "processes" / name
        path.write_text(path.read_text().replace("tshirt:tshirt", "plain:tshirt"))
    flows = toy / "tshirt-dyed" / "plain" / "flows.csv"
    flows.write_text(flows.read_text().replace("tshirt:tshirt", "plain:tshirt"))

    with pytest.raises(InvalidDataError, match="no product of the study reaches"):
        hw.Study(
            products={"plain:tshirt": 1},
            methods=["toy-climate"],
            databases=toy / "tshirt-dyed",
            scenarios={"cleaner-dye": CLEANER_DYE},
        )


def test_a_formula_on_a_background_line_needs_one_database(toy: Path) -> None:
    """A study that reaches tshirt and dyes cannot tell whose parameters a formula on a
    background line uses."""
    change = {"process": "toy:electricity-production", "line": "e02", "amount": "kwh_per_kg / 4"}

    with pytest.raises(InvalidDataError, match="Write the number"):
        in_memory(toy, {"changes": [change]})


def test_a_scenario_changes_only_the_products_that_reach_its_lines(
    toy: Path, bg: hw.Background
) -> None:
    """Two products in two databases that do not use each other: a change to the bag's line
    moves the bag, 0.1 to 0.2 kg of fabric, and leaves the T-shirt as it is."""
    bag = toy / "tshirt" / "bag"
    (bag / "processes").mkdir(parents=True)
    (bag / "database.yaml").write_text(
        "name: bag\nbackground: toy-background@fixture\nmethods: [toy-climate]\n"
    )
    (bag / "flows.csv").write_text(
        "id,name,kind,unit,compartment,cas\nbag:bag,bag,product,unit,,\n"
    )
    (bag / "processes" / "bag.csv").write_text(
        "line,role,flow,amount,unit,supplier\n"
        "e01,reference,bag:bag,1,unit,\n"
        "e02,input,toy:cotton-fabric,0.1,kg,toy:fabric-production\n"
    )
    (bag / "processes" / "bag.yaml").write_text(
        "name: bag\nlocation: GLO\nkind: ordinary_transforming_activity\n"
    )
    study = hw.Study(
        products={"tshirt:tshirt": 1, "bag:bag": 1},
        methods=["toy-climate"],
        databases=toy / "tshirt",
        scenarios={"more": {"changes": [{"process": "bag:bag", "line": "e02", "amount": 0.2}]}},
    )

    assert study.validate(bg).errors == ()
    tshirt, bag_result = study.calc(bg, scenario="more")
    assert tshirt.score == pytest.approx(1.19205, rel=REL)
    assert bag_result.score == pytest.approx(0.2 * 3.7086, rel=REL)


def in_memory(toy: Path, scenario: dict[str, object]) -> hw.Study:
    return hw.Study(
        products={"tshirt:tshirt": 1},
        methods=["toy-climate"],
        databases=toy / "tshirt-dyed",
        scenarios={"x": scenario},
    )


# --- in memory, saved -------------------------------------------------------------------------


def test_a_study_in_memory_scores_as_the_same_study_saved(bg: hw.Background) -> None:
    saved = open_study_folder(DYED / "tshirt-footprint")
    memory = hw.Study(
        products={"tshirt:tshirt": 1},
        methods=["toy-climate"],
        databases=DYED,
        scenarios={"cleaner-dye": CLEANER_DYE},
    )

    for scenario in (None, "cleaner-dye"):
        [one] = saved.calc(bg, scenario=scenario)
        [two] = memory.calc(bg, scenario=scenario)
        assert two.score == pytest.approx(one.score, rel=REL)
        assert two.provenance.input_hash == one.provenance.input_hash
    [result] = memory.calc(bg)
    assert result.provenance.study is not None
    assert result.provenance.study.saved is False
    assert result.provenance.study.commit is None


def test_save_writes_the_study_next_to_its_databases(toy: Path, bg: hw.Background) -> None:
    memory = hw.Study(
        products={"tshirt:tshirt": 1},
        methods=["toy-climate"],
        databases=toy / "tshirt-dyed",
        scenarios={"cleaner-dye": CLEANER_DYE},
    )

    saved = memory.save(toy / "tshirt-dyed" / "my-footprint", **SCOPE)

    assert (toy / "tshirt-dyed" / "my-footprint" / "study.yaml").is_file()
    assert (toy / "tshirt-dyed" / "my-footprint" / "scenarios" / "cleaner-dye.yaml").is_file()
    assert saved.calc(bg, scenario="cleaner-dye")[0].score == pytest.approx(1.237646, rel=REL)
    assert open_study_folder(toy / "tshirt-dyed" / "my-footprint").calc(bg)[
        0
    ].provenance.input_hash == (memory.calc(bg)[0].provenance.input_hash)


def test_save_asks_for_the_scope_and_a_folder_next_to_the_databases(toy: Path) -> None:
    memory = hw.Study(products={"tshirt:tshirt": 1}, methods=["toy-climate"], databases=DYED)

    with pytest.raises(TypeError):
        memory.save(toy / "tshirt-dyed" / "x", goal="a goal")  # type: ignore[call-arg]
    with pytest.raises(ValueError, match="next to"):
        memory.save(toy / "elsewhere" / "x", **SCOPE)
    assert not (toy / "elsewhere").exists()


def test_a_saved_study_names_its_commit(tmp_path: Path, bg: hw.Background) -> None:
    repo = tmp_path / "repo"
    shutil.copytree(DYED, repo)
    git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com"]
    subprocess.run([*git, "init", "-q", "-b", "main"], check=True)
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "toy"], check=True)
    head = subprocess.run(
        [*git, "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()

    [clean] = open_study_folder(repo / "tshirt-footprint").calc(bg)
    (repo / "dyes" / "database.yaml").write_text(
        (repo / "dyes" / "database.yaml").read_text() + "# a note\n"
    )
    [edited] = open_study_folder(repo / "tshirt-footprint").calc(bg)

    assert clean.provenance.study is not None
    assert (clean.provenance.study.saved, clean.provenance.study.commit) == (True, head)
    assert clean.provenance.study.changed is False
    assert edited.provenance.study is not None
    assert (edited.provenance.study.commit, edited.provenance.study.changed) == (head, True)


# --- what a study checks ----------------------------------------------------------------------


def write_study(folder: Path, cut_off: float = 0.03, **more: object) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    data = {
        "name": folder.name,
        **SCOPE,
        "products": [{"process": "tshirt:tshirt", "amount": 1}],
        "methods": ["toy-climate"],
        "cut_off": {"criteria": ["mass", "energy"], "max_cumulative": cut_off},
        **more,
    }
    (folder / "study.yaml").write_text(yaml.safe_dump(data, sort_keys=False))
    return folder


def test_a_study_refuses_a_database_whose_cut_off_limit_is_above_its_own(
    toy: Path, bg: hw.Background
) -> None:
    """The database `tshirt` of the cut-off case allows 5% and cuts off the tape, 3.85%."""
    strict = open_study_folder(write_study(toy / "tshirt-cut-off" / "strict", cut_off=0.03))
    lenient = open_study_folder(write_study(toy / "tshirt-cut-off" / "lenient", cut_off=0.05))

    problems = strict.validate(bg).errors
    assert [p.code for p in problems] == ["study-cut-off-limit"]
    assert "tshirt" in problems[0].message and "0.05" in problems[0].message
    with pytest.raises(InvalidDataError):
        strict.calc(bg)
    [result] = lenient.calc(bg)
    assert result.score == pytest.approx(1.19205, rel=REL)
    assert [c.line_id for c in result.provenance.cut_offs] == ["e04"]


def test_a_study_may_acknowledge_a_flow_of_a_database_it_reaches(
    toy: Path, bg: hw.Background
) -> None:
    """D26: a study adds no_factor.csv rows for its methods, for a flow of any database its
    products reach."""
    database = toy / "tshirt-no-factor" / "tshirt"
    study = write_study(toy / "tshirt-no-factor" / "footprint")
    (database / "no_factor.csv").rename(study / "no_factor.csv")

    assert [p.code for p in open_database(database).validate(bg).errors] == ["no-factor"]
    [result] = open_study_folder(study).calc(bg)
    assert result.score == pytest.approx(1.19205, rel=REL)
    assert [f.flow_id for f in result.not_characterised if f.in_database] == ["tshirt:cod"]


def test_a_study_holds_no_processes_and_a_database_no_scenarios(toy: Path) -> None:
    study = toy / "tshirt-dyed" / "tshirt-footprint"
    shutil.copytree(toy / "tshirt-dyed" / "tshirt" / "processes", study / "processes")
    with pytest.raises(InvalidDataError, match="holds no processes"):
        open_study_folder(study)

    database = toy / "tshirt" / "tshirt"
    (database / "scenarios").mkdir()
    (database / "scenarios" / "x.yaml").write_text(yaml.safe_dump(CLEANER_DYE))
    with pytest.raises(InvalidDataError, match="scenarios belong in a study"):
        open_study_folder(database)


def test_a_folder_is_a_database_or_a_study_not_both(toy: Path) -> None:
    folder = toy / "tshirt" / "tshirt"
    shutil.copy(DYED / "tshirt-footprint" / "study.yaml", folder / "study.yaml")

    with pytest.raises(InvalidDataError, match="both database.yaml and study.yaml"):
        open_study_folder(folder)


def test_a_product_in_no_database_and_not_in_the_background_names_both(
    toy: Path, bg: hw.Background
) -> None:
    """In a file, a study whose products are in no database must pin the release they are in;
    in memory, the product is looked for in the background, and the error names both places."""
    folder = write_study(toy / "tshirt" / "x", products=[{"process": "shirt:shirt", "amount": 1}])
    with pytest.raises(InvalidDataError) as read:
        open_study_folder(folder)
    memory = hw.Study(
        products={"shirt:shirt": 1}, methods=["toy-climate"], databases=toy / "tshirt"
    )
    with pytest.raises(InvalidDataError) as scored:
        memory.calc(bg)

    for raised in (read, scored):
        text = str(raised.value)
        assert "no database shirt" in text and "shirt:shirt" in text


def test_a_product_of_the_background_needs_no_database(bg: hw.Background) -> None:
    fabric = hw.Study(products={"toy:fabric-production": 2}, methods=["toy-climate"])

    [result] = fabric.calc(bg)

    assert result.score == pytest.approx(2 * 3.7086, rel=REL)


def test_a_product_of_the_background_is_scored_in_a_scenario_of_database_lines(
    bg: hw.Background,
) -> None:
    """`cleaner-dye` changes a line of the dyes database only: the fabric of the background
    keeps its score. A scenario that changes a background line is refused for it."""
    study = hw.Study(
        products={"tshirt:tshirt": 1, "toy:fabric-production": 2},
        methods=["toy-climate"],
        databases=DYED,
        scenarios={
            "cleaner-dye": CLEANER_DYE,
            "grid": {"changes": [{"process": "toy:fabric-production", "line": "e01", "amount": 1}]},
        },
    )

    tshirt, fabric = study.calc(bg, scenario="cleaner-dye")
    assert tshirt.score == pytest.approx(1.237646, rel=REL)
    assert fabric.score == pytest.approx(2 * 3.7086, rel=REL)
    with pytest.raises(InvalidDataError, match="changes background lines"):
        study.scores(bg, scenario="grid")


# --- input hash of a study -----------------------------------------------------------------------

# A study's result is hashed from its products and amounts, its methods, the scenario's content,
# its own no_factor.csv rows, the records of the databases it reaches and the background: never
# its name or its text, so a study in memory and the same one saved hash alike.
FOOTPRINT = "5e4c0be9ecbe3705425b6f80563528fd6362bddac69abc6a8501a17e140712c4"
FOOTPRINT_CLEANER_DYE = "8cce1e8085073437d6518afd00d1089f64ecddadd22fd7d7d8c2c03ba29d88a8"
CLEAN_GRID = "561d11f061e94a629da49f1724741bc5c1553cd35de5539fbd6b83fbcf3fe5fb"
"""The grid scenario, which a database held before studies did: its hash moved with it."""


def test_the_input_hashes_of_the_toy_study(bg: hw.Background) -> None:
    study = open_study_folder(DYED / "tshirt-footprint")

    assert study.calc(bg)[0].provenance.input_hash == FOOTPRINT
    assert study.calc(bg, scenario="cleaner-dye")[0].provenance.input_hash == (
        FOOTPRINT_CLEANER_DYE
    )
    grid = open_study_folder(TOY / "tshirt-grid-scenario" / "tshirt-footprint")
    assert grid.calc(bg, scenario="clean-grid")[0].provenance.input_hash == CLEAN_GRID


def test_a_product_of_a_database_keeps_the_hash_it_had_before_studies(bg: hw.Background) -> None:
    """S2a pinned it: studies do not move it (tests/test_databases.py)."""
    from tests.test_databases import DYED as DYED_HASH

    database = open_database(DYED / "tshirt")
    assert database.calc(bg, "tshirt:tshirt").provenance.input_hash == DYED_HASH


# --- schemas -------------------------------------------------------------------------------------


def test_the_schemas_validate_the_toy_files_and_suggest_their_process_ids(toy: Path) -> None:
    folder = toy / "tshirt-dyed"
    done = CliRunner().invoke(app, ["schema", str(folder)], catch_exceptions=False)
    assert done.exit_code == 0, done.output

    schemas = {
        kind: json.loads((folder / ".schemas" / f"{kind}.schema.json").read_text())
        for kind in ("database", "study", "scenario")
    }
    for path in sorted(folder.glob("*/database.yaml")):
        jsonschema.validate(yaml.safe_load(path.read_text()), schemas["database"])
    for path in sorted(folder.glob("*/study.yaml")):
        jsonschema.validate(yaml.safe_load(path.read_text()), schemas["study"])
    for path in sorted(folder.glob("*/scenarios/*.yaml")):
        jsonschema.validate(yaml.safe_load(path.read_text()), schemas["scenario"])

    text = json.dumps(schemas["study"])
    assert '"tshirt:tshirt"' in text and '"dyes:reactive-dye"' in text
    assert '"dyes:reactive-dye"' in json.dumps(schemas["scenario"])
    # suggestions, not a list to choose from: a product of the background is a study's too
    background_product = {
        **yaml.safe_load((folder / "tshirt-footprint" / "study.yaml").read_text())
    }
    background_product["products"] = [{"process": "toy:fabric-production", "amount": 1}]
    jsonschema.validate(background_product, schemas["study"])
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({**background_product, "goals": "x"}, schemas["study"])
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"name": "x", "background": "no pin"}, schemas["database"])


def test_the_schemas_of_every_toy_case_read_its_files(tmp_path: Path) -> None:
    for case in ("tshirt-grid-scenario", "tshirt-parameter", "tshirt-cut-off"):
        folder = tmp_path / case
        shutil.copytree(TOY / case, folder)
        assert CliRunner().invoke(app, ["schema", str(folder)]).exit_code == 0
        study = json.loads((folder / ".schemas" / "study.schema.json").read_text())
        scenario = json.loads((folder / ".schemas" / "scenario.schema.json").read_text())
        for path in folder.glob("*/study.yaml"):
            jsonschema.validate(yaml.safe_load(path.read_text()), study)
        for path in folder.glob("*/scenarios/*.yaml"):
            jsonschema.validate(yaml.safe_load(path.read_text()), scenario)
