"""Update or refactorize: a low-rank update must give what a full refactorization gives, and the
route is picked by the number of changed columns, against the crossover (Z3).
"""

import json
from pathlib import Path

import numpy as np
import pytest
from numpy.typing import NDArray
from scipy import sparse

from headwater.calc import factorize, low_rank_update, scores, supply
from headwater.calc.update import CROSSOVER, Route, choose_route, refactorize

REL = 1e-9


def realistic(n: int = 3000, seed: int = 7) -> sparse.csc_array:
    """A technosphere shaped like ecoinvent's: a reference output of 1 per process, six purchases
    per process, half of them from the 1% most popular products, so there are loops everywhere.
    Each process buys at most 0.72 per unit, so the matrix can be solved.
    """
    rng = np.random.default_rng(seed)
    hubs = n // 100
    weights = 1.0 / np.arange(1, hubs + 1)
    weights /= weights.sum()
    rows: list[int] = list(range(n))
    cols: list[int] = list(range(n))
    data: list[float] = [1.0] * n
    for j in range(n):
        found: set[int] = set()
        while len(found) < 6:
            popular = rng.random() < 0.5
            pick = int(rng.choice(hubs, p=weights)) if popular else int(rng.integers(n))
            if pick != j:
                found.add(pick)
        for i in sorted(found):
            rows.append(i)
            cols.append(j)
            data.append(-float(rng.uniform(0.01, 0.12)))
    return sparse.csc_array((data, (rows, cols)), shape=(n, n))


def new_columns(A: sparse.csc_array, columns: NDArray[np.intp], seed: int) -> sparse.csc_array:
    """Each given column with its purchases scaled by 0.5 to 1.3, and one new supplier of 0.05."""
    rng = np.random.default_rng(seed)
    n = A.shape[0]
    rows: list[int] = []
    cols: list[int] = []
    data: list[float] = []
    for k, j in enumerate(columns.tolist()):
        column = A[:, [j]].tocoo()
        have = set(column.row.tolist())
        for i, amount in zip(column.row.tolist(), column.data.tolist(), strict=True):
            rows.append(i)
            cols.append(k)
            data.append(amount if i == j else amount * float(rng.uniform(0.5, 1.3)))
        supplier = int(rng.integers(n))
        while supplier in have:
            supplier = int(rng.integers(n))
        rows.append(supplier)
        cols.append(k)
        data.append(-0.05)
    return sparse.csc_array((data, (rows, cols)), shape=(n, len(columns)))


def replaced(
    A: sparse.csc_array, columns: NDArray[np.intp], new: sparse.csc_array
) -> sparse.csc_array:
    """A with whole columns replaced, built here and not by the library."""
    old = A.tocoo()
    keep = ~np.isin(old.col, columns)
    put = new.tocoo()
    return sparse.csc_array(
        (
            np.concatenate([old.data[keep], put.data]),
            (
                np.concatenate([old.row[keep], put.row]),
                np.concatenate([old.col[keep], columns[put.col]]),
            ),
        ),
        shape=A.shape,
    )


@pytest.fixture(scope="module")
def A() -> sparse.csc_array:
    return realistic()


@pytest.fixture(scope="module")
def h(A: sparse.csc_array) -> NDArray[np.float64]:
    return np.random.default_rng(3).lognormal(-3, 1.5, A.shape[0])


@pytest.mark.parametrize("k", [1, 10, 100])
def test_an_update_equals_a_refactorization_on_a_realistic_matrix(
    A: sparse.csc_array, h: NDArray[np.float64], k: int
) -> None:
    rng = np.random.default_rng(k)
    columns = np.sort(rng.choice(A.shape[0], k, replace=False)).astype(np.intp)
    new = new_columns(A, columns, seed=k)
    edited = replaced(A, columns, new)
    f = np.zeros(A.shape[0])
    f[columns[0]] = 1.0

    fact = factorize(A)
    update = low_rank_update(fact, columns.tolist(), new)
    fresh = factorize(edited)

    expected = scores(fresh, h).values
    np.testing.assert_allclose(update.scores(h).values, expected, rtol=REL, atol=0)
    from_stored = update.scores(h, start=scores(fact, h).values)
    np.testing.assert_allclose(from_stored.values, expected, rtol=REL, atol=0)
    np.testing.assert_allclose(update.supply(f).values, supply(fresh, f).values, rtol=REL, atol=0)
    assert update.condition == pytest.approx(fresh.condition, rel=1e-6)


