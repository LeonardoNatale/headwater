import pytest
from pydantic import ValidationError

from headwater.model import Exchange, Role
from headwater.model.uncertainty import (
    Distribution,
    ExchangeProperty,
    LinkStatus,
    Pedigree,
    Uncertainty,
)

PEDIGREE = {
    "reliability": 2,
    "completeness": 3,
    "temporal_correlation": 1,
    "geographical_correlation": 2,
    "further_technological_correlation": 5,
}


def exchange(**changes: object) -> Exchange:
    fields: dict[str, object] = {
        "process_id": "toy:fabric-production",
        "line_id": "e02",
        "role": Role.input,
        "flow_id": "toy:electricity",
        "amount": 3.0,
        "unit": "kWh",
        "supplier_id": "toy:electricity-production",
    }
    fields.update(changes)
    return Exchange.model_validate(fields)


def test_new_fields_are_optional_and_empty_by_default() -> None:
    e = exchange()

    assert e.uncertainty is None
    assert e.pedigree is None
    assert e.properties == ()
    assert e.link_status is None


def test_an_exchange_with_every_new_field_stays_hashable() -> None:
    e = exchange(
        uncertainty={"distribution": "lognormal", "loc": 1.0986, "scale": 0.1},
        pedigree=PEDIGREE,
        properties=[{"name": "price", "value": 0.1, "unit": "EUR"}],
        link_status="linked",
    )

    assert hash(e) == hash(Exchange.model_validate(e.model_dump()))
    assert e.uncertainty == Uncertainty(distribution=Distribution.lognormal, loc=1.0986, scale=0.1)
    assert e.pedigree == Pedigree.model_validate(PEDIGREE)
    assert e.link_status is LinkStatus.linked


@pytest.mark.parametrize(
    ("fields", "missing"),
    [
        ({"distribution": "lognormal", "loc": 0.0}, "scale"),
        ({"distribution": "normal", "scale": 0.2}, "loc"),
        ({"distribution": "uniform", "minimum": 1.0}, "maximum"),
        ({"distribution": "triangular", "minimum": 1.0, "maximum": 2.0}, "loc"),
    ],
)
def test_each_distribution_needs_its_parameters(fields: dict[str, object], missing: str) -> None:
    with pytest.raises(ValidationError, match=missing):
        Uncertainty.model_validate(fields)


def test_lognormal_loc_may_be_negative_but_scale_must_be_positive() -> None:
    assert Uncertainty(distribution=Distribution.lognormal, loc=-4.6, scale=0.3).loc == -4.6

    with pytest.raises(ValidationError, match="scale"):
        Uncertainty(distribution=Distribution.lognormal, loc=0.0, scale=0.0)


def test_minimum_above_maximum_is_rejected() -> None:
    with pytest.raises(ValidationError, match="minimum"):
        Uncertainty(distribution=Distribution.uniform, minimum=2.0, maximum=1.0)


def test_triangular_mode_must_lie_between_minimum_and_maximum() -> None:
    with pytest.raises(ValidationError, match="loc"):
        Uncertainty(distribution=Distribution.triangular, loc=3.0, minimum=1.0, maximum=2.0)


def test_uncertainty_values_must_be_finite() -> None:
    with pytest.raises(ValidationError):
        Uncertainty(distribution=Distribution.normal, loc=float("nan"), scale=1.0)


@pytest.mark.parametrize("score", [0, 6])
def test_pedigree_scores_run_from_1_to_5(score: int) -> None:
    with pytest.raises(ValidationError, match="reliability"):
        Pedigree.model_validate({**PEDIGREE, "reliability": score})


def test_properties_are_sorted_by_name() -> None:
    e = exchange(
        properties=[
            {"name": "price", "value": -0.2, "unit": "EUR"},
            {"name": "heating value, net", "value": 36.0, "unit": "MJ"},
        ]
    )

    assert [p.name for p in e.properties] == ["heating value, net", "price"]
    assert e.properties[1] == ExchangeProperty(name="price", value=-0.2, unit="EUR")


def test_a_property_given_twice_is_rejected_naming_the_line() -> None:
    with pytest.raises(ValidationError) as raised:
        exchange(
            properties=[
                {"name": "price", "value": 1.0, "unit": "EUR"},
                {"name": "price", "value": 2.0, "unit": "EUR"},
            ]
        )

    assert "toy:fabric-production line e02" in str(raised.value)
    assert "price" in str(raised.value)


def test_linked_needs_a_supplier() -> None:
    with pytest.raises(ValidationError, match="linked"):
        exchange(supplier_id=None, link_status="linked")


@pytest.mark.parametrize("status", ["cut_off", "data_gap"])
def test_cut_off_and_data_gap_take_no_supplier(status: str) -> None:
    assert exchange(supplier_id=None, link_status=status).link_status == LinkStatus(status)

    with pytest.raises(ValidationError, match=status):
        exchange(link_status=status)


def test_a_line_that_buys_nothing_has_no_link_status() -> None:
    with pytest.raises(ValidationError, match="emission"):
        exchange(
            role="emission", flow_id="toy:co2", unit="kg", supplier_id=None, link_status="cut_off"
        )


def test_a_supplier_without_link_status_stays_valid() -> None:
    assert exchange().supplier_id == "toy:electricity-production"
