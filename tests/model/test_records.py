import math
from datetime import datetime

import pytest
from pydantic import ValidationError

from headwater.model import (
    Allocation,
    Boundary,
    CutOffCriterion,
    Database,
    Factor,
    Flow,
    FlowKind,
    FlowProperty,
    FlowPropertyName,
    Indicator,
    InvalidDataError,
    Issue,
    IssueStatus,
    Method,
    Pin,
)
from headwater.release import method_hash

# --- Flow ------------------------------------------------------------------------------------


def test_flow() -> None:
    flow = Flow(
        id="toy:co2",
        name="Carbon dioxide, fossil",
        kind=FlowKind.elementary,
        unit="kg",
        compartment="air",
    )

    assert flow.kind is FlowKind.elementary
    assert flow.cas is None


def test_flow_kind_must_be_known() -> None:
    with pytest.raises(ValidationError, match="kind"):
        Flow.model_validate({"id": "x", "name": "x", "kind": "gas", "unit": "kg"})


def test_flow_property_names_are_the_ones_of_the_design() -> None:
    assert [p.value for p in FlowPropertyName] == [
        "heating_value_net",
        "heating_value_gross",
        "density",
        "mass_per_item",
        "dry_mass",
        "water_content",
        "carbon_content_fossil",
        "carbon_content_biogenic",
        "price",
    ]


def test_flow_property() -> None:
    prop = FlowProperty(
        flow_id="toy:gas", property=FlowPropertyName.heating_value_net, value=36, unit="MJ"
    )

    assert prop.property is FlowPropertyName.heating_value_net
    assert prop.value == pytest.approx(36, rel=1e-9)


@pytest.mark.parametrize("value", [0, -1, math.nan, math.inf])
def test_flow_property_value_must_be_positive_and_finite(value: float) -> None:
    with pytest.raises(ValidationError, match="value"):
        FlowProperty(flow_id="toy:gas", property=FlowPropertyName.density, value=value, unit="kg")


# --- Method ----------------------------------------------------------------------------------


def method(**changes: object) -> Method:
    fields: dict[str, object] = {
        "id": "toy-climate",
        "version": "1",
        "flow_list": "toy",
        "indicators": [Indicator(id="climate-change", name="Climate change", unit="kg CO2-eq")],
        "factors": [
            Factor(indicator="climate-change", flow_id="toy:co2", factor=1),
            Factor(indicator="climate-change", flow_id="toy:ch4", factor=29.8),
        ],
    }
    fields.update(changes)
    return Method.model_validate(fields)


def test_method_knows_which_flows_it_characterises() -> None:
    assert method().characterised_flow_ids == frozenset({"toy:co2", "toy:ch4"})


def test_the_flows_a_method_characterises_are_worked_out_once() -> None:
    m = method()

    assert m.characterised_flow_ids is m.characterised_flow_ids


def test_what_a_method_works_out_is_not_part_of_the_record() -> None:
    fresh, used = method(), method()
    before = method_hash(used)
    used.characterised_flow_ids  # noqa: B018
    used.factors_by_indicator  # noqa: B018

    assert used == fresh
    assert hash(used) == hash(fresh)
    assert used.model_dump() == fresh.model_dump()
    assert method_hash(used) == before
    assert Method.model_validate(used.model_dump()) == fresh


def test_a_copy_with_other_factors_works_out_its_own_tables() -> None:
    used = method()
    used.characterised_flow_ids  # noqa: B018
    used.factors_by_indicator  # noqa: B018
    other = [Factor(indicator="climate-change", flow_id="toy:n2o", factor=273)]

    copy = used.model_copy(update={"factors": other})

    assert copy.characterised_flow_ids == frozenset({"toy:n2o"})
    assert copy.factors_by_indicator == {"climate-change": {"toy:n2o": 273.0}}
    assert used.characterised_flow_ids == frozenset({"toy:co2", "toy:ch4"})  # the original's own


def test_a_copy_that_changes_nothing_keeps_the_tables() -> None:
    used = method()
    tables = used.factors_by_indicator

    assert used.model_copy().factors_by_indicator is tables
    assert used.model_copy(update={"version": "2"}).factors_by_indicator == tables


