"""The calls the M6 notebook trial asked for, on ecoinvent 3.12 with EF v3.1: Brightway's direct and
upstream, the exact lookup, flow names on release lines, and locations and tables of contribution
trees.

Runs only when these are set:

- `ECOINVENT_LCIA`: the workbook, for example `.../LCIA Implementation 3.12.xlsx`
- `ECOINVENT_RELEASE`: the release of the same version, a folder or `name@hash` in
  HEADWATER_HOME, for example `ecoinvent-3.12-cutoff@b9428cdd8045`

Each check compares two roads to one number, so no value of the licensed files is written here.
"""

import os
from pathlib import Path

import pytest

import headwater as hw
from headwater.io.lcia import import_method, read_workbook, write_method
from headwater.query._internals import internals
from headwater.query.search import SearchTable
from headwater.release import Release, find

ECOINVENT_LCIA = os.environ.get("ECOINVENT_LCIA")
ECOINVENT_RELEASE = os.environ.get("ECOINVENT_RELEASE")

pytestmark = pytest.mark.skipif(
    not (ECOINVENT_LCIA and ECOINVENT_RELEASE),
    reason="ECOINVENT_LCIA and ECOINVENT_RELEASE are not set",
)

SAMPLE = 12
"""How many processes are checked: every n-th by id, so the same ones each run."""


@pytest.fixture(scope="module")
def bg(tmp_path_factory: pytest.TempPathFactory) -> hw.Background:
    assert ECOINVENT_LCIA and ECOINVENT_RELEASE
    folder = Path(ECOINVENT_RELEASE).expanduser()
    release = Release.open(folder) if folder.is_dir() else find(ECOINVENT_RELEASE)
    imported = import_method(read_workbook(ECOINVENT_LCIA, "EF v3.1"), release)
    method = write_method(imported, tmp_path_factory.mktemp("methods") / "EF-3.1")
    return hw.load(release.path, method=method)


@pytest.fixture(scope="module")
def table(bg: hw.Background) -> SearchTable:
    return internals(bg).search


@pytest.fixture(scope="module")
def sample(table: SearchTable) -> list[str]:
    ids = sorted(table.ids)
    step = max(len(ids) // SAMPLE, 1)
    return ids[::step][:SAMPLE]


@pytest.fixture(scope="module")
def climate(bg: hw.Background) -> str:
    return next(i.id for i in bg.indicators if i.id.startswith("climate-change"))


def test_direct_is_the_own_row_of_where_and_upstream_the_others(
    bg: hw.Background, sample: list[str], climate: str
) -> None:
    for process_id in sample:
        direct, upstream = bg.direct_and_upstream(process_id, indicator=climate)
        rows = bg.where(process_id, indicator=climate, top=None)
        own = sum(e.score for e in rows.processes if e.process_id == process_id)
        others = sum(e.score for e in rows.processes if e.process_id != process_id)
        size = abs(rows.score) or 1.0
        assert abs(direct - own) <= 1e-9 * size, process_id
        assert abs(upstream - others) <= 1e-6 * size, process_id


def test_each_process_is_found_again_by_its_name_product_and_location(
    bg: hw.Background, table: SearchTable, sample: list[str]
) -> None:
    row = {process_id: i for i, process_id in enumerate(table.ids)}
    for process_id in sample:
        i = row[process_id]
        try:
            found = bg.get_process(
                table.names[i], product=table.products[i], location=table.locations[i]
            )
        except ValueError as error:  # ecoinvent's key is unique, so this names the twins
            pytest.fail(f"{process_id}: {error}")
        assert found.process_id == process_id
        (process,) = bg.processes([process_id]).values()
        assert found.flow_id == process.reference_exchange.flow_id


def test_every_line_of_a_release_process_names_its_flow(
    bg: hw.Background, sample: list[str]
) -> None:
    for process in bg.processes(sample).values():
        assert all(e.flow_name for e in process.exchanges), process.id


def test_contribution_trees_have_locations_and_their_table_adds_up(
    bg: hw.Background, table: SearchTable, sample: list[str], climate: str
) -> None:
    row = {process_id: i for i, process_id in enumerate(table.ids)}
    for process_id in sample:
        tree = bg.contributions(process_id, indicator=climate, max_depth=2)
        assert tree.location == table.locations[row[process_id]]
        rows = tree.to_dataframe().to_dict("records")
        children = [r["score"] for r in rows if r["depth"] == 1]
        added = rows[0]["direct"] + sum(children) + rows[0]["rest"]
        assert abs(added - rows[0]["score"]) <= 1e-9 * (abs(rows[0]["score"]) or 1.0)
