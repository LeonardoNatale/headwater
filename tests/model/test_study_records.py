"""Records for what a study says beyond its processes: overrides and scenarios."""

import pytest

import headwater as hw
from headwater.model import (
    DEFAULT_UNITS,
    Exchange,
    InvalidDataError,
    LineChange,
    Override,
    ParameterChange,
    Process,
    Product,
    Role,
    Scenario,
    Study,
    Unit,
    Units,
    with_article,
)

FABRIC = "toy:fabric-production"


def line(
    line_id: str = "e02",
    *,
    role: Role = Role.input,
    flow: str = "toy:electricity",
    amount: float = 3,
    unit: str = "kWh",
    supplier: str | None = "toy:electricity-production",
    process_id: str = FABRIC,
    **extra: object,
) -> Exchange:
    return Exchange.model_validate(
        {
            "process_id": process_id,
            "line_id": line_id,
            "role": role,
            "flow_id": flow,
            "amount": amount,
            "unit": unit,
            "supplier_id": supplier,
            **extra,
        }
    )


def fabric() -> Process:
    return Process(
        id=FABRIC,
        name="cotton fabric production",
        location="GLO",
        kind="ordinary_transforming_activity",  # pyright: ignore[reportArgumentType]
        exchanges=(
            line(
                "e01",
                role=Role.reference,
                flow="toy:cotton-fabric",
                amount=1,
                unit="kg",
                supplier=None,
            ),
            line("e02"),
            line("e03", role=Role.emission, flow="toy:co2", amount=2.0, unit="kg", supplier=None),
        ),
    )


# --- a line with a formula -------------------------------------------------------------------


def test_a_line_keeps_the_formula_its_amount_came_from() -> None:
    exchange = line(amount=0.25, formula="fabric_kg")

    assert exchange.formula == "fabric_kg"
    assert exchange.amount == 0.25


def test_a_line_has_no_formula_by_default() -> None:
    assert line().formula is None


def test_a_formula_that_cannot_be_read_is_refused_on_the_line() -> None:
    with pytest.raises(ValueError, match="formula"):
        line(formula="fabric_kg *")


def test_a_negative_amount_from_a_formula_says_so() -> None:
    with pytest.raises(ValueError, match=r"formula 'a - 1'"):
        line(amount=-0.5, formula="a - 1")


# --- overrides -------------------------------------------------------------------------------


def test_an_override_holds_the_changed_lines_of_one_process() -> None:
    override = Override(process_id=FABRIC, lines=(line(amount=2.5),))

    assert override.process_id == FABRIC
    assert [(e.line_id, e.amount) for e in override.lines] == [("e02", 2.5)]


def test_an_override_is_applied_by_replacing_the_lines_with_the_same_id() -> None:
    override = Override(process_id=FABRIC, lines=(line(amount=2.5),))

    changed = override.apply_to(fabric())

    assert [(e.line_id, e.amount) for e in changed.exchanges] == [
        ("e01", 1),
        ("e02", 2.5),
        ("e03", 2.0),
    ]
    assert [(e.line_id, e.amount) for e in fabric().exchanges][1] == (
        "e02",
        3,
    )  # the original stays


def test_an_override_needs_a_line() -> None:
    with pytest.raises(ValueError, match="at least one line"):
        Override(process_id=FABRIC, lines=())


def test_an_override_line_must_belong_to_its_process() -> None:
    with pytest.raises(ValueError, match="toy:other"):
        Override(process_id=FABRIC, lines=(line(process_id="toy:other"),))


def test_an_override_names_a_line_once() -> None:
    with pytest.raises(ValueError, match="e02"):
        Override(process_id=FABRIC, lines=(line(amount=1), line(amount=2)))


def test_an_override_cannot_change_the_reference_line() -> None:
    reference = line(
        "e01", role=Role.reference, flow="toy:cotton-fabric", amount=2, unit="kg", supplier=None
    )

    with pytest.raises(ValueError, match="reference"):
        Override(process_id=FABRIC, lines=(reference,))


def test_applying_an_override_to_a_process_without_the_line_is_an_error() -> None:
    override = Override(process_id=FABRIC, lines=(line("e22"),))

    with pytest.raises(InvalidDataError) as raised:
        override.apply_to(fabric())

    (problem,) = raised.value.problems
    assert problem.item == "toy:fabric-production line e22"
    assert "e02" in problem.message  # the lines it has are named


# --- scenarios -------------------------------------------------------------------------------


def test_a_line_change_has_an_amount_a_unit_or_a_supplier() -> None:
    assert LineChange(process_id=FABRIC, line_id="e02", amount=2.5).amount == 2.5
    assert LineChange(process_id=FABRIC, line_id="e02", unit="MJ").unit == "MJ"
    assert (
        LineChange(process_id=FABRIC, line_id="e02", supplier_id="toy:wind").supplier_id
        == "toy:wind"
    )
    with pytest.raises(ValueError, match="amount, a unit or a supplier"):
        LineChange(process_id=FABRIC, line_id="e02")


def test_a_line_change_with_a_formula_carries_the_amount_it_gives() -> None:
    change = LineChange(process_id=FABRIC, line_id="e02", amount=0.6, formula="fabric_kg * 2")

    assert (change.amount, change.formula) == (0.6, "fabric_kg * 2")
    with pytest.raises(ValueError, match="amount"):
        LineChange(process_id=FABRIC, line_id="e02", formula="fabric_kg * 2")


