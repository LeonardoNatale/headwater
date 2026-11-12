"""The exclude file: source files left out of an import on purpose, each with a reason.

A CSV with one file per row:

    file,reason,resolved_by,resolved_at
    20b97099-..._a9007f10-....spold,The file is empty in the download,Leo,2026-10-11T09:30:00+00:00

Lines that bought from an excluded file become data gaps, with this reason. `resolved_at` is an
ISO time with a time zone. It comes from the file, not from the clock, so importing the same source
twice gives the same release.
"""

import csv
from pathlib import Path

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

from headwater.model import InvalidDataError, Problem

COLUMNS = ("file", "reason", "resolved_by", "resolved_at")


class Exclusion(BaseModel):
    """One source file left out, why, who decided and when."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    file: str = Field(min_length=1)
    """The file name as it is in the source folder, for example `<activity>_<product>.spold`."""
    reason: str = Field(min_length=1)
    resolved_by: str = Field(min_length=1)
    resolved_at: AwareDatetime


def read_exclude(path: Path | str) -> tuple[Exclusion, ...]:
    """Read an exclude file. Raises `InvalidDataError` naming every bad row."""
    path = Path(path)
    name = path.name
    if not path.is_file():
        raise InvalidDataError([Problem("missing-file", str(path), "does not exist")])
    problems: list[Problem] = []
    found: list[Exclusion] = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        header = list(reader.fieldnames or [])
        for column in COLUMNS:
            if column not in header:
                problems.append(
                    Problem(
                        "missing-column", name, f"has no {column!r} column", location=f"{name}:1"
                    )
                )
        for column in header:
            if column not in COLUMNS:
                problems.append(
                    Problem(
                        "unknown-column",
                        name,
                        f"has a column {column!r}; the columns are {', '.join(COLUMNS)}",
                        location=f"{name}:1",
                    )
                )
        if problems:
            raise InvalidDataError(problems)
        seen: set[str] = set()
        for number, row in enumerate(reader, start=2):
            where = f"{name}:{number}"
            if None in row:
                problems.append(
                    Problem(
                        "invalid-row",
                        f"{name} row {number}",
                        "has more values than columns",
                        location=where,
                    )
                )
                continue
            try:
                exclusion = Exclusion.model_validate(row)
            except ValidationError as error:
                for err in error.errors():
                    field = ".".join(str(part) for part in err["loc"])
                    problems.append(
                        Problem(
                            "invalid-field",
                            f"{name} row {number}: {field}",
                            err["msg"],
                            location=where,
                        )
                    )
                continue
            if exclusion.file in seen:
                problems.append(
                    Problem(
                        "duplicate-exclusion",
                        f"{name} row {number}",
                        f"lists {exclusion.file} again",
                        location=where,
                    )
                )
            seen.add(exclusion.file)
            found.append(exclusion)
    if problems:
        raise InvalidDataError(problems)
    return tuple(found)
