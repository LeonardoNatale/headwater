"""`hw.monte_carlo` on a database's own lines and parameters, its local overrides, and the
scenarios of a study.

A database line is drawn from a uniform keyed by its process id and line id, with the (seed, k)
of the run, so its draw never depends on what else is asked. Example: the T-shirt's 0.25 kg of
fabric, lognormal with a GSD of 1.1, makes the T-shirt 0.2649 + 3.7086 F in every iteration.

Every toy T-shirt is the process `tshirt:tshirt`, so two of them cannot be items of one run.
Each is drawn in its own run with the same seed: the draws are keyed by ids, so the pairs are
exact. A product of a study and the same product in one of the study's scenarios have different
labels, `tshirt:tshirt` and `tshirt:tshirt[clean-grid]`, so they are items of one run:
`(study, "tshirt:tshirt")` and `(study, "tshirt:tshirt", "clean-grid")`.
"""

import math
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from scipy.special import ndtri
from scipy.stats import norm

import headwater as hw
from headwater.calc.monte_carlo import (
    key_words,
    keyed_uniforms,
    line_key,
    parameter_key,
    uniforms,
)
from headwater.model import Exchange, InvalidDataError, Process
from tests.conftest import add_lines, open_database, open_study_folder, toy_database

from .conftest import TOY_DIR, Records, pinned, pinned_study, toy_records

N = 2000

LN11 = math.log(1.1)
LN12 = math.log(1.2)
COLUMNS = ",distribution,scale,minimum,maximum"
TSHIRT = "tshirt:tshirt"
DYE = "dyes:reactive-dye"
WEAVING = (
    "description: Fabric takes 2.5 kWh per kg.\n"
    "changes:\n  - process: toy:fabric-production\n    line: e02\n    amount: 2.5\n"
)


def lognormal(median: float, scale: float) -> dict[str, object]:
    return {"distribution": "lognormal", "loc": math.log(median), "scale": scale}


def wind_records() -> Records:
    """The toy plus wind electricity at 0.01 kg CO2 per kWh, and fabric's 3 kWh lognormal."""
    electricity = next(p for p in toy_records().processes if p.id == "toy:electricity-production")
    wind_id = "toy:wind-electricity"
    lines = (
        Exchange.model_validate({**electricity.exchanges[0].model_dump(), "process_id": wind_id}),
        Exchange.model_validate(
            {
                **electricity.exchanges[1].model_dump(),
                "process_id": wind_id,
                "amount": 0.01,
            }
        ),
    )
    wind = Process.model_validate(
        {
            **electricity.model_dump(exclude={"exchanges"}),
            "id": wind_id,
            "name": "wind electricity",
            "exchanges": lines,
        }
    )
    records = toy_records().with_line(
        "toy:fabric-production", "e02", uncertainty=lognormal(3.0, LN11)
    )
    return replace(records, processes=(*records.processes, wind))


@pytest.fixture(scope="module")
def fixed(tmp_path_factory: pytest.TempPathFactory) -> hw.Background:
    """The toy as a release with no distribution: only a database's own lines are drawn."""
    return hw.load(toy_records().build(tmp_path_factory.mktemp("r")).path, method="toy-climate")


@pytest.fixture(scope="module")
def uncertain_fabric(tmp_path_factory: pytest.TempPathFactory) -> hw.Background:
    """Fabric production's 3 kWh of electricity, lognormal with median 3 and GSD 1.1."""
    records = toy_records().with_line(
        "toy:fabric-production", "e02", uncertainty=lognormal(3.0, LN11)
    )
    return hw.load(records.build(tmp_path_factory.mktemp("r")).path, method="toy-climate")