@pytest.mark.parametrize("k", [1, 10, 100])
def test_a_refactorization_of_the_changed_columns_is_that_of_the_edited_matrix(
    A: sparse.csc_array, h: NDArray[np.float64], k: int
) -> None:
    rng = np.random.default_rng(k)
    columns = np.sort(rng.choice(A.shape[0], k, replace=False)).astype(np.intp)
    new = new_columns(A, columns, seed=k)
    edited = replaced(A, columns, new)

    fresh = refactorize(factorize(A), columns.tolist(), new)

    assert abs(fresh.matrix - edited).max() == 0.0
    np.testing.assert_allclose(
        scores(fresh, h).values, scores(factorize(edited), h).values, rtol=1e-12, atol=0
    )
    assert fresh.settings == factorize(A).settings


def test_a_refactorization_keeps_the_pivoting_of_the_factors_it_replaces(
    A: sparse.csc_array,
) -> None:
    pivoted = factorize(A, pivoting=True)
    columns = np.array([4, 9], dtype=np.intp)

    fresh = refactorize(pivoted, columns.tolist(), new_columns(A, columns, seed=1))

    assert fresh.settings.diag_pivot_thresh == pivoted.settings.diag_pivot_thresh == 1.0


@pytest.mark.parametrize("columns", [[1], [1, 2]])
def test_the_toy_updates_and_refactorizes_to_the_same_scores(toy_waste, columns: list[int]) -> None:
    # Fabric uses 2.5 kWh per kg instead of 3, and electricity buys 0.01 kg of fabric per kWh.
    t = toy_waste
    changed = {1: [0.0, 1.0, -2.5, 0.0], 2: [0.0, -0.01, 1.0, 0.0]}
    new = np.column_stack([changed[c] for c in columns])
    fact = factorize(t.A)

    update = low_rank_update(fact, columns, new)
    fresh = refactorize(fact, columns, new)

    np.testing.assert_allclose(
        update.scores(t.h).values, scores(fresh, t.h).values, rtol=REL, atol=0
    )


def test_a_bad_refactorization_is_refused_as_an_update_is(toy) -> None:
    fact = factorize(toy.A)

    with pytest.raises(ValueError, match="once"):
        refactorize(fact, [1, 1], np.zeros((4, 2)))
    with pytest.raises(ValueError, match="shape"):
        refactorize(fact, [1], np.zeros((3, 1)))
    with pytest.raises(ValueError, match="between"):
        refactorize(fact, [7], np.zeros((4, 1)))


def test_the_route_is_picked_on_its_side_of_the_crossover() -> None:
    assert not choose_route(59, 60).refactorized
    assert not choose_route(60, 60).refactorized
    assert choose_route(61, 60).refactorized
    assert not choose_route(CROSSOVER).refactorized
    assert choose_route(CROSSOVER + 1).refactorized
    assert choose_route(1, 0).refactorized


def test_the_route_says_which_path_ran_and_why() -> None:
    assert str(choose_route(140, 60)) == (
        "refactorized: 140 changed processes, above the crossover of 60"
    )
    assert str(choose_route(12, 60)) == "updated: 12 changed processes, within the crossover of 60"
    assert str(choose_route(1, 60)) == "updated: 1 changed process, within the crossover of 60"
    assert str(choose_route(0, 60)) == "stored factorization: no purchase changed"


def test_a_route_forced_by_a_failed_residual_check_says_so() -> None:
    route = choose_route(12, 60).failed("residual 3.2e-09 above tolerance 1e-12")

    assert route.refactorized
    assert str(route) == (
        "refactorized: 12 changed processes, within the crossover of 60, "
        "but the update failed its check: residual 3.2e-09 above tolerance 1e-12"
    )


def test_a_negative_count_is_refused() -> None:
    with pytest.raises(ValueError, match="changed"):
        choose_route(-1, 60)
    with pytest.raises(ValueError, match="crossover"):
        choose_route(1, -1)


def test_the_route_is_a_value() -> None:
    assert choose_route(3, 60) == Route(3, 60, False)


def test_a_refactorized_route_cannot_fail_again() -> None:
    with pytest.raises(ValueError, match="already a refactorization"):
        choose_route(500, 60).failed("residual 3.2e-09 above tolerance 1e-12")


ROOT = Path(__file__).resolve().parents[2]
BENCH = ROOT / "bench" / "results" / "scenario_crossover_results.json"
BENCHMARKS = ROOT / "docs" / "appendix" / "benchmarks.md"


def test_the_crossover_in_use_is_the_one_recorded() -> None:
    measured = json.loads(BENCH.read_text())

    assert measured["finished"]
    assert measured["usable_up_to"] == CROSSOVER
    section = BENCHMARKS.read_text().split("## Scenario crossover")[1]
    assert f"`CROSSOVER` in `calc/update.py` is {CROSSOVER}:" in section
