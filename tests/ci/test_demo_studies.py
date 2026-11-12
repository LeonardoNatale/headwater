"""The demo is a repository in miniature: a frozen toy release, databases that pin it, and a study
of one of them. These tests keep them in step, so the CI demo cannot drift from the library."""

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

import headwater as hw
from headwater.release import Release
from tests.conftest import only_process, open_database

DEMO = Path(__file__).resolve().parents[2] / "src" / "headwater" / "demo"
RELEASE = next((DEMO / "releases").glob("toy-background@*"))
REL = 1e-9
DATABASES = sorted(p.parent for p in (DEMO / "studies").glob("*/database.yaml"))
STUDIES = sorted(p.parent for p in (DEMO / "studies").glob("*/study.yaml"))


def _build_script() -> ModuleType:
    path = Path(__file__).resolve().parent / "build_demo_release.py"
    spec = importlib.util.spec_from_file_location("demo_build_release", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_the_committed_release_is_what_the_build_script_writes(tmp_path: Path) -> None:
    rebuilt = _build_script().build_toy_release(tmp_path)

    assert rebuilt.path.name == RELEASE.name, (
        "The toy background or the release format changed. Run "
        "`uv run python tests/ci/build_demo_release.py`, replace the folder in "
        "src/headwater/demo/releases/, and pin the studies to the new name."
    )
    committed = Release.open(RELEASE)
    committed.verify()
    assert committed.hash == rebuilt.hash


def test_there_are_four_demo_databases_and_one_study() -> None:
    assert [p.name for p in DATABASES] == [
        "dyes",
        "tshirt",
        "tshirt-dyed",
        "tshirt-local-electricity",
    ]
    assert [p.name for p in STUDIES] == ["tshirt-footprint"]


@pytest.mark.parametrize("database", DATABASES, ids=lambda p: p.name)
def test_each_demo_database_pins_the_committed_release(database: Path) -> None:
    pin = open_database(database).folder.database.background

    assert f"{pin.name}@{pin.version}" == RELEASE.name


@pytest.mark.parametrize(
    ("study", "score"),
    [
        ("tshirt", 1.19205),  # 0.25 × 3.7086 + 0.5 × 0.5298, docs/calculation.md
        ("tshirt-local-electricity", 1.09205),  # electricity CO2 0.5 → 0.3 for this purchase only
        ("dyes", 5.0596),  # 2 kWh × 0.5298 + 4 kg CO2, per kg of dye
        ("tshirt-dyed", 1.242646),  # the T-shirt, plus 0.01 kg of dye from the study dyes
    ],
)
def test_the_demo_databases_have_the_hand_checked_scores(study: str, score: float) -> None:
    opened = open_database(DEMO / "studies" / study)
    [method] = opened.folder.database.methods
    background = hw.load(str(opened.folder.database.background), method=method)

    result = opened.calc(background, only_process(opened))

    assert result.score == pytest.approx(score, rel=REL)


def test_the_demo_study_scores_its_t_shirt_and_its_scenario() -> None:
    study = hw.open(DEMO / "studies" / "tshirt-footprint")
    assert isinstance(study, hw.Study)
    background = hw.load(RELEASE.name, method="toy-climate")

    [base] = study.calc(background)
    [cleaner] = study.calc(background, scenario="cleaner-dye")

    assert base.score == pytest.approx(1.242646, rel=REL)
    assert cleaner.score == pytest.approx(1.237646, rel=REL)


def test_the_demo_comment_lists_the_databases_then_the_study(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    collect: ModuleType,
    render: ModuleType,
    headwater: list[str],
) -> None:
    """A pull request that lowers the dye's CO2 from 4 to 3.5 kg per kg, as the README says."""
    repo = tmp_path / "demo"
    shutil.copytree(DEMO / "studies", repo / "studies")

    def run(*args: str) -> None:
        subprocess.run(
            ["git", "-c", "commit.gpgsign=false", "-C", str(repo), *args],
            check=True,
            capture_output=True,
        )

    for name, value in {
        "GIT_CONFIG_GLOBAL": str(tmp_path / "gitconfig"),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }.items():
        monkeypatch.setenv(name, value)
    (tmp_path / "gitconfig").write_text("")
    run("init", "--quiet", "--initial-branch", "main")
    run("add", "--all")
    run("commit", "--quiet", "-m", "the demo")
    dye = repo / "studies" / "dyes" / "processes" / "reactive-dye.csv"
    dye.write_text(dye.read_text().replace("toy:co2,4,", "toy:co2,3.5,"))
    run("commit", "--quiet", "--all", "-m", "a cleaner dye")
    monkeypatch.chdir(repo)

    body = render.render(collect.collect_report(repo, headwater, "HEAD~1", "HEAD"))

    heads = [line for line in body.splitlines() if line.startswith("### ")]
    assert heads == [
        "### Database `studies/dyes`",
        "### Database `studies/tshirt-dyed`",
        "### Study `studies/tshirt-footprint`",
    ]
    assert "| 1 × T-shirt | Climate change (kg CO2-eq) | 1.24265 | 1.23765 |" in body
