"""The mapping file: what a person decided about the lines that do not match by themselves.

The import links a line to a supplier of the release by exact name, reference product and
location. A line that does not match is never linked by a guess. A person reads the report, looks
the supplier up, and writes the decision here, so that it is reviewed, kept in git, and used again
the next time the workbook is imported.

```
type,name,reference_product,location,categories,target,who,when,comment
technosphere,market for steel,steel,RER,,ecoinvent:3.10:cutoff:a1_b1,leo,2026-10-12,renamed in 3.10
biosphere,Water,,,water::fresh,ecoinvent:flow:c3,leo,2026-10-12,
database,Textile Chemicals,,,,textile-chemicals,leo,2026-10-12,shared study
```

- `technosphere`: the line named `name`, with this reference product and location (as the
  workbook writes them, blank when it writes none), buys from the release process `target`.
- `biosphere`: the line named `name` with these `categories` (written `air::urban air close to
  ground`) is the elementary flow `target`.
- `database`: lines whose database column says `name` come from `target`: `release` (the
  background, whatever the database is called), `own` (this workbook, under another name), or a
  study, by the name of its folder next to the study written (`textile-chemicals`), which
  becomes an entry of `uses:` once that folder exists.

`who` and `when` (an ISO date or time) are required on every row: a mapping is a decision
somebody made, and a row says who and when, like a row of `cut_offs.csv`, which the file is read
like.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from headwater.model import InvalidDataError, Problem
from headwater.model.database import DATABASE_NAME
from headwater.study._files import Row, read_csv, read_when

COLUMNS = ("type", "name", "target", "who", "when")
OPTIONAL = ("reference_product", "location", "categories", "comment")
TYPES = ("technosphere", "biosphere", "database")

type TechnosphereKey = tuple[str, str | None, str | None]
type BiosphereKey = tuple[str, tuple[str, ...]]


@dataclass(frozen=True)
class MappingRow:
    """One decision of the mapping file."""

    type: str
    name: str
    reference_product: str | None
    location: str | None
    categories: tuple[str, ...]
    target: str
    who: str
    when: datetime
    """In UTC."""
    comment: str | None
    source: str
    """The file and line, `mapping.csv:3`."""


@dataclass(frozen=True)
class DatabaseDecision:
    """What a database named in the workbook is: the background, this workbook, or a study."""

    kind: str
    """`release`, `own` or `database`."""
    study: str | None
    """For a study, the name of its folder, next to the study written."""
    row: MappingRow


@dataclass(frozen=True)
class ImportMapping:
    """The decisions of a mapping file, by what they decide."""

    rows: tuple[MappingRow, ...] = ()
    technosphere: dict[TechnosphereKey, MappingRow] = field(
        default_factory=dict[TechnosphereKey, MappingRow]
    )
    biosphere: dict[BiosphereKey, MappingRow] = field(
        default_factory=dict[BiosphereKey, MappingRow]
    )
    databases: dict[str, DatabaseDecision] = field(default_factory=dict[str, DatabaseDecision])

    def technosphere_row(
        self, name: str, product: str | None, location: str | None
    ) -> MappingRow | None:
        return self.technosphere.get((name, product, location))

    def biosphere_row(self, name: str, categories: tuple[str, ...]) -> MappingRow | None:
        return self.biosphere.get((name, categories))

    def database(self, name: str) -> DatabaseDecision | None:
        return self.databases.get(name)


def read_mapping(path: Path | str) -> ImportMapping:
    """Read a mapping file. Raises `InvalidDataError` naming every row that is wrong: an unknown
    type, a missing name, target or who, a database target that is not a study folder's name,
    and a line decided twice."""
    root = Path(path)
    problems: list[Problem] = []
    cells = read_csv(
        root,
        root.parent,
        required=COLUMNS,
        optional=OPTIONAL,
        renamed={"date": "when"},
        problems=problems,
    )
    rows = [row for row in (_row(c, problems) for c in cells) if row is not None]
    mapping = _index(rows, problems)
    if problems:
        raise InvalidDataError(problems)
    return mapping


def _row(cells: Row, problems: list[Problem]) -> MappingRow | None:
    source = cells.location
    item = f"mapping {source}"
    get = cells.get

    def report(code: str, message: str) -> None:
        problems.append(Problem(code, item, message, location=source))

    kind = get("type")
    if kind not in TYPES:
        report("bad-mapping", f"type {kind!r} is not one of {', '.join(TYPES)}")
        return None
    before = len(problems)
    for column in ("name", "target", "who", "when"):
        if not get(column):
            report("bad-mapping", f"has no {column}")
    target = get("target")
    if kind == "database" and target and target not in ("release", "own"):
        if not re.fullmatch(DATABASE_NAME, target):
            pinned = target.split("@")[0] if "@" in target else None
            hint = (
                f" A pinned version is not used: write the folder's name, {pinned}."
                if pinned
                else ""
            )
            report(
                "bad-mapping",
                f"target {target!r} is not `release`, `own`, or the name of a database folder "
                f"next to the database written, such as textile-chemicals.{hint}",
            )
    when = read_when(get("when"), report) if get("when") else None
    if len(problems) > before or when is None:
        return None
    categories = get("categories")
    return MappingRow(
        type=kind,
        name=get("name"),
        reference_product=get("reference_product") or None,
        location=get("location") or None,
        categories=tuple(p.strip() for p in categories.split("::")) if categories else (),
        target=get("target"),
        who=get("who"),
        when=when,
        comment=get("comment") or None,
        source=source,
    )


def _index(rows: Iterable[MappingRow], problems: list[Problem]) -> ImportMapping:
    technosphere: dict[TechnosphereKey, MappingRow] = {}
    biosphere: dict[BiosphereKey, MappingRow] = {}
    databases: dict[str, DatabaseDecision] = {}
    every = tuple(rows)
    for row in every:
        if row.type == "technosphere":
            key: TechnosphereKey = (row.name, row.reference_product, row.location)
            known = technosphere.setdefault(key, row)
        elif row.type == "biosphere":
            bkey: BiosphereKey = (row.name, row.categories)
            known = biosphere.setdefault(bkey, row)
        else:
            decision = DatabaseDecision(
                "database" if row.target not in ("release", "own") else row.target,
                row.target if row.target not in ("release", "own") else None,
                row,
            )
            known = databases.setdefault(row.name, decision).row
        if known is not row:
            problems.append(
                Problem(
                    "bad-mapping",
                    f"mapping {row.source}",
                    f"decides {row.type} {row.name!r} again: {known.source} already does",
                    location=row.source,
                )
            )
    return ImportMapping(every, technosphere, biosphere, databases)
