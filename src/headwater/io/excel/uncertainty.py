"""Brightway's uncertainty fields as the distribution of a line or a parameter.

A workbook and a project both write a distribution with the fields of stats_arrays:
`uncertainty type` (2 lognormal, 3 normal, 4 uniform, 5 triangular), `loc`, `scale`, `minimum`
and `maximum`. Both importers put them on their rows as written, and this one mapping turns them
into a study's distribution columns.

Example: 0.5 kWh with `uncertainty type` 2, `loc` ln 0.5 and `scale` ln 1.1 is a line with
`distribution` lognormal and `scale` 0.0953102. A waste line turns its sign: -0.05 kg uniform
between -0.06 and -0.04 is 0.05 kg uniform between 0.04 and 0.06.

A study centres every distribution on the amount, so `loc` is not written: it must agree with the
amount, or the distribution is not copied, and the report says so. With no `loc`, Brightway takes
the amount as `loc`, so a normal or a triangular is centred on it, as in a study, and a lognormal
is not, since its `loc` is the logarithm of its median.
"""

import math
from collections.abc import Mapping

from headwater.model import Problem, Uncertainty
from headwater.study._uncertainty import read_uncertainty

FIELDS = ("uncertainty type", "loc", "scale", "shape", "minimum", "maximum", "pedigree")
"""The fields of a Brightway record that say how uncertain its amount is."""
_SPREAD = ("scale", "shape", "minimum", "maximum", "pedigree")
"""The fields that mean a distribution was meant, when there is no `uncertainty type`."""

_DISTRIBUTIONS = {2: "lognormal", 3: "normal", 4: "uniform", 5: "triangular"}
_OTHERS = {
    6: "Bernoulli",
    7: "discrete uniform",
    8: "Weibull",
    9: "gamma",
    10: "beta",
    11: "generalized extreme value",
    12: "Student's t",
}
_AGREES = 1e-6
"""How close Brightway's `loc` must be to the centre the amount gives, relatively."""


def fields_of(record: Mapping[str, object]) -> dict[str, object]:
    """The uncertainty fields a Brightway record has, from `FIELDS`."""
    return {key: record[key] for key in FIELDS if record.get(key) is not None}


def cells(
    fields: Mapping[str, object], amount: float, *, flip: bool
) -> dict[str, object] | str | None:
    """The distribution columns of a study for Brightway's `fields` on `amount`, as Brightway
    writes the amount. `flip` turns the sign, as on a waste line. None when there is no
    distribution, and the reason as text when a study cannot hold it."""
    kind = fields.get("uncertainty type")
    if kind is None:
        given = [key for key in _SPREAD if not _missing(fields.get(key))]
        if given:
            return (
                f"has {' and '.join(given)} but no uncertainty type, so Brightway draws no "
                "distribution either"
            )
        return None
    if isinstance(kind, float) and kind.is_integer():
        kind = int(kind)  # written by a tool that stores every number as a float
    if isinstance(kind, bool) or not isinstance(kind, int):
        return f"has uncertainty type {kind!r}, which is not a stats_arrays number"
    if kind in (0, 1):
        return None
    name = _DISTRIBUTIONS.get(kind)
    if name is None:
        return (
            f"has uncertainty type {kind} ({_OTHERS.get(kind, 'unknown')}). A database holds "
            "lognormal (2), normal (3), uniform (4) and triangular (5) only"
        )
    values: dict[str, float | None] = {}
    for key in ("loc", "scale", "minimum", "maximum"):
        raw = fields.get(key)
        if raw is None or (key != "loc" and _missing(raw)):
            values[key] = None  # Brightway takes the amount as a `loc` it does not have
        elif isinstance(raw, int | float) and not isinstance(raw, bool) and math.isfinite(raw):
            values[key] = float(raw)
        else:
            return f"has {key} {raw!r}, which is not a number"
    loc = values["loc"]
    if name != "uniform":
        if name == "lognormal":
            if amount == 0:
                return "is a lognormal on an amount of 0, which has no logarithm"
            centre, said = math.log(abs(amount)), "ln of the amount"
            agrees = loc is not None and math.isclose(math.exp(loc), abs(amount), rel_tol=_AGREES)
        else:
            centre, said = amount, "the amount"
            agrees = loc is None or math.isclose(loc, amount, rel_tol=_AGREES, abs_tol=1e-12)
        if not agrees:
            given = "no loc" if loc is None else f"loc {loc:g}"
            return (
                f"is a {name} with {given}, and a database centres it on {said}, {centre:g}. "
                "Set its loc to that, or give the line the distribution in the database"
            )
    if name in ("lognormal", "normal"):
        return {"distribution": name, "scale": values["scale"]}
    low, high = values["minimum"], values["maximum"]
    if flip and low is not None and high is not None:
        low, high = -high, -low
    return {"distribution": name, "minimum": low, "maximum": high}


def distribution(
    fields: Mapping[str, object],
    written: float,
    centre: float,
    *,
    flip: bool,
    what: str,
    item: str,
) -> tuple[Uncertainty | None, str | None]:
    """The distribution of a line or a parameter whose amount Brightway writes as `written` and
    a study holds as `centre`, or why a study cannot hold it. `what` names the centre in
    messages (`amount`, `value`)."""
    found = cells(fields, written, flip=flip)
    if found is None or isinstance(found, str):
        return None, found
    problems: list[Problem] = []
    uncertainty = read_uncertainty(
        found,
        centre,
        what=what,
        item=item,
        location=None,
        number=_number,
        problems=problems,
    )
    if problems:
        return None, "does not fit: " + "; ".join(p.message for p in problems)
    return uncertainty, None


def _missing(raw: object) -> bool:
    return raw is None or (isinstance(raw, float) and math.isnan(raw))


def _number(raw: object, column: str) -> float:
    if isinstance(raw, int | float) and not isinstance(raw, bool):
        return float(raw)
    raise ValueError(f"{column} {raw!r} is not a number")
