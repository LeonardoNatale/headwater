"""An indicators release is named by its recipe, not by its scores.

Two machines solving the same parent with the same method, engine and solver settings get scores
that differ in the last bits. They must get the same release. The scores are still checked: each
file's sha256 when the release is opened, and the rows' digest by `verify()`.
"""

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import headwater.release.write as write_module
from headwater.calc import SolverSettings, Step
from headwater.model import InvalidDataError, Method
from headwater.release import Release, build_indicators, content_hash
from headwater.release.indicators import write_indicators

SOLVER = {
    "solver": "scipy.sparse.linalg.splu",
    "ordering": "MMD_AT_PLUS_A",
    "scaling": "rows, then columns, so the largest entry of each is 1",
    "tolerance": 1e-12,
    "condition_limit": 1e10,
}
"""The solver settings an indicators release is computed with: part of its recipe."""


def _problems(error: pytest.ExceptionInfo[InvalidDataError]) -> list[tuple[str, str]]:
    return [(p.code, p.item) for p in error.value.problems]


def _nudged(scores: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """Every score moved by one unit in the last place, as another machine's solver could."""
    return {k: np.nextafter(v, np.inf) for k, v in scores.items()}


def test_scores_moved_in_the_last_bit_give_the_same_release(
    toy_release: Release, toy_method: Method, toy_scores, tmp_path: Path
) -> None:
    ids, scores = toy_scores
    nudged = _nudged(scores)
    assert all((nudged[k] != scores[k]).all() for k in scores)

    first = build_indicators(
        toy_release, toy_method, product_ids=ids, scores=scores, solver=SOLVER, into=tmp_path / "a"
    )
    second = build_indicators(
        toy_release, toy_method, product_ids=ids, scores=nudged, solver=SOLVER, into=tmp_path / "b"
    )

    assert second.hash == first.hash
    assert second.path.name == first.path.name
    table = "indicators/toy-climate.parquet"
    assert second.manifest.tables[table] != first.manifest.tables[table]
    assert second.manifest.files[table] != first.manifest.files[table]
    second.verify()
    assert content_hash(second.path) == first.hash


def test_the_recipe_is_the_parent_the_method_the_engine_and_the_solver(
    toy_release: Release, toy_method: Method, toy_scores
) -> None:
    ids, scores = toy_scores

    derived = build_indicators(
        toy_release, toy_method, product_ids=ids, scores=scores, solver=SOLVER
    )

    manifest = json.loads((derived.path / "release.json").read_text())
    assert manifest["recipe"] == {"engine_version": manifest["engine_version"], "solver": SOLVER}
    assert manifest["parent"] == {"name": toy_release.name, "hash": toy_release.hash}
    assert manifest["methods"][0]["hash"] == derived.method_entries[0].hash
    assert derived.recipe == manifest["recipe"]


def test_provenance_is_recorded_but_not_part_of_the_recipe(
    toy_release: Release, toy_method: Method, toy_scores, tmp_path: Path
) -> None:
    ids, scores = toy_scores

    first = build_indicators(
        toy_release,
        toy_method,
        product_ids=ids,
        scores=scores,
        solver=SOLVER,
        provenance={"residual": 1e-16, "steps": ["lu"]},
        into=tmp_path / "a",
    )
    second = build_indicators(
        toy_release,
        toy_method,
        product_ids=ids,
        scores=scores,
        solver=SOLVER,
        provenance={"residual": 3e-15, "steps": ["lu", "pivoted_lu", "refined"]},
        into=tmp_path / "b",
    )

    assert second.hash == first.hash
    assert second.manifest.provenance == {
        "residual": 3e-15,
        "steps": ["lu", "pivoted_lu", "refined"],
    }


@pytest.mark.parametrize(
    "change",
    [
        {"tolerance": 1e-10},
        {"ordering": "COLAMD"},
        {"condition_limit": 1e12},
    ],
)
def test_other_solver_settings_give_another_release(
    toy_release: Release, toy_method: Method, toy_scores, change: dict[str, float | str]
) -> None:
    ids, scores = toy_scores

    first = build_indicators(toy_release, toy_method, product_ids=ids, scores=scores, solver=SOLVER)
    other = build_indicators(
        toy_release, toy_method, product_ids=ids, scores=scores, solver={**SOLVER, **change}
    )

    assert other.hash != first.hash


def test_another_engine_version_gives_another_release(
    toy_release: Release, toy_method: Method, toy_scores, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids, scores = toy_scores
    first = build_indicators(toy_release, toy_method, product_ids=ids, scores=scores, solver=SOLVER)

    monkeypatch.setattr(write_module, "version", lambda _: "9.9.9")
    other = build_indicators(toy_release, toy_method, product_ids=ids, scores=scores, solver=SOLVER)

    assert other.hash != first.hash
    assert other.recipe == {"engine_version": "9.9.9", "solver": SOLVER}


def test_another_parent_or_method_gives_another_release(
    records, toy_release: Release, toy_method: Method, toy_scores, tmp_path: Path
) -> None:
    ids, scores = toy_scores
    first = build_indicators(toy_release, toy_method, product_ids=ids, scores=scores, solver=SOLVER)
    other_parent = records.with_line("toy:fabric-production", "e02", amount=2.5).build(
        tmp_path / "other"
    )
    other_method = Method.model_validate({**toy_method.model_dump(), "version": "2"})

    by_parent = build_indicators(
        other_parent, toy_method, product_ids=ids, scores=scores, solver=SOLVER
    )
    by_method = build_indicators(
        toy_release, other_method, product_ids=ids, scores=scores, solver=SOLVER
    )

    assert len({first.hash, by_parent.hash, by_method.hash}) == 3


def test_rebuilding_a_recipe_keeps_the_release_when_the_scores_agree(
    toy_release: Release, toy_method: Method, toy_scores, snapshot
) -> None:
    ids, scores = toy_scores
    first = build_indicators(toy_release, toy_method, product_ids=ids, scores=scores, solver=SOLVER)
    before = snapshot(first.path)

    again = build_indicators(
        toy_release, toy_method, product_ids=ids, scores=_nudged(scores), solver=SOLVER
    )

    assert again.path == first.path
    assert snapshot(first.path) == before


def test_rebuilding_a_recipe_with_other_scores_is_refused(
    toy_release: Release, toy_method: Method, toy_scores
) -> None:
    # Same parent, method, engine version and solver, but another number: the engine changed
    # without a new version, and the release on disk would silently win.
    ids, scores = toy_scores
    build_indicators(toy_release, toy_method, product_ids=ids, scores=scores, solver=SOLVER)
    changed = {k: v.copy() for k, v in scores.items()}
    changed["climate-change"][1] *= 1 + 1e-6

    with pytest.raises(InvalidDataError) as raised:
        build_indicators(toy_release, toy_method, product_ids=ids, scores=changed, solver=SOLVER)

    assert _problems(raised) == [("recipe-conflict", "indicator climate-change")]
    assert "toy:fabric-production" in str(raised.value)


def test_verify_catches_a_changed_score_whose_sha256_was_updated(
    toy_release: Release, toy_method: Method, toy_scores
) -> None:
    ids, scores = toy_scores
    derived = build_indicators(
        toy_release, toy_method, product_ids=ids, scores=scores, solver=SOLVER
    )
    name = "indicators/toy-climate.parquet"
    path = derived.path / name
    table = pq.read_table(path)
    doubled = pa.array(table.column("climate-change").to_numpy() * 2, pa.float64())
    pq.write_table(table.set_column(1, "climate-change", doubled), path)
    manifest_path = derived.path / "release.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files"][name] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest))

    opened = Release.open(derived.path)  # the recipe and every sha256 still agree
    with pytest.raises(InvalidDataError) as raised:
        opened.verify()

    assert _problems(raised) == [("changed-content", name)]


