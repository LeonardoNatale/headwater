"""`headwater find`: the id of a background process, from part of its name, product or place.

Each match is one line on standard output, so a script can read the first word. Notes (more
matches than `--limit`, no match) go to standard error. `--json` prints the matches as an object.
"""

import json
from collections.abc import Callable
from pathlib import Path

from headwater.release import Release

Invoke = Callable[..., tuple[int, str]]

ELECTRICITY_LINE = (
    "toy:electricity-production  electricity production | electricity | GLO  kWh  "
    "(name starts with)"
)


def edit(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    assert old in text, f"{old!r} not found in {path}"
    path.write_text(text.replace(old, new, 1))


def ref(release: Release) -> str:
    return f"{release.name}@{release.hash[:12]}"


def test_find_lists_the_process_and_the_rule_that_matched(
    toy_release: Release, invoke_stdout: Invoke
) -> None:
    code, output = invoke_stdout("find", "electricity", "--release", ref(toy_release))

    assert code == 0, output
    assert output == ELECTRICITY_LINE + "\n"


def test_find_takes_a_release_folder_and_needs_no_method(
    toy_release: Release, invoke_stdout: Invoke
) -> None:
    code, output = invoke_stdout("find", "electricity", "--release", toy_release.path)

    assert code == 0, output
    assert output == ELECTRICITY_LINE + "\n"


def test_find_takes_a_folder_of_process_files(toy: Path, invoke_stdout: Invoke) -> None:
    code, output = invoke_stdout(
        "find", "electricity", "--release", toy / "background", "--namespace", "toy"
    )

    assert code == 0, output
    assert output == ELECTRICITY_LINE + "\n"


def test_the_matches_come_in_the_order_of_the_rules(
    toy_release: Release, invoke_stdout: Invoke
) -> None:
    code, output = invoke_stdout("find", "production", "--release", ref(toy_release))

    assert code == 0, output
    assert output.splitlines() == [
        "toy:fabric-production  cotton fabric production | cotton fabric | GLO  kg  "
        "(every word in name)",
        "toy:electricity-production  electricity production | electricity | GLO  kWh  "
        "(every word in name)",
    ]


def test_json_lists_the_same_matches(toy_release: Release, invoke_stdout: Invoke) -> None:
    code, output = invoke_stdout("find", "electricity", "--release", ref(toy_release), "--json")

    assert code == 0, output
    assert json.loads(output) == {
        "format": "headwater-find/1",
        "matches": [
            {
                "process_id": "toy:electricity-production",
                "flow_id": "toy:electricity",
                "name": "electricity production",
                "reference_product": "electricity",
                "location": "GLO",
                "unit": "kWh",
                "kind": "ordinary_transforming_activity",
                "why": "name starts with",
            }
        ],
    }


def test_location_product_and_kind_filter(toy: Path, invoke_stdout: Invoke) -> None:
    processes = toy / "background" / "processes.csv"
    edit(processes, "electricity production,GLO", "electricity production,RER")
    edit(
        processes,
        "cotton fabric production,GLO,ordinary_transforming_activity",
        "cotton fabric production,GLO,market_activity",
    )
    args = ("--release", toy / "background", "--namespace", "toy")

    _, in_rer = invoke_stdout("find", "production", *args, "--location", "RER")
    _, of_cotton = invoke_stdout("find", "production", *args, "--product", "cotton")
    _, markets = invoke_stdout("find", "production", *args, "--kind", "market_activity")
    _, none = invoke_stdout(
        "find", "production", *args, "--location", "RER", "--kind", "market_activity"
    )

    assert [line.split()[0] for line in in_rer.splitlines()] == ["toy:electricity-production"]
    assert [line.split()[0] for line in of_cotton.splitlines()] == ["toy:fabric-production"]
    assert [line.split()[0] for line in markets.splitlines()] == ["toy:fabric-production"]
    assert none == ""


def test_limit_shows_the_best_and_says_there_are_more(
    toy_release: Release, invoke: Invoke, invoke_stdout: Invoke
) -> None:
    args = ("find", "production", "--release", ref(toy_release), "--limit", "1")

    _, shown = invoke_stdout(*args)
    code, everything = invoke(*args)

    assert code == 0
    assert shown.splitlines() == [
        "toy:fabric-production  cotton fabric production | cotton fabric | GLO  kg  "
        "(every word in name)"
    ]
    assert "More processes match: raise --limit to see them." in everything


def test_limit_that_shows_every_match_says_nothing_more(
    toy_release: Release, invoke: Invoke
) -> None:
    code, output = invoke("find", "production", "--release", ref(toy_release), "--limit", "2")

    assert code == 0
    assert "More processes" not in output


def test_json_holds_only_the_matches_shown(toy_release: Release, invoke_stdout: Invoke) -> None:
    code, output = invoke_stdout(
        "find", "production", "--release", ref(toy_release), "--limit", "1", "--json"
    )

    assert code == 0, output
    assert [m["process_id"] for m in json.loads(output)["matches"]] == ["toy:fabric-production"]


def test_no_match_is_an_answer_not_a_failure(
    toy_release: Release, invoke: Invoke, invoke_stdout: Invoke
) -> None:
    code, output = invoke("find", "steel", "--release", ref(toy_release))
    _, stdout = invoke_stdout("find", "steel", "--release", ref(toy_release))
    _, as_json = invoke_stdout("find", "steel", "--release", ref(toy_release), "--json")

    assert code == 0
    assert "No process matches 'steel'." in output
    assert stdout == ""
    assert json.loads(as_json) == {"format": "headwater-find/1", "matches": []}


def test_an_unknown_location_stops_with_the_places_the_release_has(
    toy_release: Release, invoke: Invoke, invoke_stdout: Invoke
) -> None:
    args = ("find", "electricity", "--release", ref(toy_release), "--location", "GL0")

    code, output = invoke(*args)
    _, stdout = invoke_stdout(*args)

    assert code == 2
    assert "'GL0'" in output
    assert "Did you mean 'GLO'?" in output
    assert stdout == ""


def test_an_unknown_kind_stops_with_the_kinds(toy_release: Release, invoke: Invoke) -> None:
    code, output = invoke(
        "find", "electricity", "--release", ref(toy_release), "--kind", "market_activty"
    )

    assert code == 2
    assert "Did you mean 'market_activity'?" in output


def test_empty_text_is_a_usage_error(toy_release: Release, invoke: Invoke) -> None:
    code, output = invoke("find", " ", "--release", ref(toy_release))

    assert code == 2
    assert "text to look for" in output


def test_a_release_that_is_not_in_the_cache_says_how_to_pull_it(home: Path, invoke: Invoke) -> None:
    code, output = invoke("find", "electricity", "--release", "toy-background@0123456789ab")

    assert code == 1
    assert "headwater release pull" in output


def test_release_is_needed(invoke: Invoke) -> None:
    code, output = invoke("find", "electricity")

    assert code == 2
    assert "--release" in output


def test_limit_must_be_positive(toy_release: Release, invoke: Invoke) -> None:
    code, _ = invoke("find", "electricity", "--release", ref(toy_release), "--limit", "0")

    assert code == 2


def test_a_namespace_for_a_release_is_a_usage_error(toy_release: Release, invoke: Invoke) -> None:
    code, output = invoke(
        "find", "electricity", "--release", ref(toy_release), "--namespace", "toy"
    )

    assert code == 2
    assert "namespace is for a folder of process files" in output
