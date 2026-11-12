"""Splitting a process that makes several products, with the factors its source gives."""

import math
from pathlib import Path
from typing import Any

import pyarrow as pa
import pytest

from headwater.io.allocate import Allocated, allocate
from headwater.io.inventory import link_inventory
from headwater.io.source import ALLOCATION
from headwater.model import InvalidDataError, IssueStatus


def _run(
    gin: Any,
    lines: list[dict[str, Any]] | None = None,
    factors: tuple[dict[str, Any], ...] | None = None,
) -> Allocated:
    source = gin.source(lines, factors)
    return allocate(source.processes, source.lines, source.allocation)


def _amounts(table: pa.Table, process_id: str) -> dict[str, float]:
    return {r["line_id"]: r["amount"] for r in table.to_pylist() if r["process_id"] == process_id}


def test_the_gin_becomes_one_process_per_product_with_lines_scaled(gin) -> None:
    done = _run(gin)

    assert sorted(str(p) for p in done.processes.column("id").to_pylist()) == [
        "farm",
        "gin:p1",
        "gin:p2",
        "grid",
        "shirt",
    ]
    fibre, seed = _amounts(done.lines, "gin:p1"), _amounts(done.lines, "gin:p2")
    assert fibre == pytest.approx({"p1": 0.35, "e03": 0.9, "e04": 0.18, "e05": 0.09}, rel=1e-12)
    assert seed == pytest.approx({"p2": 0.6, "e03": 0.1, "e04": 0.02, "e05": 0.01}, rel=1e-12)
    assert (done.issues, done.problems) == ((), ())


def test_a_line_bought_from_the_gin_by_id_buys_from_the_split_that_makes_it(gin) -> None:
    done = _run(gin)

    [shirt] = [r for r in done.lines.to_pylist() if r["process_id"] == "shirt" and r["supplier_id"]]
    assert shirt["supplier_id"] == "gin:p1"


def test_a_factor_for_one_line_wins_over_the_products_factor(gin) -> None:
    per_line = (
        *gin.factors,
        {"process_id": "gin", "product": "p1", "line_id": "e04", "factor": 0.5},
        {"process_id": "gin", "product": "p2", "line_id": "e04", "factor": 0.5},
    )

    done = _run(gin, factors=per_line)

    assert _amounts(done.lines, "gin:p1")["e04"] == pytest.approx(0.1)
    assert _amounts(done.lines, "gin:p2")["e04"] == pytest.approx(0.1)
    assert _amounts(done.lines, "gin:p1")["e03"] == pytest.approx(0.9)


@pytest.mark.parametrize(
    ("factors", "words"),
    [
        ((), "gives no allocation factor"),
        (({"process_id": "gin", "product": "p1", "factor": 0.9},), "no factor for p2"),
        (
            (
                {"process_id": "gin", "product": "p1", "factor": 0.9},
                {"process_id": "gin", "product": "p2", "factor": 0.2},
            ),
            "sum to 1.1",
        ),
        (
            (
                {"process_id": "gin", "product": "p1", "factor": 1.2},
                {"process_id": "gin", "product": "p2", "factor": -0.2},
            ),
            "outside 0 and 1",
        ),
    ],
    ids=["no factors", "one missing", "not summing to 1", "outside 0 and 1"],
)
def test_a_gin_that_cannot_be_split_is_an_open_issue_and_stays_whole(
    gin, factors: tuple[dict[str, Any], ...], words: str
) -> None:
    done = _run(gin, factors=factors)

    assert "gin" in done.processes.column("id").to_pylist()
    [issue] = done.issues
    assert (issue.process_id, issue.line_id, issue.status) == ("gin", "p1", IssueStatus.open)
    [problem] = done.problems
    assert (problem.code, problem.item) == ("not-allocated", "process gin")
    assert words in problem.message


def test_factors_for_one_line_must_sum_to_1(gin) -> None:
    per_line = (
        *gin.factors,
        {"process_id": "gin", "product": "p1", "line_id": "e04", "factor": 0.5},
    )

    done = _run(gin, factors=per_line)

    [problem] = done.problems
    assert "line e04" in problem.message and "0.6" in problem.message