@pytest.fixture(scope="module")
def uncertain_grid(tmp_path_factory: pytest.TempPathFactory) -> hw.Background:
    """Electricity production's 0.5 kg of CO2 per kWh, lognormal with median 0.5 and GSD 1.2."""
    records = toy_records().with_line(
        "toy:electricity-production", "e02", uncertainty=lognormal(0.5, LN12)
    )
    return hw.load(records.build(tmp_path_factory.mktemp("r")).path, method="toy-climate")


@pytest.fixture(scope="module")
def wind(tmp_path_factory: pytest.TempPathFactory) -> hw.Background:
    return hw.load(wind_records().build(tmp_path_factory.mktemp("r")).path, method="toy-climate")


def opened(toy: Path, name: str, background: hw.Background) -> hw.Database:
    return pinned(open_database(toy_database(toy, name)), background)


def footprint(toy: Path, case: str, background: hw.Background) -> hw.Study:
    """The study `tshirt-footprint` of a toy case, with the databases it reaches pinned to the
    release of `background`."""
    return pinned_study(open_study_folder(toy / case / "tshirt-footprint"), background)


def tshirt_of(toy: Path, case: str, background: hw.Background) -> tuple[hw.Database, str]:
    """The T-shirt of a toy case as an item of a run: `(database, "tshirt:tshirt")`."""
    return opened(toy, case, background), TSHIRT


def dye(toy: Path, background: hw.Background) -> tuple[hw.Database, str]:
    """The dye of the database `dyes` as an item of a run."""
    return opened(toy, "dyes", background), DYE


def distribute(path: Path, **lines: str) -> None:
    """Add the distribution columns to exchanges.csv of a database with one process, with these
    cells for these lines."""
    rows = path.read_text().splitlines()
    out = [rows[0] + COLUMNS]
    for row in rows[1:]:
        line = row.split(",", 2)[1]
        out.append(row + "," + lines.pop(line, ",,,"))
    assert not lines, f"no lines {sorted(lines)} in {path}"
    path.write_text("\n".join(out) + "\n")


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def keyed(seed: int, iterations: int, key: str) -> np.ndarray:
    """The uniform of one database key in each iteration."""
    words = key_words([key])
    return np.array([keyed_uniforms(seed, k, words)[0] for k in range(iterations)])


def background_line(seed: int, iterations: int, median: float, scale: float) -> np.ndarray:
    """The one uncertain line of the background in each iteration: the first uniform."""
    return np.array(
        [median * math.exp(scale * ndtri(uniforms(seed, k, 1)[0])) for k in range(iterations)]
    )


def fabric_drawn(toy: Path, case: str = "tshirt") -> None:
    distribute(toy_database(toy, case) / "exchanges.csv", e02=f"lognormal,{LN11!r},,")


# --- exit test 1: a database line with a lognormal gives the analytic range ----------------


def test_a_database_line_with_a_lognormal_gives_the_analytic_range(
    toy: Path, fixed: hw.Background
) -> None:
    """The T-shirt is 0.2649 + 3.7086 F, where F is its fabric: lognormal, median 0.25, GSD 1.1.
    Its percentiles are 0.2649 + 3.7086 × 0.25 × 1.1^z, checked by hand in calculation.md."""
    fabric_drawn(toy)
    mc = hw.monte_carlo(fixed, [tshirt_of(toy, "tshirt", fixed)], iterations=N, seed=0)
    samples = mc.samples[0, 0]

    by_hand = {2.5: 1.034069, 50.0: 1.19205, 97.5: 1.382479}
    for p, value in by_hand.items():
        z = float(norm.ppf(p / 100))
        f = 0.25 * 1.1**z
        expected = 0.2649 + 3.7086 * f
        assert expected == pytest.approx(value, abs=5e-7)
        error = LN11 * math.sqrt(p / 100 * (1 - p / 100) / N) / float(norm.pdf(z))
        assert abs(np.percentile(samples, p) - expected) < 4 * 3.7086 * f * error
    assert mc.scores[0, 0] == pytest.approx(1.19205, rel=1e-9)


