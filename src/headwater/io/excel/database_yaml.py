"""database.yaml: what the import writes, and how that merges with what a person wrote since.

A workbook says a database's name, the release it links to and the databases next to it that it
buys from. The import writes those, and `methods` when it is given one. A person may add a
`description`, change `methods` or add comments, and importing again must not take that back.

The rule, for each field of database.yaml:

- `name`, `background` and `uses` are the import's, every time. The processes it writes depend
  on them. `uses` is written only when the database uses another, and taken out when it no
  longer does.
- `methods` is the import's only while the person has not changed it. The hidden record of the
  import keeps what it wrote the last time, and a field that still holds that follows the
  import. A field that holds something else is the person's and stays, and the report says what
  the import would have written.
- Nothing else in the file moves: not another field, not a comment, not the order. A line is
  replaced only when its value must change. A field the person deleted is written again.

A field is replaced by its line, `field: value` at the start of a line. When a field must change
and its line is not that (a key in quotes, the whole file in braces, a comment at the margin
inside a list), the import stops and writes nothing.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any, cast

import yaml

from headwater.io.excel.convert import Conversion, DatabaseDraft
from headwater.io.excel.report import Note
from headwater.io.files import yaml_comments, yaml_scalar
from headwater.model import InvalidDataError, Problem
from headwater.study._files import parse_yaml

DATABASE = "database.yaml"

OWNED = frozenset({"name", "background", "uses"})
"""The fields that are the import's whatever the file says."""

_MISSING = object()


@dataclass(frozen=True)
class Kept:
    """A field the import would have written differently, and a person's value stayed."""

    field: str
    theirs: Any
    ours: Any


@dataclass(frozen=True)
class Merged:
    """database.yaml as it will be written, with what the import wrote for each field."""

    text: str
    written: dict[str, Any]
    """The value the import wrote (or would have written) for each field: the record keeps it, to
    tell next time whether a person changed the field."""
    kept: tuple[Kept, ...] = ()

    def apply(self, conversion: Conversion) -> Conversion:
        """The conversion with the notes on what was kept."""
        notes = tuple(
            Note(
                f"{DATABASE} keeps `{k.field}: {_flow_value(k.theirs)}`, which a person wrote. "
                f"This import would have written `{k.field}: {_flow_value(k.ours)}`."
            )
            for k in self.kept
        )
        report = replace(conversion.report, notes=(*conversion.report.notes, *notes))
        return replace(conversion, report=report)


def render(draft: DatabaseDraft, workbook: str) -> str:
    """database.yaml as a first import writes it."""
    source = f" from {workbook}" if workbook else ""
    lines = [
        f"name: {yaml_scalar(draft.name)}\n",
        f"background: {yaml_scalar(draft.background)}\n",
    ]
    if draft.methods:
        lines.append(f"methods: {_flow_value(list(draft.methods))}\n")
    if draft.uses:
        lines.append(
            f"uses: {_flow_value(list(draft.uses))}  # databases next to this one that it buys "
            "from\n"
        )
    return yaml_comments([f"Imported by headwater{source}."]) + "".join(lines)


def _flow_value(value: Any) -> str:
    """`toy-climate`, or `[EF-3.1, IPCC-2021]` for a list."""
    if isinstance(value, list):
        return "[" + ", ".join(yaml_scalar(v) for v in cast(list[Any], value)) + "]"
    return yaml_scalar(value)


def merge(
    draft: DatabaseDraft,
    workbook: str,
    *,
    text: str | None = None,
    values: Mapping[str, Any] | None = None,
    written: Mapping[str, Any] | None = None,
) -> Merged:
    """database.yaml for `draft`, given the one in the folder.

    `text` and `values` are the file in the folder as text and as read, or None for a folder with
    none. `written` is what the earlier import wrote for each field, from its record.
    """
    fresh_text = render(draft, workbook)
    fresh: dict[str, Any] = yaml.safe_load(fresh_text)
    if text is None or values is None:
        return Merged(fresh_text, fresh)
    bom = text.startswith("\ufeff")
    text = text.removeprefix("\ufeff")
    kept: list[Kept] = []
    expected: dict[str, Any] = {}
    for field in fresh:
        ours, theirs = fresh[field], values.get(field, _MISSING)
        if field in OWNED or theirs is _MISSING or _follows(field, theirs, written):
            expected[field] = ours
            if theirs != ours:
                text = _put(text, field, _lines(fresh_text, field))
        else:
            expected[field] = theirs
            if theirs != ours:
                kept.append(Kept(field, theirs, ours))
    for field in sorted(values.keys() - fresh.keys()):
        # `uses` the database no longer has, and `methods` the import wrote and is no longer given
        if field in OWNED or (
            written is not None and values[field] == written.get(field, _MISSING)
        ):
            text = _drop(text, field)
            expected[field] = _MISSING
    _check(text, expected)
    return Merged(("\ufeff" if bom else "") + text, fresh, tuple(kept))


def _check(text: str, expected: Mapping[str, Any]) -> None:
    """Read the merged text back: it must be YAML that says what the merge meant. Lines are
    replaced by looking for `field:` at the start of a line, so a layout that lookup cannot
    follow (a key in quotes, the whole file in braces, a comment inside a list) must stop the
    import, not leave a file that no longer reads.

    Raises `InvalidDataError` naming database.yaml.
    """
    problems: list[Problem] = []
    read = parse_yaml(text, DATABASE, problems)
    wrong = [] if read is None else [f for f, v in expected.items() if read.get(f, _MISSING) != v]
    if read is not None and not wrong:
        return
    why = problems[0].message if problems else f"{', '.join(wrong)} would not read as intended"
    raise InvalidDataError(
        [
            Problem(
                "database-yaml-layout",
                DATABASE,
                f"cannot be updated in place ({why}). The import changes a field by its line, "
                "`field: value` at the start of a line. Write the fields that way, or remove the "
                "file to have it written again.",
                location=DATABASE,
            )
        ]
    )


def _follows(field: str, theirs: Any, written: Mapping[str, Any] | None) -> bool:
    """Whether the field is still the import's: it holds what the import wrote last time. What a
    person wrote is never taken back."""
    return written is not None and field in written and theirs == written[field]


def _lines(text: str, field: str) -> list[str]:
    found = _span(text.splitlines(keepends=True), field)
    assert found is not None
    return found[2]


def _span(lines: list[str], field: str) -> tuple[int, int, list[str]] | None:
    """Where the entry of a top-level field is in the file: its first line, the line after its
    last, and its lines. A value that goes on over indented lines (comments among them), or a list
    at the margin, belongs to it; a blank line, a comment at the margin or the next field does
    not."""
    key = re.compile(rf"{re.escape(field)}\s*:")
    start = next((i for i, line in enumerate(lines) if key.match(line)), None)
    if start is None:
        return None
    end = start + 1
    while end < len(lines) and (lines[end][:1] in (" ", "\t") or lines[end].startswith("- ")):
        end += 1
    return start, end, lines[start:end]


def _put(text: str, field: str, entry: list[str]) -> str:
    """`text` with the entry of `field` replaced by `entry`, or added at the end."""
    lines = text.splitlines(keepends=True)
    found = _span(lines, field)
    if found is None:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        return "".join(lines + entry)
    start, end, _ = found
    return "".join(lines[:start] + entry + lines[end:])


def _drop(text: str, field: str) -> str:
    """`text` without the entry of `field`."""
    lines = text.splitlines(keepends=True)
    found = _span(lines, field)
    if found is None:
        return text
    start, end, _ = found
    return "".join(lines[:start] + lines[end:])
