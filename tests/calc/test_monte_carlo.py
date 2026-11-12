"""Monte Carlo on arrays: drawing each distribution, the generator of (seed, k), one iteration."""

import math
import re
from dataclasses import replace

import numpy as np
import pytest
from scipy import sparse

from headwater.calc import CalculationError
from headwater.calc.monte_carlo import (
    DISTRIBUTIONS,
    MonteCarloSystem,
    Uncertain,
    draw,
    sample,
    uniforms,
)
from headwater.model import Distribution

N = 200_000


def lines(distribution: Distribution, n: int = N, **fields: float) -> Uncertain:
    """`n` uncertain lines with the same distribution and parameters."""
    code = DISTRIBUTIONS.index(distribution)

    def column(name: str) -> np.ndarray:
        return np.full(n, fields.get(name, 0.0))

    return Uncertain(
        distribution=np.full(n, code, dtype=np.int8),
        loc=column("loc"),
        scale=column("scale"),
        minimum=column("minimum"),
        maximum=column("maximum"),
        negative=np.full(n, fields.get("negative", 0.0) == 1.0),
    )


def check_mean_and_spread(drawn: np.ndarray, mean: float, sd: float) -> None:
    """The sample mean within 5 standard errors, and the sample sd within 5 of its own."""
    n = drawn.size
    assert abs(drawn.mean() - mean) < 5 * sd / math.sqrt(n)
    assert abs(drawn.std(ddof=1) - sd) < 5 * sd / math.sqrt(2 * (n - 1))


# --- exit test 3: each distribution draws with the right mean and spread ----------------------


def test_lognormal_draws_its_mean_and_spread() -> None:
    loc, scale = math.log(3.0), 0.5
    drawn = draw(lines(Distribution.lognormal, loc=loc, scale=scale), uniforms(0, 0, N))
    mean = math.exp(loc + scale**2 / 2)

    check_mean_and_spread(drawn, mean, mean * math.sqrt(math.expm1(scale**2)))
    assert np.median(drawn) == pytest.approx(3.0, rel=0.01)


def test_lognormal_on_a_negative_amount_keeps_its_sign() -> None:
    loc, scale = math.log(0.2), 0.3
    drawn = draw(
        lines(Distribution.lognormal, loc=loc, scale=scale, negative=1.0), uniforms(0, 0, N)
    )
    mean = -math.exp(loc + scale**2 / 2)

    assert (drawn < 0).all()
    check_mean_and_spread(drawn, mean, -mean * math.sqrt(math.expm1(scale**2)))


def test_normal_draws_its_mean_and_spread() -> None:
    drawn = draw(lines(Distribution.normal, loc=2.0, scale=0.3), uniforms(0, 0, N))

    check_mean_and_spread(drawn, 2.0, 0.3)


def test_uniform_draws_its_mean_and_spread() -> None:
    drawn = draw(lines(Distribution.uniform, minimum=1.0, maximum=4.0), uniforms(0, 0, N))

    check_mean_and_spread(drawn, 2.5, 3.0 / math.sqrt(12))
    assert drawn.min() > 1.0
    assert drawn.max() < 4.0


def test_triangular_draws_its_mean_and_spread() -> None:
    a, c, b = 1.0, 2.0, 5.0
    drawn = draw(lines(Distribution.triangular, minimum=a, loc=c, maximum=b), uniforms(0, 0, N))
    variance = (a * a + b * b + c * c - a * b - a * c - b * c) / 18

    check_mean_and_spread(drawn, (a + b + c) / 3, math.sqrt(variance))
    assert np.mean(drawn < c) == pytest.approx((c - a) / (b - a), abs=0.005)


def test_a_range_of_width_0_draws_its_one_value() -> None:
    for distribution in (Distribution.uniform, Distribution.triangular):
        drawn = draw(
            lines(distribution, n=10, minimum=2.0, loc=2.0, maximum=2.0), uniforms(0, 0, 10)
        )

        assert (drawn == 2.0).all()


# --- the generator --------------------------------------------------------------------------


def test_the_same_seed_and_iteration_give_the_same_uniforms() -> None:
    # Identical bits on purpose: the draws are a pure function of (seed, k).
    assert np.array_equal(uniforms(7, 3, 1000), uniforms(7, 3, 1000))
    assert not np.array_equal(uniforms(7, 3, 1000), uniforms(7, 4, 1000))
    assert not np.array_equal(uniforms(7, 3, 1000), uniforms(8, 3, 1000))


def test_uniforms_are_inside_the_open_interval() -> None:
    u = uniforms(0, 0, N)

    assert (u > 0).all()
    assert (u < 1).all()
    assert np.isfinite(draw(lines(Distribution.normal, loc=0.0, scale=1.0), u)).all()


# --- one iteration ----------------------------------------------------------------------------


