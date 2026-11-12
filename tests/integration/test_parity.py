"""Parity with Brightway on ecoinvent 3.12 cut-off. Runs only when ECOINVENT_DIR is set.

`ECOINVENT_DIR` is the folder that holds `datasets/` and `MasterData/`, for example
`/path/to/ecoinvent/3.12-cutoff`.

- X1 (a) Brightway's matrices equal headwater's, entry by entry, rounded to float32 as Brightway
  stores them. The only difference allowed: the 2,426 recyclable lines, which headwater keeps
  without a supplier (no burden under the cut-off rule). Every line links in both.
- X1 (b) The same matrices, solved by headwater's calc, give the same scores within 1e-9.
- X1 (c) Information: bw2calc's own scores, the float64 release against float32, and each side's
  componentwise backward error.
- X2 The waste rule (positive kg sent x positive burden per kg treated) gives the full solve's
  score on every activity with waste inputs.
- X3 Two imports of the same folder give the same release hash.

X1 reads Brightway's matrices and scores from $HEADWATER_WORK/reference-3.12/, written by
bench/bw_reference.py, and is skipped when they are missing. No test imports Brightway. The
method is the 3-flow climate subset of docs/appendix/benchmarks.md, built from the release's
flows by name. Run with `-s` to see the summary: counts and statistics of differences only.
"""

import json
import os
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow.parquet as pq
import pytest
from numpy.typing import NDArray
from scipy import sparse

import headwater as hw
from headwater.calc import Step, factorize, scores
from headwater.calc.solve import backward_error, structural_zeros
from headwater.io.ecospold2 import read_ecospold2
from headwater.io.inventory import Inventory
from headwater.model import Role
from headwater.query._internals import internals
from headwater.query._release import _BIOSPHERE, _TECHNOSPHERE, _Query
from headwater.release import Release

ECOINVENT_DIR = os.environ.get("ECOINVENT_DIR")
HEADWATER_WORK = os.environ.get("HEADWATER_WORK")

pytestmark = pytest.mark.skipif(not ECOINVENT_DIR, reason="ECOINVENT_DIR is not set")

VERSION = "3.12"
FACTORS = {"Carbon dioxide, fossil": 1.0, "Methane, fossil": 29.8, "Dinitrogen monoxide": 273.0}

RELATIVE = 1e-9
"""X1 (b): largest relative difference between two accurate solves of the same matrices."""

type Floats = NDArray[np.float64]
type Indices = NDArray[np.int64]
type Columns = dict[str, list[Any]]


# --- headwater ------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def source() -> Inventory:
    assert ECOINVENT_DIR
    return read_ecospold2(Path(ECOINVENT_DIR), version=VERSION)


@pytest.fixture(scope="module")
def release(source: Inventory, tmp_path_factory: pytest.TempPathFactory) -> Release:
    return source.freeze(into=tmp_path_factory.mktemp("first"))


@pytest.fixture(scope="module")
def exchanges(release: Release) -> list[dict[str, Any]]:
    columns = ["process_id", "role", "flow_id", "amount", "supplier_id", "link_status"]
    return release.table("exchanges").select(columns).to_pylist()


@pytest.fixture(scope="module")
def products(release: Release) -> dict[str, str]:
    """The flow each process makes, or treats."""
    return {p["id"]: p["reference_flow_id"] for p in release.table("processes").to_pylist()}


