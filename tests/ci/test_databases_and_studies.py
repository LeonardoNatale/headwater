"""The comment lists the databases that changed, then the studies whose products reach them (D46).

The toy: the database `dyes` makes a reactive dye that emits 4 kg CO2 per kg, the database
`tshirt` buys 0.01 kg of it per T-shirt, and the study `tshirt-footprint` scores 1 T-shirt,
1.242646. Lowering the dye's CO2 from 4 to 3.5 moves the dye by -0.5, the T-shirt by
0.01 × -0.5 = -0.005, and the study to 1.237646.
"""

import shutil
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
DYED = "studies/tshirt-dyed"
DYE_CSV = f"{DYED}/dyes/exchanges.csv"


def edit(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    assert old in text, f"{old!r} not found in {path}"
    path.write_text(text.replace(old, new, 1))


def listed(report: dict[str, Any]) -> list[tuple[str, str, str]]:
    return [(f["path"], f["kind"], f["status"]) for f in report["folders"]]


@pytest.fixture
def dyed(repo: Path, commit: Commit) -> Path:
    """The databases `dyes` and `tshirt` and the study `tshirt-footprint`, pinned to the demo's
    release, on main."""
    for name in ("dyes", "tshirt", "tshirt-footprint"):
        shutil.copytree(TOY / "tshirt-dyed" / name, repo / DYED / name)
    for name in ("dyes", "tshirt"):
        edit(repo / DYED / name / "database.yaml", "toy-background@fixture", RELEASE.name)
    commit("the dyed T-shirt, its dyes and its footprint")
    return repo


def test_a_change_to_dyes_lists_both_databases_then_the_study(
    collect: ModuleType,
    render: ModuleType,
    headwater: list[str],
    dyed: Path,
    git: Git,
    commit: Commit,
) -> None:
    git("switch", "--quiet", "--create", "cleaner")
    edit(dyed / DYE_CSV, "toy:co2,4,", "toy:co2,3.5,")
    commit("a cleaner dye")

    report = collect.collect_report(dyed, headwater, "main", "HEAD")

    assert listed(report) == [
        (f"{DYED}/dyes", "database", "changed"),
        (f"{DYED}/tshirt", "database", "uses-changed"),
        (f"{DYED}/tshirt-footprint", "study", "reaches-changed"),
    ]
    dyes, tshirt, footprint = report["folders"]
    assert all(f["ok"] for f in report["folders"])
    assert tshirt["uses"] == [{"chain": ["dyes"], "status": "changed"}]
    assert footprint["uses"] == [{"chain": ["tshirt", "dyes"], "status": "changed"}]
    [dye] = dyes["diff"]["results"]
    assert dye["new"] - dye["old"] == pytest.approx(-0.5, rel=1e-6)
    [row] = footprint["diff"]["results"]
    assert row["old"] == pytest.approx(1.242646, rel=REL)
    assert row["new"] == pytest.approx(1.237646, rel=REL)
    assert footprint["diff"]["kind"] == "study"
    assert footprint["diff"]["products"][0]["amount"] == {"old": 1.0, "new": 1.0}

    body = render.render(report)

    heads = [line for line in body.splitlines() if line.startswith("### ")]
    assert heads == [
        f"### Database `{DYED}/dyes`",
        f"### Database `{DYED}/tshirt`",
        f"### Study `{DYED}/tshirt-footprint`",
    ]
    assert body.splitlines()[1] == "## Results diff for changed databases and studies"
    study = body.split(f"### Study `{DYED}/tshirt-footprint`")[1].split("<sub>")[0]
    assert "Its products reach `tshirt`, which uses `dyes`, which changed." in study
    assert (
        "| 1 × T-shirt | Climate change (kg CO2-eq) | 1.24265 | 1.23765 | -0.005 | -0.4% | -0.005 |"
        in study
    )
    assert "Uses `dyes`, which changed." in body.split(f"### Database `{DYED}/tshirt`")[1]


def test_a_change_to_the_study_alone_diffs_the_study_only(
    collect: ModuleType,
    render: ModuleType,
    headwater: list[str],
    dyed: Path,
    git: Git,
    commit: Commit,
) -> None:
    git("switch", "--quiet", "--create", "two")
    edit(dyed / DYED / "tshirt-footprint" / "study.yaml", "amount: 1}", "amount: 2}")
    commit("two T-shirts")

    report = collect.collect_report(dyed, headwater, "main", "HEAD")

    assert listed(report) == [(f"{DYED}/tshirt-footprint", "study", "changed")]
    [row] = report["folders"][0]["diff"]["results"]
    assert row["old"] == pytest.approx(1.242646, rel=REL)
    assert row["new"] == pytest.approx(2 * 1.242646, rel=REL)
    [driver] = row["drivers"]
    assert driver["difference"] == "product tshirt:tshirt"
    assert driver["contribution"] == pytest.approx(1.242646, rel=REL)
    body = render.render(report)
    assert "`product tshirt:tshirt` (T-shirt): amount 1 → 2" in body
    assert "| 2 × T-shirt | Climate change (kg CO2-eq) | 1.24265 | 2.48529 |" in body


def test_a_new_database_is_one_line_with_its_products_counted(
    collect: ModuleType,
    render: ModuleType,
    headwater: list[str],
    dyed: Path,
    git: Git,
    commit: Commit,
    write_study: Callable[..., Path],
) -> None:
    git("switch", "--quiet", "--create", "bags")
    write_study(
        dyed / "studies" / "bags",
        {
            "bag": "e01,reference,bags:bag,1,kg,\n"
            "e02,input,toy:electricity,2,kWh,toy:electricity-production",
            "tote": "e01,reference,bags:tote,1,kg,\n"
            "e02,input,toy:electricity,1,kWh,toy:electricity-production",
        },
        flows=["bags:bag,bag,product,kg,,", "bags:tote,tote,product,kg,,"],
        background=RELEASE.name,
    )
    commit("bags")

    report = collect.collect_report(dyed, headwater, "main", "HEAD")

    [new] = report["folders"]
    assert (new["path"], new["kind"], new["status"]) == ("studies/bags", "database", "new")
    assert new["ok"] is True
    assert new["diff"]["created"] is True
    assert new["diff"]["results"] == []
    assert [p["id"] for p in new["diff"]["products"]] == ["bags:bag", "bags:tote"]
    body = render.render(report)
    section = body.split("### Database `studies/bags` (new)")[1].split("<sub>")[0].strip()
    assert section == (
        "New database: 2 products, not scored. `headwater calc studies/bags` scores them."
    )


def test_a_study_reached_directly_and_through_a_database_says_the_shortest_way(
    collect: ModuleType,
    render: ModuleType,
    headwater: list[str],
    dyed: Path,
    git: Git,
    commit: Commit,
) -> None:
    shutil.copytree(TOY / "tshirt-dyed" / "tshirt-and-dye", dyed / DYED / "tshirt-and-dye")
    commit("a batch of two T-shirts and their dye")
    git("switch", "--quiet", "--create", "cleaner")
    edit(dyed / DYE_CSV, "toy:co2,4,", "toy:co2,3.5,")
    commit("a cleaner dye")

    report = collect.collect_report(dyed, headwater, "main", "HEAD")

    batch = next(f for f in report["folders"] if f["path"].endswith("tshirt-and-dye"))
    assert batch["uses"] == [{"chain": ["dyes"], "status": "changed"}]
    shirts, dye = (r for r in batch["diff"]["results"])
    assert shirts["product"]["id"] == "tshirt:tshirt"
    assert shirts["old"] == pytest.approx(2 * 1.242646, rel=REL)
    assert shirts["new"] == pytest.approx(2 * 1.237646, rel=REL)
    assert dye["product"]["id"] == "dyes:reactive-dye"
    assert dye["old"] == pytest.approx(0.5 * 5.0596, rel=REL)
    assert dye["new"] == pytest.approx(0.5 * 4.5596, rel=REL)
    body = render.render(report)
    assert "Its products reach `dyes`, which changed." in body
