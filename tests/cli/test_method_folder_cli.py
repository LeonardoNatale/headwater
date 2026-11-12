"""`--method-folder` on every command that reads a study: for a study that names a method its
release does not hold, as the Excel import writes one when given a method folder.

The toy release in the cache holds `toy-climate` only. The T-shirt study is pinned to it and names
`toy-impacts`, whose folder is beside the toy's other methods.
"""

import os
import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

import headwater as hw
from headwater.release import Release, build
from headwater.study import read_method, read_process_folder

from .conftest import INFO

Invoke = Callable[..., tuple[int, str]]

TOY = Path(__file__).resolve().parent.parent / "fixtures" / "toy"
IMPACTS = TOY / "methods" / "toy-impacts"


@pytest.fixture
def impacts(toy: Path, toy_release: Release, pin_to: Callable[[Release], str]) -> Path:
    """The T-shirt database, pinned to the toy release, naming the method toy-impacts."""
    pin_to(toy_release)
    path = toy / "tshirt" / "tshirt"
    yaml = path / "database.yaml"
    yaml.write_text(yaml.read_text().replace("methods: [toy-climate]", "methods: [toy-impacts]"))
    return path


@pytest.mark.parametrize("command", ["validate", "calc"])
def test_without_the_folder_the_error_names_the_option_and_the_folder_layout(
    impacts: Path, invoke: Invoke, command: str
) -> None:
    code, output = invoke(command, impacts)

    assert code == 1, output
    assert "names method toy-impacts" in output
    assert "--method-folder" in output
    assert "method.yaml" in output
    assert "Traceback" not in output


def test_validate_passes_with_the_method_folder(impacts: Path, invoke: Invoke) -> None:
    code, output = invoke("validate", impacts, "--method-folder", IMPACTS)

    assert code == 0, output
    assert "method toy-impacts 1" in output
    assert "Database tshirt: valid" in output


def test_calc_scores_every_indicator_of_the_method_folder(impacts: Path, invoke: Invoke) -> None:
    code, output = invoke("calc", impacts, "--method-folder", IMPACTS)

    assert code == 0, output
    assert re.search(r"  Climate change: +1\.19205 kg CO2-eq", output)
    assert re.search(r"  Climate change, fossil: +1\.19205 kg CO2-eq", output)
    assert "Photochemical ozone formation:" in output


@pytest.mark.parametrize("command", ["contributions", "where", "sensitivity"])
def test_the_commands_on_one_indicator_take_it_too(
    impacts: Path, invoke: Invoke, command: str
) -> None:
    code, output = invoke(
        command, impacts, "--method-folder", IMPACTS, "--indicator", "climate-change"
    )

    assert code == 0, output
    assert "method toy-impacts 1" in output


def test_a_folder_for_a_method_the_release_holds_is_not_used(
    toy: Path, toy_release: Release, pin_to: Callable[[Release], str], invoke: Invoke
) -> None:
    pin_to(toy_release)  # the study names toy-climate, which the release holds

    code, output = invoke("calc", toy / "tshirt" / "tshirt", "--method-folder", IMPACTS)

    assert code == 0, output
    assert "method toy-climate 1" in output
    assert "ozone" not in output.lower()


def test_the_python_error_says_what_a_method_folder_is(toy_release: Release) -> None:
    with pytest.raises(ValueError, match="method.yaml") as raised:
        hw.load(toy_release.path, method="toy-impacts")

    assert "toy-impacts" in str(raised.value)


def test_a_method_no_folder_fills_is_named_when_another_is_filled(
    impacts: Path, toy: Path, invoke: Invoke
) -> None:
    methane = toy / "methods" / "toy-methane"
    shutil.copytree(TOY / "methods" / "toy-climate", methane)
    yaml = methane / "method.yaml"
    yaml.write_text(
        yaml.read_text()
        .replace("id: toy-climate", "id: toy-methane")
        .replace("id: climate-change", "id: methane")
    )
    (methane / "factors.csv").write_text("indicator,flow,location,factor\nmethane,toy:ch4,,1\n")
    study_yaml = impacts / "database.yaml"
    study_yaml.write_text(
        study_yaml.read_text().replace(
            "methods: [toy-impacts]", "methods: [toy-impacts, toy-methane]"
        )
    )

    code, output = invoke("calc", impacts, "--method-folder", IMPACTS)
    assert code == 1, output
    assert "names method toy-methane," in output
    assert "--method-folder" in output

    code, output = invoke("calc", impacts, "--method-folder", IMPACTS, "--method-folder", methane)
    assert code == 0, output
    assert "0.00225 kg CO2-eq" in output  # 0.25 × 0.007 + 0.5 × 0.001 kg of methane