@pytest.fixture(scope="module")
def method_folder(release: Release, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The 3-flow climate method, one factor per elementary flow with one of the names."""
    folder = tmp_path_factory.mktemp("method")
    flows = release.table("flows").to_pylist()
    rows = "".join(
        f"climate,{f['id']},,{FACTORS[f['name']]}\n"
        for f in flows
        if f["kind"] == "elementary" and f["name"] in FACTORS
    )
    (folder / "method.yaml").write_text(
        "id: climate-3-flows\n"
        'version: "1"\n'
        f"flow_list: {release.info.flow_list}\n"
        "indicators:\n"
        "  - id: climate\n"
        "    name: Climate change, 3 flows\n"
        "    unit: kg CO2-eq\n"
    )
    (folder / "factors.csv").write_text("indicator,flow,location,factor\n" + rows)
    return folder


@pytest.fixture(scope="module")
def bg(release: Release, method_folder: Path) -> hw.Background:
    start = time.perf_counter()
    loaded = hw.load(release.path, method=method_folder)
    print(f"\nhw.load: {time.perf_counter() - start:.1f} s")
    return loaded


@dataclass(frozen=True)
class Lines:
    """headwater's matrix entries line by line, before cells holding two lines are summed."""

    ids: tuple[str, ...]
    flows: tuple[str, ...]
    a: tuple[Indices, Indices, Floats]
    b: tuple[Indices, Indices, Floats]

    @property
    def n(self) -> int:
        return len(self.ids)

    def A(self, *, float32: bool = False) -> sparse.csc_array:
        return _matrix(*self.a, (self.n, self.n), float32=float32)

    def B(self, *, float32: bool = False) -> sparse.csc_array:
        return _matrix(*self.b, (len(self.flows), self.n), float32=float32)


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


def _matrix(
    rows: Indices,
    cols: Indices,
    values: Floats,
    shape: tuple[int, int],
    *,
    float32: bool = False,
) -> sparse.csc_array:
    """The lines summed into cells, in float64, or as bw2calc does it if `float32` is set: each
    line rounded to float32 and the cells summed in float32. Returned in float64."""
    dtype = np.float32 if float32 else np.float64
    matrix = sparse.csc_array((values.astype(dtype), (rows, cols)), shape=shape, dtype=dtype)
    matrix.sum_duplicates()
    matrix.eliminate_zeros()
    return sparse.csc_array(matrix, dtype=np.float64)


def _c(bg: hw.Background, flows: tuple[str, ...]) -> Floats:
    factors = {f.flow_id: f.factor for f in bg.method.factors}
    return np.array([factors.get(flow, 0.0) for flow in flows])


# --- Brightway ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Reference:
    """Brightway's matrices and scores, keyed by headwater ids."""

    technosphere: Columns
    lines: Columns
    biosphere: Columns
    method: dict[str, float]
    scores: dict[str, float]


@pytest.fixture(scope="module")
def reference() -> Reference:
    folder = Path(HEADWATER_WORK or "") / f"reference-{VERSION}"
    if not HEADWATER_WORK or not (folder / "reference.json").is_file():
        pytest.skip(
            f"no Brightway reference in $HEADWATER_WORK/reference-{VERSION}: "
            "run bench/bw_reference.py"
        )
    found = json.loads((folder / "reference.json").read_text()).get("ecoinvent_version")
    assert found == VERSION, f"the reference in {folder} is for ecoinvent {found}, not {VERSION}"

    def read(name: str) -> Columns:
        return pq.read_table(folder / f"{name}.parquet").to_pydict()

    method = read("method")
    score_table = read("scores")
    return Reference(
        technosphere=read("technosphere"),
        lines=read("technosphere_lines"),
        biosphere=read("biosphere"),
        method=dict(zip(method["flow_id"], method["factor"], strict=True)),
        scores=dict(zip(score_table["process_id"], score_table["score"], strict=True)),
    )


def _position(ids: tuple[str, ...], wanted: list[Any]) -> Indices:
    index = {id_: i for i, id_ in enumerate(ids)}
    return np.array([index[w] for w in wanted], dtype=np.int64)


def _bw_A(reference: Reference, lines: Lines) -> sparse.csc_array:
    t = reference.technosphere
    rows, cols = _position(lines.ids, t["row_id"]), _position(lines.ids, t["col_id"])
    return _matrix(rows, cols, np.array(t["value"], dtype=np.float64), (lines.n, lines.n))


def _bw_B(reference: Reference, lines: Lines) -> tuple[sparse.csc_array, tuple[str, ...]]:
    b = reference.biosphere
    flows = tuple(sorted(set(b["flow_id"])))
    rows, cols = _position(flows, b["flow_id"]), _position(lines.ids, b["col_id"])
    values = np.array(b["value"], dtype=np.float64)
    return _matrix(rows, cols, values, (len(flows), lines.n)), flows


def _bw_h(reference: Reference, lines: Lines) -> Floats:
    B, flows = _bw_B(reference, lines)
    return np.asarray(B.T @ np.array([reference.method.get(f, 0.0) for f in flows]))


def _bw_line_values(reference: Reference) -> Floats:
    """Brightway's processed technosphere lines with their sign: flipped ones are inputs."""
    t = reference.lines
    return np.array(t["amount"], dtype=np.float64) * np.where(t["flip"], -1.0, 1.0)


def _recyclable_lines(
    exchanges: list[dict[str, Any]], products: dict[str, str], reference: Reference
) -> list[int]:
    """Brightway's lines that are headwater's recyclable lines.

    Brightway links a recyclable to a recycled-content dataset, entered as +kg in its row. A
    line is one when its buyer has a recyclable line of the product that its row makes.
    """
    recyclable = {
        (e["process_id"], e["flow_id"]) for e in exchanges if e["role"] == Role.recyclable.value
    }
    t = reference.lines
    return [
        k
        for k, (row, col, amount, flip) in enumerate(
            zip(t["row_id"], t["col_id"], t["amount"], t["flip"], strict=True)
        )
        if flip and amount < 0 and (col, products[row]) in recyclable
    ]


def _cells_equal(left: sparse.csc_array, right: sparse.csc_array) -> tuple[int, int, int]:
    """Cells only in left, only in right, and in both with different values."""
    lk, lv = _keys(left)
    rk, rv = _keys(right)
    both, li, ri = np.intersect1d(lk, rk, assume_unique=True, return_indices=True)
    differ = int(np.count_nonzero(lv[li] != rv[ri]))
    return lk.size - both.size, rk.size - both.size, differ


def _keys(matrix: sparse.csc_array) -> tuple[Indices, Floats]:
    """One number per cell, row x columns + column, and the cell's value."""
    coo = matrix.tocoo()
    return coo.row.astype(np.int64) * matrix.shape[1] + coo.col.astype(np.int64), coo.data


def _rows_to(
    matrix: sparse.csc_array, rows: tuple[str, ...], into: tuple[str, ...]
) -> sparse.csc_array:
    coo = matrix.tocoo()
    position = _position(into, list(rows))
    shape = (len(into), matrix.shape[1])
    return sparse.csc_array((coo.data, (position[coo.row], coo.col)), shape=shape)


def _relative(a: Floats, b: Floats) -> Floats:
    size = np.maximum(np.abs(a), np.abs(b))
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(size > 0, np.abs(a - b) / size, 0.0)


def _summary(label: str, rel: Floats) -> None:
    print(
        f"{label}: max {rel.max():.2g}, above 1e-12 {(rel > 1e-12).sum():,}, "
        f"above 1e-9 {(rel > 1e-9).sum():,}, above 1e-6 {(rel > 1e-6).sum():,} "
        f"of {rel.size:,}"
    )


# --- X1 (a) data parity ---------------------------------------------------------------------


def test_x1a_technosphere_equals_brightway_but_for_recyclables(
    exchanges: list[dict[str, Any]], products: dict[str, str], reference: Reference, lines: Lines
) -> None:
    t = reference.lines
    recyclable = np.zeros(len(t["amount"]), dtype=bool)
    recyclable[_recyclable_lines(exchanges, products, reference)] = True
    rows, cols = _position(lines.ids, t["row_id"]), _position(lines.ids, t["col_id"])
    values = _bw_line_values(reference)
    shape = (lines.n, lines.n)
    without = _matrix(
        rows[~recyclable], cols[~recyclable], values[~recyclable], shape, float32=True
    )
    ours = lines.A(float32=True)

    only_bw, only_ours, differ = _cells_equal(without, ours)
    print(
        f"\nX1a technosphere: Brightway {len(values):,} lines, {recyclable.sum():,} of them "
        f"recyclable; without those, {without.nnz:,} cells against headwater's {ours.nnz:,}: "
        f"only in Brightway {only_bw}, only in headwater {only_ours}, values differing {differ}"
    )
    assert recyclable.sum() == 2_426
    assert np.all(values[recyclable] > 0)  # Brightway enters a recyclable sent as +kg
    assert (only_bw, only_ours, differ) == (0, 0, 0)


def test_x1a_every_line_links_in_both(
    exchanges: list[dict[str, Any]], reference: Reference, lines: Lines
) -> None:
    # No file is empty and no line is left without a supplier by choice: headwater has no data
    # gap, and Brightway deleted no line while linking (bench/bw_reference.py checks that). Both
    # hold the same datasets.
    gaps = [e for e in exchanges if e["link_status"] == "data_gap"]
    datasets = set(reference.lines["col_id"])
    print(f"\nX1a links: {len(gaps)} data gaps; Brightway {len(datasets):,} datasets")

    assert gaps == []
    assert datasets == set(lines.ids)


def test_x1a_brightway_matrix_is_its_own_lines_summed(reference: Reference, lines: Lines) -> None:
    # The exported matrix is bw2calc's. Rebuilt from the processed lines, summed in float32 as
    # bw2calc does, it must be the same: the comparison of the lines above covers the matrix
    # bw2calc solves. Summed in float64, the own-use diagonals would differ by a float32 step.
    t = reference.lines
    rows, cols = _position(lines.ids, t["row_id"]), _position(lines.ids, t["col_id"])
    rebuilt = _matrix(rows, cols, _bw_line_values(reference), (lines.n, lines.n), float32=True)

    assert _cells_equal(rebuilt, _bw_A(reference, lines)) == (0, 0, 0)


def test_x1a_biosphere_equals_brightway(reference: Reference, lines: Lines) -> None:
    bw, bw_flows = _bw_B(reference, lines)
    flows = tuple(sorted(set(bw_flows) | set(lines.flows)))
    bw = _rows_to(bw, bw_flows, flows)
    ours = _rows_to(lines.B(float32=True), lines.flows, flows)

    only_bw, only_ours, differ = _cells_equal(bw, ours)
    print(
        f"\nX1a biosphere: Brightway {bw.nnz:,} cells in {len(bw_flows):,} flows, headwater "
        f"{ours.nnz:,} in {len(lines.flows):,}: only in Brightway {only_bw}, only in "
        f"headwater {only_ours}, values differing {differ}"
    )
    assert (only_bw, only_ours, differ) == (0, 0, 0)


def test_x1a_method_equals_brightway(bg: hw.Background, reference: Reference) -> None:
    ours = {f.flow_id: f.factor for f in bg.method.factors}

    assert ours == reference.method
    assert len(ours) == 13


def test_no_ordinary_input_is_negative(
    exchanges: list[dict[str, Any]], products: dict[str, str], reference: Reference
) -> None:
    # headwater: an input is never negative, the importer refuses it. Brightway: its negative
    # technosphere inputs are exactly headwater's waste and recyclable lines.
    negative_inputs = sum(e["role"] == Role.input.value and e["amount"] < 0 for e in exchanges)
    sent_roles = (Role.waste_to_treatment.value, Role.recyclable.value)
    sent = [(e["process_id"], e["flow_id"]) for e in exchanges if e["role"] in sent_roles]
    t = reference.lines
    bw_negative = [
        (col, products[row])
        for row, col, amount, flip in zip(
            t["row_id"], t["col_id"], t["amount"], t["flip"], strict=True
        )
        if flip and amount < 0
    ]
    print(
        f"\nnegative inputs: headwater {negative_inputs}, Brightway {len(bw_negative):,}; "
        f"headwater waste and recyclable lines {len(sent):,}"
    )
    assert negative_inputs == 0
    assert len(bw_negative) == len(sent) == 47_740 + 2_426
    assert sorted(bw_negative) == sorted(sent)


def test_own_use_never_occurs_in_a_treatment(
    release: Release, exchanges: list[dict[str, Any]], products: dict[str, str]
) -> None:
    # calc gives own use no sign in a treatment. ecoinvent 3.12 cut-off never needs one: no
    # treatment buys from itself, under any role.
    waste = {f["id"] for f in release.table("flows").to_pylist() if f["kind"] == "waste"}
    treatments = {pid for pid, flow in products.items() if flow in waste}
    own_use = [e for e in exchanges if e["role"] == Role.own_use.value]
    in_treatments = [e for e in own_use if e["process_id"] in treatments]
    self_supplied = [
        e
        for e in exchanges
        if e["process_id"] in treatments
        and e["supplier_id"] == e["process_id"]
        and e["role"] != Role.reference.value
    ]
    print(
        f"\nown use: {len(own_use)} lines in {len({e['process_id'] for e in own_use})} "
        f"datasets, {len(in_treatments)} in the {len(treatments):,} treatments; lines of a "
        f"treatment buying from itself: {len(self_supplied)}"
    )
    assert len(treatments) == 4_623
    assert (len(in_treatments), len(self_supplied)) == (0, 0)


# --- X1 (b) score parity --------------------------------------------------------------------


def test_x1b_scores_of_the_same_matrices_agree(
    bg: hw.Background, reference: Reference, lines: Lines
) -> None:
    # Brightway's matrices against headwater's rounded to float32 per line, as Brightway
    # stores them: the same data, but for the recyclable entries, whose suppliers have no
    # burden. Products with no burden anywhere upstream are exactly 0 on both sides.
    bw_A, bw_h = _bw_A(reference, lines), _bw_h(reference, lines)
    start = time.perf_counter()
    bw_fact = factorize(bw_A)
    on_bw = scores(bw_fact, bw_h)
    took = time.perf_counter() - start
    ours_A = lines.A(float32=True)
    ours_h = np.asarray(lines.B(float32=True).T @ _c(bg, lines.flows))
    on_ours = scores(factorize(ours_A), ours_h)

    zero_bw = structural_zeros(abs(bw_A).T, bw_h)
    zero_ours = structural_zeros(abs(ours_A).T, ours_h)
    rel = _relative(on_bw.values, on_ours.values)
    worst = int(np.argmax(rel))
    print(
        f"\nX1b: headwater's calc on Brightway's matrices in {took:.1f} s, path "
        f"{[s.value for s in on_bw.path]}, residual {on_bw.residual:.2g}, condition "
        f"{bw_fact.condition:.3g}; structural zeros {zero_bw.sum():,} and {zero_ours.sum():,}"
    )
    _summary("X1b relative difference", rel)
    print(f"X1b largest: {internals(bg).solved.block.names[worst]} ({lines.ids[worst]})")
    assert np.array_equal(zero_bw, zero_ours)
    assert np.array_equal(on_bw.exact_zeros, zero_bw)
    assert np.array_equal(on_ours.exact_zeros, zero_ours)
    assert np.all(on_bw.values[zero_bw] == 0.0) and np.all(on_ours.values[zero_ours] == 0.0)
    assert np.all(on_bw.values[~zero_bw] != 0.0)
    assert rel.max() <= RELATIVE


def test_pivoted_factors_solve_with_exact_zeros(
    bg: hw.Background, reference: Reference, lines: Lines
) -> None:
    # With pivoting, the first solve leaves rounding noise in the products whose exact score is
    # 0, and the check fails there. calc sets them to 0, refines once, and agrees with the
    # unpivoted solve. Both on headwater's matrix and on Brightway's.
    ours = internals(bg).solved
    unpivoted = ours.x["climate"].values
    on_ours = scores(factorize(ours.factorization.matrix, pivoting=True), ours.h["climate"])
    bw_A, bw_h = _bw_A(reference, lines), _bw_h(reference, lines)
    on_bw = scores(factorize(bw_A, pivoting=True), bw_h)
    on_bw_unpivoted = scores(factorize(bw_A), bw_h)
    zero = structural_zeros(ours.factorization.magnitude.T, ours.h["climate"])

    for label, solved, reference_values in (
        ("headwater", on_ours, unpivoted),
        ("Brightway's matrices", on_bw, on_bw_unpivoted.values),
    ):
        rel = _relative(solved.values, reference_values)
        print(
            f"\npivoted, {label}: path {[s.value for s in solved.path]}, residual "
            f"{solved.residual:.2g}, largest difference from the unpivoted solve {rel.max():.2g}"
        )
        assert solved.path == (Step.pivoted_lu, Step.refined)
        assert np.all(solved.values[zero] == 0.0)
        assert rel.max() <= RELATIVE


# --- X1 (c) information ---------------------------------------------------------------------


def test_x1c_information(bg: hw.Background, reference: Reference, lines: Lines) -> None:
    ours = internals(bg).solved.x["climate"].values
    bw2calc = np.array([reference.scores[pid] for pid in lines.ids])
    bw_A, bw_h = _bw_A(reference, lines), _bw_h(reference, lines)
    accurate_on_bw = scores(factorize(bw_A), bw_h).values
    error_bw2calc = backward_error(bw_A.T, bw2calc, bw_h)
    error_ours = backward_error(
        internals(bg).solved.factorization.matrix.T, ours, internals(bg).solved.h["climate"]
    )

    print("\nX1c information")
    _summary("bw2calc against headwater on its float64 release", _relative(bw2calc, ours))
    _summary(
        "bw2calc against headwater's calc on Brightway's matrices",
        _relative(bw2calc, accurate_on_bw),
    )
    _summary(
        "headwater on its float64 release against Brightway's float32 matrices",
        _relative(ours, accurate_on_bw),
    )
    rel = _relative(bw2calc, ours)
    for j in np.argsort(-rel)[:5]:
        print(f"  bw2calc differs by {rel[j]:.2g} on {internals(bg).solved.block.names[j]}")
    for label, error in (("bw2calc", error_bw2calc), ("headwater", error_ours)):
        print(
            f"componentwise backward error, {label}: max {error.max():.2g}, "
            f"above 1e-12 {(error > 1e-12).sum():,}, above 1e-14 {(error > 1e-14).sum():,}"
        )
    assert len(reference.scores) == lines.n == 26_533
    assert error_ours.max() <= internals(bg).solved.factorization.settings.tolerance


# --- X2 the waste rule ----------------------------------------------------------------------


def test_x2_waste_rule_matches_the_full_solve_on_every_activity_with_waste_inputs(
    bg: hw.Background, exchanges: list[dict[str, Any]]
) -> None:
    # Every amount is positive and every score is per unit made, or per kg treated (the stored
    # sign convention), so each term below is positive kg or units times a positive burden.
    # No matrix sign is used: only roles.
    solved = internals(bg).solved
    index = {pid: i for i, pid in enumerate(solved.ids)}
    per_unit = solved.x["climate"].values * solved.block.reference_sign
    direct = solved.h["climate"]
    rule = direct.copy()
    gross = np.abs(direct)
    made = np.zeros(len(index))
    with_waste: set[int] = set()
    for e in exchanges:
        j = index[e["process_id"]]
        role = Role(e["role"])
        if role is Role.reference:
            made[j] += e["amount"]
        elif role is Role.own_use:
            made[j] -= e["amount"]
        elif role in (Role.input, Role.waste_to_treatment) and e["supplier_id"] is not None:
            term = e["amount"] * per_unit[index[e["supplier_id"]]]
            rule[j] += term
            gross[j] += abs(term)
            if role is Role.waste_to_treatment:
                with_waste.add(j)
        # emissions are in `direct`; recyclables and the data gap carry no burden
    rows = np.array(sorted(with_waste))
    difference = np.abs(rule[rows] / made[rows] - per_unit[rows])
    size = gross[rows] / made[rows]
    rel = np.divide(difference, size, out=np.zeros_like(size), where=size > 0)
    print(
        f"\nX2: {rows.size:,} activities with waste inputs, largest difference {rel.max():.2g} "
        f"of the sum of their terms; {(size == 0).sum()} with no burden at all"
    )
    assert rows.size == 11_465
    assert np.all(difference[size == 0] == 0.0)
    assert rel.max() <= solved.factorization.settings.tolerance


# --- X3 the same release twice --------------------------------------------------------------


def test_x3_importing_twice_gives_the_same_release_hash(
    release: Release, tmp_path_factory: pytest.TempPathFactory
) -> None:
    # The second import uses another number of workers, so files finish in another order.
    assert ECOINVENT_DIR
    again = read_ecospold2(Path(ECOINVENT_DIR), version=VERSION, workers=2)
    second = again.freeze(into=tmp_path_factory.mktemp("second"))
    print(f"\nX3: {release.path.name} and {second.path.name}")

    assert second.hash == release.hash
