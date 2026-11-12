"""Scoring many SKUs at once: a table of amounts times the stored score vectors."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.compute as _compute

from headwater.calc import sku_scores
from headwater.calc.arrays import Vector
from headwater.model import InvalidDataError, Problem, did_you_mean
from headwater.query._internals import internals
from headwater.query.background import Background
from headwater.query.result import counted
from headwater.release import Release, ReleaseRef
from headwater.release.indicators import read_indicators, stored_scores

pc: Any = _compute
"""pyarrow-stubs types the compute functions as partly unknown: the calls below are plain Arrow."""

COLUMNS = ("sku", "product_id", "amount")
SHOWN_PROBLEMS = 20
"""The most problems of one kind an error lists. The rest are counted."""


@dataclass(frozen=True, eq=False)
class ManyScores:
    """The score of every SKU for every indicator, and what they were computed from."""

    skus: tuple[str, ...]
    """In the order each SKU first appears in the table."""
    indicators: tuple[str, ...]
    units: tuple[str, ...]
    scores: Vector
    """One row per SKU and one column per indicator."""
    releases: tuple[ReleaseRef, ...]
    """The release or releases the score vectors come from. Empty for a folder."""
    method: str
    method_version: str
    method_hash: str

    def __getitem__(self, indicator: str) -> Vector:
        """The score of every SKU for one indicator."""
        if indicator not in self.indicators:
            raise KeyError(
                f"no indicator {indicator!r}. It has: {', '.join(self.indicators)}."
                f"{did_you_mean(indicator, self.indicators)}"
            )
        return self.scores[:, self.indicators.index(indicator)]

    def __repr__(self) -> str:
        """Example: `ManyScores(2 SKUs, 1 indicator, method toy-climate 1)`. `to_arrow()` holds
        the scores."""
        return (
            f"ManyScores({counted(len(self.skus), 'SKU', 'SKUs')}, "
            f"{counted(len(self.indicators), 'indicator', 'indicators')}, "
            f"method {self.method} {self.method_version})"
        )

    def to_arrow(self) -> pa.Table:
        """A table with `sku` and one float64 column per indicator."""
        columns: dict[str, Any] = {"sku": list(self.skus)}
        columns.update({i: self[i] for i in self.indicators})
        return pa.Table.from_pydict(columns)


@dataclass(frozen=True)
class _Vectors:
    """Where the scores come from: one row per product, one column per indicator."""

    product_ids: tuple[str, ...]
    indicators: tuple[str, ...]
    units: tuple[str, ...]
    scores: Vector
    releases: tuple[ReleaseRef, ...]
    method: str
    method_version: str
    method_hash: str
    source: str
    """How the error messages name it."""


def score_many(
    source: Background | Release,
    table: pa.Table | Mapping[str, Sequence[Any]],
    *,
    indicators: Sequence[str] | None = None,
) -> ManyScores:
    """Score a table of SKUs: for each, the sum of amount × the stored score of what it buys.

    This scores many products at once, as Brightway's MultiLCA does, with no solve: one sparse
    multiply with the stored score vectors, for every indicator asked.

    `table` has the columns `sku`, `product_id` and `amount`, one row per product a SKU buys. The
    `product_id` is the id of the process that makes the product, `toy:fabric-production`, and
    the amount is in the reference unit of its flow. For a waste treatment it is the kg sent,
    which scores as a positive burden per kg treated. A SKU and product that appear twice add up.

    `source` is a background, or an indicators release, which needs no factorization: its stored
    vectors are all this reads. Scores come back for `indicators`, or for every indicator of the
    method, sorted by id. Flows that the method does not cover count 0, as in every score: see
    `Background.not_characterised`.

    Example: `{"sku": ["a", "a"], "product_id": ["toy:fabric-production",
    "toy:electricity-production"], "amount": [2.0, 1.0]}` scores 2 × 3.7086 + 1 × 0.5298.
    Raises InvalidDataError naming each SKU and product with an unknown product or an amount
    that is not a finite number.
    """
    vectors = _vectors(source)
    selected = _select(vectors, indicators)
    data = _table(table)
    if data.num_rows == 0:
        return _result(vectors, selected, (), np.zeros((0, len(selected))))
    skus = pc.cast(_chunks(data, "sku"), pa.string())
    products = pc.cast(_chunks(data, "product_id"), pa.string())
    amounts = _amounts(data, skus, products)
    position = pc.index_in(products, value_set=pa.array(vectors.product_ids, pa.string()))
    _refuse_unknown(vectors, skus, products, position)
    encoded = pc.dictionary_encode(skus)
    scores = sku_scores(
        encoded.indices.to_numpy(zero_copy_only=False),
        position.to_numpy(zero_copy_only=False),
        amounts,
        vectors.scores[:, selected],
        n_skus=len(encoded.dictionary),
    )
    return _result(vectors, selected, tuple(encoded.dictionary.to_pylist()), scores)


def _vectors(source: Background | Release) -> _Vectors:
    if isinstance(source, Release):
        found = read_indicators(source)
        return _Vectors(
            product_ids=found.product_ids,
            indicators=found.indicators,
            units=found.units,
            scores=found.scores,
            releases=found.releases,
            method=found.method_id,
            method_version=found.method_version,
            method_hash=found.method_hash,
            source=f"release {source.name}",
        )
    inner = internals(source)
    solved = inner.solved
    units = {i.id: i.unit for i in source.method.indicators}
    ids = sorted(units)
    scores = np.column_stack(
        [stored_scores(solved.x[i].values, solved.block.reference_sign) for i in ids]
    )
    return _Vectors(
        product_ids=solved.ids,
        indicators=tuple(ids),
        units=tuple(units[i] for i in ids),
        scores=scores,
        releases=inner.releases,
        method=source.method.id,
        method_version=source.method.version,
        method_hash=inner.method_hash,
        source=f"method {source.method.id}",
    )


def _select(vectors: _Vectors, indicators: Sequence[str] | None) -> list[int]:
    if indicators is None:
        return list(range(len(vectors.indicators)))
    for indicator in indicators:
        if indicator not in vectors.indicators:
            raise ValueError(
                f"{vectors.source} has no indicator {indicator!r}. It has: "
                f"{', '.join(vectors.indicators)}.{did_you_mean(indicator, vectors.indicators)}"
            )
    return [vectors.indicators.index(i) for i in indicators]


def _result(
    vectors: _Vectors, selected: Sequence[int], skus: tuple[str, ...], scores: Vector
) -> ManyScores:
    return ManyScores(
        skus=skus,
        indicators=tuple(vectors.indicators[i] for i in selected),
        units=tuple(vectors.units[i] for i in selected),
        scores=scores,
        releases=vectors.releases,
        method=vectors.method,
        method_version=vectors.method_version,
        method_hash=vectors.method_hash,
    )


def _table(table: pa.Table | Mapping[str, Sequence[Any]]) -> pa.Table:
    if isinstance(table, pa.Table):
        data = table
    else:
        data = pa.Table.from_pydict({k: list(v) for k, v in table.items()})
    problems = [
        Problem(
            "missing-column",
            column,
            f"the table needs the columns {', '.join(COLUMNS)}, and has "
            f"{', '.join(data.column_names) or 'none'}",
        )
        for column in COLUMNS
        if column not in data.column_names
    ]
    if problems:
        raise InvalidDataError(problems)
    return data


def _chunks(data: pa.Table, name: str) -> "pa.Array[Any]":
    column = data.column(name)
    if column.null_count:
        raise InvalidDataError(
            [
                Problem(
                    "null-value",
                    f"column {name}",
                    f"has {column.null_count} empty values. Every row needs a SKU, a product "
                    "and an amount.",
                )
            ]
        )
    return column.combine_chunks()


def _amounts(data: pa.Table, skus: "pa.Array[Any]", products: "pa.Array[Any]") -> Vector:
    column = _chunks(data, "amount")
    try:
        amounts = pc.cast(column, pa.float64())
    except (pa.ArrowInvalid, pa.ArrowNotImplementedError) as error:
        raise InvalidDataError(
            [Problem("bad-amount", "column amount", f"must hold numbers: {error}")]
        ) from None
    finite = pc.is_finite(amounts).to_numpy(zero_copy_only=False)
    if not finite.all():
        bad = np.flatnonzero(~finite)
        raise InvalidDataError(
            _limited(
                [
                    Problem(
                        "bad-amount",
                        f"sku {skus[int(i)]}, product {products[int(i)]}",
                        f"amount {amounts[int(i)]} is not a finite number",
                    )
                    for i in bad[:SHOWN_PROBLEMS]
                ],
                len(bad),
            )
        )
    return amounts.to_numpy(zero_copy_only=False)


def _refuse_unknown(
    vectors: _Vectors, skus: "pa.Array[Any]", products: "pa.Array[Any]", position: "pa.Array[Any]"
) -> None:
    if not position.null_count:
        return
    bad = np.flatnonzero(position.is_null().to_numpy(zero_copy_only=False))
    problems = [
        Problem(
            "unknown-product",
            f"sku {skus[int(i)]}, product {products[int(i)]}",
            f"is not a product of {vectors.source}."
            f"{did_you_mean(str(products[int(i)]), vectors.product_ids)}",
        )
        for i in bad[:SHOWN_PROBLEMS]
    ]
    raise InvalidDataError(_limited(problems, len(bad)))


def _limited(problems: list[Problem], total: int) -> list[Problem]:
    if total > len(problems):
        problems.append(
            Problem("more-problems", "table", f"and {total - len(problems)} more, not shown")
        )
    return problems