def test_each_sample_is_the_drawn_fabric(toy: Path, fixed: hw.Background) -> None:
    fabric_drawn(toy)
    mc = hw.monte_carlo(fixed, [tshirt_of(toy, "tshirt", fixed)], iterations=20, seed=3)
    f = 0.25 * np.exp(LN11 * ndtri(keyed(3, 20, line_key("tshirt:tshirt", "e02"))))

    assert mc.samples[0, 0] == pytest.approx(0.2649 + 3.7086 * f, rel=1e-9)


# --- exit test 2: a database line draws the same alone or with others ----------------------


def test_a_database_line_draws_the_same_alone_or_with_others(
    toy: Path, uncertain_fabric: hw.Background
) -> None:
    fabric_drawn(toy)
    distribute(toy / "tshirt-dyed/dyes/exchanges.csv", e03=f"lognormal,{LN12!r},,")
    tshirt = tshirt_of(toy, "tshirt", uncertain_fabric)
    dyes = dye(toy, uncertain_fabric)

    alone = hw.monte_carlo(uncertain_fabric, [tshirt], iterations=50)
    others = hw.monte_carlo(
        uncertain_fabric, [dyes, "toy:fabric-production", tshirt], iterations=50
    )

    # Identical bits on purpose: a run is a pure function of the release, the files, seed and k.
    assert np.array_equal(alone.samples[0], others.samples[2])


def test_a_used_database_draws_the_same_inside_the_database_that_uses_it(
    toy: Path, uncertain_fabric: hw.Background
) -> None:
    """The dye emits 4 kg of CO2 per kg, lognormal, and takes `kwh_per_kg`, a lognormal
    parameter. The dyed T-shirt is the T-shirt plus 0.01 kg of dye in every iteration."""
    distribute(toy / "tshirt-dyed/dyes/exchanges.csv", e03=f"lognormal,{LN12!r},,")
    write(
        toy / "tshirt-dyed/dyes/parameters.yaml",
        f"database:\n  kwh_per_kg: {{value: 2, unit: kWh, distribution: lognormal, "
        f"scale: {LN11!r}}}\n",
    )
    dyes = dye(toy, uncertain_fabric)
    dyed = tshirt_of(toy, "tshirt-dyed", uncertain_fabric)

    alone = hw.monte_carlo(uncertain_fabric, [dyes], iterations=50)
    used = hw.monte_carlo(uncertain_fabric, [dyed, dyes], iterations=50)
    tshirt = hw.monte_carlo(
        uncertain_fabric, [tshirt_of(toy, "tshirt", uncertain_fabric)], iterations=50
    )

    assert np.array_equal(alone.samples[0], used.samples[1])
    assert alone.samples[0, 0].std(ddof=1) > 0.1
    assert used.samples[0, 0] == pytest.approx(
        tshirt.samples[0, 0] + 0.01 * used.samples[1, 0], rel=1e-9
    )


def test_two_versions_of_a_database_draw_the_same_line_the_same_way(
    toy: Path, fixed: hw.Background
) -> None:
    """tshirt and tshirt-waste both hold `tshirt:tshirt` line e02: one id, one draw. The waste
    line adds 0.06 in every iteration."""
    fabric_drawn(toy)
    fabric_drawn(toy, "tshirt-waste")
    tshirt = hw.monte_carlo(fixed, [tshirt_of(toy, "tshirt", fixed)], iterations=200)
    waste = hw.monte_carlo(fixed, [tshirt_of(toy, "tshirt-waste", fixed)], iterations=200)

    found = waste.samples[0, 0] - tshirt.samples[0, 0]
    assert found.mean() == pytest.approx(0.06, rel=1e-9)
    assert found.std(ddof=1) < 1e-12


