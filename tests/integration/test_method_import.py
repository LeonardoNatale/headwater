"""EF v3.1 from ecoinvent's LCIA implementation workbook, mapped to a licensed ecoinvent release.

Runs only when these are set:

- `ECOINVENT_LCIA`: the workbook, for example `.../LCIA Implementation 3.12.xlsx`
- `ECOINVENT_RELEASE`: the release of the same version, a folder or `name@hash` in
  HEADWATER_HOME, for example `ecoinvent-3.12-cutoff@b9428cdd8045`

The comparison with ecoinvent's own scores also needs `ECOINVENT_CUMULATIVE_LCIA`, the cumulative
LCIA workbook of that release (`Cut-off Cumulative LCIA v3.12.xlsx`). No value of the
licensed files is written here: counts and names only. Run with `-s` to see the summary.
"""

import os
from collections import defaultdict
from pathlib import Path
from typing import Any, cast

import numpy as np
import openpyxl
import pyarrow as pa
import pytest

import headwater as hw
from headwater.calc.cancellation import cancellation_of, process_parts
from headwater.calc.solve import supply
from headwater.io.lcia import MethodImport, import_method, read_workbook, write_method
from headwater.query._internals import internals
from headwater.release import Release, find
from headwater.study import read_method

ECOINVENT_LCIA = os.environ.get("ECOINVENT_LCIA")
ECOINVENT_RELEASE = os.environ.get("ECOINVENT_RELEASE")
ECOINVENT_CUMULATIVE_LCIA = os.environ.get("ECOINVENT_CUMULATIVE_LCIA")

pytestmark = pytest.mark.skipif(
    not (ECOINVENT_LCIA and ECOINVENT_RELEASE),
    reason="ECOINVENT_LCIA and ECOINVENT_RELEASE are not set",
)

METHOD = "EF v3.1"

CLIMATE = {"Carbon dioxide, fossil": 1.0, "Methane, fossil": 29.8, "Dinitrogen monoxide": 273.0}
"""IPCC AR6 GWP100, the factors of the 3-flow climate method in docs/appendix/benchmarks.md."""

PARTS = {
    "climate-change": (
        "climate-change-biogenic",
        "climate-change-fossil",
        "climate-change-land-use-and-land-use-change",
    ),
    "ecotoxicity-freshwater": (
        "ecotoxicity-freshwater-inorganics",
        "ecotoxicity-freshwater-organics",
    ),
    "human-toxicity-carcinogenic": (
        "human-toxicity-carcinogenic-inorganics",
        "human-toxicity-carcinogenic-organics",
    ),
    "human-toxicity-non-carcinogenic": (
        "human-toxicity-non-carcinogenic-inorganics",
        "human-toxicity-non-carcinogenic-organics",
    ),
}
"""Indicators that are the sum of their sub-indicators, flow by flow."""

FACTORS_3_12 = {
    "acidification": 20,
    "climate-change": 189,
    "climate-change-biogenic": 4,
    "climate-change-fossil": 173,
    "climate-change-land-use-and-land-use-change": 12,
    "ecotoxicity-freshwater": 7274,
    "ecotoxicity-freshwater-inorganics": 714,
    "ecotoxicity-freshwater-organics": 6560,
    "energy-resources-non-renewable": 7,
    "eutrophication-freshwater": 13,
    "eutrophication-marine": 29,
    "eutrophication-terrestrial": 14,
    "human-toxicity-carcinogenic": 821,
    "human-toxicity-carcinogenic-inorganics": 85,
    "human-toxicity-carcinogenic-organics": 736,
    "human-toxicity-non-carcinogenic": 5002,
    "human-toxicity-non-carcinogenic-inorganics": 472,
    "human-toxicity-non-carcinogenic-organics": 4530,
    "ionising-radiation-human-health": 175,
    "land-use": 155,
    "material-resources-metals-minerals": 79,
    "ozone-depletion": 60,
    "particulate-matter-formation": 27,
    "photochemical-oxidant-formation-human-health": 259,
    "water-use": 5,
}
"""Factors of each EF v3.1 indicator in the 3.12 workbook. Every one maps to the 3.12 release."""

RELATIVE = 1e-6
"""Against ecoinvent's cumulative LCIA: the default of AGENTS.md for another engine."""

NOISE = 1e-15
"""ecoinvent's largest score allowed, per unit of product, where headwater has an exact zero."""


@pytest.fixture(scope="module")
def release() -> Release:
    assert ECOINVENT_RELEASE
    folder = Path(ECOINVENT_RELEASE)
    return Release.open(folder) if folder.is_dir() else find(ECOINVENT_RELEASE)


