import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from headwater.model import Factor, InvalidDataError, Method
from headwater.release import (
    Release,
    ReleaseInfo,
    ReleaseRef,
    build_indicators,
    content_hash,
    method_hash,
)

from .conftest import Records

SOLVER = {"solver": "scipy.sparse.linalg.splu", "tolerance": 1e-12}


def _problems(error: pytest.ExceptionInfo[InvalidDataError]) -> list[tuple[str, str]]:
    return [(p.code, p.item) for p in error.value.problems]


def test_a_derived_release_records_its_parent_and_method_hashes(
    toy_release: Release, toy_method: Method, toy_scores
) -> None:
    ids, scores = toy_scores

    derived = build_indicators(
        toy_release,
        toy_method,
        product_ids=ids,
        scores=scores,
        solver=SOLVER,
        provenance={"steps": ["lu"]},
    )

    manifest = json.loads((derived.path / "release.json").read_text())
    assert manifest["kind"] == "indicators"
    assert manifest["parent"] == {"name": "toy-background", "hash": toy_release.hash}
    assert [m["hash"] for m in manifest["methods"]] == [method_hash(toy_method)]
    assert manifest["provenance"] == {"steps": ["lu"]}
    assert derived.parent == ReleaseRef(name="toy-background", hash=toy_release.hash)
    assert derived.path.parent == toy_release.path.parent
    assert derived.path.name == f"toy-background.toy-climate@{derived.hash[:12]}"
    assert sorted(manifest["files"]) == [
        "indicators/toy-climate.parquet",
        "methods/toy-climate/factors.parquet",
    ]


def test_building_a_derived_release_leaves_the_parent_unchanged(
    toy_release: Release, toy_method: Method, toy_scores, snapshot
) -> None:
    before = snapshot(toy_release.path)
    ids, scores = toy_scores

    build_indicators(toy_release, toy_method, product_ids=ids, scores=scores, solver=SOLVER)
    other = Method.model_validate({**toy_method.model_dump(), "id": "toy-co2-only"})
    other = Method.model_validate(
        {**other.model_dump(), "factors": [f for f in other.factors if f.flow_id == "toy:co2"]}
    )
    build_indicators(toy_release, other, product_ids=ids, scores=scores, solver=SOLVER)

    assert snapshot(toy_release.path) == before
    assert content_hash(toy_release.path) == toy_release.hash
    assert Release.open(toy_release.path).hash == toy_release.hash


def test_the_indicator_vectors_read_back_by_product_id(
    toy_release: Release, toy_method: Method, toy_scores
) -> None:
    ids, scores = toy_scores
    reversed_ids = list(reversed(ids))
    reversed_scores = {k: v[::-1].copy() for k, v in scores.items()}

    derived = build_indicators(
        toy_release, toy_method, product_ids=reversed_ids, scores=reversed_scores, solver=SOLVER
    )
    again = Release.open(derived.path)
    again.verify()

    table = again.table("indicators/toy-climate")
    assert table.column_names == ["product_id", "climate-change"]
    assert table.column("product_id").to_pylist() == ids
    assert table.column("climate-change").to_pylist() == scores["climate-change"].tolist()
    assert [method_hash(m) for m in again.methods()] == [method_hash(toy_method)]
    assert again.methods()[0].indicators == toy_method.indicators
    assert again.info == ReleaseInfo.model_validate(
        {**toy_release.info.model_dump(), "name": "toy-background.toy-climate"}
    )
    assert content_hash(derived.path) == derived.hash
    con = again.connect()
    assert con.execute(
        'SELECT "climate-change" FROM "indicators/toy-climate" '
        "WHERE product_id = 'toy:fabric-production'"
    ).fetchone() == (pytest.approx(3.7086, rel=1e-9),)


def test_two_derived_builds_give_the_same_hash(
    toy_release: Release, toy_method: Method, toy_scores, tmp_path: Path
) -> None:
    ids, scores = toy_scores

    first = build_indicators(toy_release, toy_method, product_ids=ids, scores=scores, solver=SOLVER)
    second = build_indicators(
        toy_release,
        toy_method,
        product_ids=ids,
        scores=scores,
        into=tmp_path / "other",
        solver=SOLVER,
    )

    assert first.hash == second.hash


def test_a_method_on_another_flow_list_is_refused(
    toy_release: Release, toy_method: Method, toy_scores
) -> None:
    ids, scores = toy_scores
    method = Method.model_validate({**toy_method.model_dump(), "flow_list": "ecoinvent-3.12"})

    with pytest.raises(InvalidDataError) as raised:
        build_indicators(toy_release, method, product_ids=ids, scores=scores, solver=SOLVER)

    assert _problems(raised) == [("flow-list-mismatch", "method toy-climate")]


def test_scores_must_cover_exactly_the_parent_processes(
    toy_release: Release, toy_method: Method, toy_scores
) -> None:
    ids, scores = toy_scores
    ids = [*ids[1:], "toy:tshirt"]

    with pytest.raises(InvalidDataError) as raised:
        build_indicators(toy_release, toy_method, product_ids=ids, scores=scores, solver=SOLVER)

    assert _problems(raised) == [
        ("missing-score", "process toy:electricity-production"),
        ("unknown-process", "process toy:tshirt"),
    ]