def test_the_folders_can_be_listed_once_in_the_environment(
    impacts: Path, toy: Path, invoke: Invoke, monkeypatch: pytest.MonkeyPatch
) -> None:
    unused = toy / "methods" / "toy-climate"  # the release holds it, so it is not used
    monkeypatch.setenv("HEADWATER_METHOD_FOLDERS", os.pathsep.join([str(unused), str(IMPACTS)]))

    for command in ("validate", "calc"):
        code, output = invoke(command, impacts)
        assert code == 0, output
        assert "method toy-impacts 1" in output
        assert (
            f"Method toy-impacts: from the folder {IMPACTS}, given by HEADWATER_METHOD_FOLDERS."
            in output
        )


def test_the_folders_on_the_command_line_come_first(
    impacts: Path, tmp_path: Path, invoke: Invoke, monkeypatch: pytest.MonkeyPatch
) -> None:
    same = tmp_path / "toy-impacts"
    shutil.copytree(IMPACTS, same)  # the same method, in another folder
    monkeypatch.setenv("HEADWATER_METHOD_FOLDERS", str(same))

    code, output = invoke("validate", impacts, "--method-folder", IMPACTS)

    assert code == 0, output
    assert f"Method toy-impacts: from the folder {IMPACTS}, given by --method-folder." in output


def test_one_id_in_two_folders_that_hold_different_methods_is_an_error_naming_both(
    impacts: Path, tmp_path: Path, invoke: Invoke, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = tmp_path / "toy-impacts"
    shutil.copytree(IMPACTS, other)
    factors = other / "factors.csv"
    lines = factors.read_text().splitlines()
    lines[1] = lines[1].rsplit(",", 1)[0] + ",2"  # another factor
    factors.write_text("\n".join(lines) + "\n")
    monkeypatch.setenv("HEADWATER_METHOD_FOLDERS", str(other))

    code, output = invoke("calc", impacts, "--method-folder", IMPACTS)

    assert code == 1, output
    assert "method folders that hold different methods" in output
    assert str(IMPACTS) in output and str(other) in output


def test_the_error_names_the_environment_variable(impacts: Path, invoke: Invoke) -> None:
    code, output = invoke("validate", impacts)

    assert code == 1, output
    assert "HEADWATER_METHOD_FOLDERS" in output


def test_across_a_release_change_the_method_note_is_printed_once(
    repo: Path, toy_release: Release, invoke: Invoke
) -> None:
    """The T-shirt moves from one toy release to another of the same flow list. Each side loads
    toy-impacts from the one folder, so the note is the same and is said once."""
    folder = read_process_folder(TOY / "background", namespace="toy")
    other = build(
        INFO.model_copy(update={"version": "2"}),
        processes=folder.processes,
        flows=folder.flows,
        flow_properties=folder.flow_properties,
        methods=(read_method(TOY / "methods" / "toy-climate"),),
    )
    yaml = repo / "tshirt" / "tshirt" / "database.yaml"
    first = f"{toy_release.name}@{toy_release.hash[:12]}"
    yaml.write_text(
        yaml.read_text()
        .replace("toy-background@fixture", first)
        .replace("methods: [toy-climate]", "methods: [toy-impacts]")
    )
    run = ["git", "-c", "commit.gpgsign=false", "-C", str(repo)]
    subprocess.run([*run, "commit", "--quiet", "-am", "on the first release"], check=True)
    yaml.write_text(yaml.read_text().replace(first, f"{other.name}@{other.hash[:12]}"))
    subprocess.run([*run, "commit", "--quiet", "-am", "on the second release"], check=True)

    code, output = invoke("diff", "HEAD~1", "HEAD", "tshirt/tshirt", "--method-folder", IMPACTS)

    assert code == 0, output
    assert "before: toy-background@" in output
    assert output.count("Method toy-impacts: from the folder") == 1, output
