"""`hw.monte_carlo`: a range instead of one number, for background products and the products of
databases."""

import math
from dataclasses import replace
from pathlib import Path

import numpy as np
import pyarrow as pa
import pytest
from scipy.stats import norm

import headwater as hw
from headwater.calc.monte_carlo import draw, uniforms
from headwater.model import InvalidDataError, Process
from headwater.query._internals import internals
from tests.conftest import only_process, open_database, toy_database

from .conftest import TOY_DIR, Make, Records, pinned, toy_records

N = 2000

LN3 = math.log(3.0)
LN11 = math.log(1.1)
LN12 = math.log(1.2)


def lognormal(loc: float, scale: float) -> dict[str, object]:
    return {"distribution": "lognormal", "loc": loc, "scale": scale}


def fabric_buys_uncertain_electricity() -> Records:
    """Fabric production's 3 kWh of electricity, lognormal with median 3 and GSD 1.1."""
    return toy_records().with_line("toy:fabric-production", "e02", uncertainty=lognormal(LN3, LN11))


def grid_emits_uncertain_co2() -> Records:
    """Electricity production's 0.5 kg of CO2 per kWh, lognormal with median 0.5 and GSD 1.2."""
    return toy_records().with_line(
        "toy:electricity-production", "e02", uncertainty=lognormal(math.log(0.5), LN12)
    )


@pytest.fixture(scope="module")
def uncertain_fabric(tmp_path_factory: pytest.TempPathFactory) -> hw.Background:
    into = tmp_path_factory.mktemp("releases")
    return hw.load(fabric_buys_uncertain_electricity().build(into).path, method="toy-climate")


@pytest.fixture(scope="module")
def uncertain_grid(tmp_path_factory: pytest.TempPathFactory) -> hw.Background:
    into = tmp_path_factory.mktemp("releases")
    return hw.load(grid_emits_uncertain_co2().build(into).path, method="toy-climate")


TSHIRT = "tshirt:tshirt"


def database(name: str, background: hw.Background) -> hw.Database:
    return pinned(open_database(toy_database(TOY_DIR, name)), background)


def tshirt_of(name: str, background: hw.Background) -> tuple[hw.Database, str]:
    """The T-shirt of a toy case as an item of a run: `(database, "tshirt:tshirt")`."""
    return database(name, background), TSHIRT


def electricity_drawn(iterations: int, seed: int = 0) -> np.ndarray:
    """The kWh fabric buys in each iteration: the only uncertain line, so the first uniform."""
    from headwater.calc.monte_carlo import DISTRIBUTIONS, Uncertain
    from headwater.model import Distribution

    line = Uncertain(
        distribution=np.array([DISTRIBUTIONS.index(Distribution.lognormal)], dtype=np.int8),
        loc=np.array([LN3]),
        scale=np.array([LN11]),
        minimum=np.zeros(1),
        maximum=np.zeros(1),
        negative=np.zeros(1, dtype=bool),
    )
    return np.array([draw(line, uniforms(seed, k, 1))[0] for k in range(iterations)])


# --- exit test 1: the T-shirt's distribution is the analytic one ------------------------------


def test_the_tshirt_range_is_the_analytic_one(uncertain_fabric: hw.Background) -> None:
    """The T-shirt is 0.79470 + 0.13245 E, where E is the kWh per kg of fabric: lognormal with
    median 3 and GSD 1.1. So the T-shirt's median is 1.19205 and every percentile is known."""
    tshirt = tshirt_of("tshirt", uncertain_fabric)
    mc = hw.monte_carlo(uncertain_fabric, [tshirt], iterations=N, seed=0)
    samples = mc.samples[0, 0]

    for p in (2.5, 50.0, 97.5):
        z = float(norm.ppf(p / 100))
        e = 3.0 * 1.1**z
        expected = 0.79470 + 0.13245 * e
        # The standard error of a sample percentile of ln E, taken to the T-shirt's scale.
        error = LN11 * math.sqrt(p / 100 * (1 - p / 100) / N) / float(norm.pdf(z))
        assert abs(np.percentile(samples, p) - expected) < 4 * 0.13245 * e * error
    shifted = np.log((samples - 0.79470) / 0.13245)
    assert abs(shifted.std(ddof=1) - LN11) < 4 * LN11 / math.sqrt(2 * (N - 1))
    assert abs(np.median(shifted) - LN3) < 4 * LN11 * math.sqrt(math.pi / 2 / N)


def test_each_tshirt_sample_is_its_drawn_electricity(uncertain_fabric: hw.Background) -> None:
    tshirt = tshirt_of("tshirt", uncertain_fabric)
    mc = hw.monte_carlo(uncertain_fabric, [tshirt, "toy:fabric-production"], iterations=20)
    e = electricity_drawn(20)

    assert mc.samples[0, 0] == pytest.approx(0.79470 + 0.13245 * e, rel=1e-9)
    assert mc.samples[1, 0] == pytest.approx(2.1192 + 0.5298 * e, rel=1e-9)
    assert mc.scores[:, 0] == pytest.approx([1.19205, 3.7086], rel=1e-9)


