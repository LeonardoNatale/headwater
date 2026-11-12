"""Normalised, weighted and single scores from a normalisation and weighting set.

The toy method `toy-impacts` has climate change (CO2 1, CH4 29.8), its fossil part, which equals
it because both toy gases are fossil, and ozone formation (CH4 1). The set `toy-nw` divides
climate change by 10 and ozone formation by 0.01, weighs them 0.6 and 0.4, in Pt, and leaves the
fossil part out. The T-shirt, by hand (docs/calculation.md, "Single scores"):

    climate change  1.19205 / 10   = 0.119205, × 0.6 = 0.071523
    ozone formation 0.00225 / 0.01 = 0.225,    × 0.4 = 0.09
    single score                                     = 0.161523 Pt
"""

from dataclasses import replace
from pathlib import Path

import pytest
import yaml

import headwater as hw
from headwater.model import Factor, Indicator, InvalidDataError, Method
from headwater.query._internals import internals
from headwater.release import method_hash, set_hash
from headwater.study import read_method, read_process_folder, read_set
from tests.conftest import open_database, toy_database
from tests.query.conftest import pinned_study

TOY = Path(__file__).resolve().parent.parent / "fixtures" / "toy"
METHOD = TOY / "methods" / "toy-impacts"
SET = TOY / "methods" / "toy-nw"
REL = 1e-9

SINGLE = "toy-nw:single-score"
NORMALISED = "toy-nw:normalised:climate-change", "toy-nw:normalised:ozone-formation"
WEIGHTED = "toy-nw:weighted:climate-change", "toy-nw:weighted:ozone-formation"
BASES = "climate-change", "ozone-formation"
COEFFICIENTS = 0.6 / 10, 0.4 / 0.01


@pytest.fixture(scope="module")
def bg() -> hw.Background:
    return hw.load(TOY / "background", method=METHOD, namespace="toy", sets=SET)


@pytest.fixture(scope="module")
def plain() -> hw.Background:
    return hw.load(TOY / "background", method=METHOD, namespace="toy")


def tshirt(name: str = "tshirt") -> hw.Database:
    """A toy database, on toy-impacts instead of the toy-climate its database.yaml names."""
    folder = open_database(toy_database(TOY, name)).folder
    record = folder.database.model_copy(update={"methods": ("toy-impacts",)})
    return hw.Database(replace(folder, database=record))


# --- exit test 1: the toy single score, hand-checked in calculation.md --------------------------


def test_the_tshirt_scores_are_the_hand_checked_ones(bg: hw.Background) -> None:
    database = tshirt()
    expected = {
        "climate-change": 1.19205,
        "ozone-formation": 0.00225,
        NORMALISED[0]: 0.119205,
        NORMALISED[1]: 0.225,
        WEIGHTED[0]: 0.071523,
        WEIGHTED[1]: 0.09,
        SINGLE: 0.161523,
    }

    for indicator, score in expected.items():
        result = database.calc(bg, "tshirt:tshirt", indicator=indicator)
        assert result.score == pytest.approx(score, rel=REL), indicator
        assert result.forward_score == pytest.approx(score, rel=REL), indicator


def test_the_set_adds_its_indicators_after_the_methods_with_their_units(
    bg: hw.Background,
) -> None:
    found = [(i.id, i.name, i.unit) for i in bg.indicators]

    assert found == [
        ("climate-change", "Climate change", "kg CO2-eq"),
        ("climate-change-fossil", "Climate change, fossil", "kg CO2-eq"),
        ("ozone-formation", "Photochemical ozone formation", "kg NMVOC-eq"),
        (NORMALISED[0], "Climate change, normalised (toy-nw)", ""),
        (NORMALISED[1], "Photochemical ozone formation, normalised (toy-nw)", ""),
        (WEIGHTED[0], "Climate change, weighted (toy-nw)", "Pt"),
        (WEIGHTED[1], "Photochemical ozone formation, weighted (toy-nw)", "Pt"),
        (SINGLE, "Single score (toy-nw)", "Pt"),
    ]
    assert tshirt().calc(bg, "tshirt:tshirt", indicator=SINGLE).unit == "Pt"
    assert tshirt().calc(bg, "tshirt:tshirt", indicator=NORMALISED[0]).unit == ""