def test_a_factor_for_a_process_with_one_product_must_be_1(gin) -> None:
    lines = [r for r in gin.lines() if r["line_id"] != "p2"]

    done = _run(gin, lines, factors=({"process_id": "gin", "product": "p1", "factor": 0.9},))
    whole = _run(gin, lines, factors=({"process_id": "gin", "product": "p1", "factor": 1.0},))

    assert [p.code for p in done.problems] == ["not-allocated"]
    assert whole.problems == ()
    per_line = _run(
        gin,
        lines,
        factors=(
            {"process_id": "gin", "product": "p1", "factor": 1.0},
            {"process_id": "gin", "product": "p1", "line_id": "e04", "factor": 1.0},
        ),
    )
    assert "factors for single lines" in per_line.problems[0].message
    assert "gin" in whole.processes.column("id").to_pylist()


@pytest.mark.parametrize(
    ("row", "code"),
    [
        ({"process_id": "mill", "product": "p1", "factor": 1.0}, "unknown-process"),
        ({"process_id": "gin", "product": "e03", "factor": 1.0}, "unknown-product"),
        ({"process_id": "gin", "product": "p1", "line_id": "p2", "factor": 1.0}, "unknown-line"),
    ],
    ids=["no such process", "not a product", "a product as a line"],
)
def test_a_factor_that_names_nothing_is_a_reader_mistake(
    gin, row: dict[str, Any], code: str
) -> None:
    with pytest.raises(InvalidDataError) as raised:
        _run(gin, factors=(*gin.factors, row))

    assert [p.code for p in raised.value.problems] == [code]


def test_distributions_are_scaled_with_their_amounts(gin) -> None:
    lines = gin.lines()
    for line in lines:
        if line["line_id"] == "e03":
            line["uncertainty"] = {"distribution": "lognormal", "loc": 0.0, "scale": 0.1}
        if line["line_id"] == "e05":
            line["uncertainty"] = {"distribution": "normal", "loc": 0.1, "scale": 0.01}

    done = _run(gin, lines)

    rows = {(r["process_id"], r["line_id"]): r["uncertainty"] for r in done.lines.to_pylist()}
    assert rows["gin:p1", "e03"]["loc"] == pytest.approx(math.log(0.9))
    assert rows["gin:p1", "e03"]["scale"] == 0.1
    assert (rows["gin:p2", "e05"]["loc"], rows["gin:p2", "e05"]["scale"]) == pytest.approx(
        (0.01, 0.001)
    )


def test_sources_with_one_product_per_process_are_not_touched(gin) -> None:
    lines = [r for r in gin.lines() if r["line_id"] != "p2"]
    source = gin.source(lines, factors=())

    done = allocate(source.processes, source.lines, ALLOCATION.schema.empty_table())

    assert done.processes is source.processes
    assert done.lines is source.lines


def test_a_factor_given_twice_is_a_reader_mistake(gin) -> None:
    with pytest.raises(InvalidDataError) as raised:
        _run(gin, factors=(*gin.factors, {"process_id": "gin", "product": "p1", "factor": 0.8}))

    assert [(p.code, p.item) for p in raised.value.problems] == [
        ("duplicate-factor", "allocation of gin product p1")
    ]


CAUSAL = tuple(
    {"process_id": "gin", "product": product, "line_id": line, "factor": factor}
    for line, (fibre, seed) in {"e03": (0.8, 0.2), "e04": (0.5, 0.5), "e05": (1.0, 0.0)}.items()
    for product, factor in (("p1", fibre), ("p2", seed))
)
"""Factors for each line of the gin and no factor for the products, as a source with causal
allocation gives them."""


def test_factors_for_every_line_split_without_factors_for_the_products(gin) -> None:
    done = _run(gin, factors=CAUSAL)

    assert (done.issues, done.problems) == ((), ())
    assert _amounts(done.lines, "gin:p1") == pytest.approx(
        {"p1": 0.35, "e03": 0.8, "e04": 0.1, "e05": 0.1}, rel=1e-12
    )
    assert _amounts(done.lines, "gin:p2") == pytest.approx(
        {"p2": 0.6, "e03": 0.2, "e04": 0.1, "e05": 0.0}, abs=1e-12
    )


def test_a_line_with_no_factor_and_no_product_factor_is_an_open_issue(gin) -> None:
    done = _run(gin, factors=tuple(r for r in CAUSAL if r["line_id"] != "e05"))

    [problem] = done.problems
    assert "no factor for p1, p2" in problem.message


