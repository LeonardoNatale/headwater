"""A real database moved from one ecoinvent release to the next. Runs only when the variables
below are set: the releases, methods and database are licensed and local.

| Variable | Holds |
|---|---|
| `HEADWATER_UPGRADE_DATABASE` | a database folder pinned to the old release, valid on it |
| `HEADWATER_UPGRADE_TO` | the release to move to, `name@hash`, in HEADWATER_HOME |
| `HEADWATER_UPGRADE_MAPPING` | optional: the reviewed rows for what the rules do not map |
| `HEADWATER_UPGRADE_OLD_METHOD`, `HEADWATER_UPGRADE_METHOD` | the method folder of each release |
| `HEADWATER_UPGRADE_EXPECTED` | optional: the same database, built on the new release apart |

Exit test of M4: every supplier maps, the upgraded database validates, and every score that
moved by more than 1% is explained: what the release changed adds up line by line, and the rest
is the database's. No value of the licensed data is written here.
"""

import csv
import os
from pathlib import Path

import pytest

import headwater as hw
from headwater.diff import StudyDiff, diff_databases
from headwater.study import DatabaseFolder, read_database
from headwater.upgrade import Plan, Rule, plan

DATABASE = os.environ.get("HEADWATER_UPGRADE_DATABASE")
TO = os.environ.get("HEADWATER_UPGRADE_TO")
MAPPING = os.environ.get("HEADWATER_UPGRADE_MAPPING")
OLD_METHOD = os.environ.get("HEADWATER_UPGRADE_OLD_METHOD")
METHOD = os.environ.get("HEADWATER_UPGRADE_METHOD")
EXPECTED = os.environ.get("HEADWATER_UPGRADE_EXPECTED")

pytestmark = pytest.mark.skipif(
    not (DATABASE and TO and OLD_METHOD and METHOD),
    reason="HEADWATER_UPGRADE_DATABASE, _TO, _OLD_METHOD and _METHOD are not set",
)

REL = 1e-9


@pytest.fixture(scope="module")
def old() -> DatabaseFolder:
    assert DATABASE
    return read_database(DATABASE)


@pytest.fixture(scope="module")
def planned(old: DatabaseFolder) -> Plan:
    assert TO
    return plan(old, to=TO, mapping=MAPPING)


@pytest.fixture(scope="module")
def new(planned: Plan, tmp_path_factory: pytest.TempPathFactory) -> DatabaseFolder:
    into = tmp_path_factory.mktemp("upgraded")
    planned.write(into=into)
    return read_database(into / planned.databases[0].path.name)


@pytest.fixture(scope="module")
def backgrounds(old: DatabaseFolder, new: DatabaseFolder) -> tuple[hw.Background, hw.Background]:
    assert OLD_METHOD and METHOD
    return (
        hw.load(str(old.database.background), method=Path(OLD_METHOD)),
        hw.load(str(new.database.background), method=Path(METHOD)),
    )


@pytest.fixture(scope="module")
def diff(
    old: DatabaseFolder, new: DatabaseFolder, backgrounds: tuple[hw.Background, hw.Background]
) -> StudyDiff:
    before, after = backgrounds
    return diff_databases(old, new, after, old_background=before, products="all")


def test_every_supplier_maps_and_nothing_stops_the_upgrade(planned: Plan) -> None:
    assert planned.unmapped == ()
    assert planned.problems == ()
    assert all(m.lines for m in planned.matches)
    counts = planned.counts()
    assert sum(suppliers for suppliers, _ in counts.values()) == len(planned.matches)
    assert counts[Rule.unmapped] == (0, 0)


def test_every_row_link_is_flagged(planned: Plan) -> None:
    row = {
        m.old.id
        for m in planned.matches
        if m.old.location == "RoW" or (m.new is not None and m.new.location == "RoW")
    }
    assert {f.old.id for f in planned.flags if f.kind == "row"} == row


def test_the_upgraded_study_validates_on_the_new_release(
    new: DatabaseFolder, backgrounds: tuple[hw.Background, hw.Background]
) -> None:
    assert not hw.Database(new).validate(backgrounds[1]).errors


def test_every_score_that_moved_is_explained(diff: StudyDiff) -> None:
    moved = [r for r in diff.rows if r.changed]
    assert moved
    for row in moved:
        assert row.old is not None and row.new is not None and row.change is not None
        assert row.background is not None
        scale = max(abs(row.old), abs(row.new))
        lines = sum(m.effect for m in row.moved)
        assert lines == pytest.approx(row.background, rel=REL, abs=REL * scale)
        drivers = sum(d.contribution or 0.0 for d in row.drivers)
        study = drivers + (row.interaction or 0.0)
        assert row.background + study == pytest.approx(row.change, rel=REL, abs=REL * scale)
        if row.percent is not None and abs(row.percent) > 1:
            assert row.moved or row.drivers, row.product_id


@pytest.mark.skipif(not EXPECTED, reason="HEADWATER_UPGRADE_EXPECTED is not set")
def test_the_upgraded_study_is_the_one_built_on_the_new_release(
    new: DatabaseFolder,
    diff: StudyDiff,
    backgrounds: tuple[hw.Background, hw.Background],
) -> None:
    assert EXPECTED
    expected = hw.Database(read_database(EXPECTED))
    assert _lines(new.path) == _lines(Path(EXPECTED))
    after = backgrounds[1]
    for row in (r for r in diff.rows if r.indicator == after.method.indicators[0].id):
        scores = expected.scores(after, row.product_id)
        for indicator, score in scores.items():
            [ours] = [
                r.new
                for r in diff.rows
                if r.product_id == row.product_id and r.indicator == indicator
            ]
            assert ours == pytest.approx(score, rel=REL)


def _lines(folder: Path) -> dict[tuple[str, str], dict[str, str]]:
    """Every line of the study's process files, by file and line id."""
    found: dict[tuple[str, str], dict[str, str]] = {}
    for path in sorted((folder / "processes").glob("*.csv")):
        with path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                found[(path.stem, row["line"])] = row
    return found