def test_a_background_product_has_a_single_score(bg: hw.Background) -> None:
    """1 kg of fabric: 0.06 × 3.7086 + 40 × 0.007 = 0.502516."""
    assert bg.contributions("toy:fabric-production", indicator=SINGLE).score == pytest.approx(
        0.502516, rel=REL
    )


def test_the_method_and_its_indicators_are_as_without_the_set(
    bg: hw.Background, plain: hw.Background
) -> None:
    assert bg.method == plain.method
    assert internals(bg).method_hash == internals(plain).method_hash
    assert [i.id for i in plain.indicators] == [i.id for i in plain.method.indicators]
    with pytest.raises(ValueError, match="toy-impacts has 3 indicators"):
        internals(bg).indicator()


# --- exit test 2: explain, where and levers work on it unchanged ---------------------------------


@pytest.mark.parametrize("name", ["tshirt", "tshirt-waste", "tshirt-local-electricity"])
def test_the_single_score_tree_adds_up_at_every_level(bg: hw.Background, name: str) -> None:
    result = tshirt(name).calc(bg, "tshirt:tshirt", indicator=SINGLE)
    root = result.contributions(min_share=0.0, max_depth=10)

    assert root.score == pytest.approx(result.score, rel=REL)
    stack = [root]
    while stack:
        node = stack.pop()
        total = node.direct + sum(c.score for c in node.children) + node.rest
        assert total == pytest.approx(node.score, rel=REL, abs=1e-15), node.process_id
        stack.extend(node.children)


@pytest.mark.parametrize("name", ["tshirt", "tshirt-waste", "tshirt-local-electricity"])
def test_where_of_the_single_score_is_the_weighted_sum_of_theirs(
    bg: hw.Background, name: str
) -> None:
    """With the waste line, incineration emits CO2 and no methane, so the two indicators are not
    proportional and a single score from one of them alone would fail."""
    database = tshirt(name)
    single = database.calc(bg, "tshirt:tshirt", indicator=SINGLE).where(top=None)
    parts = [database.calc(bg, "tshirt:tshirt", indicator=i).where(top=None) for i in BASES]

    expected: dict[str, float] = {}
    for k, where in zip(COEFFICIENTS, parts, strict=True):
        for e in where.processes:
            expected[e.process_id] = expected.get(e.process_id, 0.0) + k * e.score
    assert {e.process_id: e.score for e in single.processes} == pytest.approx(expected, rel=REL)
    assert single.score == pytest.approx(
        sum(k * w.score for k, w in zip(COEFFICIENTS, parts, strict=True)), rel=REL
    )


@pytest.mark.parametrize("name", ["tshirt", "tshirt-waste", "tshirt-parameter"])
def test_lever_sensitivities_of_the_single_score_are_the_weighted_sum_of_theirs(
    bg: hw.Background, name: str
) -> None:
    """Sensitivities add up with the weights; elasticities do not, since each is relative to its
    own score."""
    database = tshirt(name)

    def sensitivities(indicator: str) -> dict[tuple[str, str], float]:
        levers = database.calc(bg, "tshirt:tshirt", indicator=indicator).sensitivity(
            include_reference=True
        )
        return {(lever.process_id, lever.line_id): lever.sensitivity for lever in levers}

    single = sensitivities(SINGLE)
    parts = [sensitivities(i) for i in BASES]
    expected = {
        key: sum(k * part.get(key, 0.0) for k, part in zip(COEFFICIENTS, parts, strict=True))
        for key in single
    }
    assert single == pytest.approx(expected, rel=REL, abs=1e-15)
    assert set(single) == set(parts[0]) | set(parts[1])


def test_a_background_products_where_and_levers_are_the_weighted_sums(bg: hw.Background) -> None:
    product = "toy:fabric-production"
    single = {
        e.process_id: e.score for e in bg.where(product, indicator=SINGLE, top=None).processes
    }
    expected: dict[str, float] = {}
    for k, base in zip(COEFFICIENTS, BASES, strict=True):
        for e in bg.where(product, indicator=base, top=None).processes:
            expected[e.process_id] = expected.get(e.process_id, 0.0) + k * e.score
    assert single == pytest.approx(expected, rel=REL)

    levers = {
        (v.process_id, v.line_id): v.sensitivity for v in bg.sensitivity(product, indicator=SINGLE)
    }
    parts = [
        {(v.process_id, v.line_id): v.sensitivity for v in bg.sensitivity(product, indicator=b)}
        for b in BASES
    ]
    assert levers == pytest.approx(
        {
            key: sum(k * p.get(key, 0.0) for k, p in zip(COEFFICIENTS, parts, strict=True))
            for key in levers
        },
        rel=REL,
    )


