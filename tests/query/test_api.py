"""The Python API as a Brightway user meets it: the public names, `bg.score`, the names of
arguments and fields, and how the main objects print, on the toy.

The lists of public names are fixed on purpose: a name that is added, renamed or removed fails
here, so the surface changes only when someone means it to, and docs/calculation.md "Python API"
names each one.
"""

import math

import pytest

import headwater as hw
from headwater.query.monte_carlo import monte_carlo
from headwater.release import method_hash, set_hash
from headwater.study import read_method, read_set
from tests.conftest import open_database

from .conftest import TOY_DIR, Make, Records, pinned, toy_records

HEADWATER = [
    "Background",
    "Contribution",
    "Database",
    "Emitter",
    "HeadwaterError",
    "InvalidDataError",
    "ManyScores",
    "Match",
    "MonteCarlo",
    "MonteCarloProvenance",
    "NotCharacterised",
    "ParameterSensitivity",
    "Place",
    "Provenance",
    "Range",
    "Result",
    "Sensitivity",
    "Study",
    "Where",
    "__version__",
    "load",
    "monte_carlo",
    "open",
    "score_many",
]

BACKGROUND = {
    "contributions",
    "direct_and_upstream",
    "find",
    "get_process",
    "indicators",
    "method",
    "methods",
    "not_characterised",
    "processes",
    "release",
    "score",
    "sensitivity",
    "sets",
    "where",
    "write_indicators",
}

DATABASE = {"calc", "edit", "folder", "name", "new", "scores", "validate"}

STUDY = {"calc", "folder", "name", "products", "save", "scores", "validate"}

DATABASE_EDITOR = {
    "background",
    "changed",
    "cut_off",
    "name",
    "new_flow",
    "new_process",
    "override",
    "path",
    "process",
    "remove_cut_off",
    "remove_parameter",
    "remove_process",
    "save",
    "set",
    "set_parameter",
}

PROCESS_EDITOR = {
    "code",
    "csv",
    "exchange",
    "exchanges",
    "id",
    "new_exchange",
    "remove_exchange",
    "set",
    "set_exchange",
    "yaml",
}

OVERRIDE_EDITOR = {"file", "process_id", "reset_exchange", "set_exchange"}

RESULT = {
    "amount",
    "contributions",
    "direct_and_upstream",
    "forward_score",
    "indicator",
    "not_characterised",
    "parameters",
    "process_id",
    "provenance",
    "score",
    "sensitivity",
    "unit",
    "where",
}

CONTRIBUTION = {
    "amount",
    "children",
    "copy_of",
    "database",
    "direct",
    "flow_id",
    "location",
    "name",
    "per_unit",
    "process_id",
    "rest",
    "score",
    "to_dataframe",
    "unit",
}

WHERE = {
    "amount",
    "locations",
    "locations_rest",
    "locations_rest_count",
    "name",
    "process_id",
    "processes",
    "rest",
    "rest_count",
    "score",
    "to_dataframe",
    "unit",
}

MATCH = {"flow_id", "kind", "location", "name", "process_id", "reference_product", "unit", "why"}

FABRIC = "toy:fabric-production"
ELECTRICITY = "toy:electricity-production"
INCINERATION = "toy:incineration-textile-waste"


def public(thing: object) -> set[str]:
    return {name for name in dir(thing) if not name.startswith("_")}


def test_headwater_exports_exactly_these_names() -> None:
    assert sorted(hw.__all__) == HEADWATER
    for name in HEADWATER:
        assert hasattr(hw, name), name


def test_a_background_shows_only_the_calls_a_user_needs(background: hw.Background) -> None:
    """The matrices, the row of each process and the checks a study needs are not on it: integer
    indices never leave a calculation."""
    assert public(background) == BACKGROUND


def test_a_database_shows_only_the_calls_a_user_needs() -> None:
    assert public(open_database(TOY_DIR / "tshirt" / "tshirt")) == DATABASE


def test_a_study_shows_only_the_calls_a_user_needs() -> None:
    assert public(hw.open(TOY_DIR / "tshirt-dyed" / "tshirt-footprint")) == STUDY
    memory = hw.Study(products={"toy:fabric-production": 1}, methods=["toy-climate"])
    assert public(memory) == STUDY


def test_the_editors_show_only_the_calls_a_user_needs(background: hw.Background) -> None:
    editor = open_database(TOY_DIR / "tshirt" / "tshirt").edit(background)
    assert public(editor) == DATABASE_EDITOR
    assert public(editor.process("tshirt")) == PROCESS_EDITOR
    assert public(editor.override(ELECTRICITY)) == OVERRIDE_EDITOR


def test_a_result_returns_no_integer_index(background: hw.Background) -> None:
    result = open_database(TOY_DIR / "tshirt" / "tshirt").calc(background, "tshirt:tshirt")
    assert public(result) == RESULT