@pytest.mark.parametrize(
    ("scores", "code"),
    [
        ({"climate-change": np.array([0.5298, np.nan, 1.2])}, "not-finite"),
        ({"climate-change": np.array([1, 2, 3])}, "not-float64"),
        ({"climate-change": np.array([0.5298, 3.7086])}, "wrong-length"),
        ({"climate": np.array([0.5298, 3.7086, 1.2])}, "indicator-mismatch"),
    ],
)
def test_bad_scores_are_refused(
    toy_release: Release, toy_method: Method, toy_scores, scores, code: str
) -> None:
    ids, _ = toy_scores

    with pytest.raises(InvalidDataError) as raised:
        build_indicators(toy_release, toy_method, product_ids=ids, scores=scores, solver=SOLVER)

    assert [p.code for p in raised.value.problems] == [code]


def test_a_derived_release_cannot_be_a_parent(
    toy_release: Release, toy_method: Method, toy_scores
) -> None:
    ids, scores = toy_scores
    derived = build_indicators(
        toy_release, toy_method, product_ids=ids, scores=scores, solver=SOLVER
    )

    with pytest.raises(InvalidDataError) as raised:
        build_indicators(derived, toy_method, product_ids=ids, scores=scores, solver=SOLVER)

    assert [p.code for p in raised.value.problems] == ["not-a-source-release"]


def test_method_hash_ignores_order_but_not_content(toy_method: Method) -> None:
    reordered = Method.model_validate(
        {**toy_method.model_dump(), "factors": list(reversed(toy_method.factors))}
    )
    changed = Method.model_validate(
        {
            **toy_method.model_dump(),
            "factors": [
                Factor(indicator="climate-change", flow_id="toy:co2", factor=1.0),
                Factor(indicator="climate-change", flow_id="toy:ch4", factor=27.0),
            ],
        }
    )
    other_version = Method.model_validate({**toy_method.model_dump(), "version": "2"})

    assert method_hash(reordered) == method_hash(toy_method)
    assert method_hash(changed) != method_hash(toy_method)
    assert method_hash(other_version) != method_hash(toy_method)


GWP = "global warming potential (GWP100)"


def _with_category_indicator(method: Method) -> Method:
    data = method.model_dump()
    indicators = [{**i, "category_indicator": GWP} for i in data["indicators"]]
    return Method.model_validate({**data, "indicators": indicators})


def test_a_category_indicator_counts_in_the_hash_only_when_given(toy_method: Method) -> None:
    named = _with_category_indicator(toy_method)

    assert "category_indicator" not in toy_method.indicators[0].model_dump(mode="json")
    assert named.indicators[0].model_dump(mode="json")["category_indicator"] == GWP
    assert method_hash(named) != method_hash(toy_method)


def test_a_source_release_keeps_a_category_indicator_and_verifies(
    records: Records, toy_method: Method, tmp_path: Path
) -> None:
    named = _with_category_indicator(toy_method)

    built = replace(records, methods=(named,)).build(tmp_path / "releases")
    release = Release.open(built.path)
    release.verify()

    [entry] = release.method_entries
    assert [i.category_indicator for i in entry.indicators] == [GWP]
    assert entry.hash == method_hash(named)
    assert release.methods()[0].indicators == named.indicators


def test_provenance_must_be_plain_json(
    toy_release: Release, toy_method: Method, toy_scores
) -> None:
    ids, scores = toy_scores

    with pytest.raises(InvalidDataError) as raised:
        build_indicators(
            toy_release,
            toy_method,
            product_ids=ids,
            scores=scores,
            solver=SOLVER,
            provenance={"residual": float("nan")},
        )

    assert [p.code for p in raised.value.problems] == ["bad-provenance"]


def test_an_indicator_called_product_id_is_refused(
    toy_release: Release, toy_method: Method, toy_scores
) -> None:
    ids, scores = toy_scores
    data = toy_method.model_dump()
    data["indicators"] = [{**data["indicators"][0], "id": "product_id"}]
    data["factors"] = [{**f, "indicator": "product_id"} for f in data["factors"]]
    method = Method.model_validate(data)

    with pytest.raises(InvalidDataError) as raised:
        build_indicators(
            toy_release,
            method,
            product_ids=ids,
            scores={"product_id": scores["climate-change"]},
            solver=SOLVER,
        )

    assert [p.code for p in raised.value.problems] == ["bad-indicator-id"]


def test_a_bad_release_name_is_refused(
    toy_release: Release, toy_method: Method, toy_scores
) -> None:
    ids, scores = toy_scores

    with pytest.raises(InvalidDataError) as raised:
        build_indicators(
            toy_release, toy_method, product_ids=ids, scores=scores, name="a/b", solver=SOLVER
        )

    assert [p.code for p in raised.value.problems] == ["bad-name"]
