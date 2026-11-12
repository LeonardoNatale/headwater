"""`headwater upgrade` moves studies with the databases (D32 as amended by D46): a study's
scenarios name background lines, and a study of release products names their ids and its pin.

The toy: toy-1's electricity `toy:1:elec` emits 0.5 kg CO2 per kWh on line e02; toy-2's is the
same id, with the CO2 on line a2. The study `tshirt-footprint` scores the T-shirt database, and its
scenario `clean-grid` sets that CO2 to 0.3. The study `fabric` scores 0.25 kg of
`toy:1:fabric-a`, which is `toy:2:fabric-b` in toy-2, and pins toy-1.
"""

import re
from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner

import headwater as hw
from headwater.cli import app
from headwater.release import Release

from .conftest import short

Invoke = Callable[..., tuple[int, str]]
ANSI = re.compile(r"\x1b\[[0-9;]*m")
REL = 1e-9

STUDY_YAML = """\
name: {name}
goal: A test of the upgrade of a study
functional_unit: {unit}
products:
  - {{process: {process}, amount: {amount}}}
boundary: cradle-to-gate
methods: [toy-climate]
cut_off: {{criteria: [mass], max_cumulative: 0.03}}
"""


@pytest.fixture(autouse=True)
def plain_wide_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setenv("TERMINAL_WIDTH", "200")
    monkeypatch.delenv("FORCE_COLOR", raising=False)


@pytest.fixture
def invoke() -> Invoke:
    runner = CliRunner()

    def run(*args: str | Path) -> tuple[int, str]:
        result = runner.invoke(app, [str(a) for a in args], catch_exceptions=False)
        return result.exit_code, ANSI.sub("", result.output)

    return run


def footprint(studies: Path, line: str = "e02") -> Path:
    """The study of 1 T-shirt, with the scenario `clean-grid` on line `line` of `toy:1:elec`."""
    folder = studies / "tshirt-footprint"
    (folder / "scenarios").mkdir(parents=True)
    (folder / "study.yaml").write_text(
        STUDY_YAML.format(
            name="tshirt-footprint", unit="1 T-shirt", process="tshirt:tshirt", amount=1
        )
    )
    (folder / "scenarios" / "clean-grid.yaml").write_text(
        "description: A cleaner grid\n"
        "changes:\n"
        f"  - process: toy:1:elec  # the grid itself\n    line: {line}\n    amount: 0.3\n"
    )
    return folder


def fabric(studies: Path, toy1: Release) -> Path:
    """The study of 0.25 kg of a fabric of the release, which it pins."""
    folder = studies / "fabric"
    folder.mkdir()
    text = STUDY_YAML.format(
        name="fabric", unit="0.25 kg of fabric", process="toy:1:fabric-a", amount=0.25
    )
    (folder / "study.yaml").write_text(text + f"background: {short(toy1)}\n")
    return folder


def test_upgrade_moves_a_scenario_line_and_a_release_only_study_and_its_pin(
    studies: Path, toy1: Release, toy2: Release, to: str, mapping: Path, invoke: Invoke
) -> None:
    shirt, cloth = footprint(studies), fabric(studies, toy1)

    code, output = invoke(
        "upgrade", studies / "tshirt", shirt, cloth, "--to", to, "--mapping", mapping
    )

    assert code == 0, output
    scenario = (shirt / "scenarios" / "clean-grid.yaml").read_text()
    assert "  - process: toy:2:elec  # the grid itself\n    line: a2\n    amount: 0.3\n" in scenario
    study = (cloth / "study.yaml").read_text()
    assert "  - {process: toy:2:fabric-b, amount: 0.25}\n" in study
    assert study.endswith(f"background: {to}\n")
    written = output.split("Written: ")[1].splitlines()[0]
    assert "tshirt-footprint/scenarios/clean-grid.yaml" in written
    assert "fabric/study.yaml" in written
    assert "Study tshirt-footprint: valid." in output and "Study fabric: valid." in output
    background = hw.load(to, method="toy-climate")
    upgraded = hw.open(cloth)
    assert isinstance(upgraded, hw.Study)
    [result] = upgraded.calc(background)
    assert result.score == pytest.approx(0.25 * background.score("toy:2:fabric-b"), rel=REL)
    moved = hw.open(shirt)
    assert isinstance(moved, hw.Study)
    [base] = moved.calc(background)
    [clean] = moved.calc(background, scenario="clean-grid")
    assert clean.score < base.score  # the T-shirt's grid emits 0.3, not 0.4


