"""The comment shows every product of a database that moved, and counts the ones that did not.

The real tests run the command on the toy databases in a temporary git repository: the T-shirt
database also makes a bag from 2 kWh of electricity (1.0596 kg CO2-eq), which the T-shirt
does not buy.
The hand-built ones check the shapes that are cheaper to write down.
"""

from collections.abc import Callable, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from tests.conftest import add_process

Git = Callable[..., str]
Commit = Callable[[str], str]
Report = dict[str, Any]

STUDY = "studies/tshirt/tshirt"
EXCHANGES = f"{STUDY}/exchanges.csv"
BASE, HEAD = "a" * 40, "b" * 40


def edit(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    assert old in text, f"{old!r} not found in {path}"
    path.write_text(text.replace(old, new, 1))


def add_bag(repo: Path, kwh: float = 2) -> None:
    add_process(
        repo / STUDY,
        "bag",
        "e01,reference,tshirt:bag,1,kg,\n"
        f"e02,input,toy:electricity,{kwh},kWh,toy:electricity-production",
    )
    with (repo / STUDY / "flows.csv").open("a") as handle:
        handle.write("tshirt:bag,bag,product,kg,,\n")


@pytest.fixture
def with_bag(repo: Path, git: Git, commit: Commit) -> None:
    """`main` has the T-shirt and its bag. The test goes on from a branch of it."""
    add_bag(repo)
    commit("a bag")
    git("switch", "--quiet", "--create", "bags")


# --- real diffs --------------------------------------------------------------------------------


@pytest.mark.usefixtures("with_bag")
def test_a_change_to_the_bag_shows_the_bag_and_counts_the_t_shirt_as_no_change(
    collect: ModuleType, render: ModuleType, headwater: list[str], repo: Path, commit: Commit
) -> None:
    edit(repo / EXCHANGES, "toy:electricity,2,", "toy:electricity,3,")
    commit("heavier bag")

    body = render.render(collect.collect_report(repo, headwater, "main", "HEAD"))

    study = body.split("### Database `studies/tshirt/tshirt`")[1].split("<sub>")[0].strip()
    assert study.split("\n\n")[1:] == [
        "| | Lines that differ |\n|---|---|\n"
        "| e02 | `tshirt:bag line e02` (electricity): amount 2 → 3 kWh |",
        "A line's column is the change that line alone makes.",
        "<details open>\n<summary>bag: 1 score</summary>",
        "| Product | Indicator | Old | New | Change | % | e02 |\n"
        "|---|---|---:|---:|---:|---:|---:|\n"
        "| bag | Climate change (kg CO2-eq) | 1.0596 | 1.5894 | +0.5298 | +50.0% | +0.5298 |",
        "</details>",
        "1 other product, no change.",
    ]
    assert "| T-shirt |" not in body


@pytest.mark.usefixtures("with_bag")
def test_a_change_to_the_t_shirt_alone_leaves_the_bag_as_one_line(
    collect: ModuleType, render: ModuleType, headwater: list[str], repo: Path, commit: Commit
) -> None:
    edit(repo / EXCHANGES, "toy:electricity,0.5", "toy:electricity,0.6")
    commit("more electricity")

    body = render.render(collect.collect_report(repo, headwater, "main", "HEAD"))

    assert "<details open>\n<summary>T-shirt: 1 score</summary>" in body
    assert "| T-shirt | Climate change (kg CO2-eq) | 1.19205 | 1.24503 |" in body
    assert "1 other product, no change." in body
    assert "| bag |" not in body and "bag:" not in body


@pytest.mark.usefixtures("with_bag")
def test_a_change_that_moves_no_product_counts_them_all(
    collect: ModuleType, render: ModuleType, headwater: list[str], repo: Path, commit: Commit
) -> None:
    with (repo / STUDY / "database.yaml").open("a") as handle:
        handle.write("description: A T-shirt and a bag.\n")
    commit("a description")

    body = render.render(collect.collect_report(repo, headwater, "main", "HEAD"))

    study = body.split("### Database `studies/tshirt/tshirt`")[1].split("<sub>")[0]
    assert "2 other products, no change." in study
    assert "| Product |" not in study and "<details" not in study


def test_a_bag_the_head_adds_is_new_and_one_it_removes_is_removed(
    collect: ModuleType,
    render: ModuleType,
    headwater: list[str],
    repo: Path,
    git: Git,
    commit: Commit,
) -> None:
    git("switch", "--quiet", "--create", "bags")
    add_bag(repo)
    commit("a bag")

    added = render.render(collect.collect_report(repo, headwater, "main", "HEAD"))
    removed = render.render(collect.collect_report(repo, headwater, "HEAD", "main"))

    assert "<details open>\n<summary>bag (new product): 1 score</summary>" in added
    assert "| bag | Climate change (kg CO2-eq) | - | 1.0596 | only in head |  |" in added
    assert "1 other product, no change." in added
    assert "<details open>\n<summary>bag (removed product): 1 score</summary>" in removed
    assert "| bag | Climate change (kg CO2-eq) | 1.0596 | - | only in base |  |" in removed


def test_a_new_database_is_one_line_with_its_products_counted(
    collect: ModuleType,
    render: ModuleType,
    headwater: list[str],
    repo: Path,
    git: Git,
    commit: Commit,
) -> None:
    add_bag(repo)
    commit("a bag")
    git("switch", "--quiet", "--create", "rename")
    git("mv", "studies/tshirt", "studies/classic-tshirt")
    commit("rename")

    body = render.render(collect.collect_report(repo, headwater, "HEAD~1", "HEAD"))

    new = body.split("### Database `studies/classic-tshirt/tshirt` (new)")[1].split("### ")[0]
    assert new.strip().startswith(
        "New database: 2 products, not scored. `headwater calc studies/classic-tshirt/tshirt` "
        "scores them."
    )
    assert "| bag |" not in new and "T-shirt" not in new


# --- shapes that are cheaper to write by hand --------------------------------------------------


def shape(
    products: list[dict[str, Any]] | None, rows: Sequence[tuple[str, float | None, float | None]]
) -> Report:
    """A report whose study has the products `rows` score, each as (product name, old, new)."""
    results = [
        {
            "product": {"id": f"toy:{name}", "name": name},
            "indicator": {"id": "climate", "name": "Climate change", "unit": "kg CO2-eq"},
            "old": old,
            "new": new,
            "change": None if old is None or new is None else new - old,
            "percent": None,
            "changed": old is not None and new is not None and old != new,
            "drivers": [],
            "interaction": 0.0,
        }
        for name, old, new in rows
    ]
    diff: dict[str, Any] = {
        "format": "headwater-diff/2",
        "path": STUDY,
        "kind": "database",
        "tolerance": 1e-9,
        "engine_version": "9.9",
        "background": {"release": "toy-background@" + "6" * 64, "folder": None},
        "method": {"id": "toy-climate", "version": "1", "hash": "c" * 64},
        "results": results,
        "differences": [],
        "created": False,
    }
    if products is not None:
        diff["products"] = products
    study = {
        "path": STUDY,
        "status": "changed",
        "ok": True,
        "validation": {"exit_code": 0, "background": None, "errors": [], "warnings": []},
        "diff": diff,
        "diff_error": None,
    }
    return {
        "format": "headwater-ci/2",
        "base": {"label": "main", "commit": BASE},
        "head": {"label": "HEAD", "commit": HEAD},
        "failed": False,
        "folders": [study],
    }


def listed(*names: str) -> list[dict[str, Any]]:
    return [{"id": f"toy:{n}", "name": n, "process": n, "status": "both"} for n in names]


def test_forty_products_that_did_not_move_are_one_line(render: ModuleType) -> None:
    names = [f"product {i:02d}" for i in range(40)]
    rows = [(names[0], 1.0, 2.0), *((n, 1.0, 1.0) for n in names[1:])]

    body = render.render(shape(listed(*names), rows))

    assert "39 other products, no change." in body
    assert "| product 01 |" not in body
    assert body.count("| product 00 |") == 1
    assert len(body.splitlines()) < 30


def test_a_study_lists_each_product_with_its_amount_and_counts_none(render: ModuleType) -> None:
    rows = [("bag", 1.0, 1.0), ("tote", 1.0, 2.0), ("T-shirt", 1.0, 1.0)]
    products = listed("bag", "tote", "T-shirt")
    for product, amount in zip(products, (1.0, 2.0, 3.0), strict=True):
        product["amount"] = {"old": amount, "new": amount}
    report = shape(products, rows)
    report["folders"][0]["kind"] = "study"
    report["folders"][0]["diff"]["kind"] = "study"

    body = render.render(report)

    assert "### Study `studies/tshirt/tshirt`" in body
    assert body.index("1 × bag: no change.") < body.index("<summary>2 × tote: 1 score</summary>")
    assert body.index("<summary>2 × tote: 1 score</summary>") < body.index(
        "3 × T-shirt: no change."
    )
    assert "other product" not in body


def test_each_product_has_the_columns_of_the_lines_that_moved_it(render: ModuleType) -> None:
    lines = [
        {
            "id": f"line {item}",
            "kind": "line",
            "change": "changed",
            "item": item,
            "description": "amount 1 → 2",
            "fields": [],
            "affects": [],
            "scored": True,
            "flow": None,
        }
        for item in ("bag line e02", "tote line e03")
    ]
    report = shape(
        listed("T-shirt", "bag", "tote"),
        [("T-shirt", 1.0, 1.0), ("bag", 1.0, 2.0), ("tote", 1.0, 3.0)],
    )
    report["folders"][0]["diff"]["differences"] = lines
    _, bag, tote = report["folders"][0]["diff"]["results"]
    bag["drivers"] = [
        {"difference": "line bag line e02", "contribution": 1.0},
        {"difference": "line tote line e03", "contribution": 0.0},
    ]
    tote["drivers"] = [
        {"difference": "line bag line e02", "contribution": 0.0},
        {"difference": "line tote line e03", "contribution": 2.0},
    ]

    body = render.render(report)

    bag_block, tote_block = body.split("<summary>tote: 1 score</summary>")
    assert "| Product | Indicator | Old | New | Change | % | e02 |" in bag_block
    assert "e03 |" not in bag_block.split("<summary>bag: 1 score</summary>")[1]
    assert "| Product | Indicator | Old | New | Change | % | e03 |" in tote_block
    assert "| bag | Climate change (kg CO2-eq) | 1 | 2 | +1 |  | +1 |" in bag_block
    assert "| tote | Climate change (kg CO2-eq) | 1 | 3 | +2 |  | +2 |" in tote_block


def test_a_line_that_moves_no_product_is_still_listed(render: ModuleType) -> None:
    report = shape(listed("T-shirt", "bag"), [("T-shirt", 1.0, 1.0), ("bag", 1.0, 1.0)])
    diff = report["folders"][0]["diff"]
    diff["differences"] = [
        {
            "id": "line bag line e02",
            "kind": "line",
            "change": "changed",
            "item": "bag line e02",
            "description": "amount 1 → 2",
            "fields": [],
            "affects": [],
            "scored": True,
            "flow": None,
        }
    ]
    diff["results"][0]["drivers"] = [{"difference": "line bag line e02", "contribution": 0.0}]

    body = render.render(report)

    assert "| e02 | `bag line e02`: amount 1 → 2 |" in body
    assert "2 other products, no change." in body
    assert "<details" not in body and "A line's column" not in body


def test_products_with_one_name_are_told_apart_by_their_id(render: ModuleType) -> None:
    names = ["dyed fabric", "dyed fabric", "dyed fabric", "bag"]
    rows = [(n, 1.0, 2.0 if i < 3 else 1.0) for i, n in enumerate(names)]
    products = [
        {"id": f"toy:p{i}", "name": n, "process": n, "status": "both"} for i, n in enumerate(names)
    ]
    report = shape(products, rows)
    for i, row in enumerate(report["folders"][0]["diff"]["results"]):
        row["product"]["id"] = f"toy:p{i}"

    body = render.render(report)

    assert "<summary>dyed fabric (toy:p0): 1 score</summary>" in body
    assert "<summary>dyed fabric (toy:p1): 1 score</summary>" in body
    assert "| dyed fabric (toy:p2) | Climate change (kg CO2-eq) | 1 | 2 |" in body
    assert "1 other product, no change." in body and "bag (" not in body


def test_a_product_is_named_by_its_process_as_well(render: ModuleType) -> None:
    rows = [("T-shirt", 1.0, 1.0), ("bag", 1.0, 2.0)]
    products = listed("T-shirt", "bag")
    products[1]["process"] = "bag sewing"
    report = shape(products, rows)
    for i, row in enumerate(report["folders"][0]["diff"]["results"]):
        row["product"]["id"] = products[i]["id"]

    body = render.render(report)

    assert "<summary>bag sewing | bag: 1 score</summary>" in body
    assert "T-shirt |" not in body
    assert "| bag sewing \\| bag | Climate change (kg CO2-eq) | 1 | 2 |" in body


def test_a_long_comment_of_many_moved_products_lists_the_first_scores_of_each(
    render: ModuleType,
) -> None:
    names = [f"product {i:02d}" for i in range(40)]
    results = [(n, 1.0, 2.0) for n in names]
    report = shape(listed(*names), results)
    diff = report["folders"][0]["diff"]
    row = diff["results"][0]
    # 25 indicators for each product
    diff["results"] = [
        {
            **row,
            "product": {"id": f"toy:{n}", "name": n},
            "indicator": {**row["indicator"], "id": f"i{k}", "name": f"Indicator {k:02d}"},
        }
        for k in range(25)
        for n in names
    ]

    body = render.render(report)
    full = render.render(report, limit=render.SUMMARY_LIMIT)

    assert len(body) <= render.LIMIT < len(full)
    assert "in the job summary." in body
    assert body.count("<summary>product ") == 40  # every product has its block, none is cut


def test_a_study_of_one_product_that_did_not_move_is_one_line(render: ModuleType) -> None:
    products = listed("T-shirt")
    products[0]["amount"] = {"old": 1.0, "new": 1.0}
    report = shape(products, [("T-shirt", 1.0, 1.0)])
    report["folders"][0]["kind"] = "study"
    report["folders"][0]["diff"]["kind"] = "study"

    body = render.render(report)

    assert "1 × T-shirt: no change." in body
    assert "| Product |" not in body and "No score changed" not in body
