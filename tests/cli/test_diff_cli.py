"""`headwater diff`: the score changes between two git refs, with the lines that caused them.

Exit test Y2 of M2: on the toy, in a git repository made inside the test, one changed line
moves the T-shirt from 1.19205 to a new score, and the diff names that line first.
"""

import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

import headwater as hw
from headwater.calc import CalculationError
from headwater.release import Release
from tests.conftest import TABLE_HEADER, add_process, write_tables

Invoke = Callable[..., tuple[int, str]]
Git = Callable[..., str]
Commit = Callable[[str], str]

TOY = Path(__file__).resolve().parent.parent / "fixtures" / "toy"
REL = 1e-9
CSV = "tshirt/tshirt/exchanges.csv"


def edit(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    assert old in text, f"{old!r} not found in {path}"
    path.write_text(text.replace(old, new, 1))


def lighter(repo: Path, commit: Commit) -> str:
    """Second commit: the fabric goes from 0.25 to 0.22 kg."""
    edit(repo / CSV, "e02,input,toy:cotton-fabric,0.25", "e02,input,toy:cotton-fabric,0.22")
    return commit("lighter T-shirt")


# --- Y2 ----------------------------------------------------------------------------------------


def test_y2_one_changed_line_moves_the_score_and_is_named(
    repo: Path, commit: Commit, git: Git, toy_args: list[str], invoke: Invoke
) -> None:
    git("switch", "--quiet", "--create", "lighter")
    lighter(repo, commit)

    code, output = invoke("diff", "main", "tshirt/tshirt", *toy_args)

    assert code == 0, output
    # 1.19205 - 0.03 × 3.7086 = 1.080792
    assert "Climate change: 1.19205 → 1.08079 kg CO2-eq, change -0.111258 (-9.3%)" in output
    drivers = output.split("Lines that differ")[1].strip().splitlines()[1:]
    assert drivers[0].strip().startswith("-0.111258")
    assert "tshirt:tshirt line e02" in drivers[0]
    assert "cotton fabric" in drivers[0]
    assert "amount 0.25 → 0.22 kg" in drivers[0]
    assert len([d for d in drivers if d.strip()]) == 1
    assert "Traceback" not in output


def test_the_header_names_the_database_the_refs_and_the_background(
    repo: Path, commit: Commit, git: Git, toy_args: list[str], invoke: Invoke
) -> None:
    first = git("rev-parse", "HEAD")
    lighter(repo, commit)

    _, output = invoke("diff", "HEAD~1", "tshirt/tshirt", *toy_args)

    lines = output.splitlines()
    assert lines[0] == f"Database tshirt/tshirt: HEAD~1 ({first[:12]}) → working tree"
    assert "toy-climate 1" in lines[1]
    assert str(TOY / "background") in lines[1]


# --- arguments ---------------------------------------------------------------------------------


def test_two_refs_compare_two_commits_whatever_the_working_tree_says(
    repo: Path, commit: Commit, toy_args: list[str], invoke: Invoke
) -> None:
    lighter(repo, commit)
    edit(repo / CSV, "0.22", "0.10")  # not committed: HEAD is compared, not the working tree

    code, output = invoke("diff", "HEAD~1", "HEAD", "tshirt/tshirt", *toy_args)

    assert code == 0, output
    assert "→ HEAD (" in output.splitlines()[0]
    assert "1.19205 → 1.08079" in output


def test_one_ref_compares_with_the_working_tree_uncommitted_changes_included(
    repo: Path, toy_args: list[str], invoke: Invoke
) -> None:
    edit(repo / CSV, "0.25", "0.10")

    code, output = invoke("diff", "main", "tshirt/tshirt", *toy_args)

    assert code == 0, output
    assert "→ working tree" in output.splitlines()[0]
    assert "1.19205 → 0.63576" in output  # 0.10 × 3.7086 + 0.5 × 0.5298


def test_the_study_may_be_given_by_an_absolute_path_from_another_folder(
    repo: Path, commit: Commit, toy_args: list[str], invoke: Invoke, monkeypatch: pytest.MonkeyPatch
) -> None:
    lighter(repo, commit)
    monkeypatch.chdir(repo / "tshirt")

    code, output = invoke("diff", "HEAD~1", repo / "tshirt" / "tshirt", *toy_args)

    assert code == 0, output
    assert "1.19205 → 1.08079" in output


def test_one_argument_or_four_is_a_usage_error(
    repo: Path, toy_args: list[str], invoke: Invoke
) -> None:
    one_code, one = invoke("diff", "tshirt/tshirt", *toy_args)
    four_code, four = invoke("diff", "a", "b", "c", "tshirt/tshirt", *toy_args)

    assert one_code == 2 and four_code == 2
    assert "REF" in one and "FOLDER" in one
    assert "REF" in four


# --- what the output says ----------------------------------------------------------------------


def test_no_change_exits_0_and_says_so(repo: Path, toy_args: list[str], invoke: Invoke) -> None:
    code, output = invoke("diff", "main", "tshirt/tshirt", *toy_args)

    assert code == 0, output
    assert "Climate change: 1.19205 kg CO2-eq, no change" in output
    assert "Lines that differ" not in output
    assert "No lines differ" in output


def test_differences_that_leave_the_score_alone_are_still_listed(
    repo: Path, toy_args: list[str], invoke: Invoke
) -> None:
    edit(repo / "tshirt/tshirt/processes.csv", "tshirt,T-shirt,", "tshirt,Polo shirt,")
    with (repo / "tshirt/tshirt/database.yaml").open("a") as handle:
        handle.write("description: Footprint of one T-shirt for the sales team\n")

    code, output = invoke("diff", "main", "tshirt/tshirt", *toy_args)

    assert code == 0, output
    assert "no change" in output
    assert "tshirt:tshirt: name T-shirt → Polo shirt" in output  # a process, scored 0
    assert "Other changes" in output
    assert "description" in output.split("Other changes")[1]


def test_changes_are_ranked_by_their_effect_not_by_their_place_in_the_file(
    repo: Path, commit: Commit, toy_args: list[str], invoke: Invoke
) -> None:
    edit(repo / CSV, "e02,input,toy:cotton-fabric,0.25", "e02,input,toy:cotton-fabric,0.24")
    edit(repo / CSV, "e03,input,toy:electricity,0.5", "e03,input,toy:electricity,1.5")
    commit("more electricity, a little less fabric")

    _, output = invoke("diff", "HEAD~1", "tshirt/tshirt", *toy_args)

    drivers = [d for d in output.split("Lines that differ")[1].splitlines()[1:] if d.strip()]
    assert "line e03" in drivers[0] and drivers[0].strip().startswith("+0.5298")
    assert "line e02" in drivers[1] and drivers[1].strip().startswith("-0.037086")


def test_what_the_changes_do_not_explain_is_shown_as_interaction(
    repo: Path, commit: Commit, toy_args: list[str], invoke: Invoke
) -> None:
    write_tables(
        repo / "tshirt/tshirt",
        {
            "tshirt": "e01,reference,tshirt:tshirt,1,unit,\n"
            "e02,input,tshirt:fabric,0.25,kg,tshirt:fabric",
            "fabric": "e01,reference,tshirt:fabric,1,kg,\n"
            "e02,input,toy:electricity,3,kWh,toy:electricity-production\n"
            "e03,emission,toy:co2,2.0,kg,",
        },
        details={
            "tshirt": "T-shirt,GLO,ordinary_transforming_activity",
            "fabric": "fabric,GLO,ordinary_transforming_activity",
        },
    )
    with (repo / "tshirt/tshirt/flows.csv").open("a") as handle:
        handle.write("tshirt:fabric,fabric,product,kg,,\n")
    commit("the study makes its own fabric")
    edit(repo / CSV, "tshirt:fabric,0.25,kg", "tshirt:fabric,0.5,kg")
    edit(repo / CSV, "toy:electricity,3,kWh", "toy:electricity,6,kWh")

    code, output = invoke("diff", "HEAD", "tshirt/tshirt", "--product", "tshirt:tshirt", *toy_args)
    _, every = invoke("diff", "HEAD", "tshirt/tshirt", *toy_args)

    assert code == 0, output
    assert "0.89735 → 2.5894" in output
    assert "+0.89735" in output and "+0.39735" in output
    assert "interaction between the changes" in output
    interaction = [line for line in output.splitlines() if "interaction" in line]
    assert interaction[0].strip().startswith("+0.39735")
    # with every product, the T-shirt's table has a column for it
    table = every.split(
        "Effect of each line alone on each score of T-shirt (tshirt:tshirt) that changed:"
    )[1]
    assert table.splitlines()[1].split() == ["1", "2", "interaction"]
    assert table.splitlines()[2].split()[-1] == "+0.39735"


def test_top_limits_the_lines_listed_and_counts_the_rest(
    repo: Path, commit: Commit, toy_args: list[str], invoke: Invoke
) -> None:
    edit(repo / CSV, "e02,input,toy:cotton-fabric,0.25", "e02,input,toy:cotton-fabric,0.22")
    edit(repo / CSV, "e03,input,toy:electricity,0.5", "e03,input,toy:electricity,0.45")

    _, output = invoke("diff", "main", "tshirt/tshirt", *toy_args, "--top", "1")

    assert "line e02" in output
    assert "line e03" not in output
    assert "1 more line differs" in output


# --- the background ----------------------------------------------------------------------------


def test_the_background_comes_from_the_pin_of_the_study(
    repo: Path, commit: Commit, toy_release: Release, invoke: Invoke
) -> None:
    edit(
        repo / "tshirt/tshirt/database.yaml",
        "toy-background@fixture",
        f"toy-background@{toy_release.hash[:12]}",
    )
    commit("pin the release")
    lighter(repo, commit)

    code, output = invoke("diff", "HEAD~1", "tshirt/tshirt")

    assert code == 0, output
    assert f"toy-background@{toy_release.hash[:12]}" in output.splitlines()[1]
    assert "1.19205 → 1.08079" in output


def test_a_pin_that_changes_to_a_release_not_in_the_cache_says_how_to_pull_it(
    repo: Path, commit: Commit, toy_release: Release, invoke: Invoke
) -> None:
    edit(
        repo / "tshirt/tshirt/database.yaml",
        "toy-background@fixture",
        f"toy-background@{toy_release.hash[:12]}",
    )
    commit("pin the release")
    edit(
        repo / "tshirt/tshirt/database.yaml",
        f"toy-background@{toy_release.hash[:12]}",
        "toy-background@ffffffffffff",
    )
    commit("move the pin")

    code, output = invoke("diff", "HEAD~1", "tshirt/tshirt")

    # each side is scored on the release it pins: tests/upgrade/test_cli.py diffs an upgrade
    assert code == 2
    assert "toy-background@ffffffffffff" in output
    assert "headwater release pull" in output
    assert "Traceback" not in output


def test_a_pin_that_names_no_release_says_to_pass_release(
    repo: Path, home: Path, invoke: Invoke
) -> None:
    code, output = invoke("diff", "main", "tshirt/tshirt")

    assert code == 2
    assert "toy-background@fixture" in output
    assert "--release" in output
    assert "Traceback" not in output


# --- errors exit 2 -----------------------------------------------------------------------------


def test_an_unknown_ref_exits_2(repo: Path, toy_args: list[str], invoke: Invoke) -> None:
    code, output = invoke("diff", "no-such-branch", "tshirt/tshirt", *toy_args)

    assert code == 2
    assert "no-such-branch" in output
    assert "Traceback" not in output


def test_a_database_that_is_not_at_the_base_is_new_and_counted(
    repo: Path, commit: Commit, toy_args: list[str], invoke: Invoke
) -> None:
    shutil.copytree(repo / "tshirt" / "tshirt", repo / "studies" / "polo")
    commit("a second database")

    code, output = invoke("diff", "HEAD~1", "studies/polo", *toy_args)

    assert code == 0, output
    assert output.splitlines()[-1] == (
        "New database: 1 product, not scored. `headwater calc studies/polo` scores them."
    )
    assert "Climate change" not in output
    assert "No lines differ" not in output
    assert "Lines that differ" not in output
    assert "Traceback" not in output


def test_a_study_that_does_not_validate_lists_its_errors_and_exits_1(
    repo: Path, commit: Commit, toy_args: list[str], invoke: Invoke
) -> None:
    edit(repo / CSV, "toy:fabric-production", "toy:nowhere")

    code, output = invoke("diff", "main", "tshirt/tshirt", *toy_args)

    assert code == 1
    assert "error: tshirt:tshirt line e02" in output
    assert "toy:nowhere" in output
    assert "Database tshirt: 1 error." in output
    assert "Climate change" not in output  # no scores
    assert "Traceback" not in output


def test_a_study_that_cannot_be_read_is_a_validation_error_and_exits_1(
    repo: Path, toy_args: list[str], invoke: Invoke
) -> None:
    edit(repo / CSV, "0.25", "lots")

    code, output = invoke("diff", "main", "tshirt/tshirt", *toy_args)

    assert code == 1
    assert "lots" in output
    assert "exchanges.csv:3" in output
    assert "Traceback" not in output


def test_a_base_that_cannot_be_read_exits_2_and_names_the_ref(
    repo: Path, commit: Commit, toy_args: list[str], invoke: Invoke
) -> None:
    edit(repo / CSV, "0.25", "lots")
    commit("not a number")
    edit(repo / CSV, "lots", "0.25")

    code, output = invoke("diff", "HEAD", "tshirt/tshirt", *toy_args)

    assert code == 2
    assert "cannot be read at HEAD" in output
    assert "lots" in output
    assert "Traceback" not in output


def test_the_background_is_loaded_once_whatever_the_study_needs(
    repo: Path,
    commit: Commit,
    toy_args: list[str],
    invoke: Invoke,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loads: list[str] = []
    real = hw.load

    def counting(path: Path | str, **kwargs: Any) -> hw.Background:
        loads.append(str(path))
        return real(path, **kwargs)

    monkeypatch.setattr(hw, "load", counting)
    lighter(repo, commit)

    code, output = invoke("diff", "HEAD~1", "tshirt/tshirt", *toy_args)

    assert code == 0, output
    assert len(loads) == 1  # to validate the study and to compare it


# --- JSON --------------------------------------------------------------------------------------

DIFF_KEYS = {
    "format",
    "path",
    "kind",
    "created",
    "old",
    "new",
    "engine_version",
    "solver",
    "background",
    "method",
    "tolerance",
    "results",
    "differences",
    "validation",
    "methods",
    "old_background",
    "processes",
    "products",
}
SIDE_KEYS = {"ref", "commit", "input_hash", "residual"}
RESULT_KEYS = {
    "product",
    "indicator",
    "old",
    "new",
    "change",
    "percent",
    "changed",
    "drivers",
    "interaction",
    "background",
    "moved",
}
DIFFERENCE_KEYS = {
    "id",
    "kind",
    "change",
    "item",
    "description",
    "text",
    "fields",
    "affects",
    "scored",
    "flow",
    "database",
}


def run_json(invoke_stdout: Invoke, *args: str | Path) -> dict[str, Any]:
    code, output = invoke_stdout("diff", *args, "--json")
    assert code == 0, output
    payload: dict[str, Any] = json.loads(output)
    return payload


def test_the_json_has_a_stable_shape(
    repo: Path, commit: Commit, git: Git, toy_args: list[str], invoke_stdout: Invoke
) -> None:
    first = git("rev-parse", "HEAD")
    lighter(repo, commit)

    payload = run_json(invoke_stdout, "HEAD~1", "tshirt/tshirt", *toy_args)

    assert set(payload) == DIFF_KEYS
    assert payload["format"] == "headwater-diff/2"
    assert (payload["path"], payload["kind"]) == ("tshirt/tshirt", "database")
    assert set(payload["old"]) == SIDE_KEYS and set(payload["new"]) == SIDE_KEYS
    assert payload["old"]["ref"] == "HEAD~1" and payload["old"]["commit"] == first
    assert payload["new"]["ref"] is None and payload["new"]["commit"] is None
    assert len(payload["old"]["input_hash"]) == 64
    assert payload["old"]["input_hash"] != payload["new"]["input_hash"]
    assert isinstance(payload["old"]["residual"], float)
    assert payload["engine_version"] == hw.__version__
    assert set(payload["solver"]) == {
        "solver",
        "ordering",
        "diag_pivot_thresh",
        "scaling",
        "tolerance",
        "condition_limit",
    }
    assert payload["solver"]["tolerance"] == 1e-12
    assert set(payload["background"]) == {"release", "folder"}
    assert payload["background"]["release"] is None
    assert payload["background"]["folder"] == str(TOY / "background")
    assert set(payload["method"]) == {"id", "version", "hash"}
    assert payload["method"]["id"] == "toy-climate"
    assert payload["methods"] == ["toy-climate"]
    assert payload["tolerance"] == 1e-9
    assert payload["created"] is False
    validation = payload["validation"]
    assert validation["format"] == "headwater-validate/2"
    assert validation["valid"] is True
    assert validation["errors"] == [] and validation["warnings"] == []
    assert (validation["path"], validation["kind"]) == ("tshirt/tshirt", "database")

    [result] = payload["results"]
    assert set(result) == RESULT_KEYS
    assert result["product"] == {
        "id": "tshirt:tshirt",
        "name": "T-shirt",
        "process": "T-shirt",
    }
    assert result["indicator"] == {
        "id": "climate-change",
        "name": "Climate change",
        "unit": "kg CO2-eq",
    }
    assert result["old"] == pytest.approx(1.19205, rel=REL)
    assert result["new"] == pytest.approx(1.080792, rel=REL)
    assert result["change"] == pytest.approx(-0.111258, rel=REL)
    assert result["percent"] == pytest.approx(-100 * 0.111258 / 1.19205, rel=REL)
    assert result["changed"] is True
    assert result["interaction"] == pytest.approx(0.0, abs=1e-12)
    [driver] = result["drivers"]
    assert set(driver) == {"difference", "contribution"}
    assert driver["contribution"] == pytest.approx(-0.111258, rel=REL)

    [difference] = payload["differences"]
    assert set(difference) == DIFFERENCE_KEYS
    assert difference["id"] == driver["difference"] == "line tshirt:tshirt line e02"
    assert difference["kind"] == "line"
    assert difference["change"] == "changed"
    assert difference["item"] == "tshirt:tshirt line e02"
    assert difference["description"] == "amount 0.25 → 0.22 kg"
    assert difference["database"] is None  # the database's own, not one it uses
    assert difference["fields"] == [{"name": "amount", "old": 0.25, "new": 0.22}]
    assert difference["affects"] == []
    assert difference["scored"] is True
    assert difference["flow"] == {"id": "toy:cotton-fabric", "name": "cotton fabric"}


def test_the_json_for_a_release_names_it_and_has_no_folder(
    repo: Path, commit: Commit, toy_release: Release, invoke_stdout: Invoke
) -> None:
    short = f"toy-background@{toy_release.hash[:12]}"
    edit(repo / "tshirt/tshirt/database.yaml", "toy-background@fixture", short)
    commit("pin the release")
    lighter(repo, commit)

    payload = run_json(invoke_stdout, "HEAD~1", "tshirt/tshirt", "--release", short)

    assert payload["background"] == {
        "release": f"toy-background@{toy_release.hash}",
        "folder": None,
    }


def test_percent_is_null_when_the_old_score_is_zero_and_nothing_is_nan(
    repo: Path, commit: Commit, toy_args: list[str], invoke_stdout: Invoke
) -> None:
    (repo / CSV).write_text(TABLE_HEADER + "tshirt,e01,reference,tshirt:tshirt,1,unit,\n")
    commit("an empty T-shirt")
    (repo / CSV).write_text(
        TABLE_HEADER + "tshirt,e01,reference,tshirt:tshirt,1,unit,\n"
        "tshirt,e03,input,toy:electricity,0.5,kWh,toy:electricity-production\n"
    )

    payload = run_json(invoke_stdout, "HEAD", "tshirt/tshirt", *toy_args)

    [result] = payload["results"]
    assert result["old"] == 0.0
    assert result["percent"] is None
    assert result["new"] == pytest.approx(0.2649, rel=REL)


def test_a_product_on_one_side_only_is_null_on_the_other(
    repo: Path, commit: Commit, toy_args: list[str], invoke_stdout: Invoke
) -> None:
    add_process(
        repo / "tshirt/tshirt",
        "polo",
        "e01,reference,tshirt:polo,1,unit,\ne02,input,toy:electricity,1,kWh,toy:electricity-production",
    )
    with (repo / "tshirt/tshirt/flows.csv").open("a") as handle:
        handle.write("tshirt:polo,polo,product,unit,,\n")

    payload = run_json(invoke_stdout, "HEAD", "tshirt/tshirt", *toy_args)

    # the T-shirt is a product of both sides, the polo of the new one only
    by_product = {r["product"]["id"]: r for r in payload["results"]}
    assert by_product["tshirt:tshirt"]["old"] == pytest.approx(1.19205, rel=REL)
    assert by_product["tshirt:tshirt"]["new"] == pytest.approx(1.19205, rel=REL)
    assert by_product["tshirt:tshirt"]["changed"] is False
    assert by_product["tshirt:polo"]["old"] is None
    assert by_product["tshirt:polo"]["change"] is None
    assert by_product["tshirt:polo"]["percent"] is None
    assert by_product["tshirt:polo"]["drivers"] == []
    assert by_product["tshirt:polo"]["interaction"] is None
    assert [(p["id"], p["status"], p["amount"]) for p in payload["products"]] == [
        ("tshirt:tshirt", "both", None),
        ("tshirt:polo", "new", None),
    ]


def test_the_json_for_an_error_is_not_printed_to_standard_output(
    repo: Path, toy_args: list[str], invoke_stdout: Invoke
) -> None:
    code, output = invoke_stdout("diff", "no-such-branch", "tshirt/tshirt", *toy_args, "--json")

    assert code == 2
    assert output == ""


def test_the_indicator_option_limits_the_results(
    repo: Path, commit: Commit, toy_args: list[str], invoke_stdout: Invoke
) -> None:
    lighter(repo, commit)

    payload = run_json(
        invoke_stdout, "HEAD~1", "tshirt/tshirt", *toy_args, "--indicator", "climate-change"
    )

    assert [r["indicator"]["id"] for r in payload["results"]] == ["climate-change"]


def test_the_indicator_option_takes_several(
    repo: Path, commit: Commit, tmp_path: Path, invoke_stdout: Invoke
) -> None:
    method = tmp_path / "three-indicators"
    shutil.copytree(TOY / "methods" / "toy-climate", method)
    edit(
        method / "method.yaml",
        "    unit: kg CO2-eq\n",
        "    unit: kg CO2-eq\n  - id: methane\n    name: Methane\n    unit: kg CH4\n"
        "  - id: carbon-dioxide\n    name: Carbon dioxide\n    unit: kg CO2\n",
    )
    with (method / "factors.csv").open("a") as handle:
        handle.write("methane,toy:ch4,,1\ncarbon-dioxide,toy:co2,,1\n")
    lighter(repo, commit)

    payload = run_json(
        invoke_stdout, "HEAD~1", "tshirt/tshirt", "--release", TOY / "background",
        "--method", method, "--namespace", "toy",
        "--indicator", "carbon-dioxide", "--indicator", "climate-change",
    )  # fmt: skip

    # in the method's order, whatever the order of the options
    assert [r["indicator"]["id"] for r in payload["results"]] == [
        "climate-change",
        "carbon-dioxide",
    ]


def test_a_score_change_that_no_line_explains_is_still_shown_as_interaction(
    repo: Path, toy_args: list[str], invoke: Invoke, monkeypatch: pytest.MonkeyPatch
) -> None:
    import headwater.diff._attribute as attribute

    edit(repo / CSV, "e02,input,toy:cotton-fabric,0.25", "e02,input,toy:cotton-fabric,0.22")
    monkeypatch.setattr(attribute, "find_differences", lambda old, new: ())

    code, output = invoke("diff", "main", "tshirt/tshirt", *toy_args)

    assert code == 0, output
    assert "change -0.111258" in output
    assert "interaction between the changes" in output
    assert output.split("interaction")[0].strip().endswith("-0.111258")


def test_the_scores_of_several_indicators_are_aligned_rows(
    repo: Path, commit: Commit, tmp_path: Path, invoke: Invoke
) -> None:
    method = tmp_path / "two-indicators"
    shutil.copytree(TOY / "methods" / "toy-climate", method)
    edit(
        method / "method.yaml",
        "    unit: kg CO2-eq\n",
        "    unit: kg CO2-eq\n  - id: methane\n    name: Methane\n    unit: kg CH4\n",
    )
    with (method / "factors.csv").open("a") as handle:
        handle.write("methane,toy:ch4,,1\n")
    lighter(repo, commit)

    _, output = invoke(
        "diff", "HEAD~1", "tshirt/tshirt", "--release", TOY / "background",
        "--method", method, "--namespace", "toy",
    )  # fmt: skip

    scores = output.split("Lines that differ")[0]
    rows = [line for line in scores.splitlines() if line.startswith(("  Climate", "  Methane"))]
    assert len(rows) == 2
    assert rows[0].index("1.19205") == rows[1].index("0.00225")
    assert rows[0].index("→") == rows[1].index("→")
    assert rows[0].index("change -") == rows[1].index("change -")


def add_wind_override(repo: Path) -> None:
    """The local electricity override of the toy: 0.3 kg CO2 per kWh instead of 0.5."""
    shutil.copytree(
        TOY / "tshirt-local-electricity" / "tshirt" / "overrides",
        repo / "tshirt" / "tshirt" / "overrides",
    )


def test_a_local_override_moves_the_score_by_its_own_row(
    repo: Path, commit: Commit, git: Git, toy_args: list[str], invoke: Invoke
) -> None:
    git("switch", "--quiet", "--create", "wind")
    add_wind_override(repo)
    commit("wind power for the electricity")

    code, output = invoke("diff", "main", "tshirt/tshirt", *toy_args)

    assert code == 0, output
    assert "Climate change: 1.19205 → 1.09205 kg CO2-eq" in output  # docs/design.md: local override
    section = output.split("Lines that differ")[1].strip().splitlines()[1:]
    drivers = [line for line in section if line.strip()]
    assert len(drivers) == 1
    assert drivers[0].strip().startswith("-0.1")  # 0.5 kWh × 0.2 kg CO2 less
    assert "override of toy:electricity-production line e02" in drivers[0]
    assert "added: emission 0.3 kg of toy:co2" in drivers[0]
    assert "copy" not in output  # the study's copy of the process is not a difference


# --- the flow of a difference, and the rows of no_factor.csv -----------------------------------


def test_a_difference_names_the_flow_of_its_line_and_a_parameter_has_none(
    repo: Path, commit: Commit, toy_args: list[str], invoke_stdout: Invoke
) -> None:
    shutil.rmtree(repo / "tshirt/tshirt")
    shutil.copytree(TOY / "tshirt-parameter" / "tshirt", repo / "tshirt/tshirt")
    commit("a study with a parameter")
    edit(repo / "tshirt/tshirt/parameters.yaml", "value: 0.25", "value: 0.30")
    edit(repo / CSV, "toy:electricity,0.5", "toy:electricity,0.4")

    payload = run_json(invoke_stdout, "HEAD", "tshirt/tshirt", *toy_args)

    flows = {d["kind"]: d["flow"] for d in payload["differences"]}
    assert flows["parameter"] is None
    assert flows["line"] == {"id": "toy:electricity", "name": "electricity"}


def test_a_changed_no_factor_reason_is_listed_and_moves_no_score(
    repo: Path, commit: Commit, toy_args: list[str], invoke: Invoke
) -> None:
    shutil.rmtree(repo / "tshirt/tshirt")
    shutil.copytree(TOY / "tshirt-no-factor" / "tshirt", repo / "tshirt/tshirt")
    commit("a study that emits COD")
    edit(
        repo / "tshirt/tshirt/no_factor.csv",
        "not characterised in toy-climate",
        "below 1% of the footprint",
    )

    code, output = invoke("diff", "HEAD", "tshirt/tshirt", *toy_args)

    assert code == 0, output
    assert "Climate change: 1.19205 kg CO2-eq, no change" in output
    assert "Other changes, not part of the score:" in output
    assert (
        "no_factor.csv row tshirt:cod (COD, Chemical Oxygen Demand): for method toy-climate: "
        "reason not characterised in toy-climate → below 1% of the footprint"
    ) in output
    assert "Traceback" not in output


# --- validation and a new study in the JSON ---------------------------------------------------


def test_the_json_carries_the_warnings_of_the_validation(
    repo: Path, commit: Commit, toy_args: list[str], invoke_stdout: Invoke
) -> None:
    lighter(repo, commit)
    (repo / "tshirt/tshirt/no_factor.csv").write_text(
        "flow,flow_name,method,reason,who,when\ntoy:co2,,toy-climate,thought so,Sam,2026-10-12\n"
    )

    payload = run_json(invoke_stdout, "HEAD~1", "tshirt/tshirt", *toy_args)

    assert payload["validation"]["valid"] is True
    [warning] = payload["validation"]["warnings"]
    assert warning["code"] == "no-factor-not-needed"
    assert payload["results"][0]["changed"] is True  # the warning does not stop the diff


def test_a_study_that_does_not_validate_has_its_validation_and_no_scores(
    repo: Path, toy_args: list[str], invoke_stdout: Invoke
) -> None:
    edit(repo / CSV, "toy:fabric-production", "toy:nowhere")

    code, output = invoke_stdout("diff", "main", "tshirt/tshirt", *toy_args, "--json")

    assert code == 1
    payload = json.loads(output)
    assert set(payload) == {"format", "path", "kind", "validation"}
    assert payload["format"] == "headwater-diff/2"
    assert payload["validation"]["valid"] is False
    [error] = payload["validation"]["errors"]
    assert error["code"] == "supplier-not-found"
    assert "toy:nowhere" in error["text"]


def test_a_study_that_cannot_be_read_has_validation_errors_and_no_background(
    repo: Path, toy_args: list[str], invoke_stdout: Invoke
) -> None:
    edit(repo / CSV, "0.25", "lots")

    code, output = invoke_stdout("diff", "main", "tshirt/tshirt", *toy_args, "--json")

    assert code == 1
    validation = json.loads(output)["validation"]
    assert validation["valid"] is False
    assert validation["background"] is None and validation["method"] is None
    assert any("lots" in e["text"] for e in validation["errors"])


def test_a_database_that_is_not_at_the_base_says_so_in_the_json(
    repo: Path, commit: Commit, git: Git, toy_args: list[str], invoke_stdout: Invoke
) -> None:
    base = git("rev-parse", "HEAD")
    shutil.copytree(repo / "tshirt" / "tshirt", repo / "studies" / "polo")
    commit("a second database")

    payload = run_json(invoke_stdout, "HEAD~1", "studies/polo", *toy_args)

    assert set(payload) == DIFF_KEYS
    assert payload["created"] is True
    assert payload["old"] == {"ref": "HEAD~1", "commit": base, "input_hash": None, "residual": None}
    assert payload["new"] == {"ref": None, "commit": None, "input_hash": None, "residual": None}
    assert payload["validation"]["valid"] is True
    assert payload["differences"] == [] and payload["results"] == []
    assert [(p["id"], p["status"]) for p in payload["products"]] == [("tshirt:tshirt", "new")]


def test_a_database_that_is_not_at_the_base_is_scored_for_the_products_given(
    repo: Path, commit: Commit, toy_args: list[str], invoke_stdout: Invoke
) -> None:
    shutil.copytree(repo / "tshirt" / "tshirt", repo / "studies" / "polo")
    commit("a second database")

    payload = run_json(
        invoke_stdout, "HEAD~1", "studies/polo", "--product", "tshirt:tshirt", *toy_args
    )

    assert payload["created"] is True
    assert len(payload["new"]["input_hash"]) == 64
    [result] = payload["results"]
    assert result["old"] is None
    assert result["new"] == pytest.approx(1.19205, rel=REL)
    assert result["change"] is None and result["percent"] is None
    assert result["changed"] is False
    assert result["drivers"] == [] and result["interaction"] is None


def test_a_new_database_that_cannot_be_calculated_exits_2_with_its_message(
    repo: Path,
    commit: Commit,
    toy_args: list[str],
    invoke: Invoke,
    invoke_stdout: Invoke,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shutil.copytree(repo / "tshirt" / "tshirt", repo / "studies" / "polo")
    commit("a second database")

    def refuses(*args: object, **kwargs: object) -> hw.Result:
        raise CalculationError("the matrix cannot be solved: tshirt:tshirt")

    monkeypatch.setattr(hw.Database, "_calc", refuses)  # what a diff calls: it names no commit
    product = ["--product", "tshirt:tshirt"]

    code, stdout = invoke_stdout("diff", "HEAD~1", "studies/polo", *product, *toy_args, "--json")
    _, both = invoke("diff", "HEAD~1", "studies/polo", *product, *toy_args, "--json")

    assert code == 2
    assert stdout == ""
    assert "working tree" in both.splitlines()[0]
    assert "the matrix cannot be solved" in both


def second_method(toy: Path) -> Path:
    """A method of its own, `toy-methane`, whose one indicator is methane."""
    folder = toy / "methods" / "toy-methane"
    shutil.copytree(TOY / "methods" / "toy-climate", folder)
    edit(folder / "method.yaml", "id: toy-climate", "id: toy-methane")
    edit(folder / "method.yaml", "id: climate-change", "id: methane")
    edit(folder / "method.yaml", "name: Climate change", "name: Methane")
    (folder / "factors.csv").write_text("indicator,flow,location,factor\nmethane,toy:ch4,,1\n")
    return folder


def test_several_methods_are_listed_by_id_and_the_joined_id_is_not_written(
    repo: Path, commit: Commit, toy: Path, toy_args: list[str], invoke_stdout: Invoke
) -> None:
    edit(
        repo / "tshirt/tshirt/database.yaml",
        "methods: [toy-climate]",
        "methods: [toy-climate, toy-methane]",
    )
    commit("two methods")
    lighter(repo, commit)
    toy_methods = TOY / "methods" / "toy-climate"
    args = [
        "--release", str(TOY / "background"), "--namespace", "toy",
        "--method", str(toy_methods), "--method", str(second_method(toy)),
    ]  # fmt: skip

    payload = run_json(invoke_stdout, "HEAD~1", "tshirt/tshirt", *args)

    assert payload["method"] is None
    assert payload["methods"] == ["toy-climate", "toy-methane"]
    assert payload["validation"]["method"] is None
    assert payload["validation"]["methods"] == ["toy-climate", "toy-methane"]
    assert "+" not in json.dumps(payload["method"]) + json.dumps(payload["methods"])
    assert [r["indicator"]["id"] for r in payload["results"]] == ["climate-change", "methane"]


# --- every product -----------------------------------------------------------------------------


def add_bag(repo: Path, kwh: float = 2) -> None:
    """A bag the T-shirt study makes from `kwh` of grid electricity: nothing in it buys the bag, so
    its score is not part of the T-shirt's."""
    add_process(
        repo / "tshirt/tshirt",
        "bag",
        f"e01,reference,tshirt:bag,1,kg,\ne02,input,toy:electricity,{kwh},kWh,"
        "toy:electricity-production",
    )
    with (repo / "tshirt/tshirt/flows.csv").open("a") as handle:
        handle.write("tshirt:bag,bag,product,kg,,\n")


@pytest.fixture
def heavier_bag(repo: Path, commit: Commit, git: Git) -> None:
    """`main` has the T-shirt and a bag of 2 kWh, the branch `heavier-bag` a bag of 3 kWh."""
    add_bag(repo)
    commit("a bag")
    git("switch", "--quiet", "--create", "heavier-bag")
    edit(repo / CSV, "toy:electricity,2,", "toy:electricity,3,")
    commit("heavier bag")


@pytest.mark.usefixtures("heavier_bag")
def test_a_change_to_one_product_is_shown_and_the_others_are_counted(
    toy_args: list[str], invoke: Invoke
) -> None:
    code, output = invoke("diff", "main", "tshirt/tshirt", *toy_args)

    assert code == 0, output
    assert "1 other product, no change" in output
    assert "bag (tshirt:bag)\n  Climate change: 1.0596 → 1.5894 kg CO2-eq, change +0.5298" in output
    assert output.count("tshirt:bag line e02") == 1
    assert "+0.5298" in output.split("Effect of each line alone")[1]


def test_a_product_is_named_by_its_process_as_well(
    repo: Path, commit: Commit, git: Git, toy_args: list[str], invoke: Invoke,
    invoke_stdout: Invoke,
) -> None:  # fmt: skip
    add_bag(repo)
    edit(repo / "tshirt/tshirt/processes.csv", "bag,bag,GLO,", "bag,bag sewing,GLO,")
    commit("a bag, made by bag sewing")
    git("switch", "--quiet", "--create", "heavier-bag")
    edit(repo / CSV, "toy:electricity,2,", "toy:electricity,3,")
    edit(repo / "tshirt/tshirt/processes.csv", "bag,bag sewing,", "bag,bag stitching,")
    commit("heavier bag, renamed")

    code, output = invoke("diff", "main", "tshirt/tshirt", *toy_args)
    payload = run_json(invoke_stdout, "main", "tshirt/tshirt", *toy_args)

    assert code == 0, output
    # the new side names the process; the T-shirt's process has the name of its product
    assert "bag stitching | bag (tshirt:bag)\n  Climate change: 1.0596 → 1.5894" in output
    assert "1 other product, no change" in output
    products = {p["id"]: p for p in payload["products"]}
    assert products["tshirt:bag"]["name"] == "bag"  # unchanged: the product's name
    assert products["tshirt:bag"]["process"] == "bag stitching"
    assert products["tshirt:tshirt"]["process"] == "T-shirt"
    [bag] = [r for r in payload["results"] if r["product"]["id"] == "tshirt:bag"]
    assert bag["product"] == {"id": "tshirt:bag", "name": "bag", "process": "bag stitching"}


@pytest.mark.usefixtures("heavier_bag")
def test_product_limits_the_products_scored(toy_args: list[str], invoke: Invoke) -> None:
    code, only_shirt = invoke(
        "diff", "main", "tshirt/tshirt", "--product", "tshirt:tshirt", *toy_args
    )
    again, only_bag = invoke("diff", "main", "tshirt/tshirt", "--product", "tshirt:bag", *toy_args)
    both, shown = invoke(
        "diff", "main", "tshirt/tshirt", "--product", "tshirt:bag", "--product", "tshirt:tshirt",
        *toy_args,
    )  # fmt: skip

    assert (code, again, both) == (0, 0, 0)
    # one product is shown as a study of one product always was
    assert "Climate change: 1.19205 kg CO2-eq, no change" in only_shirt
    assert "bag (tshirt:bag)" not in only_shirt
    assert "+0  tshirt:bag line e02" in only_shirt  # the line is listed, with no effect on it
    assert "bag (tshirt:bag)\n  Climate change: 1.0596 → 1.5894" in only_bag
    assert "T-shirt" not in only_bag and "other product" not in only_bag
    assert "1 other product, no change" in shown
    assert "bag (tshirt:bag)\n  Climate change: 1.0596 → 1.5894" in shown


@pytest.mark.usefixtures("heavier_bag")
def test_a_product_the_study_does_not_make_exits_2_and_lists_the_ones_it_does(
    toy_args: list[str], invoke: Invoke
) -> None:
    code, output = invoke("diff", "main", "tshirt/tshirt", "--product", "tshirt:bagg", *toy_args)

    assert code == 2
    assert "There is no process 'tshirt:bagg'" in output
    assert "the id of its process: tshirt:bag, tshirt:tshirt" in output
    assert "Traceback" not in output


@pytest.mark.usefixtures("heavier_bag")
def test_the_json_lists_every_product_scored_and_only_those_given(
    toy_args: list[str], invoke_stdout: Invoke
) -> None:
    every = run_json(invoke_stdout, "main", "tshirt/tshirt", *toy_args)
    one = run_json(invoke_stdout, "main", "tshirt/tshirt", "--product", "tshirt:bag", *toy_args)

    assert [(p["id"], p["status"]) for p in every["products"]] == [
        ("tshirt:bag", "both"),
        ("tshirt:tshirt", "both"),
    ]
    assert [r["product"]["id"] for r in every["results"]] == ["tshirt:bag", "tshirt:tshirt"]
    assert [p["id"] for p in one["products"]] == ["tshirt:bag"]
    assert [r["product"]["id"] for r in one["results"]] == ["tshirt:bag"]


def test_a_new_database_is_counted_unless_products_are_given(
    repo: Path, commit: Commit, toy_args: list[str], invoke: Invoke
) -> None:
    add_bag(repo)
    shutil.copytree(repo / "tshirt" / "tshirt", repo / "studies" / "polo")
    commit("a second database, with a bag")

    code, output = invoke("diff", "HEAD~1", "studies/polo", *toy_args)
    _, asked = invoke("diff", "HEAD~1", "studies/polo", "--product", "tshirt:tshirt", *toy_args)

    assert code == 0, output
    assert "New database: 2 products, not scored." in output
    assert "Climate change" not in output
    assert "Climate change: 1.19205 kg CO2-eq, only in working tree" in asked
    assert "bag (" not in asked