def test_a_line_change_is_applied_to_a_line() -> None:
    changed = LineChange(process_id=FABRIC, line_id="e02", amount=2.5).apply_to(line())

    assert (changed.amount, changed.unit, changed.supplier_id) == (
        2.5,
        "kWh",
        "toy:electricity-production",
    )
    assert changed.formula is None


def test_a_new_plain_amount_drops_the_old_formula() -> None:
    changed = LineChange(process_id=FABRIC, line_id="e02", amount=2.5).apply_to(line(formula="x"))

    assert changed.formula is None


def test_a_new_supplier_keeps_the_amount_and_its_formula() -> None:
    changed = LineChange(process_id=FABRIC, line_id="e02", supplier_id="toy:wind").apply_to(
        line(amount=0.25, formula="fabric_kg")
    )

    assert (changed.amount, changed.formula, changed.supplier_id) == (0.25, "fabric_kg", "toy:wind")


def test_a_line_change_that_breaks_the_line_is_an_error_from_the_line() -> None:
    with pytest.raises(ValueError, match="negative"):
        LineChange(process_id=FABRIC, line_id="e02", amount=-1).apply_to(line())


def test_a_parameter_change_has_a_value_or_a_formula() -> None:
    assert ParameterChange(parameter="fabric_kg", value=0.3).value == 0.3
    assert ParameterChange(parameter="fabric_kg", formula="0.25 * 1.2").formula == "0.25 * 1.2"
    with pytest.raises(ValueError, match="value or a formula"):
        ParameterChange(parameter="fabric_kg")
    with pytest.raises(ValueError, match="value or a formula"):
        ParameterChange(parameter="fabric_kg", value=1, formula="2")


def test_a_scenario_has_a_name_and_changes() -> None:
    scenario = Scenario(
        name="clean-grid",
        description="CO2 of electricity 0.5 to 0.3",
        changes=(
            LineChange(process_id="toy:electricity-production", line_id="e02", amount=0.3),
            ParameterChange(parameter="fabric_kg", database="tshirt", value=0.3),
        ),
    )

    assert [c.line_id for c in scenario.line_changes] == ["e02"]
    assert [c.parameter for c in scenario.parameter_changes] == ["fabric_kg"]
    assert scenario.parameter_values("tshirt") == {"fabric_kg": 0.3}
    assert scenario.parameter_values("dyes") == {}


def test_a_scenario_parameter_values_use_the_formula_text_when_there_is_one() -> None:
    scenario = Scenario(
        name="s",
        changes=(
            ParameterChange(parameter="a", database="d", formula="b * 2"),
            ParameterChange(parameter="c", database="d", value=1),
        ),
    )

    assert scenario.parameter_values("d") == {"a": "b * 2", "c": 1}


@pytest.mark.parametrize("name", ["", "has space", "-start", "a/b"])
def test_a_scenario_name_is_a_plain_name(name: str) -> None:
    with pytest.raises(ValueError, match="name"):
        Scenario(name=name, changes=(ParameterChange(parameter="a", value=1),))


def test_a_scenario_needs_a_change() -> None:
    with pytest.raises(ValueError, match="at least one change"):
        Scenario(name="empty", changes=())


# --- small things from M1 --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("phrase", "expected"),
    [
        ("elementary flow", "an elementary flow"),
        ("product or waste flow", "a product or waste flow"),
        ("input", "an input"),
        ("flow", "a flow"),
    ],
)
def test_with_article(phrase: str, expected: str) -> None:
    assert with_article(phrase) == expected


def test_tkm_is_the_freight_work_of_ecoinvent() -> None:
    # The unit table of an ecoinvent 3.10 release holds this row for "metric ton*km".
    release = Units(
        [Unit(name="metric ton*km", quantity="freight-transport-work", factor_to_reference=1.0)]
    )

    merged = DEFAULT_UNITS.merge(release)

    assert DEFAULT_UNITS.quantity_of("tkm") == "freight-transport-work"
    assert merged.convert(250.0, "tkm", "metric ton*km") == pytest.approx(250.0, rel=1e-12)
    assert merged.convert(3.0, "metric ton*km", "tkm") == pytest.approx(3.0, rel=1e-12)


def test_invalid_data_error_is_in_the_public_api() -> None:
    assert hw.InvalidDataError is InvalidDataError
    assert "InvalidDataError" in hw.__all__


# --- the study record ------------------------------------------------------------------------


def study_fields(**changes: object) -> dict[str, object]:
    return {
        "name": "tshirt-footprint",
        "goal": "Footprint of one T-shirt",
        "functional_unit": "1 T-shirt",
        "products": [{"process": "tshirt:tshirt", "amount": 1}],
        "boundary": "cradle-to-gate",
        "methods": ["toy-climate"],
        "cut_off": {"criteria": ["mass"], "max_cumulative": 0.03},
        **changes,
    }


def test_a_study_has_a_goal_products_and_methods() -> None:
    study = Study.model_validate(study_fields())

    assert [(p.process, p.amount) for p in study.products] == [("tshirt:tshirt", 1)]
    assert study.background is None
    assert study.methods == ("toy-climate",)


@pytest.mark.parametrize("amount", [0, -1])
def test_a_product_amount_is_above_zero(amount: float) -> None:
    with pytest.raises(ValueError, match="amount"):
        Product(process="tshirt:tshirt", amount=amount)


def test_a_study_names_each_product_once() -> None:
    twice = [{"process": "tshirt:tshirt", "amount": 1}] * 2

    with pytest.raises(ValueError, match="tshirt:tshirt twice"):
        Study.model_validate(study_fields(products=twice))
