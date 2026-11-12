"""Scoring many SKUs: a table of (sku, product id, amount) times the stored score vectors."""

from pathlib import Path

import numpy as np
import pyarrow as pa
import pytest

import headwater as hw
from headwater.model import InvalidDataError, Method
from headwater.release import Release
from headwater.study import read_method

TOY = Path(__file__).resolve().parent.parent / "fixtures" / "toy"
REL = 1e-9
FABRIC, ELECTRICITY, INCINERATION = (
    "toy:fabric-production",
    "toy:electricity-production",
    "toy:incineration-textile-waste",
)


def table(*rows: tuple[str, str, float]) -> pa.Table:
    return pa.table(
        {
            "sku": [r[0] for r in rows],
            "product_id": [r[1] for r in rows],
            "amount": [r[2] for r in rows],
        }
    )


def test_a_sku_is_the_amounts_times_the_scores(release_background: hw.Background) -> None:
    skus = hw.score_many(
        release_background,
        table(
            ("a", FABRIC, 2.0),
            ("a", ELECTRICITY, 1.0),
            ("a", INCINERATION, 0.1),  # 0.1 kg of waste sent: positive burden per kg treated
            ("b", ELECTRICITY, 10.0),
        ),
    )

    assert skus.skus == ("a", "b")
    assert skus.indicators == ("climate-change",)
    assert skus.units == ("kg CO2-eq",)
    # 2 kg × 3.7086 + 1 kWh × 0.5298 + 0.1 kg × 1.2
    assert skus["climate-change"].tolist() == pytest.approx([8.067, 5.298], rel=REL)
    assert skus.scores.shape == (2, 1)
    assert skus.scores.dtype == np.float64


def test_the_waste_line_matches_the_studys_waste_line(release_background: hw.Background) -> None:
    # 0.25 kg of fabric, 0.5 kWh and 0.05 kg of scraps sent: the T-shirt with the waste line.
    skus = hw.score_many(
        release_background,
        table(("t", FABRIC, 0.25), ("t", ELECTRICITY, 0.5), ("t", INCINERATION, 0.05)),
    )

    assert skus["climate-change"][0] == pytest.approx(1.25205, rel=REL)


def test_a_repeated_sku_and_product_adds_up(release_background: hw.Background) -> None:
    skus = hw.score_many(
        release_background,
        table(("a", FABRIC, 1.0), ("a", FABRIC, 0.5), ("b", FABRIC, 1.5)),
    )

    assert skus["climate-change"].tolist() == pytest.approx([1.5 * 3.7086, 1.5 * 3.7086], rel=REL)


def test_skus_come_back_in_order_of_first_appearance(release_background: hw.Background) -> None:
    skus = hw.score_many(
        release_background, table(("z", FABRIC, 1.0), ("a", FABRIC, 2.0), ("z", ELECTRICITY, 1.0))
    )

    assert skus.skus == ("z", "a")
    assert skus["climate-change"].tolist() == pytest.approx([3.7086 + 0.5298, 2 * 3.7086], rel=REL)


def test_a_table_may_be_a_dict_of_columns_or_empty(release_background: hw.Background) -> None:
    as_dict = hw.score_many(
        release_background,
        {"sku": ["a", "a"], "product_id": [FABRIC, ELECTRICITY], "amount": [1.0, 2.0]},
    )
    empty = hw.score_many(release_background, table())

    assert as_dict["climate-change"].tolist() == pytest.approx([3.7086 + 2 * 0.5298], rel=REL)
    assert empty.skus == ()
    assert empty.scores.shape == (0, 1)


def test_an_unknown_product_is_named(release_background: hw.Background) -> None:
    with pytest.raises(InvalidDataError, match="toy:fabric-productoin") as info:
        hw.score_many(
            release_background,
            table(("a", "toy:fabric-productoin", 1.0), ("b", FABRIC, 1.0), ("c", "nowhere", 1.0)),
        )

    assert [(p.code, p.item) for p in info.value.problems] == [
        ("unknown-product", "sku a, product toy:fabric-productoin"),
        ("unknown-product", "sku c, product nowhere"),
    ]
    assert "Did you mean 'toy:fabric-production'" in str(info.value)


def test_an_amount_that_is_not_a_number_is_named(release_background: hw.Background) -> None:
    with pytest.raises(InvalidDataError, match="sku b, product toy:fabric-production") as info:
        hw.score_many(release_background, table(("a", FABRIC, 1.0), ("b", FABRIC, float("nan"))))

    assert [p.code for p in info.value.problems] == ["bad-amount"]


