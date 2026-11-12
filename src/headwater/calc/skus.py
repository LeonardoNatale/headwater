"""Scores of many SKUs at once: a sparse matrix of amounts times the stored score vectors."""

import numpy as np
from numpy.typing import ArrayLike
from scipy import sparse

from headwater.calc.arrays import Vector, as_vector


def sku_scores(
    sku: ArrayLike,
    product: ArrayLike,
    amount: ArrayLike,
    vectors: ArrayLike,
    *,
    n_skus: int,
) -> Vector:
    """The score of each SKU for every indicator: amounts times the score of what is bought.

    Example: SKU 0 buys 2 of product 0 and 1 of product 2, so `sku=[0, 0]`, `product=[0, 2]`,
    `amount=[2.0, 1.0]`. Its score is 2 × vectors[0] + 1 × vectors[2], one value per indicator.

    `sku` and `product` are indices of the same length as `amount`, one entry per line of the
    table. A SKU and product that appear twice add up. `vectors` holds one row per product and
    one column per indicator: the score per unit of the product, which for a waste treatment is
    the burden per kg treated, so a waste sent is a positive amount. Returns an array of shape
    (n_skus, n_indicators). A SKU with no line scores 0.
    """
    matrix = np.asarray(vectors, dtype=np.float64)
    if matrix.ndim != 2:
        raise ValueError(f"vectors must have one row per product, got shape {matrix.shape}")
    sku_index = np.asarray(sku, dtype=np.int64)
    product_index = np.asarray(product, dtype=np.int64)
    if not sku_index.shape == product_index.shape == np.shape(amount):
        raise ValueError("sku, product and amount must have the same length")
    amounts = as_vector(amount, sku_index.shape[0], "amount")
    if sku_index.size and not (0 <= sku_index.min() and sku_index.max() < n_skus):
        raise ValueError(f"sku index out of range for {n_skus} SKUs")
    if product_index.size and not (0 <= product_index.min() and product_index.max() < len(matrix)):
        raise ValueError(f"product index out of range for {len(matrix)} products")
    counts = sparse.csr_array((amounts, (sku_index, product_index)), shape=(n_skus, len(matrix)))
    return np.asarray(counts @ matrix, dtype=np.float64)
