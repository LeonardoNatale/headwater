"""Links by UUID, missing suppliers, and files excluded as data gaps (X4)."""

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from headwater.io.ecospold2 import read_ecospold2
from headwater.io.exclude import Exclusion, read_exclude
from headwater.io.inventory import Inventory
from headwater.model import InvalidDataError, Issue, IssueStatus
from headwater.model.uncertainty import LinkStatus

RESOLVED_AT = datetime(2026, 10, 11, 9, 30, tzinfo=UTC)


def test_x4_the_empty_file_stops_the_import_naming_it(fx) -> None:
    with pytest.raises(InvalidDataError) as raised:
        read_ecospold2(fx.source, version="3.10", workers=1)

    assert [(p.code, p.item) for p in raised.value.problems] == [
        ("empty-file", f"datasets/{fx.empty_file}")
    ]
    assert "exclude" in str(raised.value)


def test_x4_excluded_it_is_a_data_gap_on_the_one_market_that_bought_from_it(
    source: Inventory, fx
) -> None:
    gaps = [
        e for p in source.processes for e in p.exchanges if e.link_status is LinkStatus.data_gap
    ]

    assert [(e.process_id, e.line_id) for e in gaps] == [(fx.pid(6, 3), fx.line(6, 3))]
    [issue] = source.issues
    assert issue.model_dump(exclude={"reason"}) == Issue(
        process_id=fx.pid(6, 3),
        line_id=fx.line(6, 3),
        flow_id=fx.product(3),
        status=IssueStatus.data_gap,
        reason="x",
        resolved_by="fixture author",
        resolved_at=RESOLVED_AT,
    ).model_dump(exclude={"reason"})
    assert issue.reason is not None
    assert "The source file is empty" in issue.reason
    assert fx.empty_file in issue.reason
    assert fx.pid(8, 3) not in {p.id for p in source.processes}
    assert source.problems == ()


def test_a_link_to_a_missing_dataset_is_an_open_issue_naming_both_ends(
    source_copy: Callable[..., Path], fx
) -> None:
    folder = source_copy(add=(fx.missing_link_file,))

    found = read_ecospold2(
        folder, version="3.10", exclude=read_exclude(folder / "exclude.csv"), workers=1
    )

    buyer, line = fx.pid(10, 2), fx.line(10, 2)
    missing = f"ecoinvent:3.10:cutoff:{fx.activity(99)}_b0000000-0000-4000-8000-000000000003"
    open_issue = Issue(
        process_id=buyer, line_id=line, flow_id=fx.product(3), status=IssueStatus.open
    )
    assert open_issue in found.issues
    [problem] = found.problems
    assert (problem.code, problem.item) == ("missing-supplier", f"{buyer} line {line}")
    assert missing in problem.message
    assert f"{fx.activity(99)}_b0000000-0000-4000-8000-000000000003.spold" in problem.message
    e = fx.exchange(found, buyer, line)
    assert (e.supplier_id, e.link_status) == (None, None)


def test_an_open_issue_stops_the_freeze(
    source_copy: Callable[..., Path], fx, tmp_path: Path
) -> None:
    folder = source_copy(add=(fx.missing_link_file,))
    found = read_ecospold2(
        folder, version="3.10", exclude=read_exclude(folder / "exclude.csv"), workers=1
    )

    with pytest.raises(InvalidDataError) as raised:
        found.freeze(into=tmp_path / "releases")

    assert [p.code for p in raised.value.problems] == ["missing-supplier"]
    assert not (tmp_path / "releases").exists()


def test_an_input_with_no_activity_link_is_an_open_issue(
    source_copy: Callable[..., Path], fx
) -> None:
    gas_power = f"datasets/{fx.file_name(3, 2)}"
    folder = source_copy(
        edit={gas_power: [(' activityLinkId="a0000000-0000-4000-8000-000000000006"', "")]}
    )

    found = read_ecospold2(
        folder, version="3.10", exclude=read_exclude(folder / "exclude.csv"), workers=1
    )

    [problem] = found.problems
    assert (problem.code, problem.item) == ("unlinked-line", f"{fx.pid(3, 2)} line {fx.line(3, 2)}")


def test_an_excluded_file_nobody_buys_from_is_a_warning(fx, tmp_path: Path) -> None:
    unused = fx.file_name(9, 5)
    exclusions = (
        *read_exclude(fx.exclude),
        Exclusion(file=unused, reason="not needed", resolved_by="me", resolved_at=RESOLVED_AT),
    )

    found = read_ecospold2(fx.source, version="3.10", exclude=exclusions, workers=1)

    assert fx.pid(9, 5) not in {p.id for p in found.processes}
    assert [(w.code, w.item) for w in found.warnings if w.code == "excluded-unused"] == [
        ("excluded-unused", f"datasets/{unused}")
    ]


def test_an_excluded_file_must_be_in_the_folder(fx) -> None:
    ghost = Exclusion(
        file=fx.file_name(77, 1), reason="typo", resolved_by="me", resolved_at=RESOLVED_AT
    )

    with pytest.raises(InvalidDataError) as raised:
        read_ecospold2(
            fx.source, version="3.10", exclude=(*read_exclude(fx.exclude), ghost), workers=1
        )

    assert [(p.code, p.item) for p in raised.value.problems] == [
        ("unknown-file", f"datasets/{fx.file_name(77, 1)}")
    ]


def _exclude_file(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "exclude.csv"
    path.write_text(text, encoding="utf-8")
    return path


def test_the_exclude_file_is_read(fx) -> None:
    assert read_exclude(fx.exclude) == (
        Exclusion(
            file=fx.empty_file,
            reason="The source file is empty",
            resolved_by="fixture author",
            resolved_at=RESOLVED_AT,
        ),
    )


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("file,reason,resolved_by\nx.spold,empty,me\n", "missing-column"),
        (
            "file,reason,resolved_by,resolved_at\nx.spold,,me,2026-10-11T09:30:00+00:00\n",
            "invalid-field",
        ),
        (
            "file,reason,resolved_by,resolved_at\nx.spold,empty,me,2026-10-11T09:30:00\n",
            "invalid-field",
        ),
        ("file,reason,resolved_by,resolved_at\nx.spold,empty,me,2026-10-11\n", "invalid-field"),
        (
            "file,reason,resolved_by,resolved_at\n"
            "x.spold,empty,me,2026-10-11T09:30:00+00:00\n"
            "x.spold,again,me,2026-10-11T09:30:00+00:00\n",
            "duplicate-exclusion",
        ),
        (
            "file,reason,resolved_by,resolved_at,note\nx.spold,empty,me,2026-10-11T09:30:00Z,hi\n",
            "unknown-column",
        ),
    ],
    ids=[
        "no resolved_at column",
        "blank reason",
        "no time zone",
        "a date only",
        "twice",
        "extra column",
    ],
)
def test_a_bad_exclude_file_fails_naming_the_row(tmp_path: Path, text: str, code: str) -> None:
    with pytest.raises(InvalidDataError) as raised:
        read_exclude(_exclude_file(tmp_path, text))

    assert [p.code for p in raised.value.problems] == [code]
    assert all("exclude.csv" in (p.location or p.item) for p in raised.value.problems)


def test_a_missing_exclude_file_is_named(tmp_path: Path) -> None:
    with pytest.raises(InvalidDataError) as raised:
        read_exclude(tmp_path / "nope.csv")

    assert [p.code for p in raised.value.problems] == ["missing-file"]
