"""Uncertainty types 1 to 4, and the pedigree matrix a comment carries.

ecoSpold1: type 1 lognormal (`standardDeviation95` is the square of the geometric standard
deviation), 2 normal (`standardDeviation95` is twice the standard deviation), 3 triangular and
4 uniform (`minValue`, `maxValue`, and `mostLikelyValue` for the mode). 0 is no distribution.
"""

import math

import pytest

from headwater.io.formats import inventory_from
from headwater.model import InvalidDataError, Severity
from headwater.model.uncertainty import Distribution, Pedigree, Uncertainty
from tests.conftest import approx_numbers


def _line(es1, amount: float = 2.0, group: str = "in5", **attributes):
    """The second line of `widget`, as read, with these uncertainty attributes."""
    lines = es1.lines_of(
        es1.dataset(
            "widget",
            es1.exchange(1, "widget", "kg", 1, "out0"),
            es1.exchange(2, "thing", "kg", amount, group, location="CH", **attributes),
        )
    )
    return lines["widget", 2]


def _close(found: dict | None, expected: Uncertainty) -> None:
    assert found is not None
    assert Uncertainty(**found).model_dump() == approx_numbers(expected.model_dump(), rel=1e-12)


def test_lognormal_takes_ln_of_the_amount_and_the_geometric_deviation(es1) -> None:
    line = _line(es1, 2.0, uncertaintyType=1, standardDeviation95=1.21)

    _close(
        line["uncertainty"],
        Uncertainty(distribution=Distribution.lognormal, loc=math.log(2.0), scale=math.log(1.1)),
    )


def test_normal_takes_the_amount_and_half_the_interval(es1) -> None:
    line = _line(es1, 2.0, uncertaintyType=2, standardDeviation95=0.5)

    _close(
        line["uncertainty"],
        Uncertainty(distribution=Distribution.normal, loc=2.0, scale=0.25),
    )


def test_triangular_takes_its_bounds_and_most_likely_value(es1) -> None:
    line = _line(es1, 2.0, uncertaintyType=3, minValue=1, maxValue=4, mostLikelyValue=1.5)

    _close(
        line["uncertainty"],
        Uncertainty(distribution=Distribution.triangular, minimum=1, maximum=4, loc=1.5),
    )


def test_triangular_without_a_most_likely_value_takes_the_mode_from_the_mean(es1) -> None:
    """The mean of a triangular distribution is (min + mode + max) / 3, and the file's meanValue
    is calculated from the most likely value."""
    line = _line(es1, 2.0, uncertaintyType=3, minValue=1, maxValue=4)

    _close(
        line["uncertainty"],
        Uncertainty(distribution=Distribution.triangular, minimum=1, maximum=4, loc=1.0),
    )


def test_uniform_takes_its_bounds(es1) -> None:
    line = _line(es1, 2.0, uncertaintyType=4, minValue=1, maxValue=3)

    _close(
        line["uncertainty"], Uncertainty(distribution=Distribution.uniform, minimum=1, maximum=3)
    )


def test_type_0_and_no_type_give_no_distribution(es1) -> None:
    assert _line(es1, uncertaintyType=0)["uncertainty"] is None
    assert _line(es1)["uncertainty"] is None


def test_a_standard_deviation_with_no_type_is_the_default_lognormal(es1) -> None:
    line = _line(es1, 2.0, standardDeviation95=1.21)

    assert line["uncertainty"]["distribution"] == "lognormal"


def test_a_lognormal_deviation_of_one_is_a_fixed_amount_with_a_warning(es1) -> None:
    folder = es1.make(
        es1.dataset(
            "widget",
            es1.exchange(1, "widget", "kg", 1, "out0"),
            es1.exchange(2, "thing", "kg", 2, "in5", uncertaintyType=1, standardDeviation95=1),
        )
    )

    source = es1.read(folder)

    assert [w.code for w in source.warnings] == ["zero-variance"]
    assert source.warnings[0].severity is Severity.warning
    assert all(r["uncertainty"] is None for r in source.lines.to_pylist())


def test_a_lognormal_around_zero_is_a_fixed_amount_with_a_warning(es1) -> None:
    folder = es1.make(
        es1.dataset(
            "widget",
            es1.exchange(1, "widget", "kg", 1, "out0"),
            es1.exchange(2, "thing", "kg", 0, "in5", uncertaintyType=1, standardDeviation95=1.5),
        )
    )

    source = es1.read(folder)

    assert [w.code for w in source.warnings] == ["zero-amount-uncertainty"]


