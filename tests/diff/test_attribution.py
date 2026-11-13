"""Each score change, and what each difference alone would have done to it.

Hand-checked numbers on the toy background (docs/calculation.md): fabric 3.7086 and electricity
0.5298 kg CO2-eq per unit, so the T-shirt is 0.25 × 3.7086 + 0.5 × 0.5298 = 1.19205.
"""

import shutil
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

import headwater as hw
from headwater.diff import Change, Difference, diff_databases, differences
from headwater.diff._changes import no_effect
from headwater.model import HeadwaterError
from headwater.study import DatabaseFolder, read_database
from tests.conftest import add_lines, add_process, toy_database, write_tables

TOY = Path(__file__).resolve().parent.parent / "fixtures" / "toy"
REL = 1e-9  # AGENTS.md: relative 1e-9 for toy fixtures


def edit(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    assert old in text, f"{old!r} not found in {path}"
    path.write_text(text.replace(old, new, 1))


def study(root: Path, name: str = "tshirt") -> DatabaseFolder:
    return read_database(toy_database(root, name))


def csv(root: Path) -> Path:
    return root / "tshirt" / "tshirt" / "exchanges.csv"


@pytest.fixture
def old(toy: Path) -> DatabaseFolder:
    """The T-shirt as committed, read before a test edits the copy."""
    return study(toy)


def test_one_changed_line_gets_the_whole_change(
    toy: Path, old: DatabaseFolder, background: hw.Background
) -> None:
    edit(csv(toy), "e02,input,toy:cotton-fabric,0.25", "e02,input,toy:cotton-fabric,0.22")

    result = diff_databases(old, study(toy), background)

    [row] = result.rows
    assert row.product_id == "tshirt:tshirt"
    assert row.indicator == "climate-change"
    assert row.old == pytest.approx(1.19205, rel=REL)
    assert row.new == pytest.approx(1.080792, rel=REL)  # 1.19205 - 0.03 × 3.7086
    assert row.change == pytest.approx(-0.111258, rel=REL)
    assert row.percent == pytest.approx(-100 * 0.111258 / 1.19205, rel=REL)
    assert row.changed
    [driver] = row.drivers
    assert driver.difference.item == "tshirt:tshirt line e02"
    assert driver.contribution == pytest.approx(-0.111258, rel=REL)
    assert row.interaction == pytest.approx(0.0, abs=1e-12)


def test_drivers_are_ranked_by_the_size_of_their_effect(
    toy: Path, old: DatabaseFolder, background: hw.Background
) -> None:
    edit(csv(toy), "e02,input,toy:cotton-fabric,0.25", "e02,input,toy:cotton-fabric,0.22")
    edit(csv(toy), "e03,input,toy:electricity,0.5", "e03,input,toy:electricity,0.45")

    [row] = diff_databases(old, study(toy), background).rows

    # fabric: -0.03 × 3.7086 = -0.111258, electricity: -0.05 × 0.5298 = -0.02649
    assert [d.difference.item for d in row.drivers] == [
        "tshirt:tshirt line e02",
        "tshirt:tshirt line e03",
    ]
    assert [d.contribution for d in row.drivers] == pytest.approx([-0.111258, -0.02649], rel=REL)
    assert row.change == pytest.approx(-0.137748, rel=REL)
    assert row.interaction == pytest.approx(0.0, abs=1e-12)


def test_the_biggest_effect_comes_first_whatever_the_order_in_the_file(
    toy: Path, old: DatabaseFolder, background: hw.Background
) -> None:
    # electricity, line e03, is changed by more than the fabric, line e02
    edit(csv(toy), "e02,input,toy:cotton-fabric,0.25", "e02,input,toy:cotton-fabric,0.24")
    edit(csv(toy), "e03,input,toy:electricity,0.5", "e03,input,toy:electricity,1.5")

    [row] = diff_databases(old, study(toy), background).rows

    assert [d.difference.item for d in row.drivers] == [
        "tshirt:tshirt line e03",  # +0.5298
        "tshirt:tshirt line e02",  # -0.037086
    ]


def two_process_study(toy: Path) -> Path:
    """The T-shirt buys 0.25 kg of a fabric the study makes itself, from 3 kWh and 2 kg CO2.

    Fabric: 2.0 + 3 × 0.5298 = 3.5894. T-shirt: 0.25 × 3.5894 = 0.89735.
    """
    root = toy / "tshirt" / "tshirt"
    write_tables(
        root,
        {
            "tshirt": "e01,reference,tshirt:tshirt,1,unit,\n"
            "e02,input,tshirt:fabric,0.25,kg,tshirt:fabric",
            "fabric": "e01,reference,tshirt:fabric,1,kg,\n"
            "e02,input,toy:electricity,3,kWh,toy:electricity-production\n"
            "e03,emission,toy:co2,2.0,kg,",
        },
        details={"tshirt": "T-shirt,GLO,ordinary_transforming_activity"},
    )
    with (root / "flows.csv").open("a") as handle:
        handle.write("tshirt:fabric,fabric,product,kg,,\n")
    return root


def test_what_two_changes_do_together_beyond_each_alone_is_the_interaction(
    toy: Path, background: hw.Background
) -> None:
    two_process_study(toy)
    old = study(toy)
    same = {r.product_id: r for r in diff_databases(old, old, background).rows}
    assert same["tshirt:tshirt"].old == pytest.approx(0.89735, rel=REL)
    edit(csv(toy), "tshirt:fabric,0.25,kg", "tshirt:fabric,0.5,kg")
    edit(csv(toy), "toy:electricity,3,kWh", "toy:electricity,6,kWh")

    rows = diff_databases(old, study(toy), background).rows
    row = {r.product_id: r for r in rows}["tshirt:tshirt"]

    # new: 0.5 × (2.0 + 6 × 0.5298) = 2.5894. Alone, the T-shirt's 0.5 kg gives 0.5 × 3.5894 =
    # 1.7947 (+0.89735), and the fabric's 6 kWh gives 0.25 × 5.1788 = 1.2947 (+0.39735). Together
    # they add 1.69205, which is 0.39735 more than the two alone: 0.25 kg more fabric × 1.5894.
    assert row.change == pytest.approx(1.69205, rel=REL)
    contributions = {d.difference.item: d.contribution for d in row.drivers}
    assert contributions == pytest.approx(
        {"tshirt:tshirt line e02": 0.89735, "tshirt:fabric line e02": 0.39735}, rel=REL
    )
    assert row.interaction == pytest.approx(0.39735, rel=REL)
    known = sum(d.contribution for d in row.drivers if d.contribution is not None)
    assert known + (row.interaction or 0.0) == pytest.approx(row.change, rel=REL)


def test_a_line_that_buys_from_a_new_process_gets_the_effect_of_that_purchase(
    toy: Path, old: DatabaseFolder, background: hw.Background
) -> None:
    folder = toy / "tshirt" / "tshirt"
    add_process(
        folder,
        "packaging",
        "e01,reference,tshirt:packaging,1,unit,\ne02,emission,toy:co2,0.5,kg,",
    )
    with (folder / "flows.csv").open("a") as handle:
        handle.write("tshirt:packaging,packaging,product,unit,,\n")
    add_lines(folder, "tshirt", "e04,input,tshirt:packaging,2,unit,tshirt:packaging")

    rows = diff_databases(old, study(toy), background).rows
    row = {r.product_id: r for r in rows}["tshirt:tshirt"]

    # 2 units of packaging at 0.5 kg CO2 each
    assert row.new == pytest.approx(2.19205, rel=REL)
    by_item = {d.difference.item: d.contribution for d in row.drivers}
    assert by_item["tshirt:tshirt line e04"] == pytest.approx(1.0, rel=REL)
    assert by_item["tshirt:packaging"] == pytest.approx(0.0, abs=1e-12)  # bought by nothing alone
    assert by_item["tshirt:packaging"] is not None
    assert row.interaction == pytest.approx(0.0, abs=1e-12)


def test_a_difference_that_cannot_be_applied_alone_is_not_separable(
    toy: Path, old: DatabaseFolder, background: hw.Background
) -> None:
    edit(csv(toy), "e02,input,toy:cotton-fabric,0.25", "e02,input,toy:cotton-fabric,0.22")
    found = differences(old, study(toy))

    def breaks(folder: DatabaseFolder) -> DatabaseFolder:
        raise hw.InvalidDataError([])

    broken = Difference(
        id="line broken",
        kind="line",
        change=Change.changed,
        item="broken",
        description="cannot be applied alone",
        fields=(),
        apply=breaks,
    )

    [row] = diff_databases(old, study(toy), background, differences=(*found, broken)).rows

    contributions = {d.difference.item: d.contribution for d in row.drivers}
    assert contributions["broken"] is None
    assert contributions["tshirt:tshirt line e02"] == pytest.approx(-0.111258, rel=REL)
    assert row.interaction == pytest.approx(0.0, abs=1e-12)  # the rest is what is not explained
    assert row.drivers[-1].difference.item == "broken"  # not separable ones come last


def test_a_parameter_gets_the_effect_of_every_line_it_moves(
    toy: Path, background: hw.Background
) -> None:
    shutil.rmtree(toy / "tshirt" / "tshirt")
    shutil.copytree(TOY / "tshirt-parameter" / "tshirt", toy / "tshirt" / "tshirt")
    old = study(toy)
    edit(toy / "tshirt/tshirt/parameters.yaml", "value: 0.25", "value: 0.30")

    [row] = diff_databases(old, study(toy), background).rows

    assert row.new == pytest.approx(1.37748, rel=REL)  # docs/calculation.md
    [driver] = row.drivers
    assert driver.difference.kind == "parameter"
    assert driver.difference.item == "fabric_kg"
    assert driver.contribution == pytest.approx(0.05 * 3.7086, rel=REL)


def test_a_change_that_does_not_move_the_score_is_a_driver_of_zero(
    toy: Path, old: DatabaseFolder, background: hw.Background
) -> None:
    edit(toy / "tshirt/tshirt/processes.csv", "tshirt,T-shirt,", "tshirt,Polo shirt,")

    [row] = diff_databases(old, study(toy), background).rows

    assert not row.changed
    assert row.change == 0.0
    [driver] = row.drivers
    assert driver.difference.item == "tshirt:tshirt"
    assert driver.contribution == 0.0


def test_a_score_that_moves_by_rounding_only_is_unchanged(
    toy: Path, old: DatabaseFolder, background: hw.Background
) -> None:
    edit(csv(toy), "e02,input,toy:cotton-fabric,0.25", "e02,input,toy:cotton-fabric,0.25000000001")

    [row] = diff_databases(old, study(toy), background).rows

    assert row.change != 0.0
    assert not row.changed


@pytest.fixture(scope="module")
def two_indicators(tmp_path_factory: pytest.TempPathFactory) -> hw.Background:
    """The toy background with a second indicator, methane, next to climate change."""
    method = tmp_path_factory.mktemp("method") / "two-indicators"
    shutil.copytree(TOY / "methods" / "toy-climate", method)
    edit(
        method / "method.yaml",
        "    unit: kg CO2-eq\n",
        "    unit: kg CO2-eq\n  - id: methane\n    name: Methane\n    unit: kg CH4\n",
    )
    with (method / "factors.csv").open("a") as handle:
        handle.write("methane,toy:ch4,,1\n")
    return hw.load(TOY / "background", method=method, namespace="toy")


def test_every_indicator_of_the_method_is_compared(
    toy: Path, old: DatabaseFolder, two_indicators: hw.Background
) -> None:
    two = two_indicators
    edit(csv(toy), "e02,input,toy:cotton-fabric,0.25", "e02,input,toy:cotton-fabric,0.30")

    rows = diff_databases(old, study(toy), two).rows
    only_methane = diff_databases(old, study(toy), two, indicators=["methane"]).rows

    assert [r.indicator for r in rows] == ["climate-change", "methane"]
    assert [r.indicator for r in only_methane] == ["methane"]
    methane = rows[1]
    assert methane.indicator_name == "Methane"
    # methane: fabric 0.004 + 3 kWh × 0.001 = 0.007 per kg, electricity 0.001 per kWh
    assert methane.old == pytest.approx(0.25 * 0.007 + 0.5 * 0.001, rel=REL)
    assert methane.change == pytest.approx(0.05 * 0.007, rel=REL)
    assert methane.drivers[0].contribution == pytest.approx(0.05 * 0.007, rel=REL)


def test_a_product_that_exists_on_one_side_only_has_no_drivers(
    toy: Path, old: DatabaseFolder, background: hw.Background
) -> None:
    add_process(
        toy / "tshirt" / "tshirt",
        "polo",
        "e01,reference,tshirt:polo,1,unit,\n"
        "e02,input,toy:electricity,1,kWh,toy:electricity-production",
    )
    with (toy / "tshirt" / "tshirt" / "flows.csv").open("a") as handle:
        handle.write("tshirt:polo,polo,product,unit,,\n")

    new = study(toy)
    rows = {r.product_id: r for r in diff_databases(old, new, background).rows}
    back = {r.product_id: r for r in diff_databases(new, old, background).rows}

    assert rows["tshirt:tshirt"].change == pytest.approx(0.0, abs=1e-12)
    assert rows["tshirt:polo"].old is None
    assert rows["tshirt:polo"].new == pytest.approx(0.5298, rel=REL)
    assert rows["tshirt:polo"].drivers == ()
    assert not rows["tshirt:polo"].changed
    # the same product, taken away
    assert back["tshirt:polo"].old == pytest.approx(0.5298, rel=REL)
    assert back["tshirt:polo"].new is None
    assert back["tshirt:polo"].change is None
    assert back["tshirt:polo"].drivers == ()


def test_only_the_old_and_the_new_study_are_calculated_in_full(
    toy: Path, old: DatabaseFolder, background: hw.Background, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    real = hw.Database._calc  # pyright: ignore[reportPrivateUsage]

    def counting(self: hw.Database, *args: Any, **kwargs: Any) -> hw.Result:
        calls.append(self.name)
        return real(self, *args, **kwargs)

    edit(csv(toy), "e02,input,toy:cotton-fabric,0.25", "e02,input,toy:cotton-fabric,0.22")
    edit(csv(toy), "e03,input,toy:electricity,0.5", "e03,input,toy:electricity,0.4")
    monkeypatch.setattr(hw.Database, "_calc", counting)  # a diff names no commit: `_calc`

    diff_databases(old, study(toy), background)

    # a full result for the old and the new study; each difference alone is only scored
    assert len(calls) == 2


def test_each_difference_is_scored_for_every_indicator_in_one_pass(
    toy: Path, old: DatabaseFolder, two_indicators: hw.Background, monkeypatch: pytest.MonkeyPatch
) -> None:
    scored: list[list[str]] = []
    real = hw.Database.scores

    def counting(
        self: hw.Database,
        bg: hw.Background,
        process_id: str,
        *,
        indicators: Sequence[str] | None = None,
    ) -> dict[str, float]:
        scored.append(list(indicators or []))
        return real(self, bg, process_id, indicators=indicators)

    edit(csv(toy), "e02,input,toy:cotton-fabric,0.25", "e02,input,toy:cotton-fabric,0.22")
    edit(csv(toy), "e03,input,toy:electricity,0.5", "e03,input,toy:electricity,0.4")
    monkeypatch.setattr(hw.Database, "scores", counting)

    diff_databases(old, study(toy), two_indicators)

    # one call for each of the two differences, each asked for both indicators
    assert scored == [["climate-change", "methane"]] * 2


def test_only_the_indicators_asked_for_are_scored(
    toy: Path, old: DatabaseFolder, two_indicators: hw.Background, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[list[str]] = []
    real = hw.Database.scores

    def counting(
        self: hw.Database,
        bg: hw.Background,
        process_id: str,
        *,
        indicators: Sequence[str] | None = None,
    ) -> dict[str, float]:
        asked.append(list(indicators or []))
        return real(self, bg, process_id, indicators=indicators)

    edit(csv(toy), "e02,input,toy:cotton-fabric,0.25", "e02,input,toy:cotton-fabric,0.22")
    monkeypatch.setattr(hw.Database, "scores", counting)

    diff_databases(old, study(toy), two_indicators, indicators=["methane"])

    assert asked == [["methane"]]


def test_the_error_of_a_side_says_which_side(
    toy: Path, old: DatabaseFolder, background: hw.Background
) -> None:
    edit(csv(toy), "kg,toy:fabric-production", "kg,toy:nowhere")

    with pytest.raises(HeadwaterError) as error:
        diff_databases(old, study(toy), background, labels=("main", "the working tree"))

    assert "the working tree" in str(error.value)
    assert "main" not in str(error.value).splitlines()[0]


def test_an_indicator_the_method_does_not_have_is_named(
    toy: Path, old: DatabaseFolder, background: hw.Background
) -> None:
    with pytest.raises(HeadwaterError) as error:
        diff_databases(old, study(toy), background, indicators=["climat-change"])

    assert "climat-change" in str(error.value)
    assert "climate-change" in str(error.value)


# --- the effects are what calculating each difference alone gives ---------------------------


def alone(
    found: tuple[Difference, ...], old: DatabaseFolder, background: hw.Background, indicator: str
) -> dict[str, float]:
    """The effect of each scored difference, by calculating the old study with it in full."""
    before = hw.Database(old).calc(background, "tshirt:tshirt", indicator=indicator).score
    return {
        d.id: hw.Database(d.apply(old)).calc(background, "tshirt:tshirt", indicator=indicator).score
        - before
        for d in found
        if d.apply is not None and d.apply is not no_effect
    }


def test_the_effects_equal_calculating_each_difference_alone_for_every_indicator(
    toy: Path, old: DatabaseFolder, two_indicators: hw.Background
) -> None:
    edit(csv(toy), "e02,input,toy:cotton-fabric,0.25", "e02,input,toy:cotton-fabric,0.30")
    edit(csv(toy), "e03,input,toy:electricity,0.5", "e03,input,toy:electricity,0.4")
    new = study(toy)

    rows = diff_databases(old, new, two_indicators).rows

    assert [r.indicator for r in rows] == ["climate-change", "methane"]
    for row in rows:
        expected = alone(differences(old, new), old, two_indicators, row.indicator)
        got = {d.difference.id: d.contribution for d in row.drivers}
        assert got.keys() == expected.keys()
        for key, effect in expected.items():
            assert got[key] == pytest.approx(effect, rel=1e-9, abs=1e-15), (row.indicator, key)


@pytest.mark.parametrize(
    ("name", "file", "old_text", "new_text"),
    [
        ("tshirt-parameter", "parameters.yaml", "value: 0.25", "value: 0.30"),
        (
            "tshirt-local-electricity",
            "overrides/own-wind-power.csv",
            "emission,toy:co2,0.3",
            "emission,toy:co2,0.2",
        ),
        (
            "tshirt-local-fabric",
            "overrides/efficient-weaving.csv",
            "toy:electricity,2.5,kWh",
            "toy:electricity,3,kWh",
        ),
    ],
)
def test_the_effects_of_a_parameter_and_of_an_override_equal_calculating_them_alone(
    name: str,
    file: str,
    old_text: str,
    new_text: str,
    toy: Path,
    two_indicators: hw.Background,
) -> None:
    shutil.rmtree(toy / "tshirt" / "tshirt")
    shutil.copytree(toy_database(TOY, name), toy / "tshirt" / "tshirt")
    before = study(toy)
    edit(toy / "tshirt" / "tshirt" / file, old_text, new_text)
    new = study(toy)

    rows = diff_databases(before, new, two_indicators).rows

    for row in rows:
        expected = alone(differences(before, new), before, two_indicators, row.indicator)
        assert expected, "the edit should be a scored difference"
        got = {d.difference.id: d.contribution for d in row.drivers}
        for key, effect in expected.items():
            assert got[key] == pytest.approx(effect, rel=1e-9, abs=1e-15), (row.indicator, key)


# --- a study that is new ---------------------------------------------------------------------


def test_a_new_database_is_counted_and_not_scored(toy: Path, two_indicators: hw.Background) -> None:
    result = diff_databases(None, study(toy), two_indicators)

    assert result.created is True
    assert result.rows == () and result.differences == ()
    assert result.new_input_hash is None and result.new_residual is None
    assert [(p.id, p.status) for p in result.products] == [("tshirt:tshirt", "new")]


def test_a_new_database_is_scored_alone_for_the_products_given(
    toy: Path, two_indicators: hw.Background
) -> None:
    result = diff_databases(None, study(toy), two_indicators, products=["tshirt:tshirt"])

    assert result.created is True
    assert result.differences == ()
    assert result.old_input_hash is None and result.old_residual is None
    assert result.new_input_hash is not None and len(result.new_input_hash) == 64
    assert [(r.indicator, r.old) for r in result.rows] == [
        ("climate-change", None),
        ("methane", None),
    ]
    assert result.rows[0].new == pytest.approx(1.19205, rel=REL)
    assert result.rows[1].new == pytest.approx(0.00225, rel=REL)
    for row in result.rows:
        assert row.drivers == () and row.interaction is None and not row.changed
        assert row.change is None and row.percent is None


def test_a_new_database_that_cannot_be_calculated_names_its_side(
    toy: Path, two_indicators: hw.Background
) -> None:
    edit(csv(toy), "kg,toy:fabric-production", "kg,toy:nowhere")

    with pytest.raises(HeadwaterError) as error:
        diff_databases(
            None,
            study(toy),
            two_indicators,
            products=["tshirt:tshirt"],
            labels=("main", "the working tree"),
        )

    assert "the working tree" in str(error.value).splitlines()[0]


def test_a_changed_database_is_not_a_new_one(
    toy: Path, old: DatabaseFolder, background: hw.Background
) -> None:
    result = diff_databases(old, study(toy), background)

    assert result.created is False
    assert result.old_input_hash == result.new_input_hash
