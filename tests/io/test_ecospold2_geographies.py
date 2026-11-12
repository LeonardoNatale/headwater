"""ecoinvent's geographies, read from MasterData/Geographies.xml into the release."""

import os
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pyarrow as pa
import pytest

from headwater.io.ecospold2 import read_ecospold2
from headwater.io.ecospold2.geographies import read_geographies
from headwater.io.exclude import read_exclude
from headwater.io.inventory import Inventory
from headwater.model import InvalidDataError
from headwater.release.schema import GEOGRAPHIES

ECOINVENT_DIR = os.environ.get("ECOINVENT_DIR")

FIXTURE = {
    "CN-SX": "China, Shanxi (山西)",
    "DE": "Germany",
    "ES": "Spain",
    "GLO": "Global",
    "RER": "Europe",
    "RoW": "Rest-of-World",
    "TT": "Trinidad and Tobago",
}


def _problems(error: pytest.ExceptionInfo[InvalidDataError]) -> list[tuple[str, str]]:
    return [(p.code, p.item) for p in error.value.problems]


def test_geographies_are_read_by_code(fx) -> None:
    table = read_geographies(fx.source / "MasterData" / "Geographies.xml")

    assert table.schema == GEOGRAPHIES.schema
    assert dict(zip(*table.select(["code", "name"]).to_pydict().values(), strict=True)) == FIXTURE
    assert table.column("code").to_pylist() == sorted(FIXTURE)


def test_the_release_holds_the_geographies(source: Inventory, tmp_path: Path) -> None:
    release = source.freeze(into=tmp_path)

    assert release.table("geographies").equals(source.geographies)
    con = release.connect()
    assert con.execute("SELECT name FROM geographies WHERE code = 'RoW'").fetchone() == (
        "Rest-of-World",
    )
    missing = con.execute(
        "SELECT DISTINCT location FROM processes WHERE location NOT IN "
        "(SELECT code FROM geographies)"
    ).fetchall()
    assert missing == []


@pytest.mark.parametrize(
    ("old", "new", "code"),
    [
        ('<shortname xml:lang="en">TT</shortname>', "", "invalid-masterdata"),
        ('<name xml:lang="en">Spain</name>', "", "invalid-masterdata"),
        (
            '<shortname xml:lang="en">TT</shortname>',
            '<shortname xml:lang="en">ES</shortname>',
            "duplicate-geography",
        ),
    ],
)
def test_a_bad_geography_is_named(
    source_copy: Callable[..., Path], old: str, new: str, code: str
) -> None:
    folder = source_copy(edit={"MasterData/Geographies.xml": [(old, new)]})

    with pytest.raises(InvalidDataError) as raised:
        read_geographies(folder / "MasterData" / "Geographies.xml")

    [problem] = raised.value.problems
    assert problem.code == code
    assert problem.item.startswith("MasterData/Geographies.xml geography a4000000-")


def test_a_source_without_geographies_is_named(source_copy: Callable[..., Path]) -> None:
    folder = source_copy()
    (folder / "MasterData" / "Geographies.xml").unlink()

    with pytest.raises(InvalidDataError) as raised:
        read_ecospold2(
            folder, version="3.10", exclude=read_exclude(folder / "exclude.csv"), workers=1
        )

    assert _problems(raised) == [("missing-file", "MasterData/Geographies.xml")]


@pytest.mark.parametrize(
    ("row", "code", "item"),
    [
        ({"code": "ES", "name": "Spain again"}, "duplicate-geography", "geography ES"),
        ({"code": "XX", "name": ""}, "invalid-field", "geography XX"),
        ({"code": " XX", "name": "Nowhere"}, "invalid-field", "geography  XX"),
    ],
)
def test_freezing_refuses_a_bad_geography_row(
    source: Inventory, tmp_path: Path, row: dict[str, str], code: str, item: str
) -> None:
    bad = pa.concat_tables(
        [source.geographies, pa.Table.from_pylist([row], schema=GEOGRAPHIES.schema)]
    )

    with pytest.raises(InvalidDataError) as raised:
        replace(source, geographies=bad).freeze(into=tmp_path)

    assert _problems(raised) == [(code, item)]


@pytest.mark.skipif(not ECOINVENT_DIR, reason="ECOINVENT_DIR is not set")
def test_the_real_geographies_are_read() -> None:
    assert ECOINVENT_DIR
    table = read_geographies(Path(ECOINVENT_DIR) / "MasterData" / "Geographies.xml")
    names = dict(zip(*table.to_pydict().values(), strict=True))

    print(f"\n{table.num_rows} geographies")
    assert table.num_rows == 541
    assert (names["GLO"], names["RoW"], names["RER"]) == ("Global", "Rest-of-World", "Europe")
