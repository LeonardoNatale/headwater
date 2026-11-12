"""Carbon indicators from ecoinvent's LCIA workbook, next to EF v3.1, on a licensed release.

Runs only when these are set:

- `ECOINVENT_LCIA`: the workbook, for example `.../LCIA Implementation 3.12.xlsx`
- `ECOINVENT_RELEASE`: the release of the same version, a folder or `name@hash` in
  HEADWATER_HOME, for example `ecoinvent-3.12-cutoff@b9428cdd8045`

The comparison with ecoinvent's own scores also needs `ECOINVENT_CUMULATIVE_LCIA`, the cumulative
LCIA workbook of that release (`Cut-off Cumulative LCIA v3.12.xlsx`). Its data sheet is read as a
stream of XML: it is 795 MB unpacked. No value of the licensed files is written here: counts,
names and differences only. Run with `-s` to see the summary.
"""

import os
import re
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import cast

import numpy as np
import pytest
from lxml import etree

import headwater as hw
from headwater.calc.cancellation import cancellation
from headwater.calc.solve import supply
from headwater.io.lcia import MethodImport, import_method, read_workbook, write_method
from headwater.query._internals import internals
from headwater.release import Release, find
from headwater.release.indicators import read_indicators

ECOINVENT_LCIA = os.environ.get("ECOINVENT_LCIA")
ECOINVENT_RELEASE = os.environ.get("ECOINVENT_RELEASE")
ECOINVENT_CUMULATIVE_LCIA = os.environ.get("ECOINVENT_CUMULATIVE_LCIA")

pytestmark = pytest.mark.skipif(
    not (ECOINVENT_LCIA and ECOINVENT_RELEASE),
    reason="ECOINVENT_LCIA and ECOINVENT_RELEASE are not set",
)

CATEGORIES = {
    "IPCC 2021 (incl. biogenic CO2)": (
        "climate change: biogenic removals (incl. CO2)",
        "climate change: biogenic emissions (incl. CO2)",
        "climate change: biogenic (incl. CO2)",
    ),
    "IPCC 2021": ("climate change: direct land use change",),
}
"""The carbon categories imported next to EF v3.1, by workbook method."""

COUNTS_3_12 = {
    "climate-change-biogenic-removals-incl-co2": 2,
    "climate-change-biogenic-emissions-incl-co2": 8,
    "climate-change-biogenic-incl-co2": 10,
    "climate-change-direct-land-use-change": 12,
}
"""Factors of each carbon indicator in the 3.12 workbook. Every one maps to the 3.12 release."""

RELATIVE = 1e-6
"""Against ecoinvent's cumulative LCIA: the default of AGENTS.md for another engine."""

NOISE = 1e-15
"""ecoinvent's largest score allowed, per unit of product, where headwater has an exact zero."""

KEY = "Activity UUID_Product UUID"
_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


@pytest.fixture(scope="module")
def release() -> Release:
    assert ECOINVENT_RELEASE
    folder = Path(ECOINVENT_RELEASE)
    return Release.open(folder) if folder.is_dir() else find(ECOINVENT_RELEASE)


@pytest.fixture(scope="module")
def imported(release: Release) -> dict[str, MethodImport]:
    """EF v3.1 whole, and the carbon categories of the IPCC methods, by method id."""
    assert ECOINVENT_LCIA
    found = [import_method(read_workbook(ECOINVENT_LCIA, "EF v3.1"), release)]
    for name, categories in CATEGORIES.items():
        found.append(
            import_method(read_workbook(ECOINVENT_LCIA, name, categories=categories), release)
        )
    return {m.method.id: m for m in found}


@pytest.fixture(scope="module")
def folders(
    imported: dict[str, MethodImport], tmp_path_factory: pytest.TempPathFactory
) -> dict[str, Path]:
    root = tmp_path_factory.mktemp("methods")
    return {i: write_method(m, root / i) for i, m in imported.items()}


