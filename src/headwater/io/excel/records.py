"""What the parts of a conversion hand to each other, and the small text helpers they share."""

import hashlib
import math
import re
import unicodedata
from dataclasses import dataclass

from headwater.io.excel.report import Status
from headwater.io.excel.workbook import Activity, ExchangeRow
from headwater.io.files import one_line
from headwater.model import FlowKind, Role, Uncertainty

NATURAL_RESOURCE = "natural resource"
EMISSION_COMPARTMENTS = frozenset({"air", "water", "soil"})

RELATIVE = 1e-9
"""The relative tolerance for comparing two numbers the workbook states for one quantity."""


STEM_LIMIT = 100
"""The most characters of an id after `<study>:`, before a number added on a clash, `-2`. It is
also the name of the process's files."""


def slug(text: str) -> str:
    """Lower-case letters and digits joined by single hyphens: `Market for steel, RER` gives
    `market-for-steel-rer`."""
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", plain.lower()).strip("-")


def process_stem(name: str, location: str | None) -> str:
    """The part of an id after `<study>:`, from a name and location alone: the same activity gets
    the same id whatever else the workbook holds.

    The location is added unless the name already ends with it: `T-shirt` in GLO is
    `t-shirt-glo`, `Autoclave no EVA input GLO` in GLO is `autoclave-no-eva-input-glo`. A name
    too long for `STEM_LIMIT` is cut after a whole word, and the first 6 characters of the
    sha256 of the slug of its whole name come after it, so two names cut alike keep apart:
    `transport-freight-lorry-...-r134a-<6 characters>-rer`. The location is never cut. A name
    with no letter or digit in a-z and 0-9 is `item` and 6 characters of the sha256 of the name.
    """
    whole = slug(name) or f"item-{_sha6(name)}"
    place = slug(location) if location else ""
    words = whole
    if place and (words == place or words.endswith(f"-{place}")):
        words = words.removesuffix(place).rstrip("-")
        if not words:
            return place
    tail = f"-{place}" if place else ""
    if len(words) + len(tail) > STEM_LIMIT:
        room = max(STEM_LIMIT - len(tail) - 7, 1)
        cut = words[:room]
        if room < len(words) and words[room] != "-" and "-" in cut:
            cut = cut.rsplit("-", 1)[0]
        words = f"{cut.strip('-')}-{_sha6(whole)}"
    return f"{words}{tail}"


def _sha6(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:6]


def database_name(database: str) -> str:
    """A study name from a database name: letters, digits, `.`, `_` and `-`."""
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", database).strip("-._")
    return name or "database"


def number(cell: object) -> tuple[float | None, str | None]:
    """A cell as a number. The second item is `no-amount` for an empty cell and `bad-amount` for
    anything that is not a finite number: text, a yes/no, a decimal comma."""
    if cell is None:
        return None, "no-amount"
    if isinstance(cell, bool):
        return None, "bad-amount"
    if isinstance(cell, int | float):
        value = float(cell)
    elif isinstance(cell, str):
        try:
            value = float(cell.strip()) if "_" not in cell else math.nan
        except ValueError:
            return None, "bad-amount"
    else:
        return None, "bad-amount"
    return (value, None) if math.isfinite(value) else (None, "bad-amount")


def join(*parts: str | None) -> str | None:
    text = "; ".join(one_line(p) for p in parts if p)
    return text or None


@dataclass(frozen=True)
class Head:
    """An activity that became a process."""

    activity: Activity
    id: str
    product: str
    location: str
    unit: str
    kind: FlowKind
    amount: float
    """What it makes, positive. A treatment makes a negative amount of a waste in Brightway."""
    production: ExchangeRow | None

    @property
    def label(self) -> str:
        """The product's name as written in files."""
        return one_line(self.product)


@dataclass(frozen=True)
class Refusal:
    status: Status
    code: str
    message: str
    suggestions: tuple[str, ...] = ()
    study: str | None = None
    """For a line that waits for a study folder that is not there yet: its name."""


@dataclass(frozen=True)
class Target:
    """What a line buys from, or emits to."""

    supplier_id: str | None
    flow_id: str
    flow_name: str
    kind: FlowKind
    unit: str
    how: str
    """How it was linked: `release`, `mapping`, `workbook`..."""
    own: bool = False
    note: str | None = None
    """When the link rests on less than name, product and location, what the workbook left out.
    It goes to the import's record, `.headwater-import.json`, never to the line's comment."""


@dataclass(frozen=True)
class Draft:
    """A line before it has a line id."""

    role: Role
    flow_id: str
    flow_name: str
    amount: float
    formula: str | None
    unit: str
    supplier_id: str | None
    comment: str | None
    uncertainty: Uncertainty | None = None


@dataclass(frozen=True)
class Amount:
    """The number of a line, and its formula when it has one that headwater can use."""

    value: float
    formula: str | None = None
    refusal: Refusal | None = None
    """Set when the line has a formula that cannot be copied: the value is then the number Excel
    stored."""
    note: str | None = None
    """For the line's comment, when its formula was left out."""


type Linked = tuple[Draft, str, Refusal | None, str | None]
"""A line that linked: the draft, how it linked, the refusal of its formula if it has one, and the
note of a link that rests on the name alone."""
