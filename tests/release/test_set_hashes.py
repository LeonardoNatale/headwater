"""A normalisation and weighting set changes no release: the toy release with toy-impacts, and the
indicators release written from it, have the same hashes and files whether a set is loaded or
not. The set is read from its own folder, and its scores are never written."""

import json
from pathlib import Path

import pytest

import headwater as hw
from headwater.query._internals import internals
from headwater.release import ReleaseInfo, build
from headwater.study import read_method, read_process_folder

TOY = Path(__file__).resolve().parent.parent / "fixtures" / "toy"
SET = TOY / "methods" / "toy-nw"

INFO = ReleaseInfo(
    name="toy-background",
    source="toy",
    version="1",
    system_model="cutoff",
    licence="CC0-1.0",
    flow_list="toy",
)


@pytest.fixture
def release_path(tmp_path: Path) -> Path:
    folder = read_process_folder(TOY / "background", namespace="toy")
    release = build(
        INFO,
        processes=folder.processes,
        flows=folder.flows,
        flow_properties=folder.flow_properties,
        methods=(read_method(TOY / "methods" / "toy-impacts"),),
        into=tmp_path / "releases",
    )
    return release.path


def test_a_set_used_with_a_releases_method_changes_no_hash(
    release_path: Path, tmp_path: Path
) -> None:
    files = sorted(p.relative_to(release_path) for p in release_path.rglob("*"))
    plain = hw.load(release_path, method="toy-impacts")
    with_set = hw.load(release_path, method="toy-impacts", sets=SET)

    without = plain.write_indicators(into=tmp_path / "without")
    written = with_set.write_indicators(into=tmp_path / "with")

    assert written.hash == without.hash
    assert (
        json.loads((written.path / "release.json").read_text())["files"]
        == json.loads((without.path / "release.json").read_text())["files"]
    )
    assert sorted(p.relative_to(release_path) for p in release_path.rglob("*")) == files
    assert internals(with_set).releases == internals(plain).releases
    assert internals(with_set).method_hash == internals(plain).method_hash
    assert internals(with_set).fingerprint == internals(plain).fingerprint


def test_the_set_scores_on_the_release_as_on_the_folder(release_path: Path) -> None:
    on_release = hw.load(release_path, method="toy-impacts", sets=SET)
    on_folder = hw.load(
        TOY / "background", method=TOY / "methods" / "toy-impacts", namespace="toy", sets=SET
    )

    for product in ("toy:fabric-production", "toy:electricity-production"):
        a = on_release.contributions(product, indicator="toy-nw:single-score").score
        b = on_folder.contributions(product, indicator="toy-nw:single-score").score
        assert a == pytest.approx(b, rel=1e-12)