def test_drawing_database_lines_leaves_the_background_draws_as_they_were(
    toy: Path, uncertain_fabric: hw.Background
) -> None:
    fabric_drawn(toy)
    tshirt = tshirt_of(toy, "tshirt", uncertain_fabric)
    without = hw.monte_carlo(uncertain_fabric, ["toy:fabric-production"], iterations=40)
    beside = hw.monte_carlo(uncertain_fabric, [tshirt, "toy:fabric-production"], iterations=40)
    e = background_line(0, 40, 3.0, LN11)

    assert np.array_equal(without.samples[0], beside.samples[1])
    assert without.samples[0, 0] == pytest.approx(2.1192 + 0.5298 * e, rel=1e-12)


SAMPLES_BACKGROUND_ONLY = (
    (1.224924035448502, 1.1902101982621938, 1.2050305284508616, 1.2802957090303044),
    (3.8400961417940085, 3.7012407930487754, 3.7605221138034457, 4.061582836121218),
)
"""The T-shirt and 1 kg of fabric, fabric's 3 kWh lognormal with GSD 1.1, iterations 0 to 3 of
seed 7, as the first Monte Carlo, of the background alone, drew them."""


def test_the_background_draws_are_those_of_53(uncertain_fabric: hw.Background) -> None:
    tshirt = pinned(open_database(TOY_DIR / "tshirt/tshirt"), uncertain_fabric), TSHIRT
    mc = hw.monte_carlo(uncertain_fabric, [tshirt, "toy:fabric-production"], iterations=4, seed=7)

    # Identical bits on purpose: database draws must never move the background's.
    assert mc.samples[:, 0, :].tolist() == [list(row) for row in SAMPLES_BACKGROUND_ONLY]


# --- exit test 3: an override's unchanged lines are paired with the original's -------------


OVERRIDE = "process,line,role,flow,amount,unit,supplier,distribution,scale,minimum,maximum\n"


def test_an_overrides_unchanged_lines_are_paired_with_the_originals(
    toy: Path, uncertain_fabric: hw.Background
) -> None:
    """The mill's fabric emits 1.5 kg of CO2 instead of 2.0. Its copy still buys the drawn 3 kWh,
    with the original's draw, so the T-shirt with the copy is 0.125 lower in every iteration."""
    write(
        toy / "tshirt-local-fabric/tshirt/overrides/efficient-weaving.csv",
        OVERRIDE + "toy:fabric-production,e03,emission,toy:co2,1.5,kg,,,,,\n",
    )
    mill = hw.monte_carlo(
        uncertain_fabric, [tshirt_of(toy, "tshirt-local-fabric", uncertain_fabric)], iterations=200
    )
    tshirt = hw.monte_carlo(
        uncertain_fabric, [tshirt_of(toy, "tshirt", uncertain_fabric)], iterations=200
    )

    found = mill.samples[0, 0] - tshirt.samples[0, 0]
    assert found.mean() == pytest.approx(-0.125, rel=1e-9)
    assert found.std(ddof=1) < 1e-12
    assert tshirt.range(TSHIRT).sd > 0.01


def test_a_line_an_override_changes_is_fixed(toy: Path, uncertain_fabric: hw.Background) -> None:
    """The copy buys 2.5 kWh, fixed: 0.25 × (2.1192 + 2.5 × 0.5298) + 0.5 × 0.5298."""
    mill = tshirt_of(toy, "tshirt-local-fabric", uncertain_fabric)
    mc = hw.monte_carlo(uncertain_fabric, [mill], iterations=50)

    assert mc.samples[0, 0] == pytest.approx(np.full(50, 1.125825), rel=1e-9)


