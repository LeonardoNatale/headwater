"""`headwater upgrade`, and `headwater diff` across the pin change an upgrade makes."""

import json
import re
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner

from headwater.cli import app

Invoke = Callable[..., tuple[int, str]]
ANSI = re.compile(r"\x1b\[[0-9;]*m")


@pytest.fixture(autouse=True)
def plain_wide_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setenv("TERMINAL_WIDTH", "200")
    monkeypatch.delenv("FORCE_COLOR", raising=False)


@pytest.fixture
def invoke() -> Invoke:
    """Run the command line, and return the exit code and what it printed, errors included."""
    runner = CliRunner()

    def run(*args: str | Path) -> tuple[int, str]:
        result = runner.invoke(app, [str(a) for a in args], catch_exceptions=False)
        return result.exit_code, ANSI.sub("", result.output)

    return run


@pytest.fixture
def invoke_stdout() -> Invoke:
    """As `invoke`, but only what went to standard output."""
    runner = CliRunner()

    def run(*args: str | Path) -> tuple[int, str]:
        result = runner.invoke(app, [str(a) for a in args], catch_exceptions=False)
        return result.exit_code, ANSI.sub("", result.stdout)

    return run


def snapshot(path: Path) -> dict[Path, bytes]:
    return {p.relative_to(path): p.read_bytes() for p in path.rglob("*") if p.is_file()}


def test_an_upgrade_that_maps_every_supplier_is_written_and_explained(
    studies: Path, to: str, mapping: Path, invoke: Invoke
) -> None:
    code, output = invoke("upgrade", studies / "tshirt", "--to", to, "--mapping", mapping)

    assert code == 0, output
    assert f"→ {to}" in output.splitlines()[0]
    assert "mapping row      2 suppliers, 2 lines" in output
    assert "same id          1 supplier, 1 line" in output
    assert "same dataset     1 supplier, 1 line" in output
    assert "by test on 2026-10-25 (upgrade-mapping.csv:2)" in output
    assert "[RoW]" in output and "[market]" in output
    assert "Written: tshirt/database.yaml, tshirt/processes/tshirt.csv" in output
    assert "1.2048 → 0.8775" in output
    assert "Release change" in output
    assert "toy:2:fabric-b" in (studies / "tshirt" / "processes" / "tshirt.csv").read_text()


def test_an_unmapped_supplier_writes_nothing_and_exits_1_with_a_row_to_fill(
    studies: Path, to: str, invoke: Invoke
) -> None:
    before = snapshot(studies)

    code, output = invoke("upgrade", studies / "tshirt", "--to", to)

    assert code == 1
    assert snapshot(studies) == before
    assert "Not mapped: 2 suppliers, 2 lines" in output
    assert "[not-in-release] tape production | tape | GLO (toy:1:tape)" in output
    assert "maybe: toy:2:tape-rer (tape production | tape | RER)" in output
    assert "at tshirt:tshirt line e06" in output
    assert "row: toy:1:tape,,,,,<supplier>,,<who>,<when>," in output
    assert "Nothing was written" in output


def test_check_writes_nothing_and_shows_the_diff(
    studies: Path, to: str, mapping: Path, invoke: Invoke
) -> None:
    before = snapshot(studies)

    code, output = invoke(
        "upgrade", studies / "tshirt", "--to", to, "--mapping", mapping, "--check"
    )

    assert code == 0, output
    assert snapshot(studies) == before
    assert "1.2048 → 0.8775" in output
    assert "Written" not in output
    assert "--check: nothing was written" in output


def test_the_json_has_the_mapping_the_flags_and_the_diff(
    studies: Path, to: str, mapping: Path, invoke_stdout: Invoke
) -> None:
    code, output = invoke_stdout(
        "upgrade", studies / "tshirt", "--to", to, "--mapping", mapping, "--json"
    )

    assert code == 0
    data = json.loads(output)
    assert data["format"] == "headwater-upgrade/2"
    assert data["to"].startswith("toy-2@") and data["from"].startswith("toy-1@")
    assert data["counts"]["same-dataset"] == {"suppliers": 1, "lines": 1}
    fabric = next(m for m in data["matches"] if m["old"]["id"] == "toy:1:fabric-a")
    assert fabric["rule"] == "same-dataset"
    assert fabric["new"]["id"] == "toy:2:fabric-b"
    assert {f["kind"] for f in data["flags"]} == {"row", "market"}
    assert data["written"] == ["tshirt/database.yaml", "tshirt/processes/tshirt.csv"]
    [diff] = data["diffs"]
    [result] = diff["results"]
    assert result["background"] == pytest.approx(0.8775 - 1.2048, rel=1e-9)
    assert [m["line"] for m in result["moved"]] == ["e02", "e03", "e04", "e05", "e06"]


def test_an_unknown_indicator_exits_2_and_writes_nothing(
    studies: Path, to: str, mapping: Path, invoke: Invoke
) -> None:
    before = snapshot(studies)

    code, output = invoke(
        "upgrade", studies / "tshirt", "--to", to, "--mapping", mapping, "--indicator", "nope"
    )

    assert code == 2
    assert "nope" in output
    assert snapshot(studies) == before


def test_an_unknown_target_release_exits_2(studies: Path, invoke: Invoke) -> None:
    code, output = invoke("upgrade", studies / "tshirt", "--to", "toy-3@0123456789ab")

    assert code == 2
    assert "toy-3@0123456789ab" in output
    assert "Traceback" not in output


# --- headwater diff across the pin change ---------------------------------------------------


