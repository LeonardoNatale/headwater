"""Exit test: a toy database exists for every Y1 case, and its numbers can be reached.

The reference numbers of Y1 were worked out by hand (docs/calculation.md, "The toy background").
`reach` below is a few lines of plain arithmetic on the records, so these tests show that each
fixture holds exactly what a calculation needs to give the hand-checked number. It is not the
engine: the engine reaches the same numbers in tests/query/test_y1.py.
"""

from collections.abc import Callable, Mapping
from pathlib import Path

import pytest

import headwater as hw
from headwater.model import (
    CutOffCriterion,
    Exchange,
    IssueStatus,
    LinkStatus,
    Method,
    Process,
    ProcessKind,
    Role,
    Scenario,
)
from headwater.study import (
    DatabaseFolder,
    read_database,
    read_method,
    read_process_folder,
    validate,
)
from tests.conftest import open_database, open_study_folder, toy_database

REL = 1e-9  # AGENTS.md: relative 1e-9 for toy fixtures
ELECTRICITY = "toy:electricity-production"
FABRIC = "toy:fabric-production"


def reach(
    database: DatabaseFolder,
    background: Mapping[str, Process],
    method: Method,
    *,
    scenario: Scenario | None = None,
    use_overrides: bool = True,
) -> float:
    """The T-shirt's climate score from the records, by walking its purchases.

    A change to a background process in a scenario changes that process for everyone. An
    override is a copy that only the database's own purchases use. The copy still buys from the
    background as it is.
    """
    factors = {f.flow_id: f.factor for f in method.factors}
    parameters = database.parameters
    values: dict[str, str | float] = scenario.parameter_values("tshirt") if scenario else {}
    parameters = parameters.with_changes(values) if values else parameters
    world = dict(background)
    for change in scenario.line_changes if scenario else ():
        if change.process_id not in world:
            continue
        process = world[change.process_id]
        world[change.process_id] = Process.model_validate(
            {
                **process.model_dump(exclude={"exchanges"}),
                "exchanges": [
                    change.apply_to(e) if e.line_id == change.line_id else e
                    for e in process.exchanges
                ],
            }
        )
    copies = {o.process_id: o.apply_to(world[o.process_id]) for o in database.overrides}

    def amount(e: Exchange) -> float:
        return parameters.evaluate(e.formula, e.process_id) if e.formula else e.amount

    def x(process: Process, buys: Callable[[Exchange], Process]) -> float:
        total = 0.0
        for e in process.exchanges:
            if e.role is Role.emission:
                total += amount(e) * factors[e.flow_id]
            elif e.role in (Role.input, Role.waste_to_treatment) and e.link_status is None:
                total += amount(e) * x(buys(e), lambda e2: world[e2.supplier_id or ""])
        return total / amount(process.reference_exchange)

    def database_buys(e: Exchange) -> Process:
        supplier = e.supplier_id or ""
        return copies[supplier] if use_overrides and supplier in copies else world[supplier]

    [target] = database.processes  # the T-shirt, the one process of each toy database
    return x(target, database_buys)


@pytest.fixture
def background(toy_dir: Path) -> Mapping[str, Process]:
    return {p.id: p for p in read_process_folder(toy_dir / "background", namespace="toy").processes}


@pytest.fixture
def method(toy_dir: Path) -> Method:
    return read_method(toy_dir / "methods/toy-climate")


@pytest.fixture
def open_toy(toy_dir: Path) -> Callable[[str], DatabaseFolder]:
    def _open(name: str) -> DatabaseFolder:
        return read_database(toy_database(toy_dir, name))

    return _open


def test_the_base_database_still_gives_the_hand_checked_number(
    open_toy: Callable[[str], DatabaseFolder], background: Mapping[str, Process], method: Method
) -> None:
    assert reach(open_toy("tshirt"), background, method) == pytest.approx(1.19205, rel=REL)
    assert reach(open_toy("tshirt-waste"), background, method) == pytest.approx(1.25205, rel=REL)


def test_every_y1_database_validates_against_the_toy_background(
    toy_dir: Path, open_toy: Callable[[str], DatabaseFolder], method: Method
) -> None:
    folder = read_process_folder(toy_dir / "background", namespace="toy")
    for name in (
        "tshirt-local-electricity",
        "tshirt-grid-scenario",
        "tshirt-local-fabric",
        "tshirt-parameter",
        "tshirt-cut-off",
    ):
        report = validate(open_toy(name), background=folder, method=method)
        assert report.errors == (), name