def test_an_override_can_give_its_changed_line_a_distribution(
    toy: Path, uncertain_fabric: hw.Background
) -> None:
    """Drawn with the copy's own key, not the original's: 2.5 kWh, lognormal with GSD 1.1."""
    write(
        toy / "tshirt-local-fabric/tshirt/overrides/efficient-weaving.csv",
        OVERRIDE + "toy:fabric-production,e02,input,toy:electricity,2.5,kWh,"
        f"toy:electricity-production,lognormal,{LN11!r},,\n",
    )
    mill = tshirt_of(toy, "tshirt-local-fabric", uncertain_fabric)
    mc = hw.monte_carlo(uncertain_fabric, [mill], iterations=30, seed=2)
    key = line_key("tshirt:copy:toy:fabric-production", "e02")
    kwh = 2.5 * np.exp(LN11 * ndtri(keyed(2, 30, key)))

    assert mc.samples[0, 0] == pytest.approx(0.2649 + 0.25 * (2.1192 + 0.5298 * kwh), rel=1e-9)


def test_an_override_that_changes_only_the_supplier_keeps_the_originals_draw(
    toy: Path, wind: hw.Background
) -> None:
    """The mill buys the same 3 kWh from wind at 0.01 kg CO2 per kWh, drawn as the original."""
    write(
        toy / "tshirt-local-fabric/tshirt/overrides/efficient-weaving.csv",
        OVERRIDE + "toy:fabric-production,e02,input,toy:electricity,3,kWh,"
        "toy:wind-electricity,,,,\n",
    )
    mill = tshirt_of(toy, "tshirt-local-fabric", wind)
    mc = hw.monte_carlo(wind, [mill], iterations=30)
    e = background_line(0, 30, 3.0, LN11)

    assert mc.samples[0, 0] == pytest.approx(0.2649 + 0.25 * (2.1192 + 0.01 * e), rel=1e-9)


# --- exit test 4: base and scenario stay paired ---------------------------------------------


def test_a_scenario_stays_paired_with_its_base(uncertain_fabric: hw.Background) -> None:
    """clean-grid sets the grid at 0.3 kg CO2 per kWh. With the fabric's E kWh drawn, the base is
    0.79470 + 0.13245 E and the scenario 0.6947 + 0.08245 E: they differ by 0.1 + 0.05 E."""
    study = footprint(TOY_DIR, "tshirt-grid-scenario", uncertain_fabric)
    base, clean = (study, TSHIRT), (study, TSHIRT, "clean-grid")
    mc = hw.monte_carlo(uncertain_fabric, [base, clean], iterations=500)
    other = hw.monte_carlo(uncertain_fabric, [clean], iterations=500, seed=1)
    e = background_line(0, 500, 3.0, LN11)

    assert mc.items == ("tshirt:tshirt", "tshirt:tshirt[clean-grid]")
    assert mc.scores[:, 0] == pytest.approx([1.19205, 0.94205], rel=1e-9)
    paired = mc.difference(base, clean)
    assert mc.samples[0, 0] - mc.samples[1, 0] == pytest.approx(0.1 + 0.05 * e, rel=1e-9)
    apart = mc.samples[0, 0] - other.samples[0, 0]
    assert paired.sd < 0.5 * apart.std(ddof=1)
    assert mc.range(clean).item == "tshirt:tshirt[clean-grid]"
    assert mc.range("tshirt:tshirt[clean-grid]") == mc.range(clean)


def test_a_scenario_that_changes_a_purchase_stays_paired(
    toy: Path, uncertain_grid: hw.Background
) -> None:
    """Fabric takes 2.5 kWh per kg, for everyone: a column of A changes. With the grid's C kg of
    CO2 per kWh drawn, electricity scores C + 0.0298 and the T-shirt 0.5298 + 1.25 (C + 0.0298)
    in the base, 0.5298 + 1.125 (C + 0.0298) in the scenario."""
    write(toy / "tshirt-grid-scenario/tshirt-footprint/scenarios/efficient-weaving.yaml", WEAVING)
    study = footprint(toy, "tshirt-grid-scenario", uncertain_grid)
    mc = hw.monte_carlo(
        uncertain_grid, [(study, TSHIRT), (study, TSHIRT, "efficient-weaving")], iterations=100
    )
    electricity = background_line(0, 100, 0.5, LN12) + 0.0298

    assert mc.samples[0, 0] == pytest.approx(0.5298 + 1.25 * electricity, rel=1e-9)
    assert mc.samples[1, 0] == pytest.approx(0.5298 + 1.125 * electricity, rel=1e-9)


