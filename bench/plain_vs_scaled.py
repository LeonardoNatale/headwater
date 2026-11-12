"""A plain SuperLU solve of Aᵀ against headwater's scaled solve, each against ecoinvent's scores.

    export ECOINVENT_LCIA=".../LCIA Implementation 3.12.xlsx"
    export ECOINVENT_RELEASE=".../ecoinvent-3.12-cutoff@<hash>"        # a folder or name@hash
    export ECOINVENT_CUMULATIVE_LCIA=".../Cut-off Cumulative LCIA v3.12.xlsx"
    uv run python bench/plain_vs_scaled.py

EF v3.1 is imported from the LCIA workbook and loaded on the release. For each of its indicators
the product scores come from solves of Aᵀ x = h:

- `plain_lu_of_A`: `splu(A)` with SciPy's defaults (COLAMD, partial pivoting), no scaling, solved
  with `trans="T"`: the factors of A, as bw2calc's factorized solver has them, used for Aᵀ.
- `plain_lu_of_At`: the same defaults on Aᵀ itself, `splu(Aᵀ)`, solved as it is.
  Neither refines the solution or sets anything to zero.
- `scaled`: headwater's own (`hw.load`): rows and columns scaled, no pivoting, exact zeros from
  the structure of A, one refinement.

Each is compared with the scores in ecoinvent's cumulative LCIA, relative to the larger of the
two. A product that is an exact zero by structure is left out of that comparison, since
ecoinvent's file holds rounding noise there; the largest absolute value each solve gives at those
products is reported. A product above 1e-6 is also compared with its gross,
the sum of the sizes of its positive and negative terms (headwater.calc.cancellation), from an
accurate forward solve: a difference that is within 1e-6 of the gross is cancellation, not the
solver.

Writes bench/results/plain_vs_scaled_results.json: counts, errors and the load average only, no
score. Run it alone: the load average at the start and the end is in the file.
"""

import json
import os
import platform
import re
import sys
import tempfile
import time
import zipfile
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import scipy
from lxml import etree
from scipy.sparse.linalg import splu

import headwater as hw
from headwater.calc.cancellation import cancellation_of, process_parts
from headwater.calc.solve import backward_error
from headwater.io.lcia import import_method, read_workbook, write_method
from headwater.query._internals import internals
from headwater.release import Release, find

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results" / "plain_vs_scaled_results.json"
RELATIVE = 1e-6
METHOD = "EF v3.1"
KEY = "Activity UUID_Product UUID"
BATCH = 250
_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def main() -> None:
    lcia = Path(os.environ["ECOINVENT_LCIA"])
    cumulative = Path(os.environ["ECOINVENT_CUMULATIVE_LCIA"])
    folder = Path(os.environ["ECOINVENT_RELEASE"])
    release = Release.open(folder) if folder.is_dir() else find(str(folder))
    load_start = os.getloadavg()

    with tempfile.TemporaryDirectory() as work:
        imported = import_method(read_workbook(lcia, METHOD), release)
        bg = hw.load(release.path, method=write_method(imported, Path(work) / "method"))
    solved = internals(bg).solved
    fact = solved.factorization
    A, n = fact.matrix, fact.n
    columns = {(imported.workbook.method, r.indicator.name): r.indicator.id for r in imported.indicators}
    theirs = read_cumulative(cumulative, columns)
    keys = [pid.rsplit(":", 1)[1] for pid in solved.ids]
    print(f"{n:,} products, {len(columns)} indicators, {len(theirs):,} in the workbook", flush=True)

    plain: dict[str, dict[str, float]] = {}
    start = time.perf_counter()
    lu_of_A = splu(A)
    plain["plain_lu_of_A"] = factors(lu_of_A, start)
    start = time.perf_counter()
    lu_of_At = splu(A.T.tocsc())
    plain["plain_lu_of_At"] = factors(lu_of_At, start)
    print(plain, flush=True)

    sign = solved.block.reference_sign
    found: dict[str, dict[str, object]] = {}
    for indicator in sorted(columns.values()):
        h = solved.h[indicator]
        b = np.array([theirs.get(k, {}).get(indicator, np.nan) for k in keys])
        have = ~np.isnan(b)
        zero = solved.x[indicator].exact_zeros
        solves = {
            "plain_lu_of_A": lu_of_A.solve(h, trans="T"),
            "plain_lu_of_At": lu_of_At.solve(h),
            "scaled": solved.x[indicator].values,
        }
        parts = process_parts(solved.block.B, solved.c[indicator])
        found[indicator] = {
            "products": int(have.sum()),
            "exact_zeros": int((zero & have).sum()),
            **{
                name: solver_row(A, y, h, sign, b, have, zero, parts, fact)
                for name, y in solves.items()
            },
        }
        print(indicator, found[indicator], flush=True)

    RESULTS.parent.mkdir(exist_ok=True)
    RESULTS.write_text(
        json.dumps(
            {
                "release": release.path.name,
                "method": f"{imported.method.id} {imported.method.version}",
                "machine": f"{platform.machine()}, {platform.system()} {platform.release()}",
                "python": platform.python_version(),
                "numpy": np.__version__,
                "scipy": scipy.__version__,
                "date": time.strftime("%Y-%m-%d"),
                "load_average_start": [round(x, 2) for x in load_start],
                "load_average_end": [round(x, 2) for x in os.getloadavg()],
                "processes": n,
                "non_zeros": int(A.nnz),
                "plain_factorizations": plain,
                "relative_threshold": RELATIVE,
                "indicators": found,
            },
            indent=1,
        )
        + "\n"
    )


