"""The header of a SimaPro CSV export: the `{...}` lines before the first block.

Example:

```
{SimaPro 9.5.0.0}
{processes}
{CSV separator: Semicolon}
{Decimal separator: ,}
{Short date format: dd/MM/yyyy}
{Convert expressions to constants: Yes}
```

The first line gives SimaPro's version, which names the flow list: `simapro-9.5`. Then the kind of
export, which the readers do not need since the blocks say it, and fields. The separators are
required: a number read with the wrong decimal separator would be wrong, so a header without them
is refused.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from headwater.model import InvalidDataError, Problem

FIRST_LINE = "{SimaPro "
"""How every SimaPro CSV export starts."""

_VERSION = re.compile(r"^\{SimaPro ((\d+)\.(\d+)[^{}]*)\}$")
_FIELD = re.compile(r"^\{([^:{}]+):(.*)\}$")

SEPARATORS = {"Semicolon": ";", "Comma": ",", "Tab": "\t"}
"""SimaPro's names for the CSV separators it writes."""

DECIMALS = (".", ",")


@dataclass(frozen=True)
class Header:
    simapro: str
    """SimaPro's version, `9.5.0.0`."""
    flow_list: str
    """The flow list its names belong to, from SimaPro's major and minor version: `simapro-9.5`."""
    csv_separator: str
    decimal_separator: str
    date_format: str | None
    """`Short date format`, such as `dd/MM/yyyy`."""
    constants: bool | None
    """`Convert expressions to constants`: Yes when SimaPro wrote computed numbers, not formulas."""
    size: int
    """Lines of the header."""


def read_header(lines: Sequence[str], file: str) -> Header:
    """The header at the top of `lines`. Raises `InvalidDataError` when it is not one."""
    size = 0
    while size < len(lines) and lines[size].startswith("{") and lines[size].rstrip().endswith("}"):
        size += 1
    found = [line.strip() for line in lines[:size]]
    version = _VERSION.match(found[0]) if found else None
    if version is None:
        raise InvalidDataError(
            [Problem("bad-header", f"file {file}", "does not start with a {SimaPro x.y} line")]
        )
    fields: dict[str, str] = {}
    for line in found[1:]:
        if (field := _FIELD.match(line)) is not None:
            fields[field[1].strip()] = field[2].strip()
    problems: list[Problem] = []
    separator = SEPARATORS.get(fields.get("CSV separator", ""))
    decimal = fields.get("Decimal separator")
    if separator is None:
        given = fields.get("CSV separator")
        problems.append(
            Problem(
                "bad-header",
                f"file {file}",
                f"has the CSV separator {given!r}; SimaPro writes {', '.join(SEPARATORS)}"
                if given
                else "has no {CSV separator: ...} line",
            )
        )
    if decimal not in DECIMALS:
        problems.append(
            Problem(
                "bad-header",
                f"file {file}",
                f"has the decimal separator {decimal!r}; SimaPro writes '.' or ','"
                if decimal
                else "has no {Decimal separator: ...} line",
            )
        )
    elif decimal == separator:
        problems.append(
            Problem(
                "bad-header", f"file {file}", f"uses {decimal!r} as both CSV and decimal separator"
            )
        )
    if problems:
        raise InvalidDataError(problems)
    assert separator is not None and decimal is not None
    constants = fields.get("Convert expressions to constants")
    return Header(
        simapro=version[1].strip(),
        flow_list=f"simapro-{version[2]}.{version[3]}",
        csv_separator=separator,
        decimal_separator=decimal,
        date_format=fields.get("Short date format"),
        constants=None if constants is None else constants.lower() == "yes",
        size=size,
    )