def test_a_line_the_scenario_changes_is_fixed(toy: Path, uncertain_fabric: hw.Background) -> None:
    """The drawn 3 kWh is set to 2.5 by the scenario: the T-shirt is 1.125825 in every
    iteration."""
    write(toy / "tshirt-grid-scenario/tshirt-footprint/scenarios/efficient-weaving.yaml", WEAVING)
    study = footprint(toy, "tshirt-grid-scenario", uncertain_fabric)
    mc = hw.monte_carlo(uncertain_fabric, [(study, TSHIRT, "efficient-weaving")], iterations=50)

    assert mc.samples[0, 0] == pytest.approx(np.full(50, 1.125825), rel=1e-9)


def test_a_scenario_that_changes_only_a_supplier_keeps_the_draw(
    toy: Path, wind: hw.Background
) -> None:
    write(
        toy / "tshirt-grid-scenario/tshirt-footprint/scenarios/wind-mill.yaml",
        "description: Fabric buys wind electricity.\n"
        "changes:\n  - process: toy:fabric-production\n    line: e02\n"
        "    supplier: toy:wind-electricity\n",
    )
    study = footprint(toy, "tshirt-grid-scenario", wind)
    mc = hw.monte_carlo(wind, [(study, TSHIRT, "wind-mill")], iterations=30)
    e = background_line(0, 30, 3.0, LN11)

    assert mc.samples[0, 0] == pytest.approx(0.2649 + 0.25 * (2.1192 + 0.01 * e), rel=1e-9)


def test_a_scenario_that_sets_a_drawn_parameter_fixes_it(toy: Path, fixed: hw.Background) -> None:
    write(
        toy / "tshirt-parameter/tshirt/parameters.yaml",
        f"database:\n  fabric_kg: {{value: 0.25, distribution: lognormal, scale: {LN11!r}}}\n",
    )
    study = footprint(toy, "tshirt-parameter", fixed)
    mc = hw.monte_carlo(fixed, [(study, TSHIRT), (study, TSHIRT, "more-fabric")], iterations=50)

    assert mc.range(TSHIRT).sd > 0.01
    assert mc.samples[1, 0] == pytest.approx(np.full(50, 1.37748), rel=1e-9)


# --- exit test 5: a parameter with a distribution moves every line that uses it together ----


def test_a_parameter_with_a_distribution_moves_every_line_that_uses_it(
    toy: Path, fixed: hw.Background
) -> None:
    """The fabric is `fabric_kg` and the scraps sent to incineration `fabric_kg * 0.2`. With
    `fabric_kg` drawn as P, the T-shirt is 0.2649 + 3.7086 P + 1.2 × 0.2 P in every iteration."""
    root = toy / "tshirt-parameter/tshirt"
    write(
        root / "parameters.yaml",
        f"database:\n  fabric_kg: {{value: 0.25, distribution: lognormal, scale: {LN11!r}}}\n",
    )
    add_lines(
        root,
        "tshirt",
        "e04,waste_to_treatment,toy:textile-waste,fabric_kg * 0.2,kg,"
        "toy:incineration-textile-waste",
    )
    mc = hw.monte_carlo(fixed, [tshirt_of(toy, "tshirt-parameter", fixed)], iterations=30, seed=5)
    p = 0.25 * np.exp(LN11 * ndtri(keyed(5, 30, parameter_key("tshirt", "fabric_kg"))))

    assert mc.samples[0, 0] == pytest.approx(0.2649 + 3.7086 * p + 0.24 * p, rel=1e-9)


