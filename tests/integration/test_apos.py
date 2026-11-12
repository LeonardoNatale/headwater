"""ecoinvent 3.12 APOS as its own release, against Brightway. Needs ECOINVENT_APOS_DIR.

`ECOINVENT_APOS_DIR` is the APOS folder that holds `datasets/` and `MasterData/`, for example
`/path/to/ecoinvent/3.12-apos`.

- A1 Every file reads and every line links: nothing is excluded and nothing is a data gap. The
  193,006 lines sent to a treatment are Brightway's negative technosphere inputs: 178,279 Waste,
  13,909 Recyclable and 818 allocatable.
- A2 Two imports of the same folder give the same release hash.
- A3 Its flows are cut-off's, but for the 88 it treats, which are wastes here. Methods hold
  elementary flows only, so a method mapped onto the cut-off release maps onto it as it is.
  Needs `ECOINVENT_DIR`, the cut-off folder. With `ECOINVENT_LCIA`, the LCIA workbook, EF v3.1
  maps onto it to the very method it maps to on cut-off.
- A4 Brightway's matrices equal headwater's cell by cell, both rounded to float32 as Brightway
  stores them, and the same matrices solved by headwater's calc give scores within 1e-9 (D21).
  Reads `$HEADWATER_WORK/reference-3.12-apos/`, written by
  `bench/bw_reference.py --system-model apos`, and is skipped when it is missing.

EF 3.1 against ecoinvent's APOS cumulative LCIA is `tests/integration/test_method_import.py`, run
with `ECOINVENT_RELEASE` set to the APOS release and `ECOINVENT_CUMULATIVE_LCIA` to its file.
No test imports Brightway. Run with `-s` to see counts and differences, never values.
"""

import json
import os
import time
from collections import Counter
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow.parquet as pq
import pytest
from numpy.typing import NDArray
from scipy import sparse

from headwater.calc import factorize, scores
from headwater.calc.solve import structural_zeros
from headwater.io.ecospold2 import read_ecospold2
from headwater.io.ecospold2.masterdata import read_masterdata
from headwater.io.formats import method_from
from headwater.io.inventory import Inventory
from headwater.io.source import MethodOptions
from headwater.model import FlowKind
from headwater.query._release import _BIOSPHERE, _TECHNOSPHERE, _Query
from headwater.release import Release
from headwater.release.content import method_hash

ECOINVENT_APOS_DIR = os.environ.get("ECOINVENT_APOS_DIR")
ECOINVENT_DIR = os.environ.get("ECOINVENT_DIR")
ECOINVENT_LCIA = os.environ.get("ECOINVENT_LCIA")
HEADWATER_WORK = os.environ.get("HEADWATER_WORK")

pytestmark = pytest.mark.skipif(not ECOINVENT_APOS_DIR, reason="ECOINVENT_APOS_DIR is not set")

VERSION = "3.12"
WORKERS = 2
FACTORS = {"Carbon dioxide, fossil": 1.0, "Methane, fossil": 29.8, "Dinitrogen monoxide": 273.0}
"""The 3-flow climate method of docs/appendix/benchmarks.md, as bench/bw_reference.py writes it."""

EF_3_1 = "dbb0de237f259caa43f9249d30e4dfacf134c97c387da2a6172ba7391863752e"
"""EF v3.1 mapped onto the cut-off release (tests/integration/test_import_hashes.py)."""

TREATED_FLOWS = 88
"""Recyclable (57) and allocatable (31) flows that an APOS dataset treats: a negative reference."""

RELATIVE = 1e-9
"""A4: largest relative difference between two accurate solves of the same matrices."""

type Floats = NDArray[np.float64]
type Indices = NDArray[np.int64]


@pytest.fixture(scope="module")
def source() -> Inventory:
    assert ECOINVENT_APOS_DIR
    start = time.perf_counter()
    found = read_ecospold2(Path(ECOINVENT_APOS_DIR), version=VERSION, workers=WORKERS)
    print(f"\nread {len(found.processes):,} datasets in {time.perf_counter() - start:.1f} s")
    return found


@pytest.fixture(scope="module")
def release(source: Inventory, tmp_path_factory: pytest.TempPathFactory) -> Release:
    return source.freeze(into=tmp_path_factory.mktemp("first"))


# --- A1, A2, A3 -----------------------------------------------------------------------------


def test_a1_every_file_reads_and_every_line_links(source: Inventory, release: Release) -> None:
    roles = Counter(e.role.value for p in source.processes for e in p.exchanges)
    print(f"\nA1 {release.path.name}: {len(source.processes):,} datasets, roles {dict(roles)}")

    assert len(source.processes) == 27_197
    assert roles == {
        "reference": 27_197,
        "input": 726_507,
        "own_use": 855,
        "waste_to_treatment": 193_006,
        "emission": 719_193,
        "resource": 67_810,
    }
    assert release.info.system_model == "apos"
    assert release.name == f"ecoinvent-{VERSION}-apos"
    assert all(p.id.startswith(f"ecoinvent:{VERSION}:apos:") for p in source.processes)
    assert (source.issues, source.problems) == ((), ())


