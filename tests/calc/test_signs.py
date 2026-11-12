import math

import numpy as np
import pytest
from scipy import sparse

import headwater.calc.signs as signs_module
import headwater.model as model
import headwater.model.roles as roles_module
from headwater.calc import Matrix, factorize, matrix_entry, scores
from headwater.model.roles import Role

TECHNOSPHERE_CASES = [
    (Role.reference, 1.0, False, 1.0),
    (Role.reference, 1.0, True, -1.0),
    (Role.input, 0.25, False, -0.25),
    (Role.input, 0.25, True, -0.25),
    (Role.own_use, 0.02, False, -0.02),
    (Role.waste_to_treatment, 0.05, False, 0.05),
    (Role.avoided_product, 0.3, False, 0.3),
]


@pytest.mark.parametrize(("role", "amount", "treatment", "value"), TECHNOSPHERE_CASES)
def test_technosphere_roles(role: Role, amount: float, treatment: bool, value: float) -> None:
    entry = matrix_entry(role, amount, treatment=treatment)
    assert entry is not None
    assert entry.matrix == Matrix.technosphere
    assert entry.value == pytest.approx(value, rel=1e-9)


@pytest.mark.parametrize("role", [Role.emission, Role.resource])
@pytest.mark.parametrize("amount", [2.0, -0.5, 0.0])
def test_elementary_flows_go_to_the_biosphere_with_their_own_sign(
    role: Role, amount: float
) -> None:
    entry = matrix_entry(role, amount)
    assert entry is not None
    assert entry.matrix == Matrix.biosphere
    assert entry.value == pytest.approx(amount, rel=1e-9)


def test_recyclable_makes_no_matrix_entry() -> None:
    # Cut-off rule: material sent to recycling carries no burden and no credit.
    assert matrix_entry(Role.recyclable, 0.1) is None


@pytest.mark.parametrize("role", list(Role))
def test_every_role_has_a_rule(role: Role) -> None:
    entry = matrix_entry(role, 1.0)
    assert entry is None or math.isfinite(entry.value)


@pytest.mark.parametrize(
    "role",
    [r for r in Role if r not in (Role.emission, Role.resource)],
)
def test_negative_amount_is_an_error_outside_elementary_flows(role: Role) -> None:
    with pytest.raises(ValueError, match=role.value):
        matrix_entry(role, -1.0)


def test_the_roles_that_allow_a_negative_amount_come_from_model_roles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # One list decides what a file may hold. signs.py reads it, so a role added to the list
    # needs no second edit in calc.
    assert model.ROLES_ALLOWING_NEGATIVE is roles_module.ROLES_ALLOWING_NEGATIVE
    assert signs_module.ROLES_ALLOWING_NEGATIVE is roles_module.ROLES_ALLOWING_NEGATIVE
    with pytest.raises(ValueError, match="input"):
        matrix_entry(Role.input, -1.0)

    monkeypatch.setattr(
        signs_module, "ROLES_ALLOWING_NEGATIVE", roles_module.ROLES_ALLOWING_NEGATIVE | {Role.input}
    )

    entry = matrix_entry(Role.input, -1.0)
    assert entry is not None
    assert entry.value == pytest.approx(1.0, rel=1e-9)


@pytest.mark.parametrize("amount", [math.nan, math.inf, -math.inf])
def test_non_finite_amount_is_an_error(amount: float) -> None:
    with pytest.raises(ValueError, match="finite"):
        matrix_entry(Role.emission, amount)


def test_own_use_in_a_treatment_process_is_an_error() -> None:
    with pytest.raises(ValueError, match="own_use"):
        matrix_entry(Role.own_use, 0.01, treatment=True)


