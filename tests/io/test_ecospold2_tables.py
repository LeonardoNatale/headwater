"""The importer hands its lines to `build_tables` as Arrow tables: no record per line."""

from collections.abc import Callable
from pathlib import Path

import pytest

from headwater.io import inventory as inventory_module
from headwater.io.ecospold2 import import_ecospold2, read_ecospold2
from headwater.io.exclude import read_exclude
from headwater.io.inventory import Inventory
from headwater.model import Process
from headwater.release import build


def test_the_tables_freeze_into_the_release_the_records_freeze_into(
    source: Inventory, tmp_path: Path
) -> None:
    from_tables = source.freeze(into=tmp_path / "tables")
    from_records = build(
        source.info,
        processes=source.processes,
        flows=source.flows,
        flow_properties=source.flow_properties,
        units=source.units,
        issues=source.issues,
        geographies=source.geographies,
        into=tmp_path / "records",
    )

    assert from_tables.hash == from_records.hash
    assert from_tables.manifest.files == from_records.manifest.files


def test_reading_and_freezing_make_no_record_per_line(fx, tmp_path: Path) -> None:
    found = read_ecospold2(fx.source, version="3.10", exclude=read_exclude(fx.exclude), workers=1)

    found.freeze(into=tmp_path)

    assert "processes" not in vars(found)  # the records are made only when asked for
    assert found.exchange_table.num_rows == 24


def test_linking_makes_no_record_even_for_lines_that_did_not_link(
    source_copy: Callable[..., Path], fx, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = source_copy(add=(fx.missing_link_file,))
    made: list[tuple[Process, ...]] = []
    real = inventory_module.read_processes

    def counted(*args: object) -> tuple[Process, ...]:
        found = real(*args)  # pyright: ignore[reportArgumentType]
        made.append(found)
        return found

    monkeypatch.setattr(inventory_module, "read_processes", counted)

    found = read_ecospold2(
        folder, version="3.10", exclude=read_exclude(folder / "exclude.csv"), workers=1
    )

    assert made == []
    assert [p.code for p in found.problems] == ["missing-supplier"]


def test_two_workers_send_the_same_tables_as_one(fx, tmp_path: Path) -> None:
    exclude = read_exclude(fx.exclude)
    one = read_ecospold2(fx.source, version="3.10", exclude=exclude, workers=1)
    two = read_ecospold2(fx.source, version="3.10", exclude=exclude, workers=2)

    assert two.process_table.equals(one.process_table)
    assert two.exchange_table.sort_by(
        [("process_id", "ascending"), ("line_id", "ascending")]
    ).equals(one.exchange_table.sort_by([("process_id", "ascending"), ("line_id", "ascending")]))
    assert (
        import_ecospold2(fx.source, version="3.10", exclude=exclude, into=tmp_path, workers=2).hash
        == one.freeze(into=tmp_path / "one").hash
    )