def loop(scale: float, *, condition_limit: float = 1e10) -> MonteCarloSystem:
    """Electricity (0) and fabric (1). Fabric buys 3 kWh; electricity buys 0.1 kg of fabric, with
    a lognormal of `scale` on that line, so a draw can bring the loop's gain 3 × 0.1 near 1.
    Electricity emits 0.5 kg CO2 per kWh, fabric 2 kg per kg. One indicator, CO2 = 1."""
    a_rows = np.array([0, 1, 0, 1])
    a_cols = np.array([0, 0, 1, 1])
    a_signs = np.array([1.0, -1.0, -1.0, 1.0])
    a_amounts = np.array([1.0, 0.1, 3.0, 1.0])
    uncertain = Uncertain(
        distribution=np.array([DISTRIBUTIONS.index(Distribution.lognormal)], dtype=np.int8),
        loc=np.array([math.log(0.1)]),
        scale=np.array([scale]),
        minimum=np.zeros(1),
        maximum=np.zeros(1),
        negative=np.zeros(1, dtype=bool),
    )
    return MonteCarloSystem(
        n=2,
        a_rows=a_rows,
        a_cols=a_cols,
        a_signs=a_signs,
        a_amounts=a_amounts,
        b_rows=np.array([0, 0]),
        b_cols=np.array([0, 1]),
        b_amounts=np.array([0.5, 2.0]),
        flows=1,
        uncertain=uncertain,
        in_a=np.array([True]),
        position=np.array([1]),
        reference=np.array([False]),
        factors=np.array([[1.0]]),
        outputs=sparse.csr_array(np.eye(2)),
        offsets=np.zeros((2, 1)),
        tolerance=1e-12,
        condition_limit=condition_limit,
    )


def test_one_iteration_solves_the_drawn_matrix() -> None:
    system = loop(0.2)
    found = sample(system, 0, [0, 1, 2])
    for k in range(3):
        g = float(draw(system.uncertain, uniforms(0, k, 1))[0])
        # electricity: e = 0.5 + g f, fabric: f = 2 + 3 e
        e = (0.5 + 2 * g) / (1 - 3 * g)
        f = 2 + 3 * e

        assert found.values[:, 0, k] == pytest.approx([e, f], rel=1e-9)
    assert found.values.shape == (2, 1, 3)
    assert found.residual <= 1e-12
    assert found.conditions.shape == (3,)


def test_a_draw_that_breaks_the_solve_stops_naming_the_iteration_and_the_processes() -> None:
    system = loop(1.0, condition_limit=20.0)

    with pytest.raises(CalculationError) as caught:
        sample(system, 0, range(200))
    message = str(caught.value)
    match = re.search(r"iteration (\d+) of seed 0", message)

    assert match, message
    k = int(match.group(1))
    assert "nearly singular" in message
    assert set(caught.value.indices) == {0, 1}
    sample(system, 0, range(k))  # every iteration before it solves
    with pytest.raises(CalculationError, match=f"iteration {k} of seed 0"):
        sample(system, 0, [k])


def test_a_reference_that_draws_0_or_below_stops_the_run() -> None:
    system = loop(0.2)
    normal = DISTRIBUTIONS.index(Distribution.normal)
    reference = Uncertain(
        distribution=np.array([normal], dtype=np.int8),
        loc=np.array([1.0]),
        scale=np.array([1.0]),
        minimum=np.zeros(1),
        maximum=np.zeros(1),
        negative=np.zeros(1, dtype=bool),
    )
    system = replace(
        system, uncertain=reference, position=np.array([3]), reference=np.array([True])
    )
    first = next(k for k in range(100) if draw(reference, uniforms(0, k, 1))[0] <= 0)

    with pytest.raises(CalculationError, match=f"iteration {first} of seed 0") as caught:
        sample(system, 0, range(100))
    assert caught.value.indices == (1,)
    assert "reference" in str(caught.value)


def test_draws_of_the_opposite_sign_are_counted() -> None:
    system = loop(0.2)
    normal = DISTRIBUTIONS.index(Distribution.normal)
    crossing = Uncertain(
        distribution=np.array([normal], dtype=np.int8),
        loc=np.array([0.1]),
        scale=np.array([0.1]),
        minimum=np.zeros(1),
        maximum=np.zeros(1),
        negative=np.zeros(1, dtype=bool),
    )
    system = replace(system, uncertain=crossing)
    expected = sum(int(draw(crossing, uniforms(0, k, 1))[0] < 0) for k in range(50))

    assert expected > 0
    assert sample(system, 0, range(50)).opposite_signs == expected


def test_an_own_use_drawn_above_its_reference_stops_the_run() -> None:
    """Fabric uses 0.5 kg of its own fabric per kg made, uniform from 0.5 to 1.5: above 1 the
    net output turns negative."""
    system = loop(0.2)
    own = MonteCarloSystem(
        **{
            **{f: getattr(system, f) for f in system.__dataclass_fields__},
            "a_rows": np.append(system.a_rows, 1),
            "a_cols": np.append(system.a_cols, 1),
            "a_signs": np.append(system.a_signs, -1.0),
            "a_amounts": np.append(system.a_amounts, 0.5),
            "uncertain": lines(Distribution.uniform, n=1, minimum=0.5, maximum=1.5),
            "position": np.array([4]),
        }
    )
    first = next(k for k in range(100) if 0.5 + uniforms(0, k, 1)[0] >= 1)

    with pytest.raises(CalculationError, match=f"iteration {first} of seed 0") as caught:
        sample(own, 0, range(100))
    assert caught.value.indices == (1,)
