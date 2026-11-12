"""The toy background from docs/calculation.md, as matrices.

Processes (columns) and products (rows) share one index:
0 T-shirt, 1 cotton fabric, 2 electricity, 3 incineration of textile waste.
Elementary flows: 0 CO2, 1 CH4. Method toy-climate: CO2 = 1, CH4 = 29.8.

The matrices use ecoinvent's convention: a reference output is on the diagonal, inputs are
negative, a treatment's reference is -1 and a waste sent is +kg in the row of the waste.
"""

from dataclasses import dataclass

import numpy as np
import pytest
from numpy.typing import NDArray
from scipy import sparse


@dataclass(frozen=True)
class Toy:
    A: sparse.csc_array
    B: sparse.csc_array
    c: NDArray[np.float64]
    tshirt: int = 0
    fabric: int = 1
    electricity: int = 2
    incineration: int = 3

    @property
    def h(self) -> NDArray[np.float64]:
        return self.B.T @ self.c

    @property
    def n(self) -> int:
        return self.A.shape[0]


def toy_technosphere(waste_kg: float = 0.0) -> NDArray[np.float64]:
    return np.array(
        [
            [1.0, 0.0, 0.0, 0.0],
            [-0.25, 1.0, 0.0, 0.0],
            [-0.5, -3.0, 1.0, 0.0],
            [waste_kg, 0.0, 0.0, -1.0],
        ]
    )


TOY_B = np.array(
    [
        [0.0, 2.0, 0.5, 1.2],
        [0.0, 0.004, 0.001, 0.0],
    ]
)
TOY_C = np.array([1.0, 29.8])


@pytest.fixture
def toy() -> Toy:
    """The T-shirt without the waste line."""
    return Toy(A=sparse.csc_array(toy_technosphere()), B=sparse.csc_array(TOY_B), c=TOY_C)


@pytest.fixture
def toy_waste() -> Toy:
    """The T-shirt sending 0.05 kg of scraps to incineration."""
    return Toy(A=sparse.csc_array(toy_technosphere(0.05)), B=sparse.csc_array(TOY_B), c=TOY_C)
