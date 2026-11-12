"""Linking by id: a process of the source, a supplier left out on purpose, or an open issue."""

from collections.abc import Callable
from pathlib import Path

import pyarrow as pa

from headwater.link import DataGap, link_by_id, read_supplier_mapping
from headwater.model import IssueStatus

KNOWN = pa.array(["p:electricity", "p:fabric"])


def _buy(lx, supplier: str | None) -> pa.Table:
    return lx.table(
        lx.line("p:fabric", "e01", "reference", "f:fabric"),
        lx.line("p:fabric", "e02", "input", "f:electricity", supplier_id=supplier),
        lx.line("p:fabric", "e03", "emission", "f:co2"),
    )


def test_a_supplier_that_is_a_process_links_and_other_roles_get_no_status(lx) -> None:
    linked = link_by_id(_buy(lx, "p:electricity"), KNOWN)

    assert lx.columns(linked.lines, "line_id", "supplier_id", "link_status") == [
        ("e01", None, None),
        ("e02", "p:electricity", "linked"),
        ("e03", None, None),
    ]
    assert (linked.issues, linked.problems) == ((), ())


def test_the_supplier_name_columns_are_dropped(lx) -> None:
    linked = link_by_id(_buy(lx, "p:electricity"), KNOWN)

    assert "supplier_product" not in linked.lines.column_names
    assert linked.lines.column_names[-1] == "link_status"


def test_a_missing_supplier_is_an_open_issue_naming_both_ends_with_the_hint(lx) -> None:
    linked = link_by_id(_buy(lx, "p:gas"), KNOWN, hint=lambda s: f"No file for {s}.")

    assert lx.columns(linked.lines, "supplier_id", "link_status")[1] == (None, None)
    [issue] = linked.issues
    assert (issue.line_id, issue.status) == ("e02", IssueStatus.open)
    [problem] = linked.problems
    assert (problem.code, problem.item) == ("missing-supplier", "p:fabric line e02")
    assert "p:gas" in problem.message
    assert "No file for p:gas." in problem.message


def test_a_buying_line_that_names_no_supplier_is_an_open_issue(lx) -> None:
    linked = link_by_id(_buy(lx, None), KNOWN)

    assert [p.code for p in linked.problems] == ["unlinked-line"]


def test_a_supplier_left_out_is_a_data_gap_and_an_unused_one_is_reported(lx) -> None:
    gaps = {
        "p:gas": DataGap("empty file", "leo", lx.when, "datasets/gas.spold"),
        "p:oil": DataGap("not needed", "leo", lx.when),
    }

    linked = link_by_id(_buy(lx, "p:gas"), KNOWN, left_out=gaps)

    assert lx.columns(linked.lines, "supplier_id", "link_status")[1] == (None, "data_gap")
    [issue] = linked.issues
    assert (issue.status, issue.reason, issue.resolved_by) == (
        IssueStatus.data_gap,
        "empty file",
        "leo",
    )
    assert linked.problems == ()
    assert linked.unused_gaps == ("p:oil",)


def test_a_mapping_row_by_id_decides_first(lx, csv_file: Callable[[str], Path]) -> None:
    mapping = read_supplier_mapping(
        csv_file(
            "supplier_id,supplier,reason,who,when\n"
            "p:gas,p:electricity,,leo,2026-10-24\n"
            "p:oil,,Not in the package,leo,2026-10-24\n"
            "p:coal,,Never bought,leo,2026-10-24\n"
        )
    )
    table = lx.table(
        lx.line("p:fabric", "e02", "input", "f:electricity", supplier_id="p:gas"),
        lx.line("p:fabric", "e04", "input", "f:oil", supplier_id="p:oil"),
    )

    linked = link_by_id(table, KNOWN, decided=mapping)

    assert lx.columns(linked.lines, "supplier_id", "link_status") == [
        ("p:electricity", "linked"),
        (None, "data_gap"),
    ]
    decided, gap = linked.issues
    assert (decided.line_id, decided.status, decided.supplier_id, decided.resolved_by) == (
        "e02",
        IssueStatus.linked,
        "p:electricity",
        "leo",
    )
    assert (gap.line_id, gap.reason, gap.resolved_at) == ("e04", "Not in the package", lx.when)
    assert [r.key for r in linked.unused_rows] == ["p:coal"]


def test_a_mapping_row_also_decides_a_supplier_that_exists(
    lx, csv_file: Callable[[str], Path]
) -> None:
    mapping = read_supplier_mapping(
        csv_file(
            "supplier_id,supplier,reason,who,when,comment\n"
            "p:electricity,p:fabric,,leo,2026-10-24,the mill buys fabric power\n"
        )
    )

    linked = link_by_id(_buy(lx, "p:electricity"), KNOWN, decided=mapping)

    assert lx.columns(linked.lines, "supplier_id")[1] == ("p:fabric",)
    [issue] = linked.issues
    assert (issue.status, issue.reason) == (IssueStatus.linked, "the mill buys fabric power")


def test_rows_no_line_needs_are_returned_when_every_line_links(
    lx, csv_file: Callable[[str], Path]
) -> None:
    mapping = read_supplier_mapping(
        csv_file("supplier_id,supplier,reason,who,when\np:coal,,Never bought,leo,2026-10-24\n")
    )

    linked = link_by_id(_buy(lx, "p:electricity"), KNOWN, decided=mapping)

    assert [r.key for r in linked.unused_rows] == ["p:coal"]


def test_a_mapping_row_to_a_process_that_is_not_there_is_an_open_issue(
    lx, csv_file: Callable[[str], Path]
) -> None:
    mapping = read_supplier_mapping(
        csv_file("supplier_id,supplier,reason,who,when\np:gas,p:nowhere,,leo,2026-10-24\n")
    )

    linked = link_by_id(_buy(lx, "p:gas"), KNOWN, decided=mapping)

    [problem] = linked.problems
    assert problem.code == "mapping-target-missing"
    assert "mapping.csv:2" in problem.message


def test_row_order_is_kept_when_lines_do_not_link(lx) -> None:
    table = lx.table(
        lx.line("p:b", "e01", "input", "f:x", supplier_id="p:nowhere"),
        lx.line("p:a", "e01", "input", "f:x", supplier_id="p:fabric"),
    )

    linked = link_by_id(table, KNOWN)

    assert lx.columns(linked.lines, "process_id", "supplier_id") == [
        ("p:b", None),
        ("p:a", "p:fabric"),
    ]