def test_a_scenario_changes_the_single_score_as_it_changes_each_score(bg: hw.Background) -> None:
    """`clean-grid`: electricity emits 0.3 kg CO2 per kWh instead of 0.5, for everything."""
    case = TOY / "tshirt-grid-scenario"
    scenario = yaml.safe_load(
        (case / "tshirt-footprint" / "scenarios" / "clean-grid.yaml").read_text()
    )
    study = hw.Study(
        {"tshirt:tshirt": 1},
        ["toy-impacts"],
        databases=case,
        scenarios={"clean-grid": scenario},
    )
    study = pinned_study(study, bg)
    folder = study.folder
    on_impacts = tuple(
        replace(d, database=d.database.model_copy(update={"methods": ("toy-impacts",)}))
        for d in folder.databases
    )
    study = hw.Study._of(replace(folder, databases=on_impacts))  # pyright: ignore[reportPrivateUsage]
    single = study.calc(bg, indicator=SINGLE, scenario="clean-grid")[0].score
    parts = [study.calc(bg, indicator=i, scenario="clean-grid")[0].score for i in BASES]

    assert single == pytest.approx(
        sum(k * p for k, p in zip(COEFFICIENTS, parts, strict=True)), rel=REL
    )
    assert parts[0] == pytest.approx(0.94205, rel=REL)


def test_scores_gives_every_indicator_of_the_set_as_calc_does(bg: hw.Background) -> None:
    """What `headwater diff` scores each difference with."""
    database = tshirt("tshirt-waste")
    scores = database.scores(bg, "tshirt:tshirt")

    assert list(scores) == [i.id for i in bg.indicators]
    for indicator, score in scores.items():
        expected = database.calc(bg, "tshirt:tshirt", indicator=indicator).score
        assert score == pytest.approx(expected, rel=REL, abs=1e-15), indicator
    assert scores[SINGLE] == pytest.approx(0.06 * 1.25205 + 40 * 0.00225, rel=REL)


def test_an_exact_zero_stays_an_exact_zero(bg: hw.Background) -> None:
    """Incineration emits CO2 only: its ozone score is exactly 0, and so is its normalised one,
    while its single score counts its climate change."""
    x = internals(bg).solved.x
    row = internals(bg).index["toy:incineration-textile-waste"]

    assert x["ozone-formation"].exact_zeros[row]
    assert x[NORMALISED[1]].exact_zeros[row]
    assert x[NORMALISED[1]].values[row] == 0.0
    assert not x[SINGLE].exact_zeros[row]
    assert bg.contributions(
        "toy:incineration-textile-waste", indicator=SINGLE
    ).score == pytest.approx(0.06 * 1.2, rel=REL)


# --- exit test 3: coverage is explicit -----------------------------------------------------------


def test_a_row_for_an_indicator_the_method_does_not_have_is_an_error_naming_it(
    tmp_path: Path,
) -> None:
    folder = _copy_set(tmp_path)
    with (folder / "factors.csv").open("a") as handle:
        handle.write("water-use,100,0.1\n")

    with pytest.raises(InvalidDataError, match="water-use") as raised:
        hw.load(TOY / "background", method=METHOD, namespace="toy", sets=folder)

    assert [p.code for p in raised.value.problems] == ["unknown-indicator"]
    assert "toy-impacts" in str(raised.value)


def test_an_indicator_the_set_does_not_cover_is_listed_on_the_result(bg: hw.Background) -> None:
    for indicator in (SINGLE, WEIGHTED[0], NORMALISED[1]):
        nw_set = tshirt().calc(bg, "tshirt:tshirt", indicator=indicator).provenance.nw_set
        assert nw_set is not None
        assert nw_set.left_out == ("climate-change-fossil",)
    assert "1 indicator left out" in repr(tshirt().calc(bg, "tshirt:tshirt", indicator=SINGLE))


