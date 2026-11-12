"""A normalisation and weighting set on EF v3.1 and ecoinvent 3.12 changes no method and no release.

Runs only when these are set:

- `ECOINVENT_LCIA`: the workbook, for example `.../LCIA Implementation 3.12.xlsx`
- `ECOINVENT_RELEASE`: the release of the same version, a folder or `name@hash` in
  HEADWATER_HOME, for example `ecoinvent-3.12-cutoff@b9428cdd8045`

The set is synthetic: every normalisation and weight is 1, so its single score is the plain sum
of EF's 16 categories, with their units mixed. It is no EF single score: JRC's factors are not
here. It checks the hashes and that the set's scores are the sums of EF's on every product. No
value of the licensed files is written here.
"""

import os
from pathlib import Path

import numpy as np
import pytest

import headwater as hw
from headwater.io.lcia import import_method, read_workbook, write_method
from headwater.io.nwset import make_set, write_set
from headwater.model import NwRow
from headwater.query._internals import internals
from headwater.release import Release, find, method_hash

ECOINVENT_LCIA = os.environ.get("ECOINVENT_LCIA")
ECOINVENT_RELEASE = os.environ.get("ECOINVENT_RELEASE")

pytestmark = pytest.mark.skipif(
    not (ECOINVENT_LCIA and ECOINVENT_RELEASE),
    reason="ECOINVENT_LCIA and ECOINVENT_RELEASE are not set",
)

RELEASE_3_12 = "b9428cdd8045"
EF_3_1 = "dbb0de237f25"
"""The hashes of the 3.12 release and of EF v3.1 mapped to it (tests/integration/
test_import_hashes.py)."""


@pytest.fixture(scope="module")
def release() -> Release:
    assert ECOINVENT_RELEASE
    folder = Path(ECOINVENT_RELEASE).expanduser()
    return Release.open(folder) if folder.is_dir() else find(ECOINVENT_RELEASE)


@pytest.fixture(scope="module")
def folders(release: Release, tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    """EF v3.1 as a method folder, and the synthetic set beside it."""
    assert ECOINVENT_LCIA
    root = tmp_path_factory.mktemp("methods")
    imported = import_method(read_workbook(ECOINVENT_LCIA, "EF v3.1"), release)
    method = write_method(imported, root / "EF-3.1")
    ids = [i.id for i in imported.method.indicators]
    # A category's sub-indicators extend its id: climate-change-fossil.
    categories = [i for i in ids if not any(i.startswith(f"{other}-") for other in ids)]
    rows = [NwRow(indicator=i, normalisation=1.0, weight=1.0) for i in categories]
    nw_set, _ = make_set(
        rows,
        imported.method,
        id="EF-3.1-synthetic",
        version="0",
        unit="Pt",
        source="Synthetic: every factor 1, for the hash checks only",
    )
    return method, write_set(nw_set, root / "EF-3.1-synthetic")


def test_a_set_on_ef_changes_no_method_and_no_release(
    release: Release, folders: tuple[Path, Path], tmp_path: Path
) -> None:
    method, nw_set = folders
    files = {p.name: p.read_bytes() for p in method.iterdir()}

    bg = hw.load(release.path, method=method, sets=nw_set)
    written = bg.write_indicators(into=tmp_path)

    assert release.hash.startswith(RELEASE_3_12)
    assert internals(bg).method_hash.startswith(EF_3_1)
    assert method_hash(bg.method).startswith(EF_3_1)
    assert {p.name: p.read_bytes() for p in method.iterdir()} == files
    assert [m.hash[:12] for m in written.method_entries] == [EF_3_1]
    columns = written.table("indicators/EF-3.1").column_names
    assert columns == ["product_id", *sorted(i.id for i in bg.method.indicators)]
    print(f"\nindicators release with the set loaded: {written.name}@{written.hash[:12]}")


def test_the_single_score_is_the_sum_of_the_categories_on_every_product(
    release: Release, folders: tuple[Path, Path]
) -> None:
    method, nw_set = folders
    bg = hw.load(release.path, method=method, sets=nw_set)
    single = "EF-3.1-synthetic:single-score"
    terms = internals(bg)._derived[single].terms  # pyright: ignore[reportPrivateUsage]
    x = internals(bg).solved.x

    expected = sum(k * x[i].values for i, k in terms)
    total = sum(abs(k) * np.abs(x[i].values) for i, k in terms)

    assert len(terms) == 16
    assert np.all(np.abs(x[single].values - expected) <= 1e-12 * total)
    assert x[single].residual <= internals(bg).solved.factorization.settings.tolerance
    product = internals(bg).solved.ids[0]
    root = bg.contributions(product, indicator=single, min_share=0.0, max_depth=1)
    assert root.direct + sum(c.score for c in root.children) + root.rest == pytest.approx(
        root.score, rel=1e-9
    )
