"""Finding the studies a pull request changed, and running the command line on each of them."""

import json
import shlex
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from headwater.release import find

REL = 1e-9
Git = Callable[..., str]
Commit = Callable[[str], str]
FABRIC = "studies/tshirt/tshirt/processes/tshirt.csv"


def edit(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    assert old in text, f"{old!r} not found in {path}"
    path.write_text(text.replace(old, new, 1))


def lighter(repo: Path, git: Git, commit: Commit) -> None:
    """A branch off `main` that takes the fabric from 0.25 to 0.22 kg."""
    git("switch", "--quiet", "--create", "lighter")
    edit(repo / FABRIC, "toy:cotton-fabric,0.25", "toy:cotton-fabric,0.22")
    commit("lighter T-shirt")


def paths(changes: list[Any]) -> dict[str, str]:
    return {c.path: c.status for c in changes}


# --- which studies ---------------------------------------------------------------------------


def test_a_study_is_changed_when_a_file_in_its_folder_is_edited(
    collect: ModuleType, repo: Path, git: Git, commit: Commit
) -> None:
    lighter(repo, git, commit)

    found = collect.changed_folders(repo, "main", "HEAD")

    assert paths(found) == {"studies/tshirt/tshirt": "changed"}


def test_a_file_in_a_subfolder_of_a_study_counts(
    collect: ModuleType, repo: Path, git: Git, commit: Commit
) -> None:
    git("switch", "--quiet", "--create", "wind")
    edit(
        repo / "studies/tshirt-local-electricity/tshirt/overrides/own-wind-power.csv",
        "emission,toy:co2,0.3",
        "emission,toy:co2,0.2",
    )
    commit("windier")

    assert paths(collect.changed_folders(repo, "main", "HEAD")) == {
        "studies/tshirt-local-electricity/tshirt": "changed"
    }


def test_files_outside_any_study_are_not_studies(
    collect: ModuleType, repo: Path, git: Git, commit: Commit
) -> None:
    git("switch", "--quiet", "--create", "docs")
    (repo / "README.md").write_text("Changed.\n")
    commit("readme")

    assert collect.changed_folders(repo, "main", "HEAD") == []


def test_a_study_added_is_new_and_a_study_deleted_is_removed(
    collect: ModuleType, repo: Path, git: Git, commit: Commit
) -> None:
    git("switch", "--quiet", "--create", "reshuffle")
    copy = repo / "studies/tshirt-copy/tshirt"
    (copy / "processes").mkdir(parents=True)
    for f in ("database.yaml", "flows.csv"):
        (copy / f).write_text((repo / "studies/tshirt/tshirt" / f).read_text())
    for f in ("tshirt.csv", "tshirt.yaml"):
        text = (repo / "studies/tshirt/tshirt/processes" / f).read_text()
        (copy / "processes" / f).write_text(text)
    git("rm", "--quiet", "-r", "studies/tshirt-local-electricity/tshirt")
    commit("copy one, delete one")

    assert paths(collect.changed_folders(repo, "main", "HEAD")) == {
        "studies/tshirt-copy/tshirt": "new",
        "studies/tshirt-local-electricity/tshirt": "removed",
    }


def test_a_renamed_study_folder_is_one_removed_and_one_new(
    collect: ModuleType, repo: Path, git: Git, commit: Commit
) -> None:
    git("switch", "--quiet", "--create", "rename")
    git("mv", "studies/tshirt", "studies/classic-tshirt")
    commit("rename")

    assert paths(collect.changed_folders(repo, "main", "HEAD")) == {
        "studies/classic-tshirt/tshirt": "new",
        "studies/tshirt/tshirt": "removed",
    }


def test_under_limits_the_search_to_a_folder_by_whole_path_parts(
    collect: ModuleType, repo: Path, git: Git, commit: Commit
) -> None:
    git("switch", "--quiet", "--create", "both")
    edit(repo / FABRIC, "toy:cotton-fabric,0.25", "toy:cotton-fabric,0.22")
    edit(
        repo / "studies/tshirt-local-electricity/tshirt/processes/tshirt.csv",
        "toy:cotton-fabric,0.25",
        "toy:cotton-fabric,0.22",
    )
    commit("both lighter")

    both = {
        "studies/tshirt/tshirt": "changed",
        "studies/tshirt-local-electricity/tshirt": "changed",
    }
    assert paths(collect.changed_folders(repo, "main", "HEAD")) == both
    assert paths(collect.changed_folders(repo, "main", "HEAD", under="studies")) == both
    assert paths(collect.changed_folders(repo, "main", "HEAD", under="./studies/")) == both
    only = collect.changed_folders(repo, "main", "HEAD", under="studies/tshirt/tshirt")
    assert paths(only) == {"studies/tshirt/tshirt": "changed"}
    assert collect.changed_folders(repo, "main", "HEAD", under="elsewhere") == []


# --- running the command line ------------------------------------------------------------------


def test_a_changed_study_is_validated_and_diffed(
    collect: ModuleType, headwater: list[str], repo: Path, git: Git, commit: Commit
) -> None:
    lighter(repo, git, commit)

    report = collect.collect_report(repo, headwater, "main", "HEAD")

    assert report["format"] == "headwater-ci/2"
    [study] = report["folders"]
    assert study["path"] == "studies/tshirt/tshirt"
    assert study["status"] == "changed"
    assert study["ok"] is True
    assert study["validation"]["exit_code"] == 0
    assert study["validation"]["errors"] == []
    assert study["diff_error"] is None
    diff = study["diff"]
    assert diff["format"] == "headwater-diff/2"
    [row] = diff["results"]
    assert row["old"] == pytest.approx(1.19205, rel=REL)
    assert row["new"] == pytest.approx(1.080792, rel=REL)
    assert row["drivers"][0]["difference"] == "line tshirt:tshirt line e02"
    assert report["base"]["commit"] == git("rev-parse", "main")
    assert report["head"]["commit"] == git("rev-parse", "HEAD")
    json.dumps(report, allow_nan=False)


def test_the_diff_is_made_between_the_commits_not_the_working_tree(
    collect: ModuleType, headwater: list[str], repo: Path, git: Git, commit: Commit
) -> None:
    lighter(repo, git, commit)
    edit(repo / FABRIC, "toy:cotton-fabric,0.22", "toy:cotton-fabric,0.99")  # never committed

    [study] = collect.collect_report(repo, headwater, "main", "HEAD")["folders"]

    assert study["diff"]["results"][0]["new"] == pytest.approx(1.080792, rel=REL)


def test_a_study_that_does_not_validate_is_not_diffed_and_is_not_ok(
    collect: ModuleType, headwater: list[str], repo: Path, git: Git, commit: Commit
) -> None:
    git("switch", "--quiet", "--create", "broken")
    edit(repo / FABRIC, "toy:fabric-production", "toy:fabric-productoin")
    edit(repo / FABRIC, "0.5,kWh", "0.5,MJ2")
    commit("two mistakes")

    [study] = collect.collect_report(repo, headwater, "main", "HEAD")["folders"]

    assert study["ok"] is False
    assert study["validation"]["exit_code"] == 1
    errors = study["validation"]["errors"]
    assert len(errors) == 2
    assert (
        "toy:fabric-productoin" in errors[0] and "Did you mean 'toy:fabric-production'" in errors[0]
    )
    assert "MJ2" in errors[1]
    assert study["diff"] is None and study["diff_error"] is None


def test_a_release_that_is_not_in_the_cache_fails_the_study_with_its_message(
    collect: ModuleType,
    headwater: list[str],
    repo: Path,
    git: Git,
    commit: Commit,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    lighter(repo, git, commit)
    monkeypatch.setenv("HEADWATER_HOME", str(tmp_path / "empty"))

    [study] = collect.collect_report(repo, headwater, "main", "HEAD")["folders"]

    assert study["ok"] is False
    assert study["validation"] is None
    assert study["diff"] is None
    assert "toy-background@6864b7dbb65d" in study["diff_error"]
    assert "headwater release pull" in study["diff_error"]


def test_a_diff_that_cannot_be_made_is_reported_and_is_not_ok(
    collect: ModuleType, headwater: list[str], repo: Path, git: Git, commit: Commit
) -> None:
    git("switch", "--quiet", "--create", "repair")
    database_yaml = repo / "studies/tshirt/tshirt/database.yaml"
    edit(database_yaml, "name: tshirt\n", "name: [\n")
    broken = commit("break the database")
    edit(database_yaml, "name: [\n", "name: tshirt\n")
    commit("repair it")

    [study] = collect.collect_report(repo, headwater, broken, "HEAD")["folders"]

    assert study["validation"] is None
    assert study["diff"] is None
    assert study["ok"] is False
    assert "cannot be read at" in study["diff_error"] and broken[:12] in study["diff_error"]


def test_a_new_database_is_validated_and_counted_by_the_same_command(
    collect: ModuleType, headwater: list[str], repo: Path, git: Git, commit: Commit
) -> None:
    git("switch", "--quiet", "--create", "new-study")
    git("mv", "studies/tshirt", "studies/classic-tshirt")
    commit("rename")

    report = collect.collect_report(repo, headwater, "main", "HEAD")

    by_path = {s["path"]: s for s in report["folders"]}
    new = by_path["studies/classic-tshirt/tshirt"]
    assert new["status"] == "new" and new["ok"] is True
    assert new["validation"]["exit_code"] == 0
    assert new["diff_error"] is None
    assert new["diff"]["created"] is True
    assert new["diff"]["results"] == []
    assert [p["id"] for p in new["diff"]["products"]] == ["tshirt:tshirt"]
    removed = by_path["studies/tshirt/tshirt"]
    assert removed["status"] == "removed" and removed["ok"] is True
    assert removed["validation"] is None and removed["diff"] is None


def test_labels_name_the_refs_in_the_report(
    collect: ModuleType, headwater: list[str], repo: Path, git: Git, commit: Commit
) -> None:
    lighter(repo, git, commit)

    report = collect.collect_report(
        repo, headwater, "main", "HEAD", base_label="origin/main", head_label="merge result"
    )

    assert report["base"]["label"] == "origin/main"
    assert report["head"]["label"] == "merge result"


# --- one process for each study -----------------------------------------------------------------


def commands_run(collect: ModuleType, monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """The arguments of every command `collect` runs from now on."""
    calls: list[list[str]] = []
    real = collect._run

    def recording(headwater: list[str], args: list[str], repo: Path) -> Any:
        calls.append(list(args))
        return real(headwater, args, repo)

    monkeypatch.setattr(collect, "_run", recording)
    return calls


def test_a_changed_study_is_one_run_of_the_command_so_the_background_loads_once(
    collect: ModuleType,
    headwater: list[str],
    repo: Path,
    git: Git,
    commit: Commit,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lighter(repo, git, commit)
    calls = commands_run(collect, monkeypatch)

    collect.collect_report(repo, headwater, "main", "HEAD")

    # The other run, `study uses`, reads only database.yaml files: it loads no background.
    assert [c for c in calls if c[0] == "diff"] == [
        [
            "diff",
            git("rev-parse", "main"),
            git("rev-parse", "HEAD"),
            "studies/tshirt/tshirt",
            "--json",
        ]
    ]


def test_a_new_study_is_one_run_of_the_same_command(
    collect: ModuleType,
    headwater: list[str],
    repo: Path,
    git: Git,
    commit: Commit,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    git("switch", "--quiet", "--create", "new-study")
    git("mv", "studies/tshirt", "studies/classic-tshirt")
    commit("rename")
    calls = commands_run(collect, monkeypatch)

    collect.collect_report(repo, headwater, "main", "HEAD")

    [call] = [c for c in calls if c[0] == "diff"]  # the removed study is not run
    assert call[0] == "diff" and call[3] == "studies/classic-tshirt/tshirt"


def test_the_warnings_of_a_valid_study_are_kept(
    collect: ModuleType, headwater: list[str], repo: Path, git: Git, commit: Commit
) -> None:
    git("switch", "--quiet", "--create", "warned")
    edit(repo / FABRIC, "toy:cotton-fabric,0.25", "toy:cotton-fabric,0.22")
    (repo / "studies/tshirt/tshirt/no_factor.csv").write_text(
        "flow,flow_name,method,reason,who,when\ntoy:co2,,toy-climate,thought so,Sam,2026-10-12\n"
    )
    commit("a row that is not needed")

    [study] = collect.collect_report(repo, headwater, "main", "HEAD")["folders"]

    assert study["ok"] is True
    [warning] = study["validation"]["warnings"]
    assert warning.startswith("flow toy:co2: row not needed")
    assert study["validation"]["errors"] == []
    assert study["diff"] is not None


def test_output_that_is_not_json_fails_that_study_with_what_was_printed(
    collect: ModuleType, repo: Path, git: Git, commit: Commit
) -> None:
    lighter(repo, git, commit)
    command = [sys.executable, "-c", "print('Traceback (most recent call last): boom')"]

    [study] = collect.collect_report(repo, command, "main", "HEAD")["folders"]

    assert study["ok"] is False
    assert study["validation"] is None and study["diff"] is None
    assert "Traceback (most recent call last): boom" in study["diff_error"]
    json.dumps(study, allow_nan=False)


def test_a_command_that_exits_with_an_error_fails_that_study_with_its_message(
    collect: ModuleType, repo: Path, git: Git, commit: Commit
) -> None:
    lighter(repo, git, commit)
    command = [sys.executable, "-c", "import sys; sys.stderr.write('no good\\n'); sys.exit(2)"]

    [study] = collect.collect_report(repo, command, "main", "HEAD")["folders"]

    assert study["ok"] is False
    assert study["validation"] is None and study["diff"] is None
    assert study["diff_error"] == "no good"


# --- releases --------------------------------------------------------------------------------


def test_a_release_folder_is_pulled_into_the_cache(
    collect: ModuleType,
    headwater: list[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("HEADWATER_HOME", str(tmp_path / "fresh"))
    release = next((Path(__file__).parents[2] / "src/headwater/demo/releases").iterdir())

    pulled = collect.pull_releases(headwater, [str(release)])

    assert pulled == [release.name]
    assert find(release.name).name == "toy-background"


def test_a_folder_of_releases_pulls_each_release_in_it(
    collect: ModuleType,
    headwater: list[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("HEADWATER_HOME", str(tmp_path / "fresh"))
    folder = Path(__file__).parents[2] / "src/headwater/demo/releases"

    pulled = collect.pull_releases(headwater, [str(folder), ""])

    assert pulled == [p.name for p in sorted(folder.iterdir())]


def test_a_path_with_no_release_in_it_is_an_error(
    collect: ModuleType, headwater: list[str], tmp_path: Path
) -> None:
    (tmp_path / "empty").mkdir()

    with pytest.raises(collect.CollectError, match="no release"):
        collect.pull_releases(headwater, [str(tmp_path / "empty")])


# --- the command ---------------------------------------------------------------------------------


def test_main_writes_the_report_and_the_outputs_for_the_workflow(
    collect: ModuleType,
    headwater_process: list[str],
    repo: Path,
    git: Git,
    commit: Commit,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The one test that starts `headwater` as a process, as the action does."""
    lighter(repo, git, commit)
    outputs = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(outputs))
    out = tmp_path / "report.json"

    code = collect.main(
        ["--out", str(out), "--base", "main", "--headwater", shlex.join(headwater_process)]
    )

    assert code == 0
    [study] = json.loads(out.read_text())["folders"]
    assert study["path"] == "studies/tshirt/tshirt" and study["ok"] is True
    assert study["diff"]["results"][0]["new"] == pytest.approx(1.080792, rel=REL)
    assert outputs.read_text().splitlines() == ["folders=1", "failed=false"]
