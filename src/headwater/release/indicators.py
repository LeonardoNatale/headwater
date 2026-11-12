"""The indicator vectors of an indicators release: what the file holds, how it is written and read.

`indicators/<method>.parquet` has one row per process of the source release: `product_id`, then
one float64 column per indicator. Each score is per unit of what the process makes, or per unit
of waste it treats, so a waste sent to treatment is a positive amount times a positive burden.
The toy: 1 kWh of electricity scores 0.5298 kg CO2-eq, 1 kg of fabric 3.7086, and 1 kg of
textile waste sent to incineration 1.2.

Inside the matrices a treatment's reference is -1, and calc's transposed solve gives it -1.2.
The file holds that number times the sign of the process's reference, once, in `stored_scores`.
Scoring a SKU, or a study's purchases, is then amount times score whatever the product is.
"""

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import cast

import numpy as np
from numpy.typing import NDArray
from pydantic import JsonValue

from headwater.calc import SolverSettings, Step
from headwater.calc.arrays import Vector
from headwater.model import InvalidDataError, Method, Problem
from headwater.model.method import JOIN
from headwater.release.content import methods_hash
from headwater.release.manifest import MethodEntry, ReleaseKind, ReleaseRef
from headwater.release.read import Release
from headwater.release.write import build_indicators


def stored_scores(x: Vector, reference_sign: Vector) -> Vector:
    """Scores as the file holds them: calc's `x` times the sign of each process's reference.

    The only place that turns solver scores into stored ones, and back (the sign is its own
    inverse).
    """
    return np.asarray(x * reference_sign, dtype=np.float64)


def write_indicators(
    parent: Release,
    method: Method | Sequence[Method],
    *,
    product_ids: Sequence[str],
    x: Mapping[str, Vector],
    reference_sign: Vector,
    solver: SolverSettings,
    steps: Mapping[str, Sequence[Step]],
    residual: float,
    exact_zeros: Mapping[str, int],
    name: str | None = None,
    into: Path | str | None = None,
) -> Release:
    """Freeze solved scores into a new indicators release beside `parent`.

    `x` maps each indicator of the method to calc's scores for `product_ids`, in the order of
    `reference_sign`. `method` can be several methods used together: the release holds each.
    The release's recipe holds the solver settings asked for. Whether the factors were pivoted
    (`diag_pivot_thresh`) is not one of them: the solver falls back to pivoting when rounding
    on this machine fails the residual check. So it goes in the provenance, with the steps of
    each solve, the largest residual, and per indicator how many scores are exactly 0 by
    structure, set rather than computed. The parent is not touched.
    """
    settings = asdict(solver)
    asked = {k: v for k, v in settings.items() if k != "diag_pivot_thresh"}
    provenance: dict[str, JsonValue] = {
        "solver": settings,
        "steps": {indicator: [step.value for step in found] for indicator, found in steps.items()},
        "residual": residual,
        "exact_zeros": dict(exact_zeros),
        "scores": "per unit of what the process makes, or of waste it treats",
    }
    return build_indicators(
        parent,
        method,
        product_ids=product_ids,
        scores={i: stored_scores(v, reference_sign) for i, v in x.items()},
        solver=asked,
        provenance=provenance,
        name=name,
        into=into,
    )


@dataclass(frozen=True, eq=False)
class Indicators:
    """The score vectors of an indicators release, read back."""

    release: Release
    methods: tuple[MethodEntry, ...]
    """The methods the release holds, in the order it lists them."""
    product_ids: tuple[str, ...]
    indicators: tuple[str, ...]
    """Sorted by id within each method, as in the files, the methods in order."""
    units: tuple[str, ...]
    scores: NDArray[np.float64]
    """One row per product and one column per indicator."""

    @property
    def method_id(self) -> str:
        """The method's id, or the ids of several joined by `+`: `EF-3.1+IPCC-2021`."""
        return JOIN.join(m.id for m in self.methods)

    @property
    def method_version(self) -> str:
        return JOIN.join(m.version for m in self.methods)

    @property
    def method_hash(self) -> str:
        return methods_hash([m.hash for m in self.methods])

    @property
    def releases(self) -> tuple[ReleaseRef, ...]:
        """The source release, then this one."""
        parent = self.release.parent
        return (parent, self.release.ref) if parent is not None else (self.release.ref,)


def read_indicators(release: Release) -> Indicators:
    """The score vectors of an indicators release. Raises for a source release: it has none."""
    if release.kind is not ReleaseKind.indicators:
        raise InvalidDataError(
            [
                Problem(
                    "source-release",
                    f"release {release.name}",
                    "is a source release and holds no scores. Load it with hw.load, call "
                    "write_indicators() on the result, and read the scores from that release.",
                )
            ]
        )
    entries = release.method_entries
    columns: list[NDArray[np.float64]] = []
    ids: tuple[str, ...] | None = None
    for entry in entries:
        table = release.table(f"indicators/{entry.id}")
        found = tuple(cast(list[str], table.column("product_id").to_pylist()))
        if ids is not None and found != ids:
            raise InvalidDataError(
                [
                    Problem(
                        "product-mismatch",
                        f"release {release.name}",
                        f"indicators/{entry.id} lists other products, or in another order, "
                        f"than indicators/{entries[0].id}",
                    )
                ]
            )
        ids = found
        columns += [table.column(i.id).to_numpy(zero_copy_only=False) for i in entry.indicators]
    assert ids is not None  # opening the release refused one with no method
    indicators = [i for entry in entries for i in entry.indicators]
    return Indicators(
        release,
        entries,
        ids,
        tuple(i.id for i in indicators),
        tuple(i.unit for i in indicators),
        np.column_stack(columns).astype(np.float64),
    )
