import pytest

from headwater.model import DEFAULT_UNITS, InvalidDataError, Unit, Units


def test_g_is_a_thousandth_of_kg() -> None:
    assert DEFAULT_UNITS.convert(250, "g", "kg") == pytest.approx(0.25, rel=1e-12)


@pytest.mark.parametrize(
    ("amount", "unit", "to", "expected"),
    [
        (1, "kWh", "MJ", 3.6),
        (1, "t", "kg", 1000),
        (1, "L", "m3", 0.001),
        (1, "ha", "m2", 10000),
        (1, "km", "m", 1000),
        (1, "MWh", "kWh", 1000),
        (1, "kg", "kg", 1),
    ],
)
def test_default_conversions(amount: float, unit: str, to: str, expected: float) -> None:
    assert DEFAULT_UNITS.convert(amount, unit, to) == pytest.approx(expected, rel=1e-12)


def test_same_unit_returns_the_amount_unchanged() -> None:
    # Exact on purpose: no arithmetic is done, so an amount in the reference unit is never rounded.
    assert DEFAULT_UNITS.convert(0.1 + 0.2, "kg", "kg") == 0.1 + 0.2


def test_quantity_of_a_unit() -> None:
    assert [DEFAULT_UNITS.quantity_of(u) for u in ("kg", "kWh", "m3", "unit", "tkm")] == [
        "mass",
        "energy",
        "volume",
        "item",
        "freight-transport-work",
    ]


def test_unknown_unit_is_not_found() -> None:
    assert "kgg" not in DEFAULT_UNITS
    assert DEFAULT_UNITS.get("kgg") is None


def test_converting_across_quantities_is_an_error_naming_both_units() -> None:
    with pytest.raises(InvalidDataError) as raised:
        DEFAULT_UNITS.convert(1, "kg", "kWh")

    assert "'kg'" in str(raised.value)
    assert "'kWh'" in str(raised.value)


def test_unit_factor_must_be_positive_and_finite() -> None:
    for bad in (0, -1, float("nan"), float("inf")):
        with pytest.raises(ValueError, match="factor_to_reference"):
            Unit(name="x", quantity="mass", factor_to_reference=bad)


def test_merge_adds_units() -> None:
    lb = Unit(name="lb", quantity="mass", factor_to_reference=0.45359237)

    merged = DEFAULT_UNITS.merge(Units([lb]))

    assert merged.convert(1, "lb", "kg") == pytest.approx(0.45359237, rel=1e-12)
    assert "kg" in merged


def test_merge_accepts_the_same_definition_twice() -> None:
    kg = Unit(name="kg", quantity="mass", factor_to_reference=1)

    assert "kg" in DEFAULT_UNITS.merge(Units([kg]))


def test_merge_rejects_a_unit_defined_two_ways() -> None:
    other_kg = Unit(name="kg", quantity="mass", factor_to_reference=2)

    with pytest.raises(InvalidDataError) as raised:
        DEFAULT_UNITS.merge(Units([other_kg]))

    (problem,) = raised.value.problems
    assert problem.code == "unit-conflict"
    assert problem.item == "unit 'kg'"
    assert "factor_to_reference" in problem.message or "factor" in problem.message


def test_a_unit_in_a_second_quantity_is_a_conflict() -> None:
    with pytest.raises(InvalidDataError) as raised:
        Units(
            [
                Unit(name="x", quantity="mass", factor_to_reference=1),
                Unit(name="x", quantity="energy", factor_to_reference=1),
            ]
        )

    assert raised.value.problems[0].code == "unit-conflict"


def test_converting_an_unknown_unit_into_itself_is_still_an_error() -> None:
    with pytest.raises(InvalidDataError) as raised:
        DEFAULT_UNITS.convert(1, "kgg", "kgg")

    (problem,) = raised.value.problems
    assert problem.code == "unknown-unit"
    assert "'kgg'" in problem.message
    assert "'kg'" in problem.message  # the closest known unit is suggested