def test_contributions_where_and_matches_show_their_fields(background: hw.Background) -> None:
    result = open_database(TOY_DIR / "tshirt" / "tshirt").calc(background, "tshirt:tshirt")
    assert public(result.contributions()) == CONTRIBUTION
    assert public(result.where()) == WHERE
    assert public(background.find("electricity")[0]) == MATCH


def test_a_database_scores_any_of_its_processes_by_process_id(background: hw.Background) -> None:
    database = open_database(TOY_DIR / "tshirt" / "tshirt")
    assert database.calc(background, process_id="tshirt:tshirt").process_id == "tshirt:tshirt"
    scores = database.scores(background, process_id="tshirt:tshirt")
    assert scores == pytest.approx({"climate-change": 1.19205}, rel=1e-9)


def test_bg_score_is_the_stored_score_per_unit(either: hw.Background) -> None:
    assert either.score(FABRIC) == pytest.approx(3.7086, rel=1e-9)
    assert either.score(ELECTRICITY) == pytest.approx(0.5298, rel=1e-9)
    # a treatment scores its positive burden per kg treated, as contributions and score_many do
    assert either.score(INCINERATION) == pytest.approx(1.2, rel=1e-9)
    named = either.score(process_id=FABRIC, indicator="climate-change")
    assert math.isclose(named, either.score(FABRIC), rel_tol=1e-12)


def test_bg_score_adds_up_to_calc(either: hw.Background) -> None:
    """The T-shirt buys 0.25 kg of fabric and 0.5 kWh of electricity and emits nothing itself."""
    result = pinned(open_database(TOY_DIR / "tshirt" / "tshirt"), either).calc(
        either, "tshirt:tshirt"
    )
    by_hand = 0.25 * either.score(FABRIC) + 0.5 * either.score(ELECTRICITY)
    assert math.isclose(by_hand, result.score, rel_tol=1e-12)
    assert math.isclose(either.score(FABRIC), either.contributions(FABRIC).score, rel_tol=1e-12)


def test_bg_score_of_a_set_indicator() -> None:
    bg = hw.load(
        TOY_DIR / "background",
        method=TOY_DIR / "methods" / "toy-impacts",
        namespace="toy",
        sets=TOY_DIR / "methods" / "toy-nw",
    )
    single = bg.score(FABRIC, indicator="toy-nw:single-score")
    assert single == pytest.approx(0.50252, abs=5e-6)
    explained = bg.contributions(FABRIC, indicator="toy-nw:single-score").score
    assert math.isclose(single, explained, rel_tol=1e-12)


def test_bg_score_names_the_closest_id(background: hw.Background) -> None:
    with pytest.raises(ValueError, match="toy:fabric-production"):
        background.score("toy:fabric-prodution")


def test_background_calls_take_a_process_id(background: hw.Background) -> None:
    assert background.contributions(process_id=FABRIC).process_id == FABRIC
    assert background.where(process_id=FABRIC, top=1).process_id == FABRIC
    assert background.sensitivity(process_id=FABRIC, top=1)[0].process_id == FABRIC


def test_provenance_names_the_pin(background: hw.Background) -> None:
    provenance = (
        open_database(TOY_DIR / "tshirt" / "tshirt").calc(background, "tshirt:tshirt").provenance
    )
    assert provenance.pin == "toy-background@fixture"
    assert provenance.releases == ()


def test_a_flow_of_the_database_itself_is_in_database(background: hw.Background) -> None:
    result = open_database(TOY_DIR / "tshirt-no-factor" / "tshirt").calc(
        background, "tshirt:tshirt"
    )
    assert [f.flow_id for f in result.not_characterised if f.in_database] == ["tshirt:cod"]


def test_a_sensitivity_line_names_its_process_and_supplier(background: hw.Background) -> None:
    levers = (
        open_database(TOY_DIR / "tshirt" / "tshirt").calc(background, "tshirt:tshirt").sensitivity()
    )
    fabric, grid = levers[0], levers[1]
    assert (fabric.name, fabric.supplier_name) == ("T-shirt", "cotton fabric production")
    assert (grid.name, grid.supplier_name) == ("electricity production", None)


def test_a_background_prints_its_source_and_method(either: hw.Background) -> None:
    source = (
        "folder background, namespace toy"
        if either.release is None
        else "release toy-background@6864b7dbb65d"
    )
    assert repr(either) == f"Background({source}, method toy-climate 1, 1 indicator, 3 processes)"


def test_a_background_with_a_set_prints_it() -> None:
    bg = hw.load(
        TOY_DIR / "background",
        method=TOY_DIR / "methods" / "toy-impacts",
        namespace="toy",
        sets=TOY_DIR / "methods" / "toy-nw",
    )
    n = len(bg.indicators)
    assert repr(bg) == (
        f"Background(folder background, namespace toy, method toy-impacts 1, set toy-nw 1, "
        f"{n} indicators, 3 processes)"
    )