# (process, product or flow, role, amount, treatment): the toy background as exchanges,
# with positive amounts as files and the API carry them.
TECHNOSPHERE_EXCHANGES = [
    (0, 0, Role.reference, 1.0, False),
    (0, 1, Role.input, 0.25, False),
    (0, 2, Role.input, 0.5, False),
    (0, 3, Role.waste_to_treatment, 0.05, False),
    (1, 1, Role.reference, 1.0, False),
    (1, 2, Role.input, 3.0, False),
    (2, 2, Role.reference, 1.0, False),
    (3, 3, Role.reference, 1.0, True),
]
BIOSPHERE_EXCHANGES = [
    (1, 0, Role.emission, 2.0, False),
    (1, 1, Role.emission, 0.004, False),
    (2, 0, Role.emission, 0.5, False),
    (2, 1, Role.emission, 0.001, False),
    (3, 0, Role.emission, 1.2, False),
]


def assemble(
    exchanges: list[tuple[int, int, Role, float, bool]], shape: tuple[int, int], matrix: Matrix
) -> sparse.csc_array:
    rows: list[int] = []
    cols: list[int] = []
    values: list[float] = []
    for process, row, role, amount, treatment in exchanges:
        entry = matrix_entry(role, amount, treatment=treatment)
        assert entry is not None
        assert entry.matrix == matrix
        rows.append(row)
        cols.append(process)
        values.append(entry.value)
    return sparse.csc_array((values, (rows, cols)), shape=shape)


def test_waste_rule_positive_kg_sent_times_positive_burden_equals_ecoinvent(toy, toy_waste) -> None:
    sent = 0.05  # kg of scraps sent to incineration, positive as the API reports it
    burden = 1.2  # kg CO2-eq per kg treated, positive as the API reports it

    A = assemble(TECHNOSPHERE_EXCHANGES, (4, 4), Matrix.technosphere)
    B = assemble(BIOSPHERE_EXCHANGES, (2, 4), Matrix.biosphere)

    # The sign function writes ecoinvent's convention: treatment reference -1, waste sent +0.05.
    np.testing.assert_allclose(A.toarray(), toy_waste.A.toarray(), rtol=1e-9, atol=0)
    np.testing.assert_allclose(B.toarray(), toy_waste.B.toarray(), rtol=1e-9, atol=0)
    assert A[3, 3] == pytest.approx(-1.0, rel=1e-9)
    assert A[3, 0] == pytest.approx(0.05, rel=1e-9)

    x = scores(factorize(A), B.T @ toy_waste.c).values
    x_without = scores(factorize(toy.A), toy.h).values

    # ecoinvent: the waste input is recorded as -0.05 kg and the treatment scores -1.2 per unit
    # of its reference product (-1 kg treated).
    assert x[toy_waste.incineration] == pytest.approx(-burden, rel=1e-9)
    ecoinvent = (-sent) * x[toy_waste.incineration]

    assert ecoinvent == pytest.approx(sent * burden, rel=1e-9)
    assert x[toy_waste.tshirt] - x_without[toy.tshirt] == pytest.approx(sent * burden, rel=1e-9)
    assert x[toy_waste.tshirt] == pytest.approx(1.25205, rel=1e-9)


def test_own_use_reduces_the_diagonal(toy) -> None:
    # Fabric production loses 10% of its fabric: 1 kg made, 0.1 kg used, 0.9 kg net.
    loss = matrix_entry(Role.own_use, 0.1)
    assert loss is not None
    A = toy.A.toarray()
    A[toy.fabric, toy.fabric] += loss.value
    assert A[toy.fabric, toy.fabric] == pytest.approx(0.9)

    x = scores(factorize(A), toy.h).values

    # Each net kg of fabric needs 1/0.9 runs: (2.1192 + 3 x 0.5298) / 0.9.
    assert x[toy.fabric] == pytest.approx(3.7086 / 0.9, rel=1e-9)


def test_avoided_product_credits(toy) -> None:
    # The T-shirt factory exports 0.1 kWh to the grid per T-shirt.
    export = matrix_entry(Role.avoided_product, 0.1)
    assert export is not None
    A = toy.A.toarray()
    A[toy.electricity, toy.tshirt] += export.value

    x = scores(factorize(A), toy.h).values

    # 1.19205 - 0.1 x 0.5298
    assert x[toy.tshirt] == pytest.approx(1.13907, rel=1e-9)
