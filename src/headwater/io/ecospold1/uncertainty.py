"""The uncertainty of an ecoSpold1 amount, and the pedigree its comment carries.

| `uncertaintyType` | Distribution | headwater's parameters                                  |
|-------------------|--------------|---------------------------------------------------------|
| 0                 | none         |                                                         |
| 1                 | lognormal    | `loc` ln of the amount, `scale` ln(SDg) (1)             |
| 2                 | normal       | `loc` the amount, `scale` half of `standardDeviation95` |
| 3                 | triangular   | `minValue`, `maxValue`, and the mode: `mostLikelyValue` |
| 4                 | uniform      | `minValue`, `maxValue`                                  |

(1) `standardDeviation95` is the square of the geometric standard deviation SDg.

No type and no `standardDeviation95` is no distribution. A `standardDeviation95` with no type is a
lognormal, the format's default. The triangular mean is calculated from the mode in the format, so
without `mostLikelyValue` the mode is 3 times the mean less the bounds.

A pedigree is five scores from 1 to 5 at the start of the comment, as ecoinvent writes them:
`(1,2,3,4,5,na)`. Anything after the fifth score is not read.
"""

import math
import re
from collections.abc import Callable
from dataclasses import dataclass

from pydantic import ValidationError

from headwater.model import RecordError
from headwater.model.uncertainty import Distribution, Pedigree, Uncertainty

_PEDIGREE = re.compile(r"\s*\(\s*" + r"\s*,\s*".join([r"([1-5])"] * 5) + r"\s*[,)]")
_FIELDS = tuple(Pedigree.model_fields)


@dataclass(frozen=True)
class Note:
    """Why an amount that names a distribution has none: a warning for people to read."""

    code: str
    message: str


class Refusal(ValueError):
    """An uncertainty that cannot be read, with the code of its problem."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def read_uncertainty(
    attribute: Callable[[str], str | None], amount: float
) -> tuple[Uncertainty | None, Note | None]:
    """The distribution of `amount` from the `uncertaintyType`, `standardDeviation95`, `minValue`,
    `maxValue` and `mostLikelyValue` of its exchange, read with `attribute(name)`. Raises
    `Refusal` for one that cannot be read. A distribution with no spread, or around zero, is no
    distribution, with a `Note`."""
    sd95 = attribute("standardDeviation95")
    kind = (attribute("uncertaintyType") or ("1" if sd95 is not None else "0")).strip()
    if kind == "0":
        return None, None

    def value(name: str) -> float:
        found = to_float(attribute(name))
        if found is None:
            raise Refusal(
                "bad-uncertainty",
                f"uncertaintyType {kind} needs a number in {name}, got {attribute(name)!r}",
            )
        return found

    try:
        match kind:
            case "1":
                sdg2 = value("standardDeviation95")
                if sdg2 < 1:
                    raise Refusal(
                        "bad-uncertainty",
                        f"standardDeviation95 {sdg2:g} of a lognormal is the square of a geometric "
                        "standard deviation, so it is at least 1",
                    )
                if amount == 0:
                    return None, Note(
                        "zero-amount-uncertainty",
                        "has a lognormal distribution around an amount of 0, which has none: "
                        "kept as a fixed amount of 0",
                    )
                if sdg2 == 1:
                    return None, _fixed("lognormal")
                return Uncertainty(
                    distribution=Distribution.lognormal,
                    loc=math.log(abs(amount)),
                    scale=math.log(sdg2) / 2,
                ), None
            case "2":
                scale = value("standardDeviation95") / 2
                if scale < 0:
                    raise Refusal(
                        "bad-uncertainty", "standardDeviation95 of a normal must not be negative"
                    )
                if scale == 0:
                    return None, _fixed("normal")
                return Uncertainty(distribution=Distribution.normal, loc=amount, scale=scale), None
            case "3":
                low, high = value("minValue"), value("maxValue")
                mode = to_float(attribute("mostLikelyValue"))
                return Uncertainty(
                    distribution=Distribution.triangular,
                    minimum=low,
                    maximum=high,
                    loc=3 * amount - low - high if mode is None else mode,
                ), None
            case "4":
                return Uncertainty(
                    distribution=Distribution.uniform,
                    minimum=value("minValue"),
                    maximum=value("maxValue"),
                ), None
            case _:
                raise Refusal(
                    "unsupported-uncertainty",
                    f"has uncertaintyType {kind!r}; supported are 0 none, 1 lognormal, 2 normal, "
                    "3 triangular and 4 uniform",
                )
    except ValidationError as error:
        problems = [
            p
            for err in error.errors()
            if isinstance(inner := (err.get("ctx") or {}).get("error"), RecordError)
            for p in inner.problems
        ]
        raise Refusal("bad-uncertainty", "; ".join(p.message for p in problems)) from None


def negate(uncertainty: Uncertainty) -> Uncertainty:
    """The distribution of the negated amount. A lognormal describes ln|x|, so it stays."""
    u = uncertainty
    match u.distribution:
        case Distribution.lognormal:
            return u
        case Distribution.normal:
            return u.model_copy(update={"loc": -u.loc if u.loc is not None else None})
        case Distribution.uniform:
            return u.model_copy(update=_bounds(u))
        case Distribution.triangular:
            mode = {"loc": -u.loc if u.loc is not None else None}
            return u.model_copy(update={**_bounds(u), **mode})


def _bounds(u: Uncertainty) -> dict[str, float | None]:
    return {
        "minimum": -u.maximum if u.maximum is not None else None,
        "maximum": -u.minimum if u.minimum is not None else None,
    }


def _fixed(name: str) -> Note:
    return Note(
        "zero-variance",
        f"has a {name} distribution with no spread: kept as a fixed amount",
    )


def to_float(text: str | None) -> float | None:
    try:
        found = float(text) if text is not None else None
    except ValueError:
        return None
    return found if found is not None and math.isfinite(found) else None


def read_pedigree(comment: str | None) -> Pedigree | None:
    """The pedigree matrix at the start of a comment, or None when it does not start with five
    scores from 1 to 5."""
    found = _PEDIGREE.match(comment or "")
    if found is None:
        return None
    return Pedigree(
        **{field: int(score) for field, score in zip(_FIELDS, found.groups(), strict=True)}
    )