def factors(lu, start: float) -> dict[str, float]:
    return {"seconds": round(time.perf_counter() - start, 1), "non_zeros": int(lu.L.nnz + lu.U.nnz)}


def solver_row(A, y, h, sign, b, have, zero, parts, fact) -> dict[str, object]:
    """The counts and errors of one solve of Aᵀ y = h, against ecoinvent's scores `b`."""
    a = y * sign
    compared = have & ~zero
    size = np.maximum(np.abs(a), np.abs(b))
    relative = np.zeros(len(a))
    np.divide(np.abs(a - b), size, out=relative, where=compared & (size > 0))
    above = np.flatnonzero(relative > RELATIVE)
    gross = gross_of(fact, parts, above)
    over_gross = np.full(len(above), np.inf)
    np.divide(np.abs(a[above] - b[above]), gross, out=over_gross, where=gross > 0)
    row_error = backward_error(A.T, y, h, fact.magnitude.T)
    normwise = np.abs(h - A.T @ y).max() / (
        fact.magnitude.sum(axis=0).max() * np.abs(y).max() + np.abs(h).max()
    )
    return {
        "largest_relative": float(relative.max()),
        "above_1e-9": int((relative > 1e-9).sum()),
        "above_1e-6": len(above),
        "above_1e-6_and_off_by_more_than_1e-6_of_gross": int((over_gross > RELATIVE).sum()),
        "largest_difference_over_gross_of_those_above_1e-6": float(over_gross.max(initial=0.0)),
        "largest_absolute_at_exact_zeros": float(np.abs(a[zero & have]).max(initial=0.0)),
        "backward_error_normwise": float(normwise),
        "backward_error_rowwise_max": float(row_error.max()),
        "rows_above_1e-12": int((row_error > 1e-12).sum()),
    }


def gross_of(fact, parts, rows) -> np.ndarray:
    """The gross of the score of the product at each of `rows`, from accurate forward solves."""
    plus, minus = parts
    gross = np.empty(len(rows))
    for first in range(0, len(rows), BATCH):
        chosen = rows[first : first + BATCH]
        demand = np.zeros((fact.n, len(chosen)))
        demand[chosen, np.arange(len(chosen))] = 1.0
        supply = fact.solve(demand)
        for k in range(len(chosen)):
            gross[first + k] = cancellation_of(plus, minus, supply[:, k]).gross
    return gross


def read_cumulative(path: Path, columns: dict[tuple[str, str], str]) -> dict[str, dict[str, float]]:
    """Scores by `<activity uuid>_<product uuid>` from ecoinvent's cumulative LCIA workbook, for
    each (method, category) of `columns`, keyed by the indicator id it maps to. The data sheet is
    read as a stream of XML: it is 795 MB unpacked. It has four header rows (method, category,
    indicator, unit); the key's column name is in the fourth."""
    with zipfile.ZipFile(path) as book:
        strings = shared_strings(book)
        for name in sorted(n for n in book.namelist() if n.startswith("xl/worksheets/sheet")):
            rows = sheet_rows(book, name, strings)
            header = [next(rows, {}) for _ in range(4)]
            if header[3].get("A") != KEY:
                continue
            wanted = {
                letter: columns[header[0][letter], header[1].get(letter, "")]
                for letter in header[0]
                if (header[0][letter], header[1].get(letter, "")) in columns
            }
            assert sorted(wanted.values()) == sorted(columns.values()), sorted(wanted.values())
            return {
                row["A"]: {i: float(row[c]) for c, i in wanted.items() if c in row}
                for row in rows
                if row.get("A")
            }
    raise SystemExit(f"no sheet of {path.name} has {KEY!r} in column A of row 4")


def shared_strings(book: zipfile.ZipFile) -> list[str]:
    strings: list[str] = []
    with book.open("xl/sharedStrings.xml") as file:
        for _, item in etree.iterparse(file, tag=f"{_NS}si"):
            strings.append("".join(item.itertext()))
            item.clear()
    return strings


def sheet_rows(book: zipfile.ZipFile, sheet: str, strings: list[str]) -> Iterator[dict[str, str]]:
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
                letters = re.match(r"[A-Z]+", cell.get("r"))
                cells[letters.group()] = text
            row.clear()
            parent = row.getparent()
            while parent is not None and row.getprevious() is not None:
                del parent[0]
            yield cells


if __name__ == "__main__":
    sys.exit(main())
