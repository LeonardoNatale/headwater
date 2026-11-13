"""Reading a distribution written next to an amount: a line of exchanges.csv, or a parameter.

The centre of a distribution is always the amount itself, so it is never written: the median of
a lognormal (`scale` is the natural log of the geometric standard deviation), the mean of a
normal (`scale` is the standard deviation) and the mode of a triangular. Example: 0.25 kg of
fabric with a GSD of 1.1 is `lognormal` with `scale` 0.0953102, and `loc` ln 0.25.
"""

import math
from collections.abc import Callable, Mapping

from headwater.model import Distribution, Problem, Uncertainty, did_you_mean

COLUMNS = ("distribution", "scale", "minimum", "maximum")
"""The cells of a distribution, as columns of exchanges.csv and keys of a parameter."""

_NEEDS: dict[Distribution, tuple[str, ...]] = {
    Distribution.lognormal: ("scale",),
    Distribution.normal: ("scale",),
    Distribution.uniform: ("minimum", "maximum"),
    Distribution.triangular: ("minimum", "maximum"),
}


def read_uncertainty(
    cells: Mapping[str, object],
    centre: float,
    *,
    what: str,
    item: str,
    location: str | None,
    number: Callable[[object, str], float],
    problems: list[Problem],
) -> Uncertainty | None:
    """The distribution in `cells` around `centre`, or None when no cell is given.

    `what` names the centre in messages (`amount`, `value`), and `number` reads one cell as a
    number, raising ValueError with a message for a person. Each mistake is added to `problems`.
    """
    given = {c: cells[c] for c in COLUMNS if not _blank(cells.get(c))}
    if not given:
        return None

    def problem(code: str, message: str) -> None:
        problems.append(Problem(code, item, message, location=location))

    text = given.pop("distribution", None)
    if text is None:
        problem("bad-uncertainty", f"{' and '.join(given)} is given without a distribution")
        return None
    try:
        distribution = Distribution(str(text).strip())
    except ValueError:
        names = [d.value for d in Distribution]
        problem(
            "unknown-distribution",
            f"unknown distribution {text!r}.{did_you_mean(str(text), names)} "
            f"Distributions: {', '.join(names)}",
        )
        return None
    values: dict[str, float] = {}
    for column, raw in given.items():
        try:
            values[column] = number(raw, column)
        except ValueError as exc:
            problem("bad-number", str(exc))
    if len(values) < len(given):
        return None

    needs = _NEEDS[distribution]
    missing = [c for c in needs if c not in values]
    if missing:
        problem(
            "incomplete-uncertainty",
            f"a {distribution.value} needs {' and '.join(needs)}",
        )
        return None
    unused = [c for c in values if c not in needs]
    if unused:
        problem(
            "bad-uncertainty",
            f"a {distribution.value} does not use {' and '.join(unused)}",
        )
        return None

    scale = values.get("scale")
    if scale is not None and scale <= 0:
        problem("bad-uncertainty", f"scale {scale:g} must be above 0")
        return None
    low, high = values.get("minimum"), values.get("maximum")
    if low is not None and high is not None:
        if low > high:
            problem("bad-uncertainty", f"minimum {low:g} is above maximum {high:g}")
            return None
        if not low <= centre <= high:
            problem(
                "bad-uncertainty",
                f"{what} {centre:g} is outside minimum {low:g} and maximum {high:g}: the "
                f"{what} is the centre of its distribution",
            )
            return None
    if distribution is Distribution.lognormal:
        if centre == 0:
            problem(
                "bad-uncertainty",
                f"a lognormal's median is the {what}, and a {what} of 0 has no logarithm",
            )
            return None
        loc = math.log(abs(centre))
    elif distribution is Distribution.uniform:
        loc = None
    else:
        loc = centre
    return Uncertainty(distribution=distribution, loc=loc, scale=scale, minimum=low, maximum=high)


def _blank(raw: object) -> bool:
    return raw is None or (isinstance(raw, str) and not raw.strip())