def _git(path: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "-C", str(path), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return done.stdout.strip()


@pytest.fixture
def repo(studies: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """A git repository around `studies/`, with the T-shirt on toy-1 committed on `main`."""
    nothing = tmp_path / "gitconfig"
    nothing.write_text("")
    for name, value in {
        "GIT_CONFIG_GLOBAL": str(nothing),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }.items():
        monkeypatch.setenv(name, value)
    root = studies.parent
    subprocess.run(["git", "init", "--quiet", "--initial-branch", "main", str(root)], check=True)
    _git(root, "add", "studies")
    _git(root, "commit", "--quiet", "-m", "the T-shirt on toy-1")
    monkeypatch.chdir(root)
    return root


def test_diff_across_an_upgrade_commit_splits_the_release_from_the_study(
    repo: Path, to: str, mapping: Path, invoke: Invoke
) -> None:
    code, _ = invoke("upgrade", "studies/tshirt", "--to", to, "--mapping", mapping)
    assert code == 0
    _git(repo, "commit", "--quiet", "-am", "upgrade to toy-2")

    code, output = invoke("diff", "HEAD~1", "HEAD", "studies/tshirt")

    assert code == 0, output
    assert "Background: toy-2@" in output and "before: toy-1@" in output
    assert "1.2048 → 0.8775" in output
    assert "Release change, line by line, on Climate change (kg CO2-eq):" in output
    assert "-0.1875  tshirt:tshirt line e02 (cotton fabric)" in output
    # suppliers in words, and a name both releases share once, with its two ids
    assert "same dataset, cotton fabric production (RoW), toy:1:fabric-a → toy:2:fabric-b" in output
    assert "as in the new database, dye production (GLO) → reactive dye production (GLO)" in output
    assert "No lines differ." in output


def test_diff_json_across_an_upgrade_has_the_release_part_per_result(
    repo: Path, to: str, mapping: Path, invoke: Invoke, invoke_stdout: Invoke
) -> None:
    code, _ = invoke("upgrade", "studies/tshirt", "--to", to, "--mapping", mapping)
    assert code == 0
    _git(repo, "commit", "--quiet", "-am", "upgrade to toy-2")

    code, output = invoke_stdout("diff", "HEAD~1", "HEAD", "studies/tshirt", "--json")

    assert code == 0
    data = json.loads(output)
    assert data["format"] == "headwater-diff/2"
    assert data["old_background"]["release"].startswith("toy-1@")
    assert data["background"]["release"].startswith("toy-2@")
    [result] = data["results"]
    assert result["background"] == pytest.approx(0.8775 - 1.2048, rel=1e-9)
    fabric = result["moved"][0]
    assert fabric == {
        "process": "tshirt:tshirt",
        "line": "e02",
        "flow": "toy:cotton-fabric",
        "unit": "kg",
        "old_supplier": "toy:1:fabric-a",
        "new_supplier": "toy:2:fabric-b",
        "old_value": pytest.approx(3.5),
        "new_value": pytest.approx(2.75),
        "effect": pytest.approx(-0.1875),
        "how": "same-dataset",
    }


STUDY_YAML = """\
name: tshirt-footprint
goal: Footprint of two T-shirts, across a release change
functional_unit: 2 T-shirts
products:
  - {process: tshirt:tshirt, amount: 2}
boundary: cradle-to-gate
methods: [toy-climate]
cut_off: {criteria: [mass], max_cumulative: 0.03}
"""


def _footprint(repo: Path) -> Path:
    """The study of 2 T-shirts, with the scenario `clean-grid` on toy-1's grid, committed."""
    folder = repo / "studies" / "tshirt-footprint"
    (folder / "scenarios").mkdir(parents=True)
    (folder / "study.yaml").write_text(STUDY_YAML)
    (folder / "scenarios" / "clean-grid.yaml").write_text(
        "changes:\n  - process: toy:1:elec\n    line: e02\n    amount: 0.3\n"
    )
    _git(repo, "add", "studies")
    _git(repo, "commit", "--quiet", "-m", "the study of 2 T-shirts")
    return folder


def test_diff_of_a_study_across_an_upgrade_splits_each_product_times_its_amount(
    repo: Path, to: str, mapping: Path, invoke: Invoke, invoke_stdout: Invoke
) -> None:
    """2 T-shirts: 2 x 1.2048 = 2.4096 on toy-1, 2 x 0.8775 = 1.755 on toy-2, all of it the
    release's (2 x -0.3273 = -0.6546), and the fabric line 2 x -0.1875 = -0.375."""
    _footprint(repo)
    code, output = invoke(
        "upgrade", "studies/tshirt", "studies/tshirt-footprint", "--to", to, "--mapping", mapping
    )
    assert code == 0, output
    _git(repo, "commit", "--quiet", "-am", "upgrade to toy-2")

    code, output = invoke("diff", "HEAD~1", "HEAD", "studies/tshirt-footprint")

    assert code == 0, output
    assert "Background: toy-2@" in output and "before: toy-1@" in output
    assert "2.4096 → 1.755" in output
    assert "release -0.6546" in output
    assert "-0.375  tshirt:tshirt line e02 (cotton fabric)" in output
    assert "scenario clean-grid" in output  # moved to toy-2's grid, line a2: not a score

    code, output = invoke_stdout("diff", "HEAD~1", "HEAD", "studies/tshirt-footprint", "--json")

    assert code == 0
    data = json.loads(output)
    assert data["old_background"]["release"].startswith("toy-1@")
    [result] = data["results"]
    assert result["old"] == pytest.approx(2 * 1.2048, rel=1e-9)
    assert result["new"] == pytest.approx(2 * 0.8775, rel=1e-9)
    assert result["background"] == pytest.approx(2 * (0.8775 - 1.2048), rel=1e-9)
    assert result["moved"][0]["effect"] == pytest.approx(2 * -0.1875, rel=1e-9)
