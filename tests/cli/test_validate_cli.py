"""`headwater validate`: errors and warnings of a database, exit 1 on errors."""

from collections.abc import Callable
from pathlib import Path

from headwater.release import Release
from tests.conftest import toy_database

Invoke = Callable[..., tuple[int, str]]


def edit(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    assert old in text, f"{old!r} not found in {path}"
    path.write_text(text.replace(old, new, 1))


def study(toy: Path, name: str = "tshirt") -> Path:
    return toy_database(toy, name)


def test_a_valid_study_exits_0(toy: Path, toy_args: list[str], invoke: Invoke) -> None:
    code, output = invoke("validate", study(toy), *toy_args)

    assert code == 0, output
    assert "tshirt" in output
    assert "valid" in output
    assert "Traceback" not in output


def test_a_warning_is_listed_and_does_not_fail(
    toy: Path, toy_args: list[str], invoke: Invoke
) -> None:
    # Carbon dioxide has a factor, so a no_factor.csv row for it is not needed.
    (study(toy) / "no_factor.csv").write_text(
        "flow,flow_name,method,reason,who,when\ntoy:co2,,toy-climate,thought so,Sam,2026-10-12\n"
    )

    code, output = invoke("validate", study(toy), *toy_args)

    assert code == 0, output
    assert "warning: flow toy:co2: row not needed" in output
    assert "1 warning" in output


def test_a_todo_the_import_left_is_a_warning_that_names_the_field(
    toy: Path, toy_args: list[str], invoke: Invoke
) -> None:
    with (study(toy) / "database.yaml").open("a") as handle:
        handle.write("description: 'TODO: say what this database holds'\n")

    code, output = invoke("validate", study(toy), *toy_args)

    assert code == 0, output
    assert (
        "warning: database tshirt: description: is still a placeholder "
        "('TODO: say what this database holds')"
    ) in output
    assert "1 warning" in output


def test_errors_are_listed_with_their_places_and_exit_1(
    toy: Path, toy_args: list[str], invoke: Invoke
) -> None:
    path = study(toy) / "exchanges.csv"
    edit(path, "toy:fabric-production", "toy:nowhere")
    edit(path, "0.5,kWh", "0.5,furlongs")

    code, output = invoke("validate", study(toy), *toy_args)

    assert code == 1
    assert "error: tshirt:tshirt line e02" in output
    assert "toy:nowhere" in output
    assert "furlongs" in output
    assert "2 errors" in output
    assert "Traceback" not in output


def test_a_study_that_cannot_be_read_is_reported_the_same_way(
    toy: Path, toy_args: list[str], invoke: Invoke
) -> None:
    edit(study(toy) / "exchanges.csv", "0.25", "lots")

    code, output = invoke("validate", study(toy), *toy_args)

    assert code == 1
    assert "lots" in output
    assert "exchanges.csv:3" in output
    assert "Traceback" not in output


def test_a_folder_that_is_not_there_is_named(
    toy: Path, toy_args: list[str], invoke: Invoke
) -> None:
    code, output = invoke("validate", toy / "studies" / "polo", *toy_args)

    assert code == 1
    assert "polo" in output
    assert "Traceback" not in output


def test_a_study_validates_against_the_release_its_pin_names(
    toy: Path,
    toy_release: Release,
    pin_to: Callable[[Release], str],
    invoke: Invoke,
) -> None:
    pin = pin_to(toy_release)

    code, output = invoke("validate", study(toy))

    assert code == 0, output
    assert pin in output or toy_release.hash[:12] in output


def test_a_pin_that_is_not_the_release_given_is_an_error(
    toy: Path, toy_release: Release, invoke: Invoke
) -> None:
    edit(study(toy) / "database.yaml", "toy-background@fixture", "toy-background@000000000000")

    code, output = invoke("validate", study(toy), "--release", toy_release.path)

    assert code == 1
    assert "background-pin-mismatch" in output or "is not the release given" in output
    assert f"background: toy-background@{toy_release.hash[:12]}" in output


def test_a_pin_that_names_no_release_says_to_pass_release(
    toy: Path, home: Path, invoke: Invoke
) -> None:
    code, output = invoke("validate", study(toy))

    assert code == 1
    assert "toy-background@fixture" in output
    assert "--release" in output
    assert "Traceback" not in output


def test_a_release_that_is_not_in_the_cache_says_how_to_pull_it(
    toy: Path, home: Path, invoke: Invoke
) -> None:
    edit(study(toy) / "database.yaml", "toy-background@fixture", "toy-background@0123456789ab")

    code, output = invoke("validate", study(toy))

    assert code == 1
    assert "toy-background@0123456789ab" in output
    assert "headwater release pull" in output
    assert "Traceback" not in output


def test_a_study_with_an_override_is_valid(toy: Path, toy_args: list[str], invoke: Invoke) -> None:
    code, output = invoke("validate", study(toy, "tshirt-local-electricity"), *toy_args)

    assert code == 0, output
    assert "valid" in output
    assert "override-not-calculated" not in output


def test_an_override_of_a_line_the_process_does_not_have_is_an_error(
    toy: Path, toy_args: list[str], invoke: Invoke
) -> None:
    folder = study(toy, "tshirt-local-electricity")
    override = folder / "overrides" / "own-wind-power.csv"
    edit(override, "toy:electricity-production,e02", "toy:electricity-production,e99")

    code, output = invoke("validate", folder, *toy_args)

    assert code == 1
    assert "toy:electricity-production line e99" in output
    assert "overrides/own-wind-power.csv" in output
    assert "Traceback" not in output


def test_a_scenario_that_changes_a_line_that_does_not_exist_is_an_error(
    toy: Path, toy_args: list[str], invoke: Invoke
) -> None:
    folder = toy / "tshirt-grid-scenario" / "tshirt-footprint"
    edit(folder / "scenarios" / "clean-grid.yaml", "line: e02", "line: e99")

    code, output = invoke("validate", folder, *toy_args)

    assert code == 1
    assert "scenario clean-grid" in output
    assert "e99" in output