def test_a_database_prints_what_it_is() -> None:
    assert repr(open_database(TOY_DIR / "tshirt" / "tshirt")) == (
        "Database(tshirt: 1 process, background toy-background@fixture, methods toy-climate)"
    )
    assert repr(hw.open(TOY_DIR / "tshirt-grid-scenario" / "tshirt-footprint")) == (
        "Study(tshirt-footprint: 1 product, methods toy-climate, scenarios clean-grid)"
    )
    memory = hw.Study(products={"toy:fabric-production": 2}, methods=["toy-climate"])
    assert repr(memory) == "Study(in memory: 1 product, methods toy-climate, not saved)"
    assert repr(open_database(TOY_DIR / "tshirt-dyed" / "tshirt")).endswith(", uses dyes)")


def test_sensitivity_lines_and_parameters_print_on_one_line(background: hw.Background) -> None:
    lever = (
        open_database(TOY_DIR / "tshirt" / "tshirt")
        .calc(background, "tshirt:tshirt")
        .sensitivity()[0]
    )
    assert repr(lever) == (
        "Sensitivity(tshirt:tshirt e02, T-shirt: input 0.25 kg, elasticity 0.7778, "
        "sensitivity 3.7086)"
    )
    parameter = (
        open_database(TOY_DIR / "tshirt-parameter" / "tshirt")
        .calc(background, "tshirt:tshirt")
        .parameters()[0]
    )
    assert repr(parameter) == (
        "ParameterSensitivity(fabric_kg = 0.25 kg, elasticity 0.7778, sensitivity 3.7086)"
    )


def test_a_match_prints_as_find_lists_it(background: hw.Background) -> None:
    assert repr(background.find("electricity")[0]) == (
        "Match(toy:electricity-production: electricity production | electricity | GLO, kWh, "
        "name starts with)"
    )


def test_many_scores_print_their_size(background: hw.Background) -> None:
    skus = hw.score_many(
        background,
        {"sku": ["a", "b"], "product_id": [FABRIC, ELECTRICITY], "amount": [1.0, 1.0]},
    )
    assert repr(skus) == "ManyScores(2 SKUs, 1 indicator, method toy-climate 1)"


def test_a_monte_carlo_run_prints_its_size(make_release: Make) -> None:
    lognormal = {"distribution": "lognormal", "loc": math.log(3.0), "scale": math.log(1.1)}
    records: Records = toy_records().with_line(FABRIC, "e02", uncertainty=lognormal)
    bg = hw.load(make_release(records).path, method="toy-climate")
    mc = monte_carlo(bg, [FABRIC, ELECTRICITY], iterations=10, seed=0)
    assert repr(mc) == "MonteCarlo(2 items, 1 indicator, 10 iterations, seed 0)"


def test_the_toy_hashes_do_not_move(make_release: Make, background: hw.Background) -> None:
    """Renames are of Python names only, and a method with no category indicator writes none:
    the release, the methods and the set are those of main before the API polish. The T-shirt's
    input hash changed once, with D46, from
    61d08237751248aead99485a1b765521a21385fc5c641ece7aba0200b7b94099: database.yaml replaced
    study.yaml, and the process scored is hashed (tests/test_databases.py)."""
    assert make_release().hash == (
        "6864b7dbb65d3138e5bca0857d39b792b03c036f241475c8c230fe00eeb0041b"
    )
    methods = TOY_DIR / "methods"
    assert method_hash(read_method(methods / "toy-climate")) == (
        "c2180f15db23e74f41ce2a980e392ff31ea786d31d2a0dedf9b67e0602f7236d"
    )
    assert method_hash(read_method(methods / "toy-impacts")) == (
        "bdc4e5667236ebb5e84a94a1ce947966bf780a3126598adaf2ce4248d7cc6832"
    )
    assert set_hash(read_set(methods / "toy-nw")) == (
        "c4c59c2225fe35d0466023d15f30ac4c3eafc01a30ae57a1f304f66c093a5644"
    )
    result = open_database(TOY_DIR / "tshirt" / "tshirt").calc(background, "tshirt:tshirt")
    assert result.provenance.input_hash == (
        "b5697b05239fd21e05b47b090e7e466061a88c091d2eeb82c1996c86fc8221c0"
    )


def test_internal_code_reaches_the_matrices(background: hw.Background) -> None:
    """What the package's own calculations read is behind `internals`, with the row of each
    process by id."""
    from headwater.query._internals import internals

    inner = internals(background)
    assert inner.index[FABRIC] == inner.solved.ids.index(FABRIC)
    result = open_database(TOY_DIR / "tshirt" / "tshirt").calc(background, "tshirt:tshirt")
    assert internals(result).ids[internals(result).target] == "tshirt:tshirt"