def test_a2_importing_twice_gives_the_same_release_hash(
    release: Release, tmp_path_factory: pytest.TempPathFactory
) -> None:
    assert ECOINVENT_APOS_DIR
    again = read_ecospold2(Path(ECOINVENT_APOS_DIR), version=VERSION, workers=WORKERS)
    second = again.freeze(into=tmp_path_factory.mktemp("second"))
    print(f"\nA2: {release.path.name} and {second.path.name}")

    assert second.hash == release.hash


@pytest.mark.skipif(not ECOINVENT_DIR, reason="ECOINVENT_DIR is not set")
def test_a3_the_flows_are_cut_off_s_but_the_treated_ones_are_wastes(source: Inventory) -> None:
    # MasterData is the same file in both folders. APOS treats 88 Recyclable and allocatable
    # flows, which are wastes in its release. A method holds elementary flows only, so one
    # mapped onto the cut-off release maps onto this one as it is.
    assert ECOINVENT_DIR
    cutoff = {f.id: f for f in read_masterdata(Path(ECOINVENT_DIR) / "MasterData").flows}
    apos = {f.id: f for f in source.flows}
    differ = sorted(i for i in apos if apos[i] != cutoff.get(i))
    print(f"\nA3: {len(apos):,} flows, {len(differ)} of another kind than in cut-off")

    assert source.info.flow_list == f"ecoinvent-{VERSION}"
    assert set(apos) == set(cutoff)
    assert len(differ) == TREATED_FLOWS
    for i in differ:
        assert cutoff[i].kind is FlowKind.product
        assert apos[i] == cutoff[i].model_copy(update={"kind": FlowKind.waste})
    assert set(source.flow_properties) == set(
        read_masterdata(Path(ECOINVENT_DIR) / "MasterData").flow_properties
    )


@pytest.mark.skipif(not ECOINVENT_LCIA, reason="ECOINVENT_LCIA is not set")
def test_a3_ef_3_1_maps_onto_it_as_onto_cut_off(release: Release) -> None:
    assert ECOINVENT_LCIA
    imported = method_from(Path(ECOINVENT_LCIA), release, MethodOptions(method="EF v3.1"))

    assert method_hash(imported.method) == EF_3_1


# --- A4 parity with Brightway ---------------------------------------------------------------


@dataclass(frozen=True)
class Lines:
    """headwater's matrix entries line by line, before cells holding two lines are summed."""

    ids: tuple[str, ...]
    flows: tuple[str, ...]
    a: tuple[Indices, Indices, Floats]
    b: tuple[Indices, Indices, Floats]


@pytest.fixture(scope="module")
def lines(release: Release) -> Lines:
    # The same queries that build A and B in assemble_release, kept line by line.
    with closing(_Query(release, check=False)) as query:
        index = query.index()
        a = query.arrays(_TECHNOSPHERE, ("row", "col", "value"))
        flows = query.flows()
        b = query.arrays(_BIOSPHERE, ("row", "col", "value"))

    def columns(found: dict[str, NDArray[Any]]) -> tuple[Indices, Indices, Floats]:
        return (
            found["row"].astype(np.int64),
            found["col"].astype(np.int64),
            found["value"].astype(np.float64),
        )

    return Lines(index.ids, flows, columns(a), columns(b))


@pytest.fixture(scope="module")
def reference() -> dict[str, dict[str, list[Any]]]:
    folder = Path(HEADWATER_WORK or "") / f"reference-{VERSION}-apos"
    if not HEADWATER_WORK or not (folder / "reference.json").is_file():
        pytest.skip(f"no Brightway reference in {folder}: run bench/bw_reference.py")
    info = json.loads((folder / "reference.json").read_text())
    found = (info.get("ecoinvent_version"), info.get("system_model"))
    assert found == (VERSION, "apos"), f"the reference in {folder} is for {found}"
    print(
        f"\nBrightway dropped {info['dropped_exchanges']} and left "
        f"{info['unlinked_exchanges']} unlinked"
    )
    assert info["unlinked_exchanges"] == 0
    assert set(info["dropped_exchanges"].values()) == {0}
    names = ("technosphere_lines", "biosphere", "method")
    return {n: pq.read_table(folder / f"{n}.parquet").to_pydict() for n in names}


def _position(ids: tuple[str, ...], wanted: list[Any]) -> Indices:
    index = {id_: i for i, id_ in enumerate(ids)}
    return np.array([index[w] for w in wanted], dtype=np.int64)


def _matrix(
    rows: Indices, cols: Indices, values: Floats, shape: tuple[int, int]
) -> sparse.csc_array:
    """Lines summed into cells as bw2calc does: each line rounded to float32 and the cells
    summed in float32. Returned in float64."""
    matrix = sparse.csc_array(
        (values.astype(np.float32), (rows, cols)), shape=shape, dtype=np.float32
    )
    matrix.sum_duplicates()
    matrix.eliminate_zeros()
    return sparse.csc_array(matrix, dtype=np.float64)


