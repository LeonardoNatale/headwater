"""`headwater calc --iterations`: the range of each score, from a Monte Carlo run."""

import math
from collections.abc import Callable
from pathlib import Path

import pytest

import headwater as hw
from headwater.model import Exchange, Process
from headwater.release import Release, build
from headwater.study import read_method, read_process_folder
from tests.conftest import open_database, open_study_folder

from .conftest import ANSI, INFO, TOY

Invoke = Callable[..., tuple[int, str]]


@pytest.fixture
def uncertain_release(home: Path) -> Release:
    """The toy background in the cache, with fabric production's 3 kWh lognormal, GSD 1.1."""
    folder = read_process_folder(TOY / "background", namespace="toy")

    def uncertain(process: Process) -> Process:
        def line(e: Exchange) -> Exchange:
            if (e.process_id, e.line_id) != ("toy:fabric-production", "e02"):
                return e
            spread = {"distribution": "lognormal", "loc": math.log(3), "scale": math.log(1.1)}
            return Exchange.model_validate({**e.model_dump(), "uncertainty": spread})

        return Process.model_validate(
            {
                **process.model_dump(exclude={"exchanges"}),
                "exchanges": list(map(line, process.exchanges)),
            }
        )

    return build(
        INFO,
        processes=[uncertain(p) for p in folder.processes],
        flows=folder.flows,
        flow_properties=folder.flow_properties,
        methods=(read_method(TOY / "methods" / "toy-climate"),),
    )


def pinned(folder: Path, pin: str) -> None:
    path = folder / "database.yaml"
    path.write_text(path.read_text().replace("toy-background@fixture", pin))


def test_calc_with_iterations_prints_the_range_of_each_score(
    toy: Path, uncertain_release: Release, pin_to: Callable[[Release], str], invoke: Invoke
) -> None:
    pin_to(uncertain_release)
    code, output = invoke("calc", toy / "tshirt" / "tshirt", "--iterations", "50", "--seed", "3")
    background = hw.load(uncertain_release.path)
    mc = hw.monte_carlo(
        background,
        [(open_database(toy / "tshirt" / "tshirt"), "tshirt:tshirt")],
        iterations=50,
        seed=3,
    )
    found = mc.range("tshirt:tshirt")

    assert code == 0, output
    assert "Climate change: 1.19205 kg CO2-eq" in output
    assert "Monte Carlo: 50 iterations, seed 3" in output
    assert (
        f"  Climate change: mean {found.mean:.6g}, median {found.median:.6g}, sd {found.sd:.3g}, "
        f"2.5% {found.percentiles[2.5]:.6g}, 97.5% {found.percentiles[97.5]:.6g} kg CO2-eq"
    ) in output
    assert "  lines drawn: 1 lognormal" in output
    assert "  draws of the opposite sign: 0" in output
    assert "  largest condition: " in output
    assert "  generator: numpy.random.PCG64" in output


def test_calc_with_iterations_in_a_scenario_prints_the_scenarios_range(
    toy: Path, uncertain_release: Release, pin_to: Callable[[Release], str], invoke: Invoke
) -> None:
    folder = toy / "tshirt-grid-scenario" / "tshirt-footprint"
    pinned(toy / "tshirt-grid-scenario" / "tshirt", pin_to(uncertain_release))
    code, output = invoke("calc", folder, "--iterations", "50", "--scenario", "clean-grid")
    background = hw.load(uncertain_release.path)
    mc = hw.monte_carlo(
        background,
        [(open_study_folder(folder), "tshirt:tshirt", "clean-grid")],
        iterations=50,
        seed=0,
    )
    found = mc.range(mc.items[0])

    assert code == 0, output
    assert "Climate change: 0.94205 kg CO2-eq" in output
    assert f"mean {found.mean:.6g}, median {found.median:.6g}" in output


def test_calc_with_iterations_names_the_database_lines_and_parameters_drawn(
    toy: Path, uncertain_release: Release, pin_to: Callable[[Release], str], invoke: Invoke
) -> None:
    folder = toy / "tshirt-parameter" / "tshirt"
    pinned(folder, pin_to(uncertain_release))
    (folder / "parameters.yaml").write_text(
        "database:\n  fabric_kg: {value: 0.25, distribution: lognormal, scale: 0.1}\n"
    )
    csv = folder / "processes" / "tshirt.csv"
    rows = csv.read_text().splitlines()
    csv.write_text(
        "\n".join(
            [rows[0] + ",distribution,scale,minimum,maximum"]
            + [r + (",normal,0.05,," if r.startswith("e03") else ",,,,") for r in rows[1:]]
        )
        + "\n"
    )
    code, output = invoke("calc", folder, "--iterations", "20")

    assert code == 0, output
    assert "  lines drawn: 1 lognormal" in output
    assert "  database lines drawn: 1 normal" in output
    assert "  parameters drawn: 1" in output


def test_iterations_on_a_folder_background_fail_naming_the_reason(
    toy: Path, toy_args: list[str], invoke: Invoke
) -> None:
    code, output = invoke("calc", toy / "tshirt" / "tshirt", *toy_args, "--iterations", "50")

    assert code == 1
    assert "needs a release" in output
    assert "Traceback" not in output


def test_a_seed_without_iterations_is_refused(
    toy: Path, toy_args: list[str], invoke: Invoke
) -> None:
    code, output = invoke("calc", toy / "tshirt" / "tshirt", *toy_args, "--seed", "4")

    assert code == 2
    assert "--iterations" in ANSI.sub("", output)
