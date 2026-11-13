"""An indicators release that holds several methods used together, and one that holds one."""

from dataclasses import replace

import numpy as np
import pytest

from headwater.model import Factor, Indicator, InvalidDataError, Method
from headwater.release import Release, build_indicators, method_hash, methods_hash
from headwater.release.indicators import read_indicators

SOLVER = {"solver": "test"}

TOY_DERIVED_HASH = "833abe2cacec511aa5cc9ac6cd678c783df25f1b0f8877a2a9615afd86a019ac"
"""The hash of the toy's indicators release for toy-climate, with the solver settings above, as
headwater 0.2.0 builds it. It holds the engine version, so it changes only with a new version of
headwater or a deliberate change of the format."""


@pytest.fixture
def land_use(toy_method: Method) -> Method:
    """A second method on the toy's flow list, with one indicator of its own."""
    return Method(
        id="toy-land-use",
        version="2021",
        flow_list=toy_method.flow_list,
        indicators=(Indicator(id="land-use-change", name="Land-use change", unit="kg CO2-eq"),),
        factors=(Factor(indicator="land-use-change", flow_id="toy:co2", factor=0.5),),
    )


def _scores(ids: list[str], *indicators: str) -> dict[str, np.ndarray]:
    return {i: np.arange(1.0, len(ids) + 1.0) * (k + 1) for k, i in enumerate(indicators)}


def test_a_release_with_one_method_keeps_its_hash(
    toy_release: Release, toy_method: Method, toy_scores, tmp_path
) -> None:
    ids, scores = toy_scores

    alone = build_indicators(
        toy_release, toy_method, product_ids=ids, scores=scores, solver=SOLVER, into=tmp_path
    )
    listed = build_indicators(
        toy_release, [toy_method], product_ids=ids, scores=scores, solver=SOLVER, into=tmp_path
    )

    assert alone.hash == TOY_DERIVED_HASH
    assert listed.path == alone.path
    assert read_indicators(alone).method_hash == method_hash(toy_method)


def test_a_release_holds_each_method_in_the_order_given(
    toy_release: Release, toy_method: Method, land_use: Method, toy_scores, tmp_path
) -> None:
    ids, _ = toy_scores
    scores = _scores(ids, "climate-change", "land-use-change")

    written = build_indicators(
        toy_release,
        [land_use, toy_method],
        product_ids=ids,
        scores=scores,
        solver=SOLVER,
        into=tmp_path,
    )
    found = read_indicators(written)

    assert written.name == "toy-background.toy-land-use.toy-climate"
    assert [m.id for m in written.method_entries] == ["toy-land-use", "toy-climate"]
    assert [m.hash for m in written.method_entries] == [
        method_hash(land_use),
        method_hash(toy_method),
    ]
    assert sorted(written.manifest.tables) == [
        "indicators/toy-climate.parquet",
        "indicators/toy-land-use.parquet",
        "methods/toy-climate/factors.parquet",
        "methods/toy-land-use/factors.parquet",
    ]
    assert found.indicators == ("land-use-change", "climate-change")
    assert found.method_id == "toy-land-use+toy-climate"
    assert found.method_hash == methods_hash([method_hash(land_use), method_hash(toy_method)])
    np.testing.assert_array_equal(found.scores[:, 0], scores["land-use-change"])
    np.testing.assert_array_equal(found.scores[:, 1], scores["climate-change"])
    assert Release.open(written.path).verify() is None


def test_an_indicator_in_two_methods_is_refused(
    toy_release: Release, toy_method: Method, toy_scores
) -> None:
    ids, scores = toy_scores
    twin = toy_method.model_copy(update={"id": "toy-twin"})

    with pytest.raises(InvalidDataError) as error:
        build_indicators(
            toy_release, [toy_method, twin], product_ids=ids, scores=scores, solver=SOLVER
        )

    [problem] = [p for p in error.value.problems if p.code == "duplicate-indicator"]
    assert "toy-climate" in problem.message
    assert "toy-twin" in problem.message


def test_the_order_of_a_source_release_s_methods_does_not_change_its_hash(
    records, land_use: Method, tmp_path
) -> None:
    first = replace(records, methods=(*records.methods, land_use)).build(tmp_path / "a")
    second = replace(records, methods=(land_use, *records.methods)).build(tmp_path / "b")

    assert first.hash == second.hash
    assert [m.id for m in first.method_entries] == ["toy-climate", "toy-land-use"]