def test_a_formula_parameter_follows_the_drawn_one(toy: Path, fixed: hw.Background) -> None:
    root = toy / "tshirt-parameter/tshirt"
    write(
        root / "parameters.yaml",
        f"database:\n  net_kg: {{value: 0.25, distribution: lognormal, scale: {LN11!r}}}\n"
        "  loss: 0.05\n  fabric_kg: net_kg * (1 + loss)\n",
    )
    mc = hw.monte_carlo(fixed, [tshirt_of(toy, "tshirt-parameter", fixed)], iterations=30)
    net = 0.25 * np.exp(LN11 * ndtri(keyed(0, 30, parameter_key("tshirt", "net_kg"))))

    assert mc.samples[0, 0] == pytest.approx(0.2649 + 3.7086 * 1.05 * net, rel=1e-9)


# --- fail loudly, refusals, provenance --------------------------------------------------------


def test_a_database_reference_drawn_at_0_stops_naming_the_iteration_and_the_process(
    toy: Path, fixed: hw.Background
) -> None:
    distribute(toy / "tshirt/tshirt/exchanges.csv", e01="normal,0.5,,")
    u = keyed(0, 1000, line_key("tshirt:tshirt", "e01"))
    first = int(np.flatnonzero(1.0 + 0.5 * ndtri(u) <= 0)[0])

    with pytest.raises(hw.HeadwaterError) as caught:
        hw.monte_carlo(fixed, [tshirt_of(toy, "tshirt", fixed)], iterations=first + 5)
    message = str(caught.value)

    assert f"iteration {first} of seed 0" in message
    assert "tshirt:tshirt" in message


def test_a_run_with_nothing_drawn_is_refused(fixed: hw.Background) -> None:
    tshirt = pinned(open_database(TOY_DIR / "tshirt/tshirt"), fixed), TSHIRT

    with pytest.raises(InvalidDataError) as caught:
        hw.monte_carlo(fixed, [tshirt, "toy:fabric-production"], iterations=10)

    assert caught.value.problems[0].code == "no-uncertainty"


def test_the_provenance_counts_the_database_lines_and_parameters_drawn(
    toy: Path, uncertain_fabric: hw.Background
) -> None:
    fabric_drawn(toy)
    distribute(toy / "tshirt-dyed/dyes/exchanges.csv", e03="normal,0.4,,")
    write(
        toy / "tshirt-dyed/dyes/parameters.yaml",
        "database:\n  kwh_per_kg: {value: 2, distribution: uniform, minimum: 1.5, maximum: 2.5}\n",
    )
    items = [tshirt_of(toy, "tshirt", uncertain_fabric), dye(toy, uncertain_fabric)]
    p = hw.monte_carlo(uncertain_fabric, items, iterations=5).provenance

    assert p.uncertain_lines == {"lognormal": 1}
    assert p.database_lines == {"lognormal": 1, "normal": 1}
    assert p.parameters == 1
    assert "sha256" in p.generator


def test_workers_give_the_same_samples_with_databases_and_scenarios(
    toy: Path, uncertain_fabric: hw.Background
) -> None:
    fabric_drawn(toy, "tshirt-grid-scenario")
    study = footprint(toy, "tshirt-grid-scenario", uncertain_fabric)
    items = [(study, TSHIRT), (study, TSHIRT, "clean-grid"), "toy:fabric-production"]
    one = hw.monte_carlo(uncertain_fabric, items, iterations=12)
    three = hw.monte_carlo(uncertain_fabric, items, iterations=12, workers=3)

    assert np.array_equal(one.samples, three.samples)


def test_a_products_score_with_no_draw_is_what_calc_gives_in_a_scenario(
    toy: Path, uncertain_fabric: hw.Background
) -> None:
    fabric_drawn(toy, "tshirt-grid-scenario")
    study = footprint(toy, "tshirt-grid-scenario", uncertain_fabric)
    mc = hw.monte_carlo(uncertain_fabric, [(study, TSHIRT, "clean-grid")], iterations=5)

    [clean] = study.calc(uncertain_fabric, scenario="clean-grid")
    assert mc.scores[0, 0] == pytest.approx(clean.score, rel=1e-12)