@pytest.fixture(scope="module")
def imported(release: Release) -> MethodImport:
    assert ECOINVENT_LCIA
    return import_method(read_workbook(ECOINVENT_LCIA, METHOD), release)


def test_counts_of_mapped_and_unmapped_factors_per_indicator(imported: MethodImport) -> None:
    print(f"\n{imported.release.name}: {imported.flows} elementary flows")
    for r in imported.indicators:
        print(
            f"{r.indicator.id:46} factors {r.factors:5}  mapped {r.mapped:5}  "
            f"not in flow list {len(r.unmapped):3}  flows with no factor "
            f"{len(r.flows_without_factor):5}"
        )
    print(f"flows with no factor for any indicator: {len(imported.not_characterised)}")

    assert imported.workbook.version == "3.12"
    assert len(imported.indicators) == 25
    assert {r.indicator.id: r.factors for r in imported.indicators} == FACTORS_3_12
    assert [r.unmapped for r in imported.indicators] == [()] * 25
    assert imported.flows == 9850
    assert len(imported.not_characterised) == 1890


def test_climate_factors_of_the_three_greenhouse_gases(
    imported: MethodImport, release: Release
) -> None:
    flows = {f.id: f.name for f in release.flows() if f.name in CLIMATE}
    factors = {(f.indicator, f.flow_id): f.factor for f in imported.method.factors}

    assert len(flows) == 13
    for indicator in ("climate-change", "climate-change-fossil"):
        for flow_id, name in flows.items():
            assert factors[indicator, flow_id] == pytest.approx(CLIMATE[name], rel=1e-9), (
                f"{indicator}, {name}, {flow_id}"
            )


def test_totals_are_the_sum_of_their_sub_indicators(imported: MethodImport) -> None:
    factors: dict[str, dict[str, float]] = defaultdict(dict)
    for f in imported.method.factors:
        factors[f.indicator][f.flow_id] = f.factor

    for total, parts in PARTS.items():
        flows = set(factors[total]).union(*(factors[p] for p in parts))
        for flow_id in flows:
            expected = factors[total].get(flow_id, 0.0)
            found = sum(factors[p].get(flow_id, 0.0) for p in parts)
            assert found == pytest.approx(expected, rel=1e-9, abs=0.0), f"{total}, {flow_id}"
            assert sum(flow_id in factors[p] for p in parts) <= 1, f"{flow_id} in two parts"


def test_the_method_folder_reads_back(imported: MethodImport, tmp_path: Path) -> None:
    folder = write_method(imported, tmp_path / imported.method.id)

    assert read_method(folder) == imported.method


def test_the_release_scores_with_the_method(
    imported: MethodImport, release: Release, tmp_path: Path
) -> None:
    background = hw.load(release.path, method=write_method(imported, tmp_path / "method"))
    x = {indicator: s.values for indicator, s in internals(background).solved.x.items()}
    print(f"\nflows with no factor that processes use: {len(background.not_characterised)}")

    assert sorted(x) == sorted(i.id for i in imported.method.indicators)
    assert all(np.isfinite(values).all() for values in x.values())
    for total, parts in PARTS.items():
        np.testing.assert_allclose(sum(x[p] for p in parts), x[total], rtol=1e-9, atol=0)


# --- against ecoinvent's cumulative LCIA ----------------------------------------------------