def test_a_missing_column_is_named(release_background: hw.Background) -> None:
    with pytest.raises(InvalidDataError, match="sku, product_id, amount") as info:
        hw.score_many(release_background, {"sku": ["a"], "product": [FABRIC], "amount": [1.0]})

    assert [(p.code, p.item) for p in info.value.problems] == [("missing-column", "product_id")]


def test_a_null_in_the_table_is_refused(release_background: hw.Background) -> None:
    rows = pa.table({"sku": ["a", None], "product_id": [FABRIC, FABRIC], "amount": [1.0, 1.0]})

    with pytest.raises(InvalidDataError, match="empty") as info:
        hw.score_many(release_background, rows)

    assert [p.code for p in info.value.problems] == ["null-value"]


def test_an_indicators_release_scores_the_same_without_a_factorization(
    release_background: hw.Background,
) -> None:
    derived = release_background.write_indicators()
    rows = table(("a", FABRIC, 2.0), ("a", INCINERATION, 0.1), ("b", ELECTRICITY, 3.0))

    from_release = hw.score_many(Release.open(derived.path), rows)
    from_background = hw.score_many(release_background, rows)

    assert from_release.skus == from_background.skus
    assert from_release.releases == (release_background.release.ref, derived.ref)  # pyright: ignore[reportOptionalMemberAccess]
    assert from_background.releases == (release_background.release.ref,)  # pyright: ignore[reportOptionalMemberAccess]
    assert from_release.method_hash == from_background.method_hash
    assert (from_release.method, from_release.method_version) == ("toy-climate", "1")
    assert from_release.indicators == from_background.indicators
    assert from_release.units == from_background.units
    np.testing.assert_allclose(from_release.scores, from_background.scores, rtol=1e-12)


def test_a_source_release_has_no_scores_to_read(release: Release) -> None:
    with pytest.raises(InvalidDataError, match="write_indicators") as info:
        hw.score_many(release, table(("a", FABRIC, 1.0)))

    assert [p.code for p in info.value.problems] == ["source-release"]


def test_several_indicators_come_back_one_column_each(make_release, records) -> None:
    methane = Method.model_validate(
        {
            **read_method(TOY / "methods" / "toy-climate").model_dump(),
            "indicators": [
                {"id": "climate-change", "name": "Climate change", "unit": "kg CO2-eq"},
                {"id": "methane", "name": "Methane", "unit": "kg CH4"},
            ],
            "factors": [
                {"indicator": "climate-change", "flow_id": "toy:co2", "factor": 1.0},
                {"indicator": "climate-change", "flow_id": "toy:ch4", "factor": 29.8},
                {"indicator": "methane", "flow_id": "toy:ch4", "factor": 1.0},
            ],
        }
    )
    release = make_release(records.with_methods(methane))
    background = hw.load(release.path, method="toy-climate")
    rows = table(("a", FABRIC, 2.0), ("a", ELECTRICITY, 1.0))

    both = hw.score_many(background, rows)
    one = hw.score_many(background, rows, indicators=["methane"])

    assert both.indicators == ("climate-change", "methane")
    assert both.units == ("kg CO2-eq", "kg CH4")
    assert both.scores.shape == (1, 2)
    # Methane: fabric 0.004 + 3 × 0.001 per kg, electricity 0.001 per kWh.
    assert both["methane"].tolist() == pytest.approx([2 * 0.007 + 0.001], rel=REL)
    assert one.indicators == ("methane",)
    np.testing.assert_allclose(one.scores, [[2 * 0.007 + 0.001]], rtol=REL)
    with pytest.raises(ValueError, match="no indicator 'nitrous'"):
        hw.score_many(background, rows, indicators=["nitrous"])
    # The same indicators come out of the stored vectors.
    stored = hw.score_many(Release.open(background.write_indicators().path), rows)
    np.testing.assert_allclose(stored.scores, both.scores, rtol=1e-12)


def test_a_table_can_be_arrow_in_and_arrow_out(release_background: hw.Background) -> None:
    skus = hw.score_many(release_background, table(("a", FABRIC, 1.0), ("b", ELECTRICITY, 1.0)))

    out = skus.to_arrow()

    assert out.column_names == ["sku", "climate-change"]
    assert out.column("sku").to_pylist() == ["a", "b"]
    assert out.column("climate-change").to_pylist() == pytest.approx([3.7086, 0.5298], rel=REL)
    assert out.schema.field("climate-change").type == pa.float64()