# --- local override of the electricity -------------------------------------------------------


def test_local_override_of_the_electricity_co2(
    open_toy: Callable[[str], DatabaseFolder], background: Mapping[str, Process], method: Method
) -> None:
    database = open_toy("tshirt-local-electricity")

    (override,) = database.overrides
    assert override.process_id == ELECTRICITY
    (line,) = override.lines
    assert (line.line_id, line.role, line.flow_id, line.amount, line.unit, line.supplier_id) == (
        "e02",
        Role.emission,
        "toy:co2",
        0.3,
        "kg",
        None,
    )
    copy = override.apply_to(background[ELECTRICITY])
    assert sum(
        e.amount * {"toy:co2": 1.0, "toy:ch4": 29.8}[e.flow_id]
        for e in copy.exchanges
        if e.role is Role.emission
    ) == pytest.approx(0.3298, rel=REL)  # the electricity copy
    assert reach(database, background, method) == pytest.approx(1.09205, rel=REL)
    assert reach(database, background, method, use_overrides=False) == pytest.approx(
        1.19205, rel=REL
    )


def test_the_local_override_leaves_the_fabrics_own_electricity_alone(
    open_toy: Callable[[str], DatabaseFolder], background: Mapping[str, Process], method: Method
) -> None:
    # Only the T-shirt's purchase of 0.5 kWh uses the copy: the fabric still buys 3.7086 per kg.
    database = open_toy("tshirt-local-electricity")
    base = reach(open_toy("tshirt"), background, method)

    assert base - reach(database, background, method) == pytest.approx(
        0.5 * (0.5298 - 0.3298), rel=REL
    )


# --- the same change as a global scenario ----------------------------------------------------


def test_the_same_change_as_a_global_scenario(
    toy_dir: Path,
    open_toy: Callable[[str], DatabaseFolder],
    background: Mapping[str, Process],
    method: Method,
) -> None:
    database = open_toy("tshirt-grid-scenario")

    (scenario,) = open_study_folder(
        toy_dir / "tshirt-grid-scenario" / "tshirt-footprint"
    ).folder.scenarios
    assert scenario.name == "clean-grid"
    (change,) = scenario.line_changes
    assert (change.process_id, change.line_id, change.amount) == (ELECTRICITY, "e02", 0.3)
    assert database.overrides == ()
    assert reach(database, background, method) == pytest.approx(1.19205, rel=REL)  # no scenario
    assert reach(database, background, method, scenario=scenario) == pytest.approx(0.94205, rel=REL)


# --- local override of the fabric ------------------------------------------------------------


def test_local_override_of_the_fabric_at_2_5_kwh_per_kg(
    open_toy: Callable[[str], DatabaseFolder], background: Mapping[str, Process], method: Method
) -> None:
    database = open_toy("tshirt-local-fabric")

    (override,) = database.overrides
    assert override.process_id == FABRIC
    (line,) = override.lines
    assert (line.line_id, line.flow_id, line.amount, line.unit, line.supplier_id) == (
        "e02",
        "toy:electricity",
        2.5,
        "kWh",
        ELECTRICITY,
    )
    assert reach(database, background, method) == pytest.approx(1.125825, rel=REL)


# --- a named parameter -----------------------------------------------------------------------


def test_the_fabric_amount_is_the_parameter_fabric_kg(
    open_toy: Callable[[str], DatabaseFolder], background: Mapping[str, Process], method: Method
) -> None:
    database = open_toy("tshirt-parameter")

    assert dict(database.parameters.values) == {"fabric_kg": 0.25}
    (parameter,) = database.parameters.parameters
    assert (parameter.unit, parameter.scope) == ("kg", None)
    fabric = next(e for p in database.processes for e in p.exchanges if e.line_id == "e02")
    assert (fabric.amount, fabric.formula) == (0.25, "fabric_kg")
    assert reach(database, background, method) == pytest.approx(1.19205, rel=REL)


def test_a_scenario_sets_the_parameter_to_0_30(
    toy_dir: Path,
    open_toy: Callable[[str], DatabaseFolder],
    background: Mapping[str, Process],
    method: Method,
) -> None:
    database = open_toy("tshirt-parameter")

    (scenario,) = open_study_folder(
        toy_dir / "tshirt-parameter" / "tshirt-footprint"
    ).folder.scenarios
    assert scenario.name == "more-fabric"
    assert scenario.parameter_values("tshirt") == {"fabric_kg": 0.30}
    assert reach(database, background, method, scenario=scenario) == pytest.approx(1.37748, rel=REL)


