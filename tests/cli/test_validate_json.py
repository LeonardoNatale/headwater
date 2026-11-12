"""`headwater validate --json`: what the text says, as data, for CI to read.

`headwater-validate/2`: `format`, `path`, `kind`, `valid`, `background`, `method`, `errors` and
`warnings`. Within version 2 keys may be added; none is renamed or removed.
"""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from headwater.release import Release
from tests.conftest import toy_database

Invoke = Callable[..., tuple[int, str]]

TOY = Path(__file__).resolve().parent.parent / "fixtures" / "toy"
KEYS = {
    "format", "path", "kind", "valid", "background", "method", "methods", "errors", "warnings",
}  # fmt: skip
PROBLEM_KEYS = {"code", "item", "message", "location", "text"}


def edit(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    assert old in text, f"{old!r} not found in {path}"
    path.write_text(text.replace(old, new, 1))


def study(toy: Path, name: str = "tshirt") -> Path:
    return toy_database(toy, name)


def run(invoke_stdout: Invoke, *args: str | Path) -> tuple[int, dict[str, Any]]:
    code, output = invoke_stdout("validate", *args, "--json")
    return code, json.loads(output)


def test_a_valid_study_has_the_shape_and_nothing_in_its_lists(
    toy: Path, toy_args: list[str], invoke_stdout: Invoke
) -> None:
    code, payload = run(invoke_stdout, study(toy), *toy_args)

    assert code == 0
    assert set(payload) == KEYS
    assert payload["format"] == "headwater-validate/2"
    assert (payload["path"], payload["kind"]) == (str(study(toy)), "database")
    assert payload["valid"] is True
    assert payload["background"] == {"release": None, "folder": str(TOY / "background")}
    assert payload["method"] == {"id": "toy-climate", "version": "1"}
    assert payload["methods"] == ["toy-climate"]
    assert payload["errors"] == [] and payload["warnings"] == []


def test_errors_carry_their_code_item_message_place_and_the_text_the_command_prints(
    toy: Path, toy_args: list[str], invoke: Invoke, invoke_stdout: Invoke
) -> None:
    path = study(toy) / "processes" / "tshirt.csv"
    edit(path, "toy:fabric-production", "toy:fabric-productoin")
    edit(path, "0.5,kWh", "0.5,MJ2")

    code, payload = run(invoke_stdout, study(toy), *toy_args)
    _, text = invoke("validate", study(toy), *toy_args)

    assert code == 1
    assert payload["valid"] is False
    first, second = payload["errors"]
    assert set(first) == PROBLEM_KEYS
    assert first["code"] == "supplier-not-found"
    assert first["item"] == "tshirt:tshirt line e02"
    assert "toy:fabric-productoin" in first["message"]
    assert "Did you mean 'toy:fabric-production'" in first["message"]
    assert "MJ2" in second["message"]
    printed = [x for x in text.splitlines() if x.startswith("error: ")]
    assert [e["text"] for e in payload["errors"]] == [x.removeprefix("error: ") for x in printed]


def test_a_warning_is_listed_and_the_study_is_still_valid(
    toy: Path, toy_args: list[str], invoke_stdout: Invoke
) -> None:
    (study(toy) / "no_factor.csv").write_text(
        "flow,flow_name,method,reason,who,when\ntoy:co2,,toy-climate,thought so,Sam,2026-10-12\n"
    )

    code, payload = run(invoke_stdout, study(toy), *toy_args)

    assert code == 0
    assert payload["valid"] is True
    [warning] = payload["warnings"]
    assert warning["code"] == "no-factor-not-needed"
    assert warning["location"] == "no_factor.csv"
    assert warning["text"].startswith("flow toy:co2: row not needed")
    assert warning["text"].endswith("[no_factor.csv]")


def test_a_study_that_cannot_be_read_gives_its_errors_and_no_background(
    toy: Path, toy_args: list[str], invoke_stdout: Invoke
) -> None:
    edit(study(toy) / "processes" / "tshirt.csv", "0.25", "lots")

    code, payload = run(invoke_stdout, study(toy), *toy_args)

    assert code == 1
    assert payload["valid"] is False
    assert payload["kind"] is None
    assert payload["background"] is None and payload["method"] is None
    assert payload["methods"] is None
    assert any("lots" in e["text"] for e in payload["errors"])
    assert any(e["location"] == "processes/tshirt.csv:3" for e in payload["errors"])


def test_the_release_is_named_in_full(
    toy: Path, toy_release: Release, invoke_stdout: Invoke
) -> None:
    edit(
        study(toy) / "database.yaml",
        "toy-background@fixture",
        f"toy-background@{toy_release.hash[:12]}",
    )

    code, payload = run(invoke_stdout, study(toy))

    assert code == 0
    assert payload["background"] == {
        "release": f"toy-background@{toy_release.hash}",
        "folder": None,
    }


def test_a_background_that_cannot_be_loaded_prints_nothing_to_standard_output(
    toy: Path, home: Path, invoke: Invoke, invoke_stdout: Invoke
) -> None:
    edit(study(toy) / "database.yaml", "toy-background@fixture", "toy-background@0123456789ab")

    code, stdout = invoke_stdout("validate", study(toy), "--json")
    _, both = invoke("validate", study(toy), "--json")

    assert code == 1
    assert stdout == ""
    assert "headwater release pull" in both


def test_the_text_is_unchanged_without_the_flag(
    toy: Path, toy_args: list[str], invoke: Invoke
) -> None:
    code, output = invoke("validate", study(toy), *toy_args)

    assert code == 0
    assert output.splitlines()[-1] == "Database tshirt: valid."
    assert output.splitlines()[0].startswith("Background: folder ")


def test_a_study_says_its_kind(toy_args: list[str], invoke_stdout: Invoke) -> None:
    path = TOY / "tshirt-dyed" / "tshirt-footprint"

    code, payload = run(invoke_stdout, path, *toy_args)

    assert code == 0, payload
    assert (payload["path"], payload["kind"], payload["valid"]) == (str(path), "study", True)