@pytest.mark.parametrize(
    "attributes",
    [
        {"uncertaintyType": 1},
        {"uncertaintyType": 1, "standardDeviation95": 0.5},
        {"uncertaintyType": 2},
        {"uncertaintyType": 3, "minValue": 1},
        {"uncertaintyType": 3, "minValue": 3, "maxValue": 1, "mostLikelyValue": 2},
        {"uncertaintyType": 3, "minValue": 1, "maxValue": 4, "mostLikelyValue": 9},
        {"uncertaintyType": 4, "minValue": 3, "maxValue": 1},
        {"uncertaintyType": 7, "standardDeviation95": 1.5},
        {"uncertaintyType": 1, "standardDeviation95": "high"},
    ],
)
def test_an_uncertainty_that_cannot_be_read_is_refused_naming_the_line(es1, attributes) -> None:
    folder = es1.make(
        es1.dataset(
            "widget",
            es1.exchange(1, "widget", "kg", 1, "out0"),
            es1.exchange(2, "thing", "kg", 2, "in5", **attributes),
        )
    )

    with pytest.raises(InvalidDataError) as raised:
        es1.read(folder)

    [problem] = raised.value.problems
    assert problem.code in {"bad-uncertainty", "unsupported-uncertainty"}
    assert "line 2" in problem.item


@pytest.mark.parametrize(
    ("attributes", "expected"),
    [
        (
            {"uncertaintyType": 2, "standardDeviation95": 0.5},
            Uncertainty(distribution=Distribution.normal, loc=2.0, scale=0.25),
        ),
        (
            {"uncertaintyType": 1, "standardDeviation95": 1.21},
            Uncertainty(
                distribution=Distribution.lognormal, loc=math.log(2.0), scale=math.log(1.1)
            ),
        ),
        (
            {"uncertaintyType": 4, "minValue": -3, "maxValue": -1},
            Uncertainty(distribution=Distribution.uniform, minimum=1, maximum=3),
        ),
        (
            {"uncertaintyType": 3, "minValue": -4, "maxValue": -1, "mostLikelyValue": -1.5},
            Uncertainty(distribution=Distribution.triangular, minimum=1, maximum=4, loc=1.5),
        ),
    ],
)
def test_a_negated_amount_negates_its_distribution(es1, attributes, expected) -> None:
    """A credit written as a negative input is stored positive, with its distribution turned the
    same way: a normal's mean flips, a uniform's bounds swap, a lognormal's ln|x| stays."""
    line = _line(es1, -2.0, **attributes)

    assert line["amount"] == 2.0
    _close(line["uncertainty"], expected)


def test_the_pedigree_is_the_first_five_scores_of_the_comment(es1) -> None:
    line = _line(es1, 2.0, generalComment="(1,2,3,4,5,na); the rest of the comment")

    assert Pedigree(**line["pedigree"]) == Pedigree(
        reliability=1,
        completeness=2,
        temporal_correlation=3,
        geographical_correlation=4,
        further_technological_correlation=5,
    )
    assert line["comment"] == "(1,2,3,4,5,na); the rest of the comment"


@pytest.mark.parametrize("comment", ["(2,1,1,1,1,BU:1.05)", "(2, 1, 1, 1, 1, 3)", "(2,1,1,1,1)"])
def test_the_pedigree_accepts_the_forms_real_files_write(es1, comment: str) -> None:
    line = _line(es1, 2.0, generalComment=comment)

    assert line["pedigree"]["reliability"] == 2


@pytest.mark.parametrize(
    "comment", ["(1,na,na,na,na,na)", "a note (1,2,3,4,5)", "(6,1,1,1,1,1)", "x"]
)
def test_a_comment_without_five_scores_has_no_pedigree(es1, comment: str) -> None:
    line = _line(es1, 2.0, generalComment=comment)

    assert line["pedigree"] is None
    assert line["comment"] == comment


def test_the_distributions_survive_the_stages(es1) -> None:
    folder = es1.make(
        es1.dataset(
            "widget",
            es1.exchange(1, "widget", "kg", 1, "out0"),
            es1.exchange(
                2,
                "Carbon dioxide",
                "kg",
                2,
                "out4",
                category="air",
                uncertaintyType=1,
                standardDeviation95=1.21,
            ),
        )
    )

    inventory = inventory_from(folder, es1.options, format="ecospold1")

    [process] = inventory.processes
    assert process.exchanges[1].uncertainty is not None
    assert process.exchanges[1].uncertainty.scale == pytest.approx(math.log(1.1))
