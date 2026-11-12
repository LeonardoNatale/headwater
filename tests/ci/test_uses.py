"""A pull request that changes a study also diffs every study that uses it, directly or not, and
the comment says why: "Uses `dyes`, which changed."
"""

import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

Git = Callable[..., str]
Commit = Callable[[str], str]

ROOT = Path(__file__).resolve().parents[2]
TOY = ROOT / "tests" / "fixtures" / "toy"
RELEASE = next((ROOT / "src" / "headwater" / "demo" / "releases").glob("toy-background@*"))
REL = 1e-9
DYE_CSV = "studies/tshirt-dyed/dyes/processes/reactive-dye.csv"
SALT = "e01,reference,chemicals:salt,1,kg,\ne02,emission,toy:co2,0.2,kg,"


def edit(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    assert old in text, f"{old!r} not found in {path}"
    path.write_text(text.replace(old, new, 1))


def by_path(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {s["path"]: s for s in report["folders"]}


@pytest.fixture
def dyed(repo: Path, commit: Commit) -> Path:
    """The CI repository with the databases `dyes` and the dyed T-shirt, pinned to the demo's
    release, on main. The studies of the fixture are left out: test_databases_and_studies.py
    has them."""
    for name in ("dyes", "tshirt"):
        shutil.copytree(TOY / "tshirt-dyed" / name, repo / "studies" / "tshirt-dyed" / name)
    for name in ("dyes", "tshirt"):
        database = repo / "studies" / "tshirt-dyed" / name / "database.yaml"
        edit(database, "toy-background@fixture", RELEASE.name)
    commit("the dyed T-shirt and its dyes")
    return repo


@pytest.fixture
def chemicals(dyed: Path, commit: Commit, write_study) -> Path:
    """`dyes` uses `chemicals`: the dye takes 0.5 kg of salt."""
    write_study(
        dyed / "studies" / "tshirt-dyed" / "chemicals",
        {"salt": SALT},
        flows=["chemicals:salt,sodium chloride,product,kg,,"],
        background=RELEASE.name,
    )
    with (dyed / "studies/tshirt-dyed/dyes/database.yaml").open("a") as f:
        f.write("uses: [chemicals]\n")
    with (dyed / DYE_CSV).open("a") as f:
        f.write("e04,input,chemicals:salt,0.5,kg,chemicals:salt\n")
    commit("dyes use chemicals")
    return dyed


def test_a_change_to_a_used_study_diffs_the_study_that_uses_it(
    collect: ModuleType, headwater: list[str], dyed: Path, git: Git, commit: Commit
) -> None:
    git("switch", "--quiet", "--create", "darker")
    edit(dyed / DYE_CSV, "toy:co2,4,", "toy:co2,4.5,")
    commit("more CO2 in the dye")

    report = collect.collect_report(dyed, headwater, "main", "HEAD")

    studies = by_path(report)
    assert list(studies) == ["studies/tshirt-dyed/dyes", "studies/tshirt-dyed/tshirt"]
    assert studies["studies/tshirt-dyed/dyes"]["status"] == "changed"
    user = studies["studies/tshirt-dyed/tshirt"]
    assert user["status"] == "uses-changed"
    assert user["uses"] == [{"chain": ["dyes"], "status": "changed"}]
    assert user["ok"] is True
    [row] = user["diff"]["results"]
    assert row["old"] == pytest.approx(1.242646, rel=REL)
    assert row["new"] == pytest.approx(1.247646, rel=REL)
    assert row["drivers"][0]["difference"] == "line dyes:reactive-dye line e03"


def test_a_study_used_through_another_names_the_chain(
    collect: ModuleType, headwater: list[str], chemicals: Path, git: Git, commit: Commit
) -> None:
    git("switch", "--quiet", "--create", "saltier")
    edit(
        chemicals / "studies/tshirt-dyed/chemicals/processes/salt.csv",
        "toy:co2,0.2,",
        "toy:co2,0.3,",
    )
    commit("more CO2 in the salt")

    studies = by_path(collect.collect_report(chemicals, headwater, "main", "HEAD"))

    assert list(studies) == [
        "studies/tshirt-dyed/chemicals",
        "studies/tshirt-dyed/dyes",
        "studies/tshirt-dyed/tshirt",
    ]
    assert studies["studies/tshirt-dyed/dyes"]["uses"] == [
        {"chain": ["chemicals"], "status": "changed"}
    ]
    assert studies["studies/tshirt-dyed/tshirt"]["uses"] == [
        {"chain": ["dyes", "chemicals"], "status": "changed"}
    ]
    [row] = studies["studies/tshirt-dyed/tshirt"]["diff"]["results"]
    # 0.01 kg of dye × 0.5 kg of salt × 0.1 more CO2 per kg
    assert row["new"] - row["old"] == pytest.approx(0.0005, rel=1e-6)


def test_a_study_that_changed_itself_also_says_what_it_uses_that_changed(
    collect: ModuleType, headwater: list[str], dyed: Path, git: Git, commit: Commit
) -> None:
    git("switch", "--quiet", "--create", "both")
    edit(dyed / DYE_CSV, "toy:co2,4,", "toy:co2,4.5,")
    edit(
        dyed / "studies/tshirt-dyed/tshirt/processes/tshirt.csv",
        "dyes:reactive-dye,0.01",
        "dyes:reactive-dye,0.02",
    )
    commit("more dye, and more CO2 in it")

    studies = by_path(collect.collect_report(dyed, headwater, "main", "HEAD"))

    assert studies["studies/tshirt-dyed/tshirt"]["status"] == "changed"
    assert studies["studies/tshirt-dyed/tshirt"]["uses"] == [
        {"chain": ["dyes"], "status": "changed"}
    ]


def test_a_study_that_uses_nothing_that_changed_is_left_out(
    collect: ModuleType, headwater: list[str], dyed: Path, git: Git, commit: Commit
) -> None:
    git("switch", "--quiet", "--create", "lighter")
    edit(
        dyed / "studies/tshirt/tshirt/processes/tshirt.csv",
        "toy:cotton-fabric,0.25",
        "toy:cotton-fabric,0.22",
    )
    commit("lighter T-shirt")

    report = collect.collect_report(dyed, headwater, "main", "HEAD")

    assert [(s["path"], s["status"], s["uses"]) for s in report["folders"]] == [
        ("studies/tshirt/tshirt", "changed", [])
    ]


def test_a_study_whose_used_study_was_removed_fails_with_the_reason(
    collect: ModuleType, headwater: list[str], dyed: Path, git: Git, commit: Commit
) -> None:
    git("switch", "--quiet", "--create", "no-dyes")
    git("rm", "--quiet", "-r", "studies/tshirt-dyed/dyes")
    commit("no more dyes")

    studies = by_path(collect.collect_report(dyed, headwater, "main", "HEAD"))

    assert studies["studies/tshirt-dyed/dyes"]["status"] == "removed"
    user = studies["studies/tshirt-dyed/tshirt"]
    assert user["uses"] == [{"chain": ["dyes"], "status": "removed"}]
    assert user["ok"] is False
    assert any("dyes" in e for e in user["validation"]["errors"])


def test_the_comment_says_why_a_study_that_did_not_change_is_shown(
    collect: ModuleType,
    render: ModuleType,
    headwater: list[str],
    dyed: Path,
    git: Git,
    commit: Commit,
) -> None:
    git("switch", "--quiet", "--create", "darker")
    edit(dyed / DYE_CSV, "toy:co2,4,", "toy:co2,4.5,")
    commit("more CO2 in the dye")
    report = collect.collect_report(dyed, headwater, "main", "HEAD", base_label="main")

    body = render.render(report)

    section = body.split("### Database `studies/tshirt-dyed/tshirt`")[1]
    assert section.startswith("\n\nUses `dyes`, which changed.\n")
    assert (
        "| T-shirt | Climate change (kg CO2-eq) | 1.24265 | 1.24765 | +0.005 | +0.4% | +0.005 |"
        in section
    )
    assert (
        "| e03 | `dyes:reactive-dye line e03` (Carbon dioxide, fossil): amount 4 → 4.5 kg |"
        in section
    )


def test_the_comment_names_each_study_of_the_chain(render: ModuleType) -> None:
    study = {
        "path": "studies/tshirt-dyed/tshirt",
        "status": "uses-changed",
        "uses": [
            {"chain": ["dyes", "chemicals"], "status": "changed"},
            {"chain": ["buttons"], "status": "removed"},
        ],
        "ok": False,
        "validation": None,
        "diff": None,
        "diff_error": "boom",
    }
    report = {
        "format": "headwater-ci/2",
        "base": {"label": "main", "commit": "a" * 40},
        "head": {"label": "HEAD", "commit": "b" * 40},
        "failed": True,
        "folders": [study],
    }

    body = render.render(report)

    assert (
        "Uses `dyes`, which uses `chemicals`, which changed. Uses `buttons`, which was removed."
        in body
    )


def test_when_the_users_cannot_be_found_the_job_fails_and_says_so(
    collect: ModuleType,
    render: ModuleType,
    headwater: list[str],
    dyed: Path,
    git: Git,
    commit: Commit,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    git("switch", "--quiet", "--create", "darker")
    edit(dyed / DYE_CSV, "toy:co2,4,", "toy:co2,4.5,")
    commit("more CO2 in the dye")
    real = collect._run

    def broken(command: list[str], args: list[str], repo: Path) -> subprocess.CompletedProcess[str]:
        if args[0] == "database":
            return subprocess.CompletedProcess([*command, *args], 2, "", "No such command 'uses'.")
        return real(command, args, repo)

    monkeypatch.setattr(collect, "_run", broken)

    report = collect.collect_report(dyed, headwater, "main", "HEAD")

    assert report["failed"] is True
    assert report["uses_error"] == "No such command 'uses'."
    assert [s["path"] for s in report["folders"]] == ["studies/tshirt-dyed/dyes"]
    body = render.render(report)
    assert "**The databases and studies that reach the changed ones could not be found**" in body
    assert "No such command 'uses'." in body


def test_a_database_yaml_that_does_not_read_is_listed_in_the_comment(
    collect: ModuleType,
    render: ModuleType,
    headwater: list[str],
    dyed: Path,
    git: Git,
    commit: Commit,
) -> None:
    git("switch", "--quiet", "--create", "broken")
    edit(dyed / DYE_CSV, "toy:co2,4,", "toy:co2,4.5,")
    (dyed / "studies/tshirt/tshirt/database.yaml").write_text("name: [\n")
    commit("more CO2 in the dye, and a broken database.yaml")

    report = collect.collect_report(dyed, headwater, "main", "HEAD")

    [unread] = report["uses_unread"]
    assert (unread["path"], unread["file"]) == ("studies/tshirt/tshirt", "database.yaml")
    body = render.render(report)
    assert "**These files do not read**" in body
    assert "- `studies/tshirt/tshirt/database.yaml`: " in body
