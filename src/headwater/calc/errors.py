"""Errors raised when a calculation cannot give a trustworthy number."""

from collections.abc import Iterable

from headwater.model.errors import HeadwaterError

SHOWN = 20


class CalculationError(HeadwaterError):
    """A calculation cannot give a trustworthy number.

    `indices` names the processes involved, as matrix indices of this calculation. The caller
    maps them back to ids.
    """

    def __init__(self, message: str, indices: Iterable[int] = ()) -> None:
        self.message = message
        self.indices: tuple[int, ...] = tuple(int(i) for i in indices)
        text = message
        if self.indices:
            shown = ", ".join(str(i) for i in self.indices[:SHOWN])
            more = len(self.indices) - SHOWN
            text = f"{message}: {shown}" + (f" and {more} more" if more > 0 else "")
        super().__init__(text)

    def __reduce__(self) -> tuple[type, tuple[object, ...]]:
        return (type(self), (self.message, self.indices))

    def shifted(self, offset: int) -> "CalculationError":
        """The same error with every index moved by `offset`.

        For a calculation on a block that sits below another one in a stacked system.
        """
        return type(self)(self.message, (i + offset for i in self.indices))


class SingularMatrixError(CalculationError):
    """The technosphere matrix cannot be solved."""


class ResidualError(CalculationError):
    """A result does not satisfy its equations within the tolerance."""

    def __init__(self, message: str, indices: Iterable[int], residual: float) -> None:
        super().__init__(f"{message} (largest {residual:.3g})", indices)
        self.message = message
        self.residual = residual

    def __reduce__(self) -> tuple[type, tuple[object, ...]]:
        return (type(self), (self.message, self.indices, self.residual))

    def shifted(self, offset: int) -> "ResidualError":
        return ResidualError(self.message, (i + offset for i in self.indices), self.residual)