def test_the_range_summarises_the_samples(uncertain_fabric: hw.Background) -> None:
    tshirt = tshirt_of("tshirt", uncertain_fabric)
    mc = hw.monte_carlo(
        uncertain_fabric, [tshirt], iterations=200, percentiles=(2.5, 5.0, 95.0, 97.5)
    )
    found = mc.range(tshirt)
    samples = mc.samples[0, 0]

    assert found.item == TSHIRT
    assert mc.range(TSHIRT) == found
    assert found.indicator == "climate-change"
    assert found.unit == "kg CO2-eq"
    assert found.score == pytest.approx(1.19205, rel=1e-9)
    assert found.mean == pytest.approx(samples.mean(), rel=1e-12)
    assert found.median == pytest.approx(np.median(samples), rel=1e-12)
    assert found.sd == pytest.approx(samples.std(ddof=1), rel=1e-12)
    assert found.percentiles[97.5] == pytest.approx(np.percentile(samples, 97.5), rel=1e-12)
    assert list(found.percentiles) == [2.5, 5.0, 95.0, 97.5]
    assert found.iterations == 200


# --- exit test 2: the same seed gives the same draws; shared iterations keep correlation -------


def test_the_same_seed_twice_gives_identical_samples(uncertain_grid: hw.Background) -> None:
    def run(seed: int) -> np.ndarray:
        return hw.monte_carlo(
            uncertain_grid, ["toy:fabric-production"], iterations=30, seed=seed
        ).samples

    # Identical bits on purpose: a run is a pure function of the release, method, seed and k.
    assert np.array_equal(run(3), run(3))
    assert not np.array_equal(run(3), run(4))


def test_two_databases_drawn_with_the_same_seed_stay_correlated(
    uncertain_grid: hw.Background,
) -> None:
    """Both buy fabric and electricity; tshirt-waste also sends 0.05 kg to incineration (+0.06).
    Both T-shirts are `tshirt:tshirt`, so each is drawn in its own run. With the same seed their
    difference is exactly -0.06 in every iteration. With another seed it spreads."""
    tshirt = hw.monte_carlo(uncertain_grid, [tshirt_of("tshirt", uncertain_grid)], iterations=500)
    waste = tshirt_of("tshirt-waste", uncertain_grid)
    alone = hw.monte_carlo(uncertain_grid, [waste], iterations=500, seed=0)
    beside = hw.monte_carlo(
        uncertain_grid, ["toy:fabric-production", waste], iterations=500, seed=0
    )
    other = hw.monte_carlo(uncertain_grid, [waste], iterations=500, seed=1)

    paired = tshirt.samples[0, 0] - alone.samples[0, 0]
    apart = tshirt.samples[0, 0] - other.samples[0, 0]

    assert paired.mean() == pytest.approx(-0.06, rel=1e-9)
    assert paired.std(ddof=1) < 1e-12
    assert np.array_equal(beside.samples[1], alone.samples[0])  # the same k, the same draw
    assert apart.std(ddof=1) > 0.1
    assert tshirt.samples[0, 0].std(ddof=1) > 0.05


# --- exit test 4: a draw that breaks the solve stops, naming the iteration and the processes ---


def test_a_reference_that_draws_0_stops_naming_the_iteration_and_the_process(
    make_release: Make,
) -> None:
    records = toy_records().with_line(
        "toy:electricity-production",
        "e01",
        uncertainty={"distribution": "normal", "loc": 1.0, "scale": 0.5},
    )
    background = hw.load(make_release(records).path, method="toy-climate")
    first = next(k for k in range(1000) if 1.0 + 0.5 * norm.ppf(uniforms(0, k, 1)[0]) <= 0)

    with pytest.raises(hw.HeadwaterError) as caught:
        hw.monte_carlo(background, ["toy:fabric-production"], iterations=first + 10)
    message = str(caught.value)

    assert f"iteration {first} of seed 0" in message
    assert "toy:electricity-production" in message


# --- exit test 5: the provenance has the seed, the iterations and the generator ---------------