def test_counts_of_ef_and_of_the_carbon_categories(imported: dict[str, MethodImport]) -> None:
    for m in imported.values():
        factors = sum(r.factors for r in m.indicators)
        print(
            f"\n{m.method.id} {m.method.version}: {factors:,} factors, "
            f"{m.flows:,} elementary flows, {len(m.not_characterised):,} with no factor"
        )
        for r in m.indicators:
            print(
                f"  {r.indicator.id:46} factors {r.factors:5}  mapped {r.mapped:5}  "
                f"not in flow list {len(r.unmapped):3}"
            )

    ef = imported["EF-3.1"]
    carbon = [r for i, m in imported.items() if i != "EF-3.1" for r in m.indicators]
    assert sorted(r.indicator.id for r in carbon) == sorted(COUNTS_3_12)
    assert ef.workbook.version == "3.12"
    assert sum(r.factors for r in ef.indicators) == 27_415
    assert ef.flows == 9_850
    assert len(ef.not_characterised) == 1_890
    assert {r.indicator.id: r.factors for r in carbon} == COUNTS_3_12
    assert all(r.mapped == r.factors for r in carbon)


def test_ef_gives_no_factor_to_biogenic_carbon_dioxide(imported: dict[str, MethodImport]) -> None:
    """The flows the carbon categories exist for: EF v3.1 as ecoinvent implements it leaves them
    out of every indicator."""
    names = {f.name for f in imported["EF-3.1"].not_characterised}

    assert {"Carbon dioxide, in air", "Carbon dioxide, non-fossil"} <= names


# --- against ecoinvent's cumulative LCIA ----------------------------------------------------


@pytest.fixture(scope="module")
def together(folders: dict[str, Path], release: Release) -> hw.Background:
    """EF v3.1 and the carbon methods, loaded together."""
    return hw.load(release.path, method=list(folders.values()))


def test_the_carbon_indicators_sit_next_to_ef_in_one_indicators_release(
    together: hw.Background, imported: dict[str, MethodImport], tmp_path: Path
) -> None:
    written = together.write_indicators(into=tmp_path)
    vectors = read_indicators(written)

    print(f"\n{written.name}: {len(vectors.indicators)} indicators")
    assert [m.id for m in written.method_entries] == list(imported)
    assert len(vectors.indicators) == 25 + len(COUNTS_3_12)
    assert vectors.method_id == "+".join(imported)
    assert set(vectors.indicators) >= set(COUNTS_3_12)
    assert np.isfinite(vectors.scores).all()


@pytest.mark.skipif(not ECOINVENT_CUMULATIVE_LCIA, reason="ECOINVENT_CUMULATIVE_LCIA is not set")
def test_scores_match_ecoinvent_cumulative_lcia(
    imported: dict[str, MethodImport], together: hw.Background
) -> None:
    """Each product's carbon scores against ecoinvent's, relative to the larger of the two.

    A product with nothing upstream that emits or takes a flow of the indicator scores exactly
    0 in headwater, set from the structure of A. ecoinvent's file can hold rounding noise there,
    so those are compared as absolute numbers, within `NOISE`. A difference above 1e-9 is
    explained by the parts of that product's score: the difference, and the score itself,
    divided by its gross, the sum of the sizes of its positive and negative terms.
    """
    assert ECOINVENT_CUMULATIVE_LCIA
    columns = {
        (m.workbook.method, r.indicator.name): r.indicator.id
        for i, m in imported.items()
        if i != "EF-3.1"
        for r in m.indicators
    }
    theirs = _cumulative(Path(ECOINVENT_CUMULATIVE_LCIA), columns)
    solved = internals(together).solved
    keys = [pid.rsplit(":", 1)[1] for pid in solved.ids]

    failures: list[str] = []
    for indicator in sorted(columns.values()):
        x = solved.x[indicator]
        a = x.values * solved.block.reference_sign
        b = np.array([theirs[k].get(indicator, np.nan) for k in keys])
        have = ~np.isnan(b)
        zero = have & x.exact_zeros
        compared = have & ~x.exact_zeros
        size = np.maximum(np.abs(a), np.abs(b))
        relative = np.zeros(len(a))
        np.divide(np.abs(a - b), size, out=relative, where=compared & (size > 0))
        noise = float(np.abs(b[zero]).max(initial=0.0))
        print(
            f"\n{indicator:46} products {int(have.sum()):,}, of which exact zeros "
            f"{int(zero.sum()):,} (ecoinvent's largest there {noise:.1e})\n"
            f"  max relative {relative.max():.2e}  above 1e-9: "
            f"{int((relative > 1e-9).sum())}  above {RELATIVE:g}: "
            f"{int((relative > RELATIVE).sum())}"
        )
        for j in np.argsort(-relative)[: min(10, int((relative > 1e-9).sum()))]:
            e = np.zeros(len(a))
            e[j] = 1.0
            parts = cancellation(
                solved.block.B, solved.c[indicator], supply(solved.factorization, e).values
            )
            print(
                f"  relative {relative[j]:.2e}  difference over gross "
                f"{abs(a[j] - b[j]) / parts.gross:.2e}  score over gross "
                f"{abs(parts.net) / parts.gross:.2e}  cancels {parts.cancels}"
            )
        if relative.max() > RELATIVE:
            failures.append(f"{indicator}: relative {relative.max():.2e}")
        if noise > NOISE:
            failures.append(f"{indicator}: {noise:.1e} where headwater has an exact zero")
    assert not failures, failures


