"""What a result and a background keep, so that the next call does not build it again."""

from pathlib import Path

import pytest
from scipy import sparse

import headwater as hw
from headwater.model import Catalog
from headwater.query._internals import internals
from tests.conftest import open_database

TOY = Path(__file__).resolve().parent.parent / "fixtures" / "toy"


def tshirt() -> hw.Database:
    return open_database(TOY / "tshirt" / "tshirt")


def test_the_stacked_matrix_is_built_once_per_result(
    background: hw.Background, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = tshirt().calc(background, "tshirt:tshirt")
    built: list[object] = []
    real = sparse.block_array

    def counting(*args, **kwargs):
        built.append(args)
        return real(*args, **kwargs)

    monkeypatch.setattr(sparse, "block_array", counting)

    result.contributions()
    result.sensitivity()
    result.contributions(min_share=0.5)
    result.sensitivity(top=2)

    assert len(built) == 1


def test_a_second_result_builds_its_own_stacked_matrix(
    background: hw.Background, monkeypatch: pytest.MonkeyPatch
) -> None:
    built: list[object] = []
    real = sparse.block_array
    monkeypatch.setattr(sparse, "block_array", lambda *a, **k: (built.append(a), real(*a, **k))[1])

    for _ in range(2):
        tshirt().calc(background, "tshirt:tshirt").contributions()

    assert len(built) == 2


def test_a_background_keeps_its_id_index(background: hw.Background) -> None:
    ids = internals(background).solved.ids

    assert dict(internals(background).index) == {pid: i for i, pid in enumerate(ids)}
    assert internals(background).index is internals(background).index


def test_calc_does_not_rebuild_the_id_index_or_a_catalog_of_the_background(
    background: hw.Background, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`Study.calc` builds one catalog, of what the study names, for its checks and its
    matrices, and takes the dict of every id from the background: never a catalog of every
    flow of the background, and not the id index again."""
    study = tshirt()
    first = study.calc(background, "tshirt:tshirt")
    index = internals(background).index
    sizes: list[int] = []
    real = Catalog.__init__

    def counting(self, flows, *args, **kwargs):
        flows = list(flows)
        sizes.append(len(flows))
        real(self, flows, *args, **kwargs)

    monkeypatch.setattr(Catalog, "__init__", counting)

    again = [study.calc(background, "tshirt:tshirt") for _ in range(2)]

    assert sizes == [3, 3]  # the T-shirt, fabric and electricity: one catalog per calc
    assert internals(background).index is index
    assert [r.score for r in again] == [first.score] * 2
