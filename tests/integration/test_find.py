"""Finding processes by name on a licensed release. Runs only when ECOINVENT_RELEASE is set.

`ECOINVENT_RELEASE` is a release folder, or `name@hash` in HEADWATER_HOME, for example
`ecoinvent-3.12-cutoff@b9428cdd8045`. The search reads two tables and loads no method, so nothing
is factorized. The test checks which process comes first and by which rule: no value or count of
the licensed data is written here. Timings are in docs/appendix/benchmarks.md, from
bench/find_timing.py.
"""

import os
from pathlib import Path

import pytest

from headwater.query import search
from headwater.query.search import SearchTable
from headwater.release import Release, find

ECOINVENT_RELEASE = os.environ.get("ECOINVENT_RELEASE")

pytestmark = pytest.mark.skipif(not ECOINVENT_RELEASE, reason="ECOINVENT_RELEASE is not set")


@pytest.fixture(scope="module")
def release() -> Release:
    assert ECOINVENT_RELEASE
    folder = Path(ECOINVENT_RELEASE)
    return Release.open(folder) if folder.is_dir() else find(ECOINVENT_RELEASE)


@pytest.fixture(scope="module")
def table(release: Release) -> SearchTable:
    return search.table_of_release(release)


def test_the_global_market_for_low_alloyed_steel_comes_first(table: SearchTable) -> None:
    found = table.find("market for steel, low-alloyed", location="GLO")

    assert found
    first = found[0]
    assert first.name == "market for steel, low-alloyed"
    assert first.location == "GLO"
    assert first.why == "name is"
    assert first.kind == "market_activity"
    assert first.process_id.startswith("ecoinvent:")


def test_the_same_name_without_a_place_lists_every_place_before_any_other_rule(
    table: SearchTable,
) -> None:
    found = table.find("market for steel, low-alloyed", limit=None)

    exact = [m for m in found if m.why == "name is"]
    assert exact
    assert [m.why for m in found[: len(exact)]] == ["name is"] * len(exact)
    assert {m.name for m in exact} == {"market for steel, low-alloyed"}
    assert [m.location for m in exact] == sorted(m.location for m in exact)


def test_words_find_the_same_market(table: SearchTable) -> None:
    found = table.find("steel low-alloyed market", location="GLO")

    assert "market for steel, low-alloyed" in {m.name for m in found}
    assert all(m.why == "every word in name" for m in found if m.name.startswith("market for"))
