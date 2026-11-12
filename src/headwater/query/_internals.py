"""What the package's own code reads from a background or a result, and users do not see.

A background's matrices and the row of each process, and the system a result solved, hold
integer indices, which never leave a calculation (AGENTS.md, rule 1). So they are not on
`Background` or `Result`: this package reaches them with `internals(bg)` and `internals(result)`.

The module imports nothing heavy, so a command can import it at the top of its module.
"""

from typing import TYPE_CHECKING, overload

if TYPE_CHECKING:
    from headwater.query._system import System
    from headwater.query.background import Background, Internals
    from headwater.query.result import Result


@overload
def internals(of: "Background") -> "Internals": ...
@overload
def internals(of: "Result") -> "System": ...
def internals(of: "Background | Result") -> "Internals | System":
    """The matrices, ids and records of a background, or the system a result solved: the study
    and its background stacked, from which a diff across a release change splits a score."""
    from headwater.query.background import Background  # loaded: `of` is one, or a Result

    if isinstance(of, Background):
        return of._internals  # pyright: ignore[reportPrivateUsage]
    return of._system  # pyright: ignore[reportPrivateUsage]
