import math

import pytest
from pydantic import ValidationError

from headwater.model import Exchange, Process, ProcessKind, Role


def exchange(**changes: object) -> Exchange:
    fields: dict[str, object] = {
        "process_id": "tshirt:tshirt",
        "line_id": "e02",
        "role": Role.input,
        "flow_id": "toy:cotton-fabric",
        "amount": 0.25,
        "unit": "kg",
        "supplier_id": "toy:fabric-production",
    }
    fields.update(changes)
    return Exchange.model_validate(fields)


def test_label_is_process_id_and_line_id() -> None:
    assert exchange().label == "tshirt:tshirt line e02"


def test_role_is_read_from_its_name() -> None:
    assert exchange(role="waste_to_treatment").role is Role.waste_to_treatment


def test_unknown_role_is_rejected() -> None:
    with pytest.raises(ValidationError, match="role"):
        exchange(role="inputt")


@pytest.mark.parametrize(
    "role",
    [r for r in Role if r not in (Role.emission, Role.resource)],
)
def test_negative_amount_is_rejected_on_every_role_except_emission_and_resource(role: Role) -> None:
    with pytest.raises(ValidationError) as raised:
        exchange(role=role, amount=-0.25)

    message = str(raised.value)
    assert "tshirt:tshirt line e02" in message
    assert "-0.25" in message
    assert role.value in message


@pytest.mark.parametrize("role", [Role.emission, Role.resource])
def test_negative_amount_is_allowed_on_emission_and_resource(role: Role) -> None:
    amount = exchange(role=role, amount=-0.25, supplier_id=None).amount

    assert amount == pytest.approx(-0.25, rel=1e-9)


def test_reference_amount_must_be_above_zero() -> None:
    with pytest.raises(ValidationError, match="tshirt:tshirt line e02"):
        exchange(role=Role.reference, amount=0, supplier_id=None)


def test_zero_is_fine_on_other_roles() -> None:
    assert exchange(amount=0).amount == pytest.approx(0, abs=1e-12)


@pytest.mark.parametrize("amount", [math.nan, math.inf, -math.inf])
def test_amount_must_be_finite(amount: float) -> None:
    with pytest.raises(ValidationError, match="amount"):
        exchange(amount=amount)


@pytest.mark.parametrize("field", ["process_id", "line_id", "flow_id", "unit"])
def test_ids_and_unit_must_not_be_empty(field: str) -> None:
    with pytest.raises(ValidationError, match=field):
        exchange(**{field: ""})


def test_unknown_field_is_rejected() -> None:
    with pytest.raises(ValidationError, match="suplier"):
        exchange(suplier="x")


def test_exchange_is_frozen() -> None:
    with pytest.raises(ValidationError):
        exchange().amount = 1.0  # type: ignore[misc]


def test_amount_is_float64() -> None:
    assert type(exchange(amount=1).amount) is float


# --- Process ---------------------------------------------------------------------------------


def process(*exchanges: Exchange, id: str = "tshirt:tshirt") -> Process:
    return Process(
        id=id,
        name="T-shirt",
        location="GLO",
        kind=ProcessKind.ordinary_transforming_activity,
        exchanges=exchanges,
    )


def reference(line_id: str = "e01") -> Exchange:
    return exchange(
        line_id=line_id,
        role=Role.reference,
        flow_id="tshirt:tshirt",
        amount=1,
        unit="unit",
        supplier_id=None,
    )


def test_process_reference_exchange() -> None:
    p = process(reference(), exchange())

    assert p.reference_exchange.line_id == "e01"
    assert p.kind is ProcessKind.ordinary_transforming_activity
    assert p.location == "GLO"
    assert p.time_start is None


def test_process_needs_exactly_one_reference() -> None:
    with pytest.raises(ValidationError, match="process tshirt:tshirt.*none"):
        process(exchange())

    with pytest.raises(ValidationError, match="e01.*e03|e03.*e01"):
        process(reference("e01"), exchange(), reference("e03"))


def test_process_lines_must_be_unique() -> None:
    with pytest.raises(ValidationError, match="tshirt:tshirt line e02"):
        process(reference(), exchange(), exchange())


def test_exchange_must_belong_to_its_process() -> None:
    with pytest.raises(ValidationError, match="toy:other"):
        process(reference(), exchange(process_id="toy:other"))
