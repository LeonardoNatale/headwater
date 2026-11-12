"""A normalisation and weighting set: its rows are checked when it is read, and against its method
when it is used. Every mistake that would make a score wrong is an error naming the row."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from headwater.model import (
    Indicator,
    InvalidDataError,
    Method,
    NwRow,
    NwSet,
    set_indicators,
)
from headwater.study import read_set
from headwater.study._files import problems_from_validation

TOY = Path(__file__).resolve().parent.parent / "fixtures" / "toy"
PINNED = {"id": "toy-impacts", "version": "1", "hash": "b" * 64}

METHOD = Method(
    id="toy-impacts",
    version="1",
    flow_list="toy",
    indicators=(
        Indicator(id="climate-change", name="Climate change", unit="kg CO2-eq"),
        Indicator(id="climate-change-fossil", name="Climate change, fossil", unit="kg CO2-eq"),
        Indicator(id="ozone-formation", name="Photochemical ozone formation", unit="kg NMVOC-eq"),
    ),
)


def nw_set(*rows: tuple[str, float | None, float | None], unit: str | None = "Pt") -> NwSet:
    return NwSet.model_validate(
        {
            "id": "toy-nw",
            "version": "1",
            "method": PINNED,
            "unit": unit,
            "source": "made up",
            "rows": [{"indicator": i, "normalisation": n, "weight": w} for i, n, w in rows],
        }
    )


def codes(error: pytest.ExceptionInfo[InvalidDataError]) -> list[str]:
    return [p.code for p in error.value.problems]


def test_the_toy_set_reads_as_written() -> None:
    found = read_set(TOY / "methods" / "toy-nw")

    assert (found.id, found.version, found.unit) == ("toy-nw", "1", "Pt")
    assert found.method.id == "toy-impacts"
    assert found.rows == (
        NwRow(indicator="climate-change", normalisation=10.0, weight=0.6),
        NwRow(indicator="ozone-formation", normalisation=0.01, weight=0.4),
    )


def test_each_indicator_of_the_set_is_a_weighted_sum_of_the_methods() -> None:
    found = set_indicators(
        nw_set(("climate-change", 10, 0.6), ("ozone-formation", 0.01, 0.4)), METHOD
    )

    terms = {i.id: i.terms for i in found}
    assert terms["toy-nw:normalised:climate-change"] == (("climate-change", pytest.approx(0.1)),)
    assert terms["toy-nw:weighted:ozone-formation"] == (("ozone-formation", pytest.approx(40.0)),)
    assert terms["toy-nw:single-score"] == (
        ("climate-change", pytest.approx(0.06)),
        ("ozone-formation", pytest.approx(40.0)),
    )
    assert all(i.left_out == ("climate-change-fossil",) for i in found)


def test_the_rows_follow_the_methods_order_whatever_the_files() -> None:
    found = set_indicators(
        nw_set(("ozone-formation", 0.01, 0.4), ("climate-change", 10, 0.6)), METHOD
    )

    assert [i.id for i in found] == [
        "toy-nw:normalised:climate-change",
        "toy-nw:normalised:ozone-formation",
        "toy-nw:weighted:climate-change",
        "toy-nw:weighted:ozone-formation",
        "toy-nw:single-score",
    ]


def test_normalisation_alone_gives_normalised_scores_and_no_single_score() -> None:
    found = set_indicators(
        nw_set(("climate-change", 10, None), ("ozone-formation", 0.01, None), unit=None), METHOD
    )

    assert [i.id for i in found] == [
        "toy-nw:normalised:climate-change",
        "toy-nw:normalised:ozone-formation",
    ]


def test_weighting_alone_weighs_the_scores_as_they_are() -> None:
    found = set_indicators(
        nw_set(("climate-change", None, 0.6), ("ozone-formation", None, 0.4)), METHOD
    )

    assert [i.id for i in found] == [
        "toy-nw:weighted:climate-change",
        "toy-nw:weighted:ozone-formation",
        "toy-nw:single-score",
    ]
    assert found[-1].terms == (("climate-change", 0.6), ("ozone-formation", 0.4))


def test_a_row_for_an_unknown_indicator_is_an_error_naming_it_and_the_method() -> None:
    with pytest.raises(InvalidDataError) as raised:
        set_indicators(nw_set(("climate-change", 10, 0.6), ("water-use", 100, 0.1)), METHOD)

    assert codes(raised) == ["unknown-indicator"]
    assert "water-use" in str(raised.value)
    assert "toy-impacts" in str(raised.value)


def test_an_id_the_method_already_has_is_refused() -> None:
    clash = METHOD.model_copy(
        update={
            "indicators": (
                *METHOD.indicators,
                Indicator(id="toy-nw:single-score", name="Single score", unit="Pt"),
            )
        }
    )

    with pytest.raises(InvalidDataError) as raised:
        set_indicators(nw_set(("climate-change", 10, 0.6)), clash)

    assert codes(raised) == ["duplicate-indicator"]


@pytest.mark.parametrize(
    ("rows", "unit", "code"),
    [
        ((("climate-change", 10, 0.6), ("climate-change", 10, 0.6)), "Pt", "duplicate-indicator"),
        ((("climate-change", 0, 0.6),), "Pt", "invalid-field"),
        ((("climate-change", -10, 0.6),), "Pt", "invalid-field"),
        ((("climate-change", float("inf"), 0.6),), "Pt", "invalid-field"),
        ((("climate-change", 10, -0.6),), "Pt", "invalid-field"),
        ((("climate-change", 10, float("nan")),), "Pt", "invalid-field"),
        ((("climate-change", 10, 0.6), ("ozone-formation", None, 0.4)), "Pt", "mixed-set"),
        ((("climate-change", 10, 0.6), ("ozone-formation", 0.01, None)), "Pt", "mixed-set"),
        ((("climate-change", None, None),), None, "empty-row"),
        ((("climate-change", 10, 0.6),), None, "missing-unit"),
        ((("climate-change", 10, None),), "Pt", "unit-not-used"),
        ((("climate-change", 10, None),), "", "invalid-field"),
        ((), "Pt", "empty-set"),
    ],
)
def test_a_row_that_would_make_a_score_wrong_is_refused(
    rows: tuple[tuple[str, float | None, float | None], ...], unit: str | None, code: str
) -> None:
    with pytest.raises(ValidationError) as raised:
        nw_set(*rows, unit=unit)

    assert code in [p.code for p in problems_from_validation(raised.value, prefix="set")]


def test_a_weight_of_zero_is_kept() -> None:
    found = set_indicators(
        nw_set(("climate-change", 10, 0.6), ("ozone-formation", 0.01, 0)), METHOD
    )

    assert found[-1].terms == (("climate-change", pytest.approx(0.06)), ("ozone-formation", 0.0))


def test_a_set_folder_with_a_bad_row_names_the_file(tmp_path: Path) -> None:
    folder = tmp_path / "toy-nw"
    folder.mkdir()
    (folder / "set.yaml").write_text((TOY / "methods" / "toy-nw" / "set.yaml").read_text())
    (folder / "factors.csv").write_text("indicator,normalisation,weight\nclimate-change,ten,0.6\n")

    with pytest.raises(InvalidDataError, match="factors.csv"):
        read_set(folder)
