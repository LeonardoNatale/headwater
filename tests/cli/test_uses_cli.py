"""The commands on a database that uses another: validate, calc, contributions, diff, database
uses. tests/cli/test_database_and_study_cli.py has the studies that reach a database."""

import json
import shutil
from pathlib import Path

import pytest

TOY = Path(__file__).resolve().parent.parent / "fixtures" / "toy"
DYE_CSV = "tshirt-dyed/dyes/exchanges.csv"


def edit(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    assert old in text, f"{old!r} not found in {path}"
    path.write_text(text.replace(old, new, 1))


@pytest.fixture
def dyed(repo: Path, commit) -> Path:
    """The databases `dyes` and `tshirt`, without the studies of the fixture."""
    for name in ("dyes", "tshirt"):
        shutil.copytree(TOY / "tshirt-dyed" / name, repo / "tshirt-dyed" / name)
    commit("the dyed T-shirt and its dyes")
    return repo


def test_validate_and_calc_resolve_the_used_study(invoke, toy_args: list[str]) -> None:
    study = TOY / "tshirt-dyed" / "tshirt"

    code, output = invoke("validate", study, *toy_args)
    assert code == 0, output
    assert "Database tshirt: valid." in output

    code, output = invoke("calc", study, *toy_args)
    assert code == 0, output
    assert "1.24265" in output
    assert "  uses: dyes\n" in output


def test_explain_shows_the_dye_line(invoke, toy_args: list[str]) -> None:
    code, output = invoke("contributions", TOY / "tshirt-dyed" / "tshirt", *toy_args)

    assert code == 0, output
    assert "0.01 kg of reactive dye production (dyes:reactive-dye): 0.05060 (4.1%)" in output


def test_diff_names_the_changed_line_of_the_used_study(
    dyed: Path, commit, git, invoke, toy_args: list[str]
) -> None:
    git("switch", "--quiet", "--create", "darker")
    edit(dyed / DYE_CSV, "toy:co2,4,", "toy:co2,4.5,")
    commit("more CO2 in the dye")

    code, output = invoke("diff", "main", "tshirt-dyed/tshirt", *toy_args)

    assert code == 0, output
    assert "Climate change: 1.24265 → 1.24765 kg CO2-eq, change +0.005 (+0.4%)" in output
    drivers = output.split("Lines that differ")[1].strip().splitlines()[1:]
    assert drivers[0].strip().startswith("+0.005")
    assert "dyes:reactive-dye line e03" in drivers[0]
    assert "amount 4 → 4.5 kg" in drivers[0]


def test_diff_json_says_which_database_a_difference_is_in(
    dyed: Path, commit, git, invoke_stdout, toy_args: list[str]
) -> None:
    git("switch", "--quiet", "--create", "darker")
    edit(dyed / DYE_CSV, "toy:co2,4,", "toy:co2,4.5,")
    commit("more CO2 in the dye")

    code, output = invoke_stdout("diff", "main", "HEAD", "tshirt-dyed/tshirt", *toy_args, "--json")

    assert code == 0, output
    [difference] = json.loads(output)["differences"]
    assert difference["id"] == "line dyes:reactive-dye line e03"
    assert difference["database"] == "dyes"


def test_database_uses_lists_the_databases_that_use_a_database(dyed: Path, invoke_stdout) -> None:
    code, output = invoke_stdout("database", "uses", "tshirt-dyed/dyes", "tshirt/tshirt", "--json")

    assert code == 0, output
    data = json.loads(output)
    assert data["format"] == "headwater-uses/2"
    assert data["databases"] == [
        {
            "path": "tshirt-dyed/dyes",
            "users": [{"path": "tshirt-dyed/tshirt", "kind": "database", "through": ["dyes"]}],
            "errors": [],
        },
        {"path": "tshirt/tshirt", "users": [], "errors": []},
    ]


def test_database_uses_follows_the_chain(dyed: Path, invoke, write_study) -> None:
    write_study(
        dyed / "tshirt-dyed" / "chemicals",
        {"salt": "e01,reference,chemicals:salt,1,kg,"},
        flows=["chemicals:salt,sodium chloride,product,kg,,"],
    )
    edit(
        dyed / "tshirt-dyed" / "dyes" / "database.yaml",
        "background: toy-background@fixture",
        "background: toy-background@fixture\nuses: [chemicals]",
    )

    code, output = invoke("database", "uses", "tshirt-dyed/chemicals")

    assert code == 0, output
    assert output.splitlines() == [
        "tshirt-dyed/chemicals is reached by:",
        "  tshirt-dyed/dyes (database, uses chemicals)",
        "  tshirt-dyed/tshirt (database, uses dyes, which uses chemicals)",
    ]


def test_database_uses_reads_a_ref(dyed: Path, commit, invoke_stdout) -> None:
    edit(dyed / "tshirt-dyed" / "tshirt" / "database.yaml", "uses: [dyes]", "uses: []")

    code, output = invoke_stdout("database", "uses", "tshirt-dyed/dyes", "--ref", "HEAD", "--json")

    assert code == 0, output
    [database] = json.loads(output)["databases"]
    assert [u["path"] for u in database["users"]] == ["tshirt-dyed/tshirt"]


def test_a_removed_database_can_still_have_users(dyed: Path, git, commit, invoke) -> None:
    git("rm", "--quiet", "-r", "tshirt-dyed/dyes")
    commit("no more dyes")

    code, output = invoke("database", "uses", "tshirt-dyed/dyes", "--ref", "HEAD")

    assert code == 0, output
    assert "tshirt-dyed/tshirt (database, uses dyes)" in output


def test_database_uses_says_when_a_database_yaml_does_not_read(dyed: Path, invoke_stdout) -> None:
    polo = dyed / "tshirt-dyed" / "polo"
    shutil.copytree(dyed / "tshirt-dyed" / "tshirt", polo)
    (polo / "database.yaml").write_text("name: [\n")

    code, output = invoke_stdout("database", "uses", "tshirt-dyed/dyes", "--json")

    assert code == 0, output
    [database] = json.loads(output)["databases"]
    assert [u["path"] for u in database["users"]] == ["tshirt-dyed/tshirt"]
    [error] = database["errors"]
    assert (error["path"], error["file"]) == ("tshirt-dyed/polo", "database.yaml")


def test_database_uses_says_when_nothing_reaches_a_database(dyed: Path, invoke) -> None:
    code, output = invoke("database", "uses", "tshirt/tshirt")

    assert code == 0, output
    assert output.splitlines() == ["tshirt/tshirt: no database or study reaches it."]


def test_database_uses_at_an_unknown_ref_fails(dyed: Path, invoke) -> None:
    code, output = invoke("database", "uses", "tshirt-dyed/dyes", "--ref", "nope")

    assert code == 2
    assert "nope" in output


def test_database_uses_writes_the_users_from_where_the_database_was_given(
    dyed: Path, invoke, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(dyed / "tshirt-dyed" / "dyes")

    code, output = invoke("database", "uses", ".")

    assert code == 0, output
    assert output.splitlines() == [". is reached by:", "  ../tshirt (database, uses dyes)"]


def test_a_database_yaml_that_is_a_link_in_git_is_an_error(
    dyed: Path, commit, invoke_stdout
) -> None:
    polo = dyed / "tshirt-dyed" / "polo"
    shutil.copytree(dyed / "tshirt-dyed" / "tshirt", polo)
    yaml = polo / "database.yaml"
    yaml.unlink()
    yaml.symlink_to("../tshirt/database.yaml")
    commit("a linked database.yaml")

    code, output = invoke_stdout("database", "uses", "tshirt-dyed/dyes", "--ref", "HEAD", "--json")

    assert code == 0, output
    [database] = json.loads(output)["databases"]
    [error] = database["errors"]
    assert error["path"] == "tshirt-dyed/polo"
    assert "symbolic link" in error["message"]