def test_a_method_gives_the_factor_of_each_flow_for_each_indicator() -> None:
    m = method(
        indicators=[
            Indicator(id="climate-change", name="Climate change", unit="kg CO2-eq"),
            Indicator(id="methane", name="Methane", unit="kg CH4"),
        ],
        factors=[
            Factor(indicator="climate-change", flow_id="toy:co2", factor=1),
            Factor(indicator="climate-change", flow_id="toy:ch4", factor=29.8),
            Factor(indicator="methane", flow_id="toy:ch4", factor=1),
        ],
    )

    assert m.factors_by_indicator == {
        "climate-change": {"toy:co2": 1.0, "toy:ch4": 29.8},
        "methane": {"toy:ch4": 1.0},
    }


def test_an_indicator_with_no_factor_has_an_empty_table() -> None:
    m = method(
        indicators=[
            Indicator(id="climate-change", name="Climate change", unit="kg CO2-eq"),
            Indicator(id="ozone", name="Ozone depletion", unit="kg CFC-11-eq"),
        ]
    )

    assert m.factors_by_indicator["ozone"] == {}


def test_factor_location_is_optional() -> None:
    assert method().factors[0].location is None


def test_factor_for_unknown_indicator() -> None:
    with pytest.raises(
        ValidationError, match="'climate-chnage'.*toy:co2|toy:co2.*'climate-chnage'"
    ):
        method(factors=[Factor(indicator="climate-chnage", flow_id="toy:co2", factor=1)])


def test_duplicate_factor() -> None:
    twice = [Factor(indicator="climate-change", flow_id="toy:co2", factor=f) for f in (1, 2)]

    with pytest.raises(ValidationError, match="toy:co2"):
        method(factors=twice)


def test_the_same_flow_may_have_factors_for_different_locations() -> None:
    factors = [
        Factor(indicator="climate-change", flow_id="toy:co2", location=loc, factor=1)
        for loc in (None, "IN")
    ]

    assert len(method(factors=factors).factors) == 2


def test_indicator_ids_must_be_unique() -> None:
    twice = [
        Indicator(id="climate-change", name="a", unit="u"),
        Indicator(id="climate-change", name="b", unit="u"),
    ]

    with pytest.raises(ValidationError, match="climate-change"):
        method(indicators=twice)


def test_factor_must_be_finite() -> None:
    with pytest.raises(ValidationError, match="factor"):
        Factor(indicator="climate-change", flow_id="toy:co2", factor=math.nan)


# --- Database --------------------------------------------------------------------------------


def database_fields(**changes: object) -> dict[str, object]:
    fields: dict[str, object] = {
        "name": "tshirt",
        "background": "toy-background@fixture",
        "methods": ["toy-climate"],
    }
    fields.update(changes)
    return fields


def test_database_from_the_design_example() -> None:
    database = Database.model_validate(database_fields(uses=["textile-chemicals"]))

    assert database.background == Pin(name="toy-background", version="fixture")
    assert str(database.background) == "toy-background@fixture"
    assert database.uses == ("textile-chemicals",)
    assert database.methods == ("toy-climate",)
    assert database.cut_off is None
    assert database.description is None


def test_a_database_needs_only_its_name_and_background() -> None:
    database = Database.model_validate({"name": "dyes", "background": "toy-background@fixture"})

    assert (database.methods, database.uses, database.cut_off) == ((), (), None)