def _write(
    parent: Release,
    method: Method,
    ids: list[str],
    x: np.ndarray,
    settings: SolverSettings,
    steps: tuple[Step, ...],
    residual: float,
    into: Path,
) -> Release:
    return write_indicators(
        parent,
        method,
        product_ids=ids,
        x={"climate-change": x},
        reference_sign=np.array([1.0, 1.0, -1.0]),
        solver=settings,
        steps={"climate-change": steps},
        residual=residual,
        exact_zeros={"climate-change": 0},
        into=into,
    )


def test_a_pivoted_fallback_is_provenance_not_recipe(
    toy_release: Release, toy_method: Method, toy_scores, tmp_path: Path
) -> None:
    # One machine solves without pivoting; another falls back to pivoted factors after its
    # residual check. They asked for the same solver, so they make the same release.
    ids, scores = toy_scores
    x = scores["climate-change"] * np.array([1.0, 1.0, -1.0])
    plain = SolverSettings(
        solver=SOLVER["solver"],
        ordering=SOLVER["ordering"],
        diag_pivot_thresh=0.0,
        scaling=SOLVER["scaling"],
        tolerance=SOLVER["tolerance"],
        condition_limit=SOLVER["condition_limit"],
    )
    pivoted = replace(plain, diag_pivot_thresh=1.0)

    first = _write(toy_release, toy_method, ids, x, plain, (Step.lu,), 1e-16, tmp_path / "a")
    second = _write(
        toy_release,
        toy_method,
        ids,
        np.nextafter(x, np.inf),
        pivoted,
        (Step.lu, Step.pivoted_lu, Step.refined),
        4e-15,
        tmp_path / "b",
    )

    assert second.hash == first.hash
    assert first.recipe == {"engine_version": first.manifest.engine_version, "solver": SOLVER}
    provenance = second.manifest.provenance
    assert provenance is not None
    assert provenance["solver"] == {**SOLVER, "diag_pivot_thresh": 1.0}
    assert provenance["steps"] == {"climate-change": ["lu", "pivoted_lu", "refined"]}
    assert provenance["residual"] == 4e-15