def test_the_provenance_has_the_seed_the_iterations_and_the_generator(
    uncertain_fabric: hw.Background,
) -> None:
    mc = hw.monte_carlo(uncertain_fabric, ["toy:fabric-production"], iterations=10, seed=42)
    p = mc.provenance
    release = uncertain_fabric.release
    assert release is not None

    assert p.seed == 42
    assert p.iterations == 10
    assert "PCG64" in p.generator
    assert "SeedSequence(seed, spawn_key=(k,))" in p.generator
    assert p.versions["numpy"] == np.__version__
    assert "scipy" in p.versions
    assert p.engine_version == hw.__version__
    assert p.releases == (release.ref,)
    assert p.method == "toy-climate"
    assert p.method_hash == internals(uncertain_fabric).method_hash
    assert p.uncertain_lines == {"lognormal": 1}
    assert p.opposite_signs == 0
    assert p.residual <= p.solver.tolerance
    assert p.largest_condition >= 1
    assert len(p.input_hash) == 64


# --- what it refuses --------------------------------------------------------------------------


def test_a_folder_background_is_refused() -> None:
    background = hw.load(
        TOY_DIR / "background", method=TOY_DIR / "methods" / "toy-climate", namespace="toy"
    )

    with pytest.raises(InvalidDataError, match="release"):
        hw.monte_carlo(background, ["toy:fabric-production"], iterations=10)


def test_a_release_with_no_uncertain_line_is_refused(
    release_background: hw.Background,
) -> None:
    with pytest.raises(InvalidDataError, match="no line with a distribution"):
        hw.monte_carlo(release_background, ["toy:fabric-production"], iterations=10)


def test_an_unknown_product_is_refused_with_the_closest_id(
    uncertain_fabric: hw.Background,
) -> None:
    with pytest.raises(ValueError, match="toy:fabric-production"):
        hw.monte_carlo(uncertain_fabric, ["toy:fabric-productio"], iterations=10)


def test_an_item_asked_twice_is_refused(uncertain_fabric: hw.Background) -> None:
    with pytest.raises(ValueError, match="twice"):
        hw.monte_carlo(
            uncertain_fabric, ["toy:fabric-production", "toy:fabric-production"], iterations=10
        )


def test_iterations_must_be_at_least_2(uncertain_fabric: hw.Background) -> None:
    with pytest.raises(ValueError, match="iterations"):
        hw.monte_carlo(uncertain_fabric, ["toy:fabric-production"], iterations=1)


# --- units, workers, output -------------------------------------------------------------------


def test_a_line_in_another_unit_draws_in_the_unit_of_its_flow(make_release: Make) -> None:
    """3 kWh written as 10.8 MJ, with its lognormal in MJ: the same draws, in kWh."""
    records = toy_records().with_line(
        "toy:fabric-production",
        "e02",
        amount=10.8,
        unit="MJ",
        uncertainty=lognormal(math.log(10.8), LN11),
    )
    background = hw.load(make_release(records).path, method="toy-climate")
    mc = hw.monte_carlo(background, ["toy:fabric-production"], iterations=20)

    assert mc.samples[0, 0] == pytest.approx(2.1192 + 0.5298 * electricity_drawn(20), rel=1e-9)


def test_workers_give_the_same_samples(uncertain_fabric: hw.Background) -> None:
    items = ["toy:fabric-production", "toy:electricity-production"]
    one = hw.monte_carlo(uncertain_fabric, items, iterations=40, seed=5)
    two = hw.monte_carlo(uncertain_fabric, items, iterations=40, seed=5, workers=2)

    assert np.array_equal(one.samples, two.samples)
    assert np.array_equal(one.conditions, two.conditions)


def test_print_shows_one_line_per_item_and_indicator(uncertain_fabric: hw.Background) -> None:
    tshirt = tshirt_of("tshirt", uncertain_fabric)
    mc = hw.monte_carlo(uncertain_fabric, [tshirt, "toy:fabric-production"], iterations=50)
    text = str(mc)

    assert text.splitlines()[0] == "Monte Carlo: 50 iterations, seed 0"
    assert "tshirt:tshirt climate-change: score 1.19205 kg CO2-eq, mean " in text
    assert "toy:fabric-production climate-change: score 3.7086 kg CO2-eq" in text
    assert "2.5% " in text and "97.5% " in text


def test_to_arrow_has_one_row_per_item_and_indicator(uncertain_fabric: hw.Background) -> None:
    mc = hw.monte_carlo(
        uncertain_fabric, ["toy:fabric-production", "toy:electricity-production"], iterations=20
    )
    table = mc.to_arrow()

    assert isinstance(table, pa.Table)
    assert table.num_rows == 2
    assert table.column_names == [
        "item",
        "indicator",
        "unit",
        "score",
        "mean",
        "median",
        "sd",
        "p2.5",
        "p97.5",
    ]


def test_a_database_on_a_background_without_uncertainty_in_its_suppliers_is_fixed(
    tmp_path: Path,
) -> None:
    """Only incineration is uncertain: the T-shirt buys none of it, so it never moves."""
    records = toy_records().with_line(
        "toy:incineration-textile-waste", "e02", uncertainty=lognormal(math.log(1.2), LN11)
    )
    background = hw.load(records.build(tmp_path).path, method="toy-climate")
    mc = hw.monte_carlo(background, [tshirt_of("tshirt", background)], iterations=20)

    assert mc.range(TSHIRT).sd == pytest.approx(0.0, abs=1e-15)