@pytest.mark.parametrize(
    ("uses", "message"),
    [
        (["textile-chemicals@v3"], "a pinned version"),
        (["../dyes"], "should match pattern"),
        (["dyes", "dyes"], "names dyes twice"),
    ],
)
def test_uses_names_database_folders_once(uses: list[str], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        Database.model_validate(database_fields(uses=uses))


def test_methods_are_named_once() -> None:
    with pytest.raises(ValidationError, match="names toy-climate twice"):
        Database.model_validate(database_fields(methods=["toy-climate", "toy-climate"]))


def test_boundaries() -> None:
    assert [b.value for b in Boundary] == ["cradle-to-gate", "cradle-to-grave", "gate-to-gate"]


def test_a_database_may_say_its_allocation_and_end_of_life_rules() -> None:
    """Text for people, as in study.yaml before D46: no calculation reads them."""
    database = Database.model_validate(
        database_fields(allocation={"rule": "physical", "basis": "mass"}, end_of_life="Cut-off")
    )

    assert database.allocation == Allocation(rule="physical", basis="mass")
    assert database.end_of_life == "Cut-off"


@pytest.mark.parametrize("pin", ["toy-background", "@x", "x@", "a@b@c", "a b@c", ""])
def test_pin_must_be_name_at_version(pin: str) -> None:
    with pytest.raises(ValidationError, match="name@version"):
        Database.model_validate(database_fields(background=pin))


@pytest.mark.parametrize("share", [-0.1, 1.1, math.nan])
def test_cut_off_share_is_between_zero_and_one(share: float) -> None:
    with pytest.raises(ValidationError, match="max_cumulative"):
        Database.model_validate(
            database_fields(cut_off={"criteria": ["mass"], "max_cumulative": share})
        )


@pytest.mark.parametrize("field", ["goal", "functional_unit", "reference_flow", "boundary"])
def test_a_database_has_no_question_fields(field: str) -> None:
    """A database holds processes. What a study asks of them is in study.yaml (D46)."""
    with pytest.raises(ValidationError, match=field):
        Database.model_validate(database_fields(**{field: "x"}))


# --- Issue -----------------------------------------------------------------------------------


def issue(**changes: object) -> Issue:
    fields: dict[str, object] = {
        "process_id": "tshirt:tshirt",
        "line_id": "e04",
        "flow_id": "toy:glue",
        "status": "open",
    }
    fields.update(changes)
    return Issue.model_validate(fields)


def test_open_issue() -> None:
    assert issue().status is IssueStatus.open


def test_cut_off_issue_records_reason_criterion_shares_who_and_when() -> None:
    resolved = issue(
        status="cut_off",
        reason="glue is 0.02% of the mass",
        criterion="mass",
        share=0.0002,
        cumulative_share=0.0002,
        resolved_by="leo",
        resolved_at=datetime(2026, 10, 11, 9, 0),
    )

    assert resolved.criterion is CutOffCriterion.mass
    assert resolved.share == pytest.approx(0.0002, rel=1e-9)


@pytest.mark.parametrize(
    "missing", ["reason", "criterion", "share", "cumulative_share", "resolved_by", "resolved_at"]
)
def test_cut_off_issue_needs_every_field(missing: str) -> None:
    fields: dict[str, object] = {
        "status": "cut_off",
        "reason": "r",
        "criterion": "mass",
        "share": 0.1,
        "cumulative_share": 0.1,
        "resolved_by": "leo",
        "resolved_at": datetime(2026, 10, 11),
    }
    del fields[missing]

    with pytest.raises(ValidationError, match=missing):
        issue(**fields)


def test_data_gap_needs_a_reason() -> None:
    with pytest.raises(ValidationError, match="reason"):
        issue(status="data_gap", resolved_by="leo", resolved_at=datetime(2026, 10, 11))


def test_linked_issue_needs_a_supplier() -> None:
    with pytest.raises(ValidationError, match="supplier_id"):
        issue(status="linked", resolved_by="leo", resolved_at=datetime(2026, 10, 11))


def test_open_issue_cannot_carry_a_resolution() -> None:
    with pytest.raises(ValidationError, match="open"):
        issue(reason="because")


def test_invalid_data_error_lists_every_problem() -> None:
    from headwater.model import Problem

    error = InvalidDataError(
        [Problem(code="a", item="x", message="m1"), Problem(code="b", item="y", message="m2")]
    )

    assert "x: m1" in str(error)
    assert "y: m2" in str(error)
    assert "2 problems" in str(error)


def test_a_copy_with_fewer_indicators_does_not_fail_on_a_factor_of_a_dropped_one() -> None:
    copy = method().model_copy(update={"indicators": ()})

    assert copy.factors_by_indicator == {"climate-change": {"toy:co2": 1.0, "toy:ch4": 29.8}}
