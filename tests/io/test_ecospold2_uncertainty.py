"""Uncertainty, pedigree, properties and comments on lines.

A line whose amount is negated (a waste sent, a recyclable, a treatment's reference) gets its
distribution negated too, so it still describes the amount that is stored.
"""

import math
from collections.abc import Callable
from pathlib import Path

import pytest

from headwater.io.ecospold2 import read_ecospold2
from headwater.io.exclude import read_exclude
from headwater.io.inventory import Inventory
from headwater.model import InvalidDataError, Severity
from headwater.model.uncertainty import Distribution, ExchangeProperty, Pedigree, Uncertainty
from tests.conftest import approx_numbers


def _close(actual: Uncertainty | None, expected: Uncertainty) -> bool:
    """Same distribution, and parameters equal to relative 1e-9."""
    return actual is not None and actual.model_dump() == approx_numbers(
        expected.model_dump(), rel=1e-9
    )


def test_lognormal_takes_ln_of_the_amount_and_the_variance_with_pedigree(
    source: Inventory, fx
) -> None:
    electricity = fx.exchange(source, fx.pid(1, 1), fx.line(1, 2))

    assert _close(
        electricity.uncertainty,
        Uncertainty(
            distribution=Distribution.lognormal, loc=math.log(2.0), scale=math.sqrt(0.0121)
        ),
    )
    assert electricity.pedigree == Pedigree(
        reliability=2,
        completeness=3,
        temporal_correlation=1,
        geographical_correlation=2,
        further_technological_correlation=4,
    )


def test_lognormal_on_an_emission(source: Inventory, fx) -> None:
    co2 = fx.exchange(source, fx.pid(1, 1), fx.line(1, 6))

    assert _close(
        co2.uncertainty,
        Uncertainty(distribution=Distribution.lognormal, loc=math.log(0.5), scale=math.sqrt(0.04)),
    )


def test_a_normal_on_a_waste_sent_is_negated_with_its_amount(source: Inventory, fx) -> None:
    waste = fx.exchange(source, fx.pid(1, 1), fx.line(1, 4))

    assert waste.amount == pytest.approx(0.05, rel=1e-9)
    assert _close(
        waste.uncertainty,
        Uncertainty(distribution=Distribution.normal, loc=0.05, scale=math.sqrt(0.0004)),
    )
    assert waste.pedigree == Pedigree(
        reliability=1,
        completeness=1,
        temporal_correlation=1,
        geographical_correlation=1,
        further_technological_correlation=1,
    )


def test_a_uniform_on_a_recyclable_is_negated_with_its_amount(source: Inventory, fx) -> None:
    scrap = fx.exchange(source, fx.pid(1, 1), fx.line(1, 5))

    assert _close(
        scrap.uncertainty,
        Uncertainty(distribution=Distribution.uniform, minimum=0.01, maximum=0.03),
    )


def test_a_triangular_on_a_negative_emission_stays_negative(source: Inventory, fx) -> None:
    cadmium = fx.exchange(source, fx.pid(1, 1), fx.line(1, 7))

    assert cadmium.amount == pytest.approx(-0.000001, rel=1e-9)
    assert _close(
        cadmium.uncertainty,
        Uncertainty(
            distribution=Distribution.triangular,
            minimum=-0.000002,
            loc=-0.000001,
            maximum=-0.0000005,
        ),
    )
    assert cadmium.pedigree is None


def test_a_normal_with_zero_variance_is_a_fixed_amount_and_a_warning(source: Inventory, fx) -> None:
    resource = fx.exchange(source, fx.pid(1, 1), fx.line(1, 8))

    assert resource.uncertainty is None
    assert resource.pedigree == Pedigree(
        reliability=1,
        completeness=2,
        temporal_correlation=1,
        geographical_correlation=2,
        further_technological_correlation=1,
    )
    assert [(w.code, w.item, w.severity) for w in source.warnings] == [
        ("zero-variance", resource.label, Severity.warning)
    ]


def test_lines_without_uncertainty_have_none(source: Inventory, fx) -> None:
    reference = fx.exchange(source, fx.pid(1, 1), fx.line(1, 1))

    assert (reference.uncertainty, reference.pedigree) == (None, None)


def test_properties_are_kept_as_written_including_zeros(source: Inventory, fx) -> None:
    reference = fx.exchange(source, fx.pid(1, 1), fx.line(1, 1))
    gas = fx.exchange(source, fx.pid(1, 1), fx.line(1, 3))

    def as_written(*props: ExchangeProperty) -> list[object]:
        return [approx_numbers(p.model_dump(), rel=1e-9) for p in props]

    assert [p.model_dump() for p in reference.properties] == as_written(
        ExchangeProperty(name="carbon content, fossil", value=0.4, unit="dimensionless"),
        ExchangeProperty(name="carbon content, non-fossil", value=0.0, unit="dimensionless"),
        ExchangeProperty(name="price", value=2.5, unit="EUR2005"),
    )
    assert [p.model_dump() for p in gas.properties] == as_written(
        ExchangeProperty(name="heating value, net", value=36.0, unit="MJ")
    )


def test_comments_are_kept(source: Inventory, fx) -> None:
    assert fx.exchange(source, fx.pid(1, 1), fx.line(1, 1)).comment == "One kg of widgets."
    assert fx.exchange(source, fx.pid(1, 1), fx.line(1, 2)).comment is None


WIDGET = "datasets/a0000000-0000-4000-8000-000000000001_b0000000-0000-4000-8000-000000000001.spold"


@pytest.mark.parametrize(
    ("old", "new", "code", "n"),
    [
        (
            '<uniform minValue="-0.03" maxValue="-0.01"/>',
            '<beta minValue="-0.03" mostLikelyValue="-0.02" maxValue="-0.01"/>',
            "unsupported-uncertainty",
            5,
        ),
        (
            '<uniform minValue="-0.03" maxValue="-0.01"/>',
            '<uniform minValue="-0.01" maxValue="-0.03"/>',
            "bad-uncertainty",
            5,
        ),
        (
            'varianceWithPedigreeUncertainty="0.0004"/>',
            'varianceWithPedigreeUncertainty="-0.0004"/>',
            "bad-uncertainty",
            4,
        ),
        ('furtherTechnologyCorrelation="3"', 'furtherTechnologyCorrelation="6"', "bad-pedigree", 5),
    ],
    ids=["beta", "uniform upside down", "negative variance", "pedigree out of range"],
)
def test_an_unusable_distribution_fails_naming_the_line(
    source_copy: Callable[..., Path], fx, old: str, new: str, code: str, n: int
) -> None:
    folder = source_copy(edit={WIDGET: [(old, new)]})

    with pytest.raises(InvalidDataError) as raised:
        read_ecospold2(
            folder, version="3.10", exclude=read_exclude(folder / "exclude.csv"), workers=1
        )

    assert [(p.code, p.item) for p in raised.value.problems] == [
        (code, f"{fx.pid(1, 1)} line {fx.line(1, n)}")
    ]
