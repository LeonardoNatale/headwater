"""`headwater calc`: scores with provenance, on the background the study pins or one given."""

import re
import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

import headwater as hw
from headwater.release import Release
from tests.conftest import open_database, toy_database

Invoke = Callable[..., tuple[int, str]]

TOY = Path(__file__).resolve().parent.parent / "fixtures" / "toy"
REL = 1e-9


def edit(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    assert old in text, f"{old!r} not found in {path}"
    path.write_text(text.replace(old, new, 1))


def study(toy: Path, name: str = "tshirt") -> Path:
    return toy_database(toy, name)


def test_the_toy_tshirt_scores_1_19205_with_its_unit_and_names(
    toy: Path, toy_args: list[str], invoke: Invoke
) -> None:
    code, output = invoke("calc", study(toy), *toy_args)

    assert code == 0, output
    assert "T-shirt (tshirt:tshirt)" in output
    assert "Climate change: 1.19205 kg CO2-eq" in output
    assert "Traceback" not in output


def test_the_provenance_is_the_results_provenance(
    toy: Path, toy_args: list[str], invoke: Invoke
) -> None:
    code, output = invoke("calc", study(toy), *toy_args)
    background = hw.load(
        TOY / "background", method=TOY / "methods" / "toy-climate", namespace="toy"
    )
    provenance = open_database(study(toy)).calc(background, "tshirt:tshirt").provenance

    assert code == 0, output
    assert f"input hash: {provenance.input_hash}" in output
    assert f"method hash: {provenance.method_hash}" in output
    assert f"engine: headwater {hw.__version__}" in output
    assert "method: toy-climate 1" in output
    assert "residual: " in output
    assert "solver: " in output
    assert "not characterised flows: 0" in output
    assert re.search(r"residual: \d\.\d+e-\d+|residual: 0", output)


def test_the_background_comes_from_the_pin_when_no_release_is_given(
    toy: Path, toy_release: Release, pin_to: Callable[[Release], str], invoke: Invoke
) -> None:
    pin = pin_to(toy_release)

    code, output = invoke("calc", study(toy))

    assert code == 0, output
    assert "Climate change: 1.19205 kg CO2-eq" in output
    assert f"background: {toy_release.name}@{toy_release.hash}" in output
    assert pin.split("@")[0] in output


def test_a_release_given_by_name_and_hash_wins_over_the_pin(
    toy: Path, toy_release: Release, invoke: Invoke
) -> None:
    edit(
        study(toy) / "database.yaml",
        "toy-background@fixture",
        f"toy-background@{toy_release.hash[:12]}",
    )

    code, output = invoke(
        "calc", study(toy), "--release", f"toy-background@{toy_release.hash[:16]}"
    )

    assert code == 0, output
    assert "Climate change: 1.19205 kg CO2-eq" in output


def test_a_release_given_as_a_path(
    toy: Path, toy_release: Release, pin_to: Callable[[Release], str], invoke: Invoke
) -> None:
    pin_to(toy_release)

    code, output = invoke(
        "calc", study(toy), "--release", toy_release.path, "--method", "toy-climate"
    )

    assert code == 0, output
    assert "Climate change: 1.19205 kg CO2-eq" in output


def test_a_pin_that_names_no_release_says_to_pass_release(
    toy: Path, home: Path, invoke: Invoke
) -> None:
    code, output = invoke("calc", study(toy))

    assert code == 1
    assert "toy-background@fixture" in output
    assert "does not name a release" in output
    assert "--release" in output
    assert "Traceback" not in output


def test_a_release_that_is_not_in_the_cache_says_how_to_pull_it(
    toy: Path, home: Path, invoke: Invoke
) -> None:
    edit(study(toy) / "database.yaml", "toy-background@fixture", "toy-background@0123456789ab")

    code, output = invoke("calc", study(toy))

    assert code == 1
    assert "toy-background@0123456789ab" in output
    assert "is not in" in output
    assert "headwater release pull" in output
    assert "Traceback" not in output


def test_a_pin_that_is_not_the_release_given_shows_the_line_to_write(
    toy: Path, toy_release: Release, invoke: Invoke
) -> None:
    edit(study(toy) / "database.yaml", "toy-background@fixture", "toy-background@000000000000")

    code, output = invoke("calc", study(toy), "--release", toy_release.path)

    assert code == 1
    assert "toy-background@000000000000" in output
    assert f"background: toy-background@{toy_release.hash[:12]}" in output
    assert "Traceback" not in output


def test_a_study_that_does_not_validate_lists_what_is_wrong(
    toy: Path, toy_args: list[str], invoke: Invoke
) -> None:
    edit(study(toy) / "processes" / "tshirt.csv", "toy:fabric-production", "toy:nowhere")

    code, output = invoke("calc", study(toy), *toy_args)

    assert code == 1
    assert "toy:nowhere" in output
    assert "tshirt:tshirt line e02" in output
    assert "Traceback" not in output


def test_a_folder_background_needs_a_method_folder(toy: Path, invoke: Invoke) -> None:
    code, output = invoke("calc", study(toy), "--release", TOY / "background", "--namespace", "toy")

    assert code == 1
    assert "method" in output
    assert "Traceback" not in output


def test_the_method_given_on_the_command_line_is_used(
    toy: Path, toy_release: Release, pin_to: Callable[[Release], str], invoke: Invoke
) -> None:
    pin_to(toy_release)

    code, output = invoke("calc", study(toy), "--method", "no-such-method")

    assert code == 1
    assert "no-such-method" in output
    assert "toy-climate" in output  # what the release holds
    assert "Traceback" not in output


def two_indicators(tmp_path: Path) -> Path:
    method = tmp_path / "two-indicators"
    shutil.copytree(TOY / "methods" / "toy-climate", method)
    edit(
        method / "method.yaml",
        "    unit: kg CO2-eq\n",
        "    unit: kg CO2-eq\n  - id: methane\n    name: Methane\n    unit: kg CH4\n",
    )
    with (method / "factors.csv").open("a") as handle:
        handle.write("methane,toy:ch4,,1\n")
    return method


def test_every_indicator_of_the_method_is_calculated(
    toy: Path, tmp_path: Path, invoke: Invoke
) -> None:
    method = two_indicators(tmp_path)

    code, output = invoke(
        "calc",
        study(toy),
        "--release",
        TOY / "background",
        "--method",
        method,
        "--namespace",
        "toy",
    )

    assert code == 0, output
    assert "Climate change: 1.19205 kg CO2-eq" in output
    assert re.search(r"Methane: +0.00225 kg CH4", output)  # 0.25 × 0.007 + 0.5 × 0.001


def test_one_indicator_can_be_asked_for(toy: Path, tmp_path: Path, invoke: Invoke) -> None:
    method = two_indicators(tmp_path)
    args = ("--release", TOY / "background", "--method", method, "--namespace", "toy")

    code, output = invoke("calc", study(toy), *args, "--indicator", "methane")

    assert code == 0, output
    assert "Methane: 0.00225 kg CH4" in output
    assert "Climate change" not in output


def test_several_indicators_can_be_asked_for(toy: Path, tmp_path: Path, invoke: Invoke) -> None:
    method = two_indicators(tmp_path)
    edit(
        method / "method.yaml",
        "    unit: kg CH4\n",
        "    unit: kg CH4\n  - id: carbon-dioxide\n    name: Carbon dioxide\n    unit: kg CO2\n",
    )
    with (method / "factors.csv").open("a") as handle:
        handle.write("carbon-dioxide,toy:co2,,1\n")
    args = ("--release", TOY / "background", "--method", method, "--namespace", "toy")

    code, output = invoke(
        "calc", study(toy), *args, "--indicator", "carbon-dioxide", "--indicator", "climate-change"
    )

    assert code == 0, output
    scores = output.split("Provenance")[0]
    rows = [line.split(":")[0].strip() for line in scores.splitlines() if line.startswith("  ")]
    # in the method's order, whatever the order of the options
    assert rows == ["Climate change", "Carbon dioxide"]


def test_an_indicator_the_method_does_not_have_is_a_usage_error(
    toy: Path, toy_args: list[str], invoke: Invoke
) -> None:
    code, output = invoke("calc", study(toy), *toy_args, "--indicator", "climat-change")

    assert code == 2
    assert "climat-change" in output
    assert "climate-change" in output  # the closest one, and the list
    assert "Traceback" not in output


def test_a_scenario_that_the_study_does_not_have_lists_the_ones_it_has(
    toy: Path, toy_args: list[str], invoke: Invoke
) -> None:
    code, output = invoke(
        "calc",
        toy / "tshirt-grid-scenario" / "tshirt-footprint",
        *toy_args,
        "--scenario",
        "clean-grd",
    )

    assert code == 2
    assert "clean-grd" in output
    assert "clean-grid" in output
    assert "Traceback" not in output


@pytest.mark.parametrize("study_name", ["tshirt", "tshirt-waste"])
def test_calc_prints_what_the_python_api_gives(
    toy: Path, toy_args: list[str], invoke: Invoke, study_name: str
) -> None:
    background = hw.load(
        TOY / "background", method=TOY / "methods" / "toy-climate", namespace="toy"
    )
    score = open_database(study(toy, study_name)).calc(background, "tshirt:tshirt").score

    code, output = invoke("calc", study(toy, study_name), *toy_args)

    assert code == 0, output
    assert f"Climate change: {score:.6g} kg CO2-eq" in output


def test_a_global_scenario_scores_the_whole_world_changed(
    toy: Path, toy_args: list[str], invoke: Invoke
) -> None:
    code, output = invoke(
        "calc",
        toy / "tshirt-grid-scenario" / "tshirt-footprint",
        *toy_args,
        "--scenario",
        "clean-grid",
    )

    assert code == 0, output
    assert "Climate change: 0.94205 kg CO2-eq" in output  # docs/design.md: the grid emits 0.3
    assert "scenario: clean-grid" in output


def test_a_scenario_that_sets_a_parameter(toy: Path, toy_args: list[str], invoke: Invoke) -> None:
    code, output = invoke(
        "calc",
        toy / "tshirt-parameter" / "tshirt-footprint",
        *toy_args,
        "--scenario",
        "more-fabric",
    )

    assert code == 0, output
    assert "Climate change: 1.37748 kg CO2-eq" in output
    assert "scenario: more-fabric" in output


def test_a_scenario_on_a_database_is_a_usage_error(
    toy: Path, toy_args: list[str], invoke: Invoke
) -> None:
    code, output = invoke(
        "calc", toy / "tshirt-grid-scenario" / "tshirt", *toy_args, "--scenario", "clean-grid"
    )

    assert code == 2
    assert "holds no scenarios" in output
    assert "Traceback" not in output


def test_without_a_scenario_the_study_is_scored_as_it_is(
    toy: Path, toy_args: list[str], invoke: Invoke
) -> None:
    code, output = invoke("calc", toy / "tshirt-grid-scenario" / "tshirt", *toy_args)

    assert code == 0, output
    assert "Climate change: 1.19205 kg CO2-eq" in output
    assert "scenario:" not in output


def test_a_local_override_is_calculated(toy: Path, toy_args: list[str], invoke: Invoke) -> None:
    code, output = invoke("calc", toy / "tshirt-local-electricity" / "tshirt", *toy_args)

    assert code == 0, output
    assert "Climate change: 1.09205 kg CO2-eq" in output  # docs/design.md: local override


def test_a_method_id_is_not_taken_for_a_folder_of_the_same_name(
    toy: Path,
    toy_release: Release,
    pin_to: Callable[[Release], str],
    invoke: Invoke,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pin_to(toy_release)
    (toy / "toy-climate").mkdir()  # a folder, but not a method folder
    monkeypatch.chdir(toy)

    code, output = invoke("calc", study(toy), "--method", "toy-climate")

    assert code == 0, output
    assert "Climate change: 1.19205 kg CO2-eq" in output


def test_the_indicators_are_one_aligned_row_each(toy: Path, tmp_path: Path, invoke: Invoke) -> None:
    method = two_indicators(tmp_path)
    args = ("--release", TOY / "background", "--method", method, "--namespace", "toy")

    _, output = invoke("calc", study(toy), *args)

    rows = [line for line in output.splitlines() if line.startswith(("  Climate", "  Methane"))]
    assert len(rows) == 2
    assert rows[0].index("1.19205") == rows[1].index("0.00225")
