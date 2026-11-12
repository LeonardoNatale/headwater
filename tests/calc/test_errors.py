import pickle

import pytest

from headwater.calc import CalculationError, ResidualError, SingularMatrixError


@pytest.mark.parametrize(
    "error",
    [
        CalculationError("bad input at indices", [3]),
        SingularMatrixError("processes with no reference output", [1, 2]),
        ResidualError("residual above tolerance, in rows", [0], 0.5),
    ],
    ids=lambda e: type(e).__name__,
)
def test_errors_survive_pickling_for_worker_processes(error: CalculationError) -> None:
    copy = pickle.loads(pickle.dumps(error))

    assert type(copy) is type(error)
    assert str(copy) == str(error)
    assert copy.indices == error.indices
    assert getattr(copy, "residual", None) == getattr(error, "residual", None)


def test_message_names_at_most_twenty_indices() -> None:
    error = SingularMatrixError("processes with no reference output", range(25))

    assert str(error).endswith("19 and 5 more")
    assert error.indices == tuple(range(25))


@pytest.mark.parametrize(
    "error",
    [
        CalculationError("bad input at indices", [0, 3]),
        SingularMatrixError("processes with no reference output", [1, 2]),
        ResidualError("residual above tolerance, in rows", [0], 0.5),
    ],
    ids=lambda e: type(e).__name__,
)
def test_shifted_moves_every_index_and_keeps_everything_else(error: CalculationError) -> None:
    moved = error.shifted(10)

    assert type(moved) is type(error)
    assert moved.indices == tuple(i + 10 for i in error.indices)
    assert moved.message == error.message
    assert getattr(moved, "residual", None) == getattr(error, "residual", None)
    assert error.indices == tuple(error.indices)  # the original is unchanged