def test_a_markets_shares_are_drawn_line_by_line(make_release: Make) -> None:
    """A market buys 0.6 and 0.4 kWh, each lognormal with GSD 1.1. Each line is drawn on its own,
    as in Brightway, so the shares sum to 1 only at their medians: in a draw their sum has a mean
    of exp(σ²/2) = 1.00455 and a standard deviation of 1.00455 × 0.09553 × √0.52 = 0.0692."""
    market = Process.model_validate(
        {
            "id": "toy:market-electricity",
            "name": "market for electricity",
            "location": "GLO",
            "kind": "market_activity",
            "exchanges": [
                {
                    "process_id": "toy:market-electricity",
                    "line_id": "e01",
                    "role": "reference",
                    "flow_id": "toy:electricity",
                    "amount": 1.0,
                    "unit": "kWh",
                },
                *(
                    {
                        "process_id": "toy:market-electricity",
                        "line_id": line,
                        "role": "input",
                        "flow_id": "toy:electricity",
                        "amount": share,
                        "unit": "kWh",
                        "supplier_id": "toy:electricity-production",
                        "uncertainty": lognormal(math.log(share), LN11),
                    }
                    for line, share in (("e02", 0.6), ("e03", 0.4))
                ),
            ],
        }
    )
    records = replace(toy_records(), processes=(*toy_records().processes, market))
    background = hw.load(make_release(records).path, method="toy-climate")
    mc = hw.monte_carlo(background, ["toy:market-electricity"], iterations=N)
    shares = mc.samples[0, 0] / 0.5298
    mean = math.exp(LN11**2 / 2)
    sd = mean * math.sqrt(math.expm1(LN11**2)) * math.sqrt(0.6**2 + 0.4**2)

    assert mc.scores[0, 0] == pytest.approx(0.5298, rel=1e-9)
    assert mean == pytest.approx(1.00455, abs=5e-6)
    assert sd == pytest.approx(0.0692, abs=5e-5)
    assert abs(shares.mean() - mean) < 4 * sd / math.sqrt(N)
    assert abs(shares.std(ddof=1) - sd) < 4 * sd / math.sqrt(2 * (N - 1))


@pytest.mark.parametrize(
    "name",
    [
        "tshirt",
        "tshirt-waste",
        "tshirt-dyed",
        "tshirt-parameter",
        "tshirt-cut-off",
        "tshirt-no-factor",
        "tshirt-grid-scenario",
        "dyes",
    ],
)
def test_a_products_score_with_no_draw_is_what_calc_gives(
    uncertain_fabric: hw.Background, name: str
) -> None:
    """A product of a database is its direct emissions plus what it buys times the background's
    scores: the same number as `calc`, for a database that uses another one (the dyed T-shirt
    uses `dyes`) too."""
    found = database(name, uncertain_fabric)
    process = only_process(found)
    mc = hw.monte_carlo(uncertain_fabric, [(found, process)], iterations=2)

    assert mc.scores[0, 0] == pytest.approx(found.calc(uncertain_fabric, process).score, rel=1e-9)


def test_a_single_score_is_drawn_with_the_indicators_it_weighs(make_release: Make) -> None:
    """toy-nw's single score is 0.6 / 10 × climate change + 0.4 / 0.01 × ozone formation, in
    every iteration: 0.161523 Pt for the T-shirt with no draw."""
    records = grid_emits_uncertain_co2().with_line(
        "toy:electricity-production",
        "e03",
        uncertainty=lognormal(math.log(0.001), LN12),
    )
    release = make_release(records)
    background = hw.load(
        release.path,
        method=TOY_DIR / "methods" / "toy-impacts",
        sets=TOY_DIR / "methods" / "toy-nw",
    )
    folder = pinned(open_database(TOY_DIR / "tshirt" / "tshirt"), background).folder
    tshirt = hw.Database(
        replace(folder, database=folder.database.model_copy(update={"methods": ("toy-impacts",)}))
    )
    mc = hw.monte_carlo(background, [(tshirt, TSHIRT)], iterations=50)
    at = {indicator: j for j, indicator in enumerate(mc.indicators)}
    samples = mc.samples[0]

    assert mc.units[at["toy-nw:single-score"]] == "Pt"
    assert mc.scores[0, at["toy-nw:single-score"]] == pytest.approx(0.161523, rel=1e-9)
    assert samples[at["toy-nw:single-score"]] == pytest.approx(
        0.06 * samples[at["climate-change"]] + 40 * samples[at["ozone-formation"]], rel=1e-9
    )
    assert samples[at["toy-nw:single-score"]].std() > 0
