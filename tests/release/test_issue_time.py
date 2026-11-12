"""`issues.resolved_at` is stored in UTC with no time zone, so DuckDB reads it without pytz."""

import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pyarrow as pa
import pytest

from headwater.model import Issue
from headwater.release import Release


def test_duckdb_reads_the_resolution_time_without_pytz(
    toy_release: Release, monkeypatch: pytest.MonkeyPatch
) -> None:
    # DuckDB imports pytz to return a time with a zone. Blocked, it would fail.
    monkeypatch.setitem(sys.modules, "pytz", None)

    rows = (
        toy_release.connect()
        .execute("SELECT line_id, resolved_at FROM issues ORDER BY line_id")
        .fetchall()
    )

    assert rows == [("e02", datetime(2026, 10, 11, 9, 30)), ("e05", datetime(2026, 10, 11, 9, 30))]


def test_the_column_is_a_timestamp_with_no_time_zone(toy_release: Release) -> None:
    assert toy_release.table("issues").schema.field("resolved_at").type == pa.timestamp("us")


def test_a_time_in_another_zone_is_stored_as_the_same_instant_in_utc(
    records, tmp_path: Path
) -> None:
    paris = timezone(timedelta(hours=2))
    at = datetime(2026, 10, 11, 11, 30, tzinfo=paris)
    issue = Issue.model_validate({**records.issues[0].model_dump(), "resolved_at": at})

    release = replace(records, issues=(issue, records.issues[1])).build(tmp_path)

    stored = release.table("issues").column("resolved_at").to_pylist()
    assert stored[0] == datetime(2026, 10, 11, 9, 30)
    (read,) = (i for i in release.issues() if i.line_id == "e02")
    assert read.resolved_at == at
    assert read.resolved_at is not None and read.resolved_at.tzinfo is UTC


def test_issues_read_back_build_the_same_release(toy_release: Release, records, tmp_path) -> None:
    again = replace(records, issues=toy_release.issues()).build(tmp_path)

    assert again.hash == toy_release.hash
