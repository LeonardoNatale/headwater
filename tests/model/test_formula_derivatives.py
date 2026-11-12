"""Exact derivatives of formulas, for the sensitivity of a score to each named parameter."""

import math

import pytest

from headwater.model import Formula, FormulaError

STEP = 1e-6


def central(text: str, values: dict[str, float], name: str) -> float:
    formula = Formula.parse(text)
    up = formula.evaluate({**values, name: values[name] + STEP})
    down = formula.evaluate({**values, name: values[name] - STEP})
    return (up - down) / (2 * STEP)


def test_a_product_has_the_other_factor_as_derivative() -> None:
    assert Formula.parse("fabric_kg * (1 + loss)").derivatives(
        {"fabric_kg": 0.25, "loss": 0.05}
    ) == pytest.approx({"fabric_kg": 1.05, "loss": 0.25}, rel=1e-15)


@pytest.mark.parametrize(
    ("text", "values"),
    [
        ("a + b - 2 * a", {"a": 1.5, "b": 0.3}),
        ("a / b", {"a": 1.5, "b": 0.3}),
        ("-a / (1 + b) ** 2", {"a": 1.5, "b": 0.3}),
        ("a ** b", {"a": 1.5, "b": 0.3}),
        ("a ** 3", {"a": -1.5}),
        ("2 ** a", {"a": 0.7}),
        ("a % b", {"a": 1.75, "b": 0.5}),
        ("a // b + a", {"a": 1.75, "b": 0.5}),
        ("exp(a) * log(b)", {"a": 0.4, "b": 2.5}),
        ("log(a, b)", {"a": 3.0, "b": 2.5}),
        ("log10(a) + sqrt(b)", {"a": 3.0, "b": 2.5}),
        ("abs(a - b)", {"a": 1.0, "b": 2.5}),
        ("max(a, b, 1) + min(a, b)", {"a": 1.2, "b": 2.5}),
        ("round(a, 1) + ceil(b) + floor(a) + a", {"a": 1.23, "b": 2.5}),
        ("a * 2 if a > b else b * 3", {"a": 1.0, "b": 2.5}),
        ("a * 2 if a > b else b * 3", {"a": 3.0, "b": 2.5}),
        ("(a > 1) * b + (not a) + (a and b) + (0 or a)", {"a": 3.0, "b": 2.5}),
        ("pi * a ** 2", {"a": 0.3}),
    ],
)
def test_derivatives_match_a_central_difference(text: str, values: dict[str, float]) -> None:
    found = Formula.parse(text).derivatives(values)

    assert set(found) == set(Formula.parse(text).names)
    for name, derivative in found.items():
        assert derivative == pytest.approx(central(text, values, name), rel=1e-6, abs=1e-9), name


def test_a_formula_with_no_names_has_no_derivatives() -> None:
    assert Formula.parse("2 * pi").derivatives({}) == {}


def test_a_name_used_twice_adds_up() -> None:
    assert Formula.parse("x * x + x").derivatives({"x": 3.0}) == {"x": 7.0}


def test_at_a_kink_the_derivative_is_that_of_the_branch_taken() -> None:
    # max returns its first largest argument, abs(x) at 0 is x itself, a comparison is flat.
    assert Formula.parse("max(a, b)").derivatives({"a": 1.0, "b": 1.0}) == {"a": 1.0, "b": 0.0}
    assert Formula.parse("abs(x)").derivatives({"x": 0.0}) == {"x": 1.0}
    assert Formula.parse("x > 1").derivatives({"x": 1.0}) == {"x": 0.0}


@pytest.mark.parametrize(
    ("text", "values"),
    [
        ("sqrt(x)", {"x": 0.0}),
        ("x ** 0.5", {"x": 0.0}),
        ("2 ** x * 0 + (-2) ** y", {"x": 1.0, "y": 2.0}),
    ],
)
def test_a_derivative_that_is_not_a_finite_number_is_an_error(
    text: str, values: dict[str, float]
) -> None:
    with pytest.raises(FormulaError, match="derivative") as error:
        Formula.parse(text).derivatives(values)

    assert repr(text) in str(error.value)


def test_a_name_with_no_value_is_an_error() -> None:
    with pytest.raises(FormulaError, match="unknown parameter"):
        Formula.parse("a * b").derivatives({"a": 1.0})


def test_the_derivative_of_a_quotient() -> None:
    formula = Formula.parse("exp(a) / b")

    assert formula.derivatives({"a": 1.0, "b": 2.0})["b"] == pytest.approx(-math.e / 4, rel=1e-15)