def _cells(left: sparse.csc_array, right: sparse.csc_array) -> tuple[int, int, int]:
    """Cells only in left, only in right, and in both with different values."""

    def keys(matrix: sparse.csc_array) -> tuple[Indices, Floats]:
        coo = matrix.tocoo()
        return coo.row.astype(np.int64) * matrix.shape[1] + coo.col.astype(np.int64), coo.data

    lk, lv = keys(left)
    rk, rv = keys(right)
    both, li, ri = np.intersect1d(lk, rk, assume_unique=True, return_indices=True)
    return lk.size - both.size, rk.size - both.size, int(np.count_nonzero(lv[li] != rv[ri]))


def _bw_A(reference: dict[str, dict[str, list[Any]]], lines: Lines) -> sparse.csc_array:
    """Brightway's processed technosphere lines, signed: a flipped line is an input."""
    t = reference["technosphere_lines"]
    values = np.array(t["amount"], dtype=np.float64) * np.where(t["flip"], -1.0, 1.0)
    rows, cols = _position(lines.ids, t["row_id"]), _position(lines.ids, t["col_id"])
    return _matrix(rows, cols, values, (len(lines.ids), len(lines.ids)))


def _bw_B(
    reference: dict[str, dict[str, list[Any]]], lines: Lines, flows: tuple[str, ...]
) -> sparse.csc_array:
    b = reference["biosphere"]
    rows, cols = _position(flows, b["flow_id"]), _position(lines.ids, b["col_id"])
    values = np.array(b["value"], dtype=np.float64)
    return _matrix(rows, cols, values, (len(flows), len(lines.ids)))


def _ours_A(lines: Lines) -> sparse.csc_array:
    return _matrix(*lines.a, (len(lines.ids), len(lines.ids)))


def _ours_B(lines: Lines, flows: tuple[str, ...]) -> sparse.csc_array:
    rows, cols, values = lines.b
    moved = _position(flows, list(lines.flows))[rows]
    return _matrix(moved, cols, values, (len(flows), len(lines.ids)))


def test_a4_technosphere_equals_brightway(
    reference: dict[str, dict[str, list[Any]]], lines: Lines
) -> None:
    bw, ours = _bw_A(reference, lines), _ours_A(lines)

    only_bw, only_ours, differ = _cells(bw, ours)
    print(
        f"\nA4 technosphere: Brightway {len(reference['technosphere_lines']['amount']):,} lines "
        f"in {bw.nnz:,} cells, headwater {len(lines.a[0]):,} lines in {ours.nnz:,} cells: only "
        f"in Brightway {only_bw}, only in headwater {only_ours}, values differing {differ}"
    )
    assert set(reference["technosphere_lines"]["col_id"]) == set(lines.ids)
    assert (only_bw, only_ours, differ) == (0, 0, 0)


def test_a4_biosphere_equals_brightway(
    reference: dict[str, dict[str, list[Any]]], lines: Lines
) -> None:
    flows = tuple(sorted(set(reference["biosphere"]["flow_id"]) | set(lines.flows)))
    bw, ours = _bw_B(reference, lines, flows), _ours_B(lines, flows)

    only_bw, only_ours, differ = _cells(bw, ours)
    print(
        f"\nA4 biosphere: Brightway {bw.nnz:,} cells, headwater {ours.nnz:,}: only in "
        f"Brightway {only_bw}, only in headwater {only_ours}, values differing {differ}"
    )
    assert (only_bw, only_ours, differ) == (0, 0, 0)


def test_a4_scores_of_the_same_matrices_agree(
    reference: dict[str, dict[str, list[Any]]], lines: Lines
) -> None:
    # headwater's calc solves Brightway's matrices and headwater's, both rounded as Brightway
    # stores them, for the 3-flow climate method. Products with no burden anywhere upstream are
    # exactly 0 on both sides.
    method = reference["method"]
    factors = dict(zip(method["flow_id"], method["factor"], strict=True))
    flows = tuple(sorted(set(reference["biosphere"]["flow_id"]) | set(lines.flows)))
    c = np.array([factors.get(f, 0.0) for f in flows])
    bw_A, ours_A = _bw_A(reference, lines), _ours_A(lines)
    bw_h = np.asarray(_bw_B(reference, lines, flows).T @ c)
    ours_h = np.asarray(_ours_B(lines, flows).T @ c)

    on_bw = scores(factorize(bw_A), bw_h)
    on_ours = scores(factorize(ours_A), ours_h)
    zero = structural_zeros(abs(ours_A).T, ours_h)
    size = np.maximum(np.abs(on_bw.values), np.abs(on_ours.values))
    rel = np.zeros_like(size)
    np.divide(np.abs(on_bw.values - on_ours.values), size, out=rel, where=size > 0)
    print(
        f"\nA4 scores: {len(lines.ids):,} products, exact zeros {int(zero.sum()):,}, largest "
        f"relative difference {rel.max():.2g}, above 1e-12 {(rel > 1e-12).sum():,}, residuals "
        f"{on_bw.residual:.2g} and {on_ours.residual:.2g}"
    )
    assert len(factors) == 13
    assert np.array_equal(zero, structural_zeros(abs(bw_A).T, bw_h))
    assert rel.max() <= RELATIVE
