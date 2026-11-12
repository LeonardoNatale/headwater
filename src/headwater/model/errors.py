import difflib
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum


class Severity(StrEnum):
    error = "error"
    warning = "warning"


@dataclass(frozen=True)
class Problem:
    """One thing a person has to fix, or to know about.

    `code` is stable, so tests and tools can match on it. `item` names what is wrong, for
    example `tshirt:tshirt line e02` or `flow toy:n2o`. `location` is a file and a row, when known.
    """

    code: str
    item: str
    message: str
    severity: Severity = Severity.error
    location: str | None = None

    def __str__(self) -> str:
        where = f" [{self.location}]" if self.location else ""
        return f"{self.item}: {self.message}{where}"


def _describe(problems: tuple[Problem, ...]) -> str:
    noun = "problem" if len(problems) == 1 else "problems"
    return "\n".join([f"{len(problems)} {noun}:", *(f"- {p}" for p in problems)])


class HeadwaterError(Exception):
    """Base class of every error headwater raises on purpose."""


class InvalidDataError(HeadwaterError):
    """Files or records are wrong. Carries every problem found, not only the first."""

    def __init__(self, problems: Iterable[Problem]) -> None:
        self.problems = tuple(problems)
        super().__init__(_describe(self.problems))

    def __reduce__(self) -> tuple[type["InvalidDataError"], tuple[tuple[Problem, ...]]]:
        return (type(self), (self.problems,))


class RecordError(ValueError):
    """Raised inside a record validator. Pydantic wraps it, the readers unwrap it again."""

    def __init__(self, problems: Iterable[Problem]) -> None:
        self.problems = tuple(problems)
        super().__init__("; ".join(str(p) for p in self.problems))

    def __reduce__(self) -> tuple[type["RecordError"], tuple[tuple[Problem, ...]]]:
        return (type(self), (self.problems,))

    @classmethod
    def one(cls, code: str, item: str, message: str) -> "RecordError":
        return cls([Problem(code=code, item=item, message=message)])


@dataclass(frozen=True)
class Report:
    """The outcome of validation: errors stop the work, warnings are for people to read."""

    problems: tuple[Problem, ...] = ()

    @property
    def errors(self) -> tuple[Problem, ...]:
        return tuple(p for p in self.problems if p.severity is Severity.error)

    @property
    def warnings(self) -> tuple[Problem, ...]:
        return tuple(p for p in self.problems if p.severity is Severity.warning)

    def raise_if_errors(self) -> None:
        if self.errors:
            raise InvalidDataError(self.errors)

    def __repr__(self) -> str:
        """`Report(no problems)`, or the count of each severity and the first problems, errors
        first::

            Report(1 error, 1 warning)
              error: tshirt:tshirt line e02: no supplier
              warning: study tshirt: goal: is still a placeholder
        """
        counts = [
            f"{n} {severity.value}{'' if n == 1 else 's'}"
            for severity, n in (
                (Severity.error, len(self.errors)),
                (Severity.warning, len(self.warnings)),
            )
            if n
        ]
        if not counts:
            return "Report(no problems)"
        shown = (*self.errors, *self.warnings)
        lines = [f"Report({', '.join(counts)})"]
        lines += [f"  {p.severity.value}: {p}" for p in shown[:_SHOWN]]
        if len(shown) > _SHOWN:
            lines.append(f"  and {len(shown) - _SHOWN} more")
        return "\n".join(lines)


_SHOWN = 5
"""A report prints at most this many problems."""


def did_you_mean(value: str, options: Iterable[str]) -> str:
    """` Did you mean 'x'?` for the closest option, or an empty string."""
    close = difflib.get_close_matches(value, list(options), n=1, cutoff=0.6)
    return f" Did you mean {close[0]!r}?" if close else ""


def with_article(phrase: str) -> str:
    """The phrase with `a` or `an` before it: `an elementary flow`, `a product flow`."""
    return f"{'an' if phrase[:1].lower() in 'aeiou' else 'a'} {phrase}"