def test_a_scenario_line_with_no_match_in_the_successor_stops_the_upgrade(
    studies: Path, to: str, mapping: Path, invoke: Invoke
) -> None:
    """Line e03 of toy-1's grid emits N2O; toy-2's grid emits none."""
    shirt = footprint(studies, line="e03")
    before = (studies / "tshirt" / "database.yaml").read_text()

    code, output = invoke("upgrade", studies / "tshirt", shirt, "--to", to, "--mapping", mapping)

    assert code == 1, output
    assert "study tshirt-footprint scenarios/clean-grid.yaml: toy:1:elec line e03" in output
    assert "Nothing was written." in output
    assert (studies / "tshirt" / "database.yaml").read_text() == before


def test_a_study_that_names_the_old_release_must_move_with_its_databases(
    studies: Path, to: str, mapping: Path, invoke: Invoke
) -> None:
    footprint(studies)

    code, output = invoke("upgrade", studies / "tshirt", "--to", to, "--mapping", mapping)

    assert code == 1, output
    assert "study tshirt-footprint" in output
    assert "names toy-1" in output and "upgrade them together" in output


def test_a_study_given_brings_the_databases_its_products_are_in(
    studies: Path, to: str, mapping: Path, invoke: Invoke
) -> None:
    shirt = footprint(studies)

    code, output = invoke("upgrade", shirt, "--to", to, "--mapping", mapping)

    assert code == 0, output
    assert f"background: {to}" in (studies / "tshirt" / "database.yaml").read_text()
    assert "toy:2:elec" in (shirt / "scenarios" / "clean-grid.yaml").read_text()


def test_a_database_given_with_its_study_is_diffed_once(
    studies: Path, to: str, mapping: Path, invoke: Invoke
) -> None:
    shirt = footprint(studies)

    code, output = invoke(
        "upgrade", studies / "tshirt", shirt, "--to", to, "--mapping", mapping, "--check"
    )

    assert code == 0, output
    assert output.count("T-shirt (tshirt:tshirt)") == 1


def _loss(studies: Path) -> Path:
    """The T-shirt's market of toy-1 loses 10% of its electricity, through the parameter `loss`
    of that release process, and the study's scenario `lossy` sets it to 1.2."""
    folder = studies / "tshirt"
    (folder / "overrides").mkdir()
    (folder / "overrides" / "losses.csv").write_text(
        "process,line,role,flow,amount,unit,supplier,comment\n"
        "toy:1:market-elec,e02,input,toy:electricity,loss,kWh,toy:1:elec,10% lost\n"
    )
    (folder / "parameters.yaml").write_text("processes:\n  toy:1:market-elec:\n    loss: 1.1\n")
    study = studies / "tshirt-footprint"
    (study / "scenarios").mkdir(parents=True)
    (study / "study.yaml").write_text(
        STUDY_YAML.format(
            name="tshirt-footprint", unit="1 T-shirt", process="tshirt:tshirt", amount=1
        )
    )
    (study / "scenarios" / "lossy.yaml").write_text(
        "changes:\n"
        "  - parameter: loss\n    process: toy:1:market-elec  # the grid market\n    value: 1.2\n"
    )
    return study


def test_upgrade_moves_a_scenario_parameter_of_a_release_process(
    studies: Path, to: str, mapping: Path, invoke: Invoke
) -> None:
    study = _loss(studies)

    code, output = invoke("upgrade", studies / "tshirt", study, "--to", to, "--mapping", mapping)

    assert code == 0, output
    assert (study / "scenarios" / "lossy.yaml").read_text() == (
        "changes:\n"
        "  - parameter: loss\n    process: toy:2:market-elec  # the grid market\n    value: 1.2\n"
    )
    moved = hw.open(study)
    assert isinstance(moved, hw.Study)
    background = hw.load(to, method="toy-climate")
    [base] = moved.calc(background)
    [lossy] = moved.calc(background, scenario="lossy")
    assert lossy.score > base.score


def test_a_study_whose_scenario_names_a_release_process_by_a_parameter_moves_with_it(
    studies: Path, to: str, mapping: Path, invoke: Invoke
) -> None:
    _loss(studies)

    code, output = invoke("upgrade", studies / "tshirt", "--to", to, "--mapping", mapping)

    assert code == 1, output
    assert "study tshirt-footprint" in output and "upgrade them together" in output