# --- exit test 4 (toy): a set changes no method and no release ----------------------------------


def test_a_set_pins_its_method_by_hash(tmp_path: Path) -> None:
    folder = _copy_set(tmp_path)
    text = (folder / "set.yaml").read_text()
    (folder / "set.yaml").write_text(text.replace("hash: bdc4e566", "hash: 00c4e566"))

    with pytest.raises(InvalidDataError, match="bdc4e5667236") as raised:
        hw.load(TOY / "background", method=METHOD, namespace="toy", sets=folder)

    assert [p.code for p in raised.value.problems] == ["set-method-mismatch"]
    assert "00c4e5667236" in str(raised.value)


def test_a_set_indicator_id_another_method_has_is_refused() -> None:
    """Methods used together: an id of the set that names an indicator of the other method would
    replace its scores."""
    other = Method(
        id="other",
        version="1",
        flow_list="toy",
        indicators=(Indicator(id=SINGLE, name="Something else", unit="kg"),),
        factors=(Factor(indicator=SINGLE, flow_id="toy:co2", factor=1.0),),
    )
    folder = read_process_folder(TOY / "background", namespace="toy")

    with pytest.raises(InvalidDataError, match="method other and of set toy-nw") as raised:
        hw.Background(folder, [read_method(METHOD), other], [read_set(SET)])

    assert [p.code for p in raised.value.problems] == ["duplicate-indicator"]


def test_a_set_for_a_method_not_loaded_is_refused() -> None:
    with pytest.raises(InvalidDataError, match="toy-impacts") as raised:
        hw.load(
            TOY / "background", method=TOY / "methods" / "toy-climate", namespace="toy", sets=SET
        )

    assert [p.code for p in raised.value.problems] == ["set-method-missing"]


def test_loading_a_set_leaves_the_method_hash_as_it_is(bg: hw.Background) -> None:
    method = read_method(METHOD)

    assert internals(bg).method_hashes == {"toy-impacts": method_hash(method)}
    assert read_set(SET).method.hash == method_hash(method)


# --- exit test 5: provenance names the set and its hash -----------------------------------------


def test_the_provenance_names_the_set_and_its_hash(bg: hw.Background, plain: hw.Background) -> None:
    nw_set = read_set(SET)
    result = tshirt().calc(bg, "tshirt:tshirt", indicator=SINGLE)
    p = result.provenance

    assert p.nw_set is not None
    assert (p.nw_set.id, p.nw_set.version, p.nw_set.hash) == ("toy-nw", "1", set_hash(nw_set))
    assert p.nw_set.method.id == "toy-impacts"
    assert p.nw_set.method.hash == method_hash(read_method(METHOD))
    assert p.nw_set.unit == "Pt"
    assert p.indicator_method.id == "toy-impacts"
    assert "set toy-nw 1" in repr(result)


def test_a_methods_own_indicator_has_no_set_and_the_same_input_hash(
    bg: hw.Background, plain: hw.Background
) -> None:
    with_set = tshirt().calc(bg, "tshirt:tshirt", indicator="climate-change").provenance
    without = tshirt().calc(plain, "tshirt:tshirt", indicator="climate-change").provenance

    assert with_set.nw_set is None
    assert with_set.input_hash == without.input_hash


def test_the_input_hash_of_a_set_indicator_depends_on_the_set(
    bg: hw.Background, tmp_path: Path
) -> None:
    folder = _copy_set(tmp_path)
    text = (folder / "factors.csv").read_text()
    (folder / "factors.csv").write_text(text.replace("0.6", "0.5"))
    other = hw.load(TOY / "background", method=METHOD, namespace="toy", sets=folder)

    first = tshirt().calc(bg, "tshirt:tshirt", indicator=SINGLE).provenance
    second = tshirt().calc(other, "tshirt:tshirt", indicator=SINGLE).provenance

    assert first.input_hash != second.input_hash
    assert first.nw_set is not None and second.nw_set is not None
    assert first.nw_set.hash != second.nw_set.hash


def _copy_set(tmp_path: Path) -> Path:
    folder = tmp_path / "toy-nw"
    folder.mkdir()
    for name in ("set.yaml", "factors.csv"):
        (folder / name).write_bytes((SET / name).read_bytes())
    return folder
