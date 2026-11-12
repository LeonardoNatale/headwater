from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest

from headwater.model import (
    Flow,
    FlowKind,
    InvalidDataError,
    Issue,
    IssueStatus,
    Method,
    Process,
)


def _problems(error: pytest.ExceptionInfo[InvalidDataError]) -> list[tuple[str, str]]:
    return [(p.code, p.item) for p in error.value.problems]


def test_freezing_with_an_open_issue_fails_naming_it(records, tmp_path: Path) -> None:
    still_open = Issue(
        process_id="toy:fabric-production",
        line_id="e05",
        flow_id="toy:dye",
        status=IssueStatus.open,
    )
    with_open_issue = replace(records, issues=(records.issues[0], still_open))

    with pytest.raises(InvalidDataError) as raised:
        with_open_issue.build(tmp_path / "releases")

    assert _problems(raised) == [("open-issue", "issue toy:fabric-production line e05")]
    assert "toy:dye" in str(raised.value)
    assert not (tmp_path / "releases").exists()


def test_every_open_issue_is_listed(records, tmp_path: Path) -> None:
    issues = tuple(
        Issue(
            process_id=i.process_id, line_id=i.line_id, flow_id=i.flow_id, status=IssueStatus.open
        )
        for i in records.issues
    )

    with pytest.raises(InvalidDataError) as raised:
        replace(records, issues=issues).build(tmp_path)

    assert _problems(raised) == [
        ("open-issue", "issue toy:fabric-production line e02"),
        ("open-issue", "issue toy:fabric-production line e05"),
    ]


def test_an_issue_must_point_at_a_line_of_the_release(records, tmp_path: Path) -> None:
    stray = Issue.model_validate({**records.issues[1].model_dump(), "line_id": "e99"})

    with pytest.raises(InvalidDataError) as raised:
        replace(records, issues=(records.issues[0], stray)).build(tmp_path)

    assert _problems(raised) == [
        ("missing-issue", "toy:fabric-production line e05"),
        ("unknown-line", "issue toy:fabric-production line e99"),
    ]


def test_a_resolution_time_needs_a_time_zone(records, tmp_path: Path) -> None:
    naive = Issue.model_validate(
        {**records.issues[0].model_dump(), "resolved_at": datetime(2026, 10, 11, 9, 30)}
    )

    with pytest.raises(InvalidDataError) as raised:
        replace(records, issues=(naive, records.issues[1])).build(tmp_path)

    assert _problems(raised) == [("naive-time", "issue toy:fabric-production line e02")]


def test_links_to_unknown_ids_fail_naming_every_one(records, tmp_path: Path) -> None:
    broken = records.with_line(
        "toy:fabric-production", "e02", supplier_id="toy:electricity-prod"
    ).with_line("toy:electricity-production", "e02", flow_id="toy:c02", unit="tonnes")

    with pytest.raises(InvalidDataError) as raised:
        broken.build(tmp_path)

    assert _problems(raised) == [
        ("unknown-flow", "toy:electricity-production line e02"),
        ("unknown-unit", "toy:electricity-production line e02"),
        ("unknown-supplier", "toy:fabric-production line e02"),
        ("issue-mismatch", "issue toy:fabric-production line e02"),
    ]
    assert "Did you mean 'toy:co2'?" in str(raised.value)
    assert "Did you mean 'toy:electricity-production'?" in str(raised.value)


def test_an_id_defined_twice_fails(records, tmp_path: Path) -> None:
    flow = Flow(id="toy:dye", name="another dye", kind=FlowKind.product, unit="kg")
    process = records.processes[0]
    twin = Process.model_validate({**process.model_dump(), "name": "a twin"})

    with pytest.raises(InvalidDataError) as raised:
        replace(records, flows=(*records.flows, flow), processes=(*records.processes, twin)).build(
            tmp_path
        )

    assert _problems(raised) == [
        ("duplicate-flow", "flow toy:dye"),
        ("duplicate-process", f"process {process.id}"),
    ]


def test_a_method_on_another_flow_list_is_refused(records, tmp_path: Path) -> None:
    method = Method.model_validate({**records.methods[0].model_dump(), "flow_list": "ecoinvent"})

    with pytest.raises(InvalidDataError) as raised:
        replace(records, methods=(method,)).build(tmp_path)

    assert _problems(raised) == [("flow-list-mismatch", "method toy-climate")]


@pytest.mark.parametrize("name", ["", "toy@1", "toy/background", ".hidden", "with space"])
def test_a_release_name_must_be_a_plain_folder_name(info, name: str) -> None:
    with pytest.raises(ValueError, match="name"):
        info.model_validate({**info.model_dump(), "name": name})


def test_a_flow_name_is_checked_then_not_stored(records, tmp_path: Path) -> None:
    named = records.with_line("toy:fabric-production", "e02", flow_name="electricity")
    wrong = records.with_line("toy:fabric-production", "e02", flow_name="gas")

    release = named.build(tmp_path)
    with pytest.raises(InvalidDataError) as raised:
        wrong.build(tmp_path / "wrong")

    assert release.hash == records.build(tmp_path).hash
    assert _problems(raised) == [("flow-name-mismatch", "toy:fabric-production line e02")]