@pytest.mark.skipif(not ECOINVENT_CUMULATIVE_LCIA, reason="ECOINVENT_CUMULATIVE_LCIA is not set")
def test_scores_match_ecoinvent_cumulative_lcia(
    imported: MethodImport, release: Release, tmp_path: Path
) -> None:
    """Each dataset's score per indicator, against ecoinvent's, relative to the larger of the two.

    Assumes the cumulative workbook has one row per dataset, an `Activity UUID_Product UUID`
    column, and header rows above it naming each column's method and category. It fails with
    what it found when the layout differs.

    A score that is the sum of terms that cancel carries the rounding of its terms, not of its
    own size, so a product above `RELATIVE` must be one whose score cancels (below 1e-3 of its
    gross, the sum of the sizes of its positive and negative terms), with a difference within
    `RELATIVE` of that gross.
    A product with nothing upstream that takes or emits a flow of the indicator scores exactly 0
    in headwater; ecoinvent's file can hold rounding noise there, so those are compared as
    absolute numbers, within `NOISE`.
    """
    assert ECOINVENT_CUMULATIVE_LCIA
    background = hw.load(release.path, method=write_method(imported, tmp_path / "method"))
    scores = background.write_indicators(into=tmp_path / "derived").table(
        f"indicators/{imported.method.id}"
    )
    ours = _by_dataset(scores)
    theirs = _cumulative(Path(ECOINVENT_CUMULATIVE_LCIA), imported)

    missing = sorted(set(theirs) - set(ours))
    assert not missing, (
        f"{len(missing)} datasets of ecoinvent are not in the release: {missing[:5]}"
    )
    solved = internals(background).solved
    position = {pid.rsplit(":", 1)[1]: i for i, pid in enumerate(solved.ids)}
    ids = sorted(theirs)
    rows = np.array([position[d] for d in ids])
    failures: list[str] = []
    for category, indicator in {i.name: i.id for i in imported.method.indicators}.items():
        a = np.array([ours[d][indicator] for d in ids])
        b = np.array([theirs[d][category] for d in ids])
        zero = solved.x[indicator].exact_zeros[rows]
        size = np.maximum(np.abs(a), np.abs(b))
        relative = np.zeros(len(ids))
        np.divide(np.abs(a - b), size, out=relative, where=~zero & (size > 0))
        noise = float(np.abs(b[zero]).max(initial=0.0))
        above = np.flatnonzero(relative > RELATIVE)
        gross, cancels = _parts(solved, indicator, rows[above])
        over_gross = np.full(len(above), np.inf)
        np.divide(np.abs(a[above] - b[above]), gross, out=over_gross, where=gross > 0)
        print(
            f"{indicator:46} products {len(ids)}  exact zeros {int(zero.sum())}  "
            f"max relative {relative.max():.2e}  above {RELATIVE:g}: {len(above)}, "
            f"largest difference over gross {over_gross.max(initial=0.0):.2e}"
        )
        failures += [
            f"{indicator}, {ids[above[k]]}: relative {relative[above[k]]:.2e}, "
            f"difference over gross {over_gross[k]:.2e}, cancels {bool(cancels[k])}"
            for k in np.flatnonzero((over_gross > RELATIVE) | ~cancels)
        ]
        if noise > NOISE:
            failures.append(f"{indicator}: {noise:.1e} where headwater has an exact zero")
    assert not failures, failures


def _parts(
    solved: Any, indicator: str, rows: np.ndarray[Any, Any]
) -> tuple[np.ndarray[Any, Any], np.ndarray[Any, Any]]:
    """The gross of the score of the product at each of `rows`, and whether it cancels: one
    forward solve each."""
    if not len(rows):
        return np.empty(0), np.empty(0, dtype=bool)
    plus, minus = process_parts(solved.block.B, solved.c[indicator])
    gross = np.empty(len(rows))
    cancels = np.empty(len(rows), dtype=bool)
    for k, row in enumerate(rows):
        demand = np.zeros(len(solved.ids))
        demand[row] = 1.0
        parts = cancellation_of(plus, minus, supply(solved.factorization, demand).values)
        gross[k], cancels[k] = parts.gross, parts.cancels
    return gross, cancels


def _by_dataset(scores: pa.Table) -> dict[str, dict[str, float]]:
    """Scores by ecoinvent's `<activity uuid>_<product uuid>`, from `ecoinvent:<v>:<m>:...`."""
    rows: list[dict[str, Any]] = scores.to_pylist()
    return {str(r.pop("product_id")).rsplit(":", 1)[1]: r for r in rows}


def _cumulative(path: Path, imported: MethodImport) -> dict[str, dict[str, float]]:
    book = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        sheet = book.worksheets[0] if len(book.worksheets) == 1 else book["LCIA"]
        rows = sheet.iter_rows(values_only=True)
        header: list[tuple[Any, ...]] = []
        for row in rows:
            header.append(row)
            if "Activity UUID_Product UUID" in row:
                break
            if len(header) > 10:
                raise AssertionError(
                    f"no 'Activity UUID_Product UUID' column in the first 10 rows of {path.name}"
                    f": {[r[:6] for r in header]}"
                )
        key = header[-1].index("Activity UUID_Product UUID")
        [method_row] = [i for i, r in enumerate(header) if METHOD in r]
        categories = header[method_row + 1]
        columns = {
            str(categories[i]): i for i, cell in enumerate(header[method_row]) if cell == METHOD
        }
        wanted = {i.name for i in imported.method.indicators}
        assert set(columns) == wanted, f"{METHOD} columns: {sorted(columns)}, expected {wanted}"
        return {
            str(row[key]): {c: float(cast(float, row[i])) for c, i in columns.items()}
            for row in rows
            if row[key]
        }
    finally:
        book.close()