def test_the_parameters_sensitivity_and_elasticity(
    open_toy: Callable[[str], DatabaseFolder], background: Mapping[str, Process], method: Method
) -> None:
    # A central finite difference on the records. The engine will use the chain rule.
    database = open_toy("tshirt-parameter")

    def at(kg: float) -> float:
        scenario = Scenario.model_validate(
            {
                "name": "probe",
                "changes": [{"parameter": "fabric_kg", "database": "tshirt", "value": kg}],
            }
        )
        return reach(database, background, method, scenario=scenario)

    step = 1e-4
    sensitivity = (at(0.25 + step) - at(0.25 - step)) / (2 * step)

    assert sensitivity == pytest.approx(3.7086, rel=1e-6)
    assert sensitivity * 0.25 / at(0.25) == pytest.approx(0.92715 / 1.19205, rel=1e-6)
    assert sensitivity * 0.25 / at(0.25) == pytest.approx(0.7778, abs=5e-5)


# --- the cut-off -----------------------------------------------------------------------------


def test_the_packaging_tape_is_cut_off_and_recorded(
    open_toy: Callable[[str], DatabaseFolder], background: Mapping[str, Process], method: Method
) -> None:
    database = open_toy("tshirt-cut-off")

    (issue,) = database.cut_offs
    assert (issue.process_id, issue.line_id, issue.flow_id) == (
        "tshirt:tshirt",
        "e04",
        "tshirt:packaging-tape",
    )
    assert issue.status is IssueStatus.cut_off
    assert issue.criterion is CutOffCriterion.mass
    assert issue.share == pytest.approx(0.01 / 0.26, rel=1e-12)
    assert issue.cumulative_share == pytest.approx(0.01 / 0.26, rel=1e-12)
    assert (issue.resolved_by, issue.resolved_at.date().isoformat()) == ("leo", "2026-10-11")  # pyright: ignore[reportOptionalMemberAccess]
    assert issue.reason
    tape = next(e for p in database.processes for e in p.exchanges if e.line_id == "e04")
    assert (tape.amount, tape.unit, tape.supplier_id, tape.link_status) == (
        0.01,
        "kg",
        None,
        LinkStatus.cut_off,
    )
    assert reach(database, background, method) == pytest.approx(1.19205, rel=REL)  # the score stays


def test_the_process_details_of_the_new_fixtures_are_the_tshirts(
    open_toy: Callable[[str], DatabaseFolder],
) -> None:
    for name in ("tshirt-local-electricity", "tshirt-parameter", "tshirt-cut-off"):
        (process,) = open_toy(name).processes
        assert (process.id, process.name, process.kind) == (
            "tshirt:tshirt",
            "T-shirt",
            ProcessKind.ordinary_transforming_activity,
        )


# --- the engine as it is today ---------------------------------------------------------------


@pytest.fixture
def toy_background(toy_dir: Path) -> hw.Background:
    return hw.load(toy_dir / "background", method=toy_dir / "methods/toy-climate", namespace="toy")


@pytest.mark.parametrize("name", ["tshirt-parameter", "tshirt-grid-scenario", "tshirt-cut-off"])
def test_the_engine_scores_the_fixtures_it_can_already_use(
    toy_dir: Path, toy_background: hw.Background, name: str
) -> None:
    # Parameters are worked out when the database is read, a cut-off line buys nothing, and a
    # scenario is used only when `calc` names it. So each of these scores the base T-shirt.
    result = open_database(toy_database(toy_dir, name)).calc(toy_background, "tshirt:tshirt")

    assert result.score == pytest.approx(1.19205, rel=REL)


@pytest.mark.parametrize(
    ("name", "score"), [("tshirt-local-electricity", 1.09205), ("tshirt-local-fabric", 1.125825)]
)
def test_the_engine_uses_the_overrides(
    toy_dir: Path, toy_background: hw.Background, name: str, score: float
) -> None:
    assert open_database(toy_database(toy_dir, name)).calc(
        toy_background, "tshirt:tshirt"
    ).score == pytest.approx(score, rel=REL)


def test_a_database_folder_can_be_hashed(open_toy: Callable[[str], DatabaseFolder]) -> None:
    for name in ("tshirt", "tshirt-local-electricity", "tshirt-parameter", "tshirt-cut-off"):
        assert isinstance(hash(open_toy(name)), int), name