def test_a_product_outside_a_list_or_a_bare_database_says_how_to_name_a_product(
    uncertain_fabric: hw.Background,
) -> None:
    grid = pinned(open_database(TOY_DIR / "tshirt-grid-scenario/tshirt"), uncertain_fabric)

    with pytest.raises(ValueError, match=r"inside the list of items"):
        hw.monte_carlo(uncertain_fabric, (grid, TSHIRT), iterations=5)
    with pytest.raises(ValueError, match=r"\(database or study, process id\)"):
        hw.monte_carlo(uncertain_fabric, [grid], iterations=5)  # pyright: ignore[reportArgumentType]
    study = footprint(TOY_DIR, "tshirt-grid-scenario", uncertain_fabric)
    with pytest.raises(ValueError, match=r"\(database or study, process id\)"):
        hw.monte_carlo(uncertain_fabric, study, iterations=5)  # pyright: ignore[reportArgumentType]


def test_a_scenario_named_as_a_product_says_how_to_name_it(
    uncertain_fabric: hw.Background,
) -> None:
    study = footprint(TOY_DIR, "tshirt-grid-scenario", uncertain_fabric)

    with pytest.raises(ValueError, match=r"'clean-grid' is a scenario.*\(study, "):
        hw.monte_carlo(uncertain_fabric, [(study, "clean-grid")], iterations=5)


def test_the_provenance_counts_scenario_refactorizations(
    toy: Path, uncertain_grid: hw.Background
) -> None:
    """One changed column is within the crossover: every iteration updates its factors."""
    write(toy / "tshirt-grid-scenario/tshirt-footprint/scenarios/efficient-weaving.yaml", WEAVING)
    study = footprint(toy, "tshirt-grid-scenario", uncertain_grid)
    mc = hw.monte_carlo(uncertain_grid, [(study, TSHIRT, "efficient-weaving")], iterations=5)

    assert mc.provenance.scenario_refactorized == 0


def test_an_unknown_scenario_is_refused_naming_the_studys_scenarios(
    uncertain_fabric: hw.Background,
) -> None:
    study = footprint(TOY_DIR, "tshirt-grid-scenario", uncertain_fabric)

    with pytest.raises(ValueError, match="clean-grid"):
        hw.monte_carlo(uncertain_fabric, [(study, TSHIRT, "clean-gird")], iterations=5)


def test_a_database_with_a_scenario_is_refused(uncertain_fabric: hw.Background) -> None:
    grid = pinned(open_database(TOY_DIR / "tshirt-grid-scenario/tshirt"), uncertain_fabric)

    with pytest.raises(ValueError, match=r"a scenario is a study's"):
        hw.monte_carlo(uncertain_fabric, [(grid, TSHIRT, "clean-grid")], iterations=5)  # pyright: ignore[reportArgumentType]


def test_a_scenario_that_sets_a_parameter_of_a_used_database_is_refused(
    toy: Path, uncertain_fabric: hw.Background
) -> None:
    """`kwh_per_kg` is a parameter of `dyes`, which the dyed T-shirt uses."""
    write(
        toy / "tshirt-dyed/tshirt-footprint/scenarios/frugal-dye.yaml",
        "changes:\n  - {parameter: kwh_per_kg, value: 1.5}\n",
    )
    study = footprint(toy, "tshirt-dyed", uncertain_fabric)

    with pytest.raises(InvalidDataError) as caught:
        hw.monte_carlo(uncertain_fabric, [(study, TSHIRT, "frugal-dye")], iterations=5)

    (problem,) = caught.value.problems
    assert problem.code == "monte-carlo-scenario-parameter"
    assert problem.item == "scenario frugal-dye"