def _cumulative(path: Path, columns: dict[tuple[str, str], str]) -> dict[str, dict[str, float]]:
    """Scores by `<activity uuid>_<product uuid>` from ecoinvent's cumulative LCIA workbook, for
    each (method, category) of `columns`, keyed by the indicator id it maps to.

    The data sheet has four header rows: method, category, indicator, and unit, which also holds
    the key's column name. A method's "no LT" variant is another method, so names match
    exactly. A blank cell is a score the file does not have.
    """
    with zipfile.ZipFile(path) as book:
        strings = _shared_strings(book)
        for name in sorted(n for n in book.namelist() if n.startswith("xl/worksheets/sheet")):
            rows = _rows(book, name, strings)
            header = [next(rows, {}) for _ in range(4)]
            if header[3].get("A") != KEY:
                continue
            wanted = {
                letter: columns[header[0][letter], header[1].get(letter, "")]
                for letter in header[0]
                if (header[0][letter], header[1].get(letter, "")) in columns
            }
            assert sorted(wanted.values()) == sorted(columns.values()), (
                f"columns of {path.name}: found {sorted(wanted.values())}"
            )
            return {
                row["A"]: {i: float(row[c]) for c, i in wanted.items() if c in row}
                for row in rows
                if row.get("A")
            }
    raise AssertionError(f"no sheet of {path.name} has {KEY!r} in column A of row 4")


def _shared_strings(book: zipfile.ZipFile) -> list[str]:
    strings: list[str] = []
    with book.open("xl/sharedStrings.xml") as file:
        for _, item in etree.iterparse(file, tag=f"{_NS}si"):
            strings.append("".join(item.itertext()))
            item.clear()
    return strings


def _rows(book: zipfile.ZipFile, sheet: str, strings: list[str]) -> Iterator[dict[str, str]]:
    """Each row of a sheet as {column letter: text}, with shared strings looked up."""
    with book.open(sheet) as file:
        for _, row in etree.iterparse(file, tag=f"{_NS}row"):
            cells: dict[str, str] = {}
            for cell in row.iterfind(f"{_NS}c"):
                value = cell.find(f"{_NS}v")
                if value is None or value.text is None:
                    continue
                text = value.text
                if cell.get("t") == "s":
                    text = strings[int(text)]
                letters = re.match(r"[A-Z]+", cast(str, cell.get("r")))
                assert letters is not None
                cells[letters.group()] = text
            row.clear()
            parent = row.getparent()
            while parent is not None and row.getprevious() is not None:
                del parent[0]
            yield cells