def _rows(table: pa.Table, process_id: str) -> dict[str, dict[str, Any]]:
    return {r["line_id"]: r for r in table.to_pylist() if r["process_id"] == process_id}


def test_own_use_of_a_product_is_bought_from_the_split_that_makes_it(gin) -> None:
    """The gin uses 0.01 kg of its own fibre: the fibre split keeps 0.009 kg as its own use, and
    the seed split buys 0.001 kg of fibre from the fibre split."""
    lines = [*gin.lines(), gin.line("gin", "e06", "own_use", "fibre", 0.01, supplier_id="gin")]

    done = _run(gin, lines)

    assert (done.issues, done.problems) == ((), ())
    fibre, seed = _rows(done.lines, "gin:p1")["e06"], _rows(done.lines, "gin:p2")["e06"]
    assert (fibre["role"], fibre["supplier_id"]) == ("own_use", "gin:p1")
    assert (seed["role"], seed["supplier_id"]) == ("input", "gin:p1")
    assert (fibre["amount"], seed["amount"]) == pytest.approx((0.009, 0.001), rel=1e-12)


def test_own_use_named_by_product_is_bought_by_id_from_the_split(gin) -> None:
    lines = [
        *gin.lines(),
        gin.line("gin", "e06", "own_use", "fibre", 0.01, supplier_product="fibre"),
    ]

    done = _run(gin, lines)

    seed = _rows(done.lines, "gin:p2")["e06"]
    assert (seed["role"], seed["supplier_id"], seed["supplier_product"]) == (
        "input",
        "gin:p1",
        None,
    )


def test_own_use_of_a_flow_no_product_of_the_gin_is_is_an_open_issue(gin) -> None:
    lines = [
        *gin.lines(),
        gin.line("gin", "e06", "own_use", "seed-cotton", 0.01, supplier_id="gin"),
    ]

    done = _run(gin, lines)

    [problem] = done.problems
    assert "uses seed-cotton (line e06) as its own product" in problem.message
    assert "gin" in done.processes.column("id").to_pylist()


def test_lognormal_bounds_are_scaled_with_the_amount(gin) -> None:
    lines = gin.lines()
    lines[6]["uncertainty"] = {
        "distribution": "lognormal",
        "loc": 0.0,
        "scale": 0.1,
        "minimum": 0.5,
        "maximum": 2.0,
    }

    done = _run(gin, lines)

    [fibre] = [
        r["uncertainty"]
        for r in done.lines.to_pylist()
        if (r["process_id"], r["line_id"]) == ("gin:p1", "e03")
    ]
    assert (fibre["minimum"], fibre["maximum"]) == pytest.approx((0.45, 1.8))


def test_a_single_product_with_only_line_factors_is_refused(gin) -> None:
    lines = [r for r in gin.lines() if r["line_id"] != "p2"]

    done = _run(
        gin,
        lines,
        factors=({"process_id": "gin", "product": "p1", "line_id": "e04", "factor": 0.3},),
    )

    [problem] = done.problems
    assert "factors for single lines" in problem.message


def test_a_gin_that_makes_one_flow_on_two_lines_is_not_split(gin) -> None:
    """The shirt buys fibre from the gin by id: with fibre on both product lines, no split could
    be chosen for it."""
    lines = [r for r in gin.lines() if r["line_id"] != "p2"]
    lines.append(gin.line("gin", "p2", "reference", "fibre", 0.6))

    done = _run(gin, lines)

    [problem] = done.problems
    assert "makes fibre on more than one of them" in problem.message
    assert "gin" in done.processes.column("id").to_pylist()


def test_a_gin_that_makes_one_flow_twice_stays_an_open_issue_that_freeze_refuses(
    gin, tmp_path: Path
) -> None:
    lines = [r for r in gin.lines() if r["line_id"] != "p2"]
    lines.append(gin.line("gin", "p2", "reference", "fibre", 0.6))

    inventory = link_inventory(gin.source(lines))

    [issue] = [i for i in inventory.issues if i.process_id == "gin"]
    assert (issue.line_id, issue.status) == ("p1", IssueStatus.open)
    [problem] = inventory.problems
    assert (problem.code, problem.item) == ("not-allocated", "process gin")
    with pytest.raises(InvalidDataError) as raised:
        inventory.freeze(into=tmp_path)
    assert "makes fibre on more than one of them" in str(raised.value)
