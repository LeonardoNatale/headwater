"""One parser for `when`, in every file where a person says when a decision was made."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from headwater.io.excel.mapping import read_mapping
from headwater.link import read_supplier_mapping
from headwater.model import InvalidDataError
from headwater.study._files import read_when

CASES = [
    ("2026-10-11", datetime(2026, 10, 11, tzinfo=UTC)),
    ("2026-10-11T09:30:00", datetime(2026, 10, 11, 9, 30, tzinfo=UTC)),
    ("2026-10-11T09:30:00+02:00", datetime(2026, 10, 11, 7, 30, tzinfo=UTC)),
]


@pytest.mark.parametrize(("text", "expected"), CASES)
def test_a_date_is_midnight_and_a_time_with_no_zone_is_utc(text: str, expected: datetime) -> None:
    problems: list[tuple[str, str]] = []
    assert read_when(text, lambda *p: problems.append(p)) == expected
    assert read_when(text, lambda *p: problems.append(p), naive=True) == expected.replace(
        tzinfo=None
    )
    assert problems == []


def test_a_when_that_is_not_a_date_is_reported_as_bad_date() -> None:
    problems: list[tuple[str, str]] = []
    assert read_when("12/10/2026", lambda *p: problems.append(p)) is None
    assert problems == [
        ("bad-date", "when '12/10/2026' is not an ISO date or time, for example 2026-10-11")
    ]


@pytest.mark.parametrize(("text", "expected"), CASES)
def test_the_mapping_files_read_it_the_same_way(
    tmp_path: Path, text: str, expected: datetime
) -> None:
    excel = tmp_path / "excel.csv"
    excel.write_text(f"type,name,target,who,when\ndatabase,x,release,leo,{text}\n")
    suppliers = tmp_path / "suppliers.csv"
    suppliers.write_text(f"supplier_id,supplier,reason,who,when\np:a,p:b,,leo,{text}\n")
    assert read_mapping(excel).rows[0].when == expected
    assert read_supplier_mapping(suppliers).by_id["p:a"].decision.when == expected


def test_the_mapping_files_refuse_a_bad_when_with_the_same_message(tmp_path: Path) -> None:
    excel = tmp_path / "excel.csv"
    excel.write_text("type,name,target,who,when\ndatabase,x,release,leo,yesterday\n")
    suppliers = tmp_path / "suppliers.csv"
    suppliers.write_text("supplier_id,supplier,reason,who,when\np:a,p:b,,leo,yesterday\n")
    messages: list[str] = []
    for read in (read_mapping, read_supplier_mapping):
        path = excel if read is read_mapping else suppliers
        with pytest.raises(InvalidDataError) as raised:
            read(path)
        messages.append(raised.value.problems[0].message)
    assert messages[0] == messages[1]
