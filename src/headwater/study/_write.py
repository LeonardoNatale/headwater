"""Database files as a person writes them, and changes made in place.

A new file is written as the toy fixtures are: a CSV has the columns it needs, a number is
written as short as it reads back (`1`, `0.25`), and a YAML file has one field per line. A file
that exists is changed in place: a CSV row that does not change keeps its text, and a YAML entry
is replaced on its own lines, so comments and layout stay. Example: setting the amount of line e03
to 0.6 changes that row, `e03,input,toy:electricity,0.6,kWh,toy:electricity-production`, and no
other byte of the database.
"""

import csv
import io
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import date
from enum import Enum
from typing import Any, cast

import yaml

from headwater.io.files import yaml_comments, yaml_scalar
from headwater.model import Database, Exchange, Flow, Parameter, ProcessDetails
from headwater.study._exchanges import EXCHANGE_COLUMNS, EXCHANGE_OPTIONAL
from headwater.study._uncertainty import COLUMNS as DISTRIBUTION_COLUMNS

FLOW_COLUMNS = ("id", "name", "kind", "unit", "compartment", "cas")

type Cells = dict[str, str]

_BOM = "﻿"


# --- values ------------------------------------------------------------------------------------


def number(value: float) -> str:
    """The shortest text that reads back as `value`: `1`, `0.25`, `1e-05`."""
    return repr(float(value)).removesuffix(".0")


def yaml_value(value: object) -> str:
    """A value of a YAML field, on one line: `1 T-shirt`, `{rule: physical, basis: mass}`,
    `2024-01-01`.

    Text is quoted only when YAML needs it, so a goal with commas reads as written.
    """
    if isinstance(value, Enum):
        value = value.value
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str) and "\n" not in value:
        dumped = yaml.safe_dump({"k": value}, allow_unicode=True, width=10**6)
        return dumped.removeprefix("k: ").removesuffix("\n")
    return _flow(value)


def _flow(value: object) -> str:
    if isinstance(value, Mapping):
        items = cast(Mapping[str, object], value).items()
        return "{" + ", ".join(f"{k}: {_flow(v)}" for k, v in items) + "}"
    if isinstance(value, list | tuple):
        return "[" + ", ".join(_flow(v) for v in cast(Sequence[object], value)) + "]"
    if isinstance(value, int | float) and not isinstance(value, bool):
        return number(value)
    return yaml_scalar(value)


# --- new files ---------------------------------------------------------------------------------


def csv_text(columns: Sequence[str], rows: Iterable[Mapping[str, str]]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow([row.get(c, "") for c in columns])
    return buffer.getvalue()


def used_columns(
    required: Sequence[str], optional: Sequence[str], rows: Sequence[Mapping[str, str]]
) -> list[str]:
    """The required columns, then each optional one that a row fills."""
    return [*required, *(c for c in optional if any(row.get(c) for row in rows))]


def exchange_cells(e: Exchange) -> Cells:
    cells = {
        "line": e.line_id,
        "role": e.role.value,
        "flow": e.flow_id,
        "amount": e.formula if e.formula is not None else number(e.amount),
        "unit": e.unit,
        "supplier": e.supplier_id or "",
        "flow_name": e.flow_name or "",
        "comment": e.comment or "",
    }
    if e.uncertainty is not None:
        u = e.uncertainty
        cells["distribution"] = u.distribution.value
        for column in ("scale", "minimum", "maximum"):
            found = cast(float | None, getattr(u, column))
            cells[column] = number(found) if found is not None else ""
    return cells


def process_csv(exchanges: Iterable[Exchange]) -> str:
    rows = [exchange_cells(e) for e in exchanges]
    return csv_text(used_columns(EXCHANGE_COLUMNS, EXCHANGE_OPTIONAL, rows), rows)


def process_yaml(details: ProcessDetails, comments: Iterable[str] = ()) -> str:
    fields = details.model_dump(include=set(ProcessDetails.model_fields))
    order = ("name", "location", "time_start", "time_end", "kind")
    body = "".join(f"{k}: {yaml_value(fields[k])}\n" for k in order if fields[k] is not None)
    return yaml_comments(comments) + body


def flows_csv(flows: Iterable[Flow]) -> str:
    return csv_text(FLOW_COLUMNS, (flow_cells(f) for f in flows))


def flow_cells(flow: Flow) -> Cells:
    return {
        "id": flow.id,
        "name": flow.name,
        "kind": flow.kind.value,
        "unit": flow.unit,
        "compartment": flow.compartment or "",
        "cas": flow.cas or "",
    }


def database_fields(database: Database) -> dict[str, Any]:
    """The fields of database.yaml as written, in their order, each only when it is set:
    `uses: []` and `methods: []` are not written."""
    data = database.model_dump(mode="json", exclude_none=True)
    data["background"] = str(database.background)
    return {k: v for k, v in data.items() if v not in (None, [])}


def database_yaml(database: Database) -> str:
    return "".join(f"{k}: {yaml_value(v)}\n" for k, v in database_fields(database).items())


def parameter_entry(parameter: Parameter) -> object:
    """A number or a formula, or a mapping when the parameter has more than that."""
    value: object = parameter.value if parameter.value is not None else parameter.formula
    entry: dict[str, object] = {"value" if parameter.value is not None else "formula": value}
    if parameter.unit is not None:
        entry["unit"] = parameter.unit
    if parameter.comment is not None:
        entry["comment"] = parameter.comment
    u = parameter.uncertainty
    if u is not None:
        entry["distribution"] = u.distribution.value
        for key in DISTRIBUTION_COLUMNS[1:]:
            if getattr(u, key) is not None:
                entry[key] = getattr(u, key)
    return entry if len(entry) > 1 else value


def entry_text(key: str, entry: object) -> str:
    """`key: value`, or `key:` and one line per field of a mapping, two spaces in."""
    if not isinstance(entry, Mapping):
        return f"{key}: {yaml_value(entry)}\n"
    items = cast(Mapping[str, object], entry).items()
    return f"{key}:\n" + "".join(f"  {k}: {yaml_value(v)}\n" for k, v in items)


def parameters_yaml(parameters: Iterable[Parameter]) -> str:
    database: list[str] = []
    processes: dict[str, list[str]] = {}
    for p in parameters:
        text = entry_text(p.name, parameter_entry(p))
        (database if p.scope is None else processes.setdefault(p.scope, [])).append(text)
    out = ["database:\n", *_indented("".join(database), 2)] if database else []
    if processes:
        out.append("processes:\n")
        for scope, entries in processes.items():
            out += [f"  {scope}:\n", *_indented("".join(entries), 4)]
    return "".join(out)


def _indented(text: str, spaces: int) -> list[str]:
    return [" " * spaces + line for line in text.splitlines(keepends=True)]


# --- CSV in place ------------------------------------------------------------------------------


def csv_rows(text: str) -> list[tuple[int, Cells]]:
    """The rows of a CSV text, each with the line it starts on, cells stripped. Blank rows are
    left out."""
    reader = csv.reader(io.StringIO(text.removeprefix(_BOM), newline=""))
    header: list[str] | None = None
    rows: list[tuple[int, Cells]] = []
    start = 1
    for cells in reader:
        if header is None:
            header = [c.strip() for c in cells]
        elif any(c.strip() for c in cells):
            rows.append((start, {c: v.strip() for c, v in zip(header, cells, strict=False)}))
        start = reader.line_num + 1
    return rows


def edit_csv(
    text: str,
    edit: Callable[[Cells], Cells | None] | None = None,
    add: Sequence[Mapping[str, str]] = (),
) -> str:
    """`text` with each record that `edit` changes written again, each it returns None for left
    out, and the rows of `add` at the end.

    The header and every record left alone keep their text, quotes and line endings. A column
    that a new or changed row fills and the header does not have is added at the end of the
    header, with an empty cell in every other row.
    """
    bom = _BOM if text.startswith(_BOM) else ""
    lines = text.removeprefix(bom).splitlines(keepends=True)
    reader = csv.reader(iter(lines))
    header: list[str] = []
    head = ""
    records: list[tuple[str, list[str], Cells | None]] = []  # text, cells, new cells or None
    kept: set[int] = set()  # the records `edit` leaves as they are
    at = 0
    for cells in reader:
        original = "".join(lines[at : reader.line_num])
        at = reader.line_num
        if not head:
            header, head = [c.strip() for c in cells], original
            continue
        row = dict(zip(header, cells, strict=False))
        new = edit(dict(row)) if edit is not None else row
        if new == row:
            kept.add(len(records))
        records.append((original, cells, new))
    ending = _ending(head) or "\n"
    filled = [new for i, (_, _, new) in enumerate(records) if new and i not in kept] + [*add]
    extra = list(dict.fromkeys(k for r in filled for k, v in r.items() if v and k not in header))
    if extra:
        head = head.rstrip("\r\n") + "," + ",".join(extra) + ending
    out = [head]
    for i, (original, cells, new) in enumerate(records):
        if new is None:
            continue
        own = _ending(original)
        if i in kept:
            widen = extra and original.strip()  # a blank line stays blank
            out.append(original.rstrip("\r\n") + "," * len(extra) + own if widen else original)
            continue
        values = [new.get(c, cells[n] if n < len(cells) else "") for n, c in enumerate(header)]
        values += cells[len(header) :] + [new.get(k, "") for k in extra]
        out.append(_record(values, own))
    out.append("".join(lines[at:]))
    if add and not _ending("".join(out)):
        out.append(ending)
    out += [_record([row.get(c, "") for c in [*header, *extra]], ending) for row in add]
    return bom + "".join(out)


def _ending(text: str) -> str:
    return text[len(text.rstrip("\r\n")) :]


def _record(values: Sequence[str], ending: str) -> str:
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator=ending).writerow(values)
    return buffer.getvalue()


# --- YAML in place -----------------------------------------------------------------------------


def yaml_entry(
    lines: Sequence[str], key: str, start: int = 0, end: int | None = None, indent: int = 0
) -> tuple[int, int] | None:
    """Where the entry of `key`, at `indent` spaces, is among `lines[start:end]`: its first line
    and the line after its last. The lines indented deeper belong to it, comments among them, and
    so does a list at the margin under a top-level key, and a blank line between them. A comment at
    its indentation or the next key does not."""
    end = len(lines) if end is None else end
    pattern = re.compile(rf"{' ' * indent}{re.escape(key)}\s*:(\s|$)")
    for i in range(start, end):
        if pattern.match(lines[i]):
            j = k = i + 1
            while k < end and (_deeper(lines[k], indent) or not lines[k].strip()):
                k += 1
                if _deeper(lines[k - 1], indent):
                    j = k  # a blank line belongs to the entry only when more of it follows
            return i, j
    return None


def _deeper(line: str, indent: int) -> bool:
    if indent == 0 and line.startswith("- "):
        return True
    margin = line[: indent + 1]
    return len(margin) == indent + 1 and not margin.strip(" \t")


def put_yaml(text: str, path: Sequence[str], entry: str | None) -> str:
    """`text` with the entry at `path` replaced by `entry`, added at the end of its parent when
    it is not there, or taken out when `entry` is None.

    `entry` is the entry's text as at the margin, `key: value`, and is indented to its place.
    Each key of `path` is found by its line, `key:` at its indentation. A parent left with no
    entry is taken out too.
    """
    bom = _BOM if text.startswith(_BOM) else ""
    lines = text.removeprefix(bom).splitlines(keepends=True)
    start, end, indent = 0, len(lines), 0
    parents: list[tuple[int, int, str]] = []  # the line, indentation and key of each parent
    for depth, key in enumerate(path):
        found = yaml_entry(lines, key, start, end, indent)
        if found is None:
            if entry is None:
                return text
            block = "".join(f"{'  ' * d}{k}:\n" for d, k in enumerate(path[depth:-1]))
            block += "".join(_indented(entry, 2 * (len(path) - 1 - depth)))
            if end > 0 and not _ending(lines[end - 1]):
                lines[end - 1] += "\n"
            lines[end:end] = _indented(block, indent)
            return bom + "".join(lines)
        i, j = found
        if depth == len(path) - 1:
            lines[i:j] = _indented(entry, indent) if entry is not None else []
            break
        parents.append((i, indent, key))
        start, end, indent = i + 1, j, _child_indent(lines[i + 1 : j], indent)
    if entry is None:
        for i, indent, key in reversed(parents):
            if not _has_children(lines, i, key, indent):
                del lines[i]
    return bom + "".join(lines)


def _child_indent(lines: Sequence[str], indent: int) -> int:
    for line in lines:
        body = line.lstrip(" ")
        if body.strip() and not body.startswith("#"):
            return len(line) - len(body)
    return indent + 2


def _has_children(lines: Sequence[str], i: int, key: str, indent: int) -> bool:
    """Whether the key on line `i` still holds something: a value on its line, or an entry
    under it."""
    after = lines[i][indent + len(key) :].lstrip().removeprefix(":")
    if after.split("#", 1)[0].strip():
        return True
    for line in lines[i + 1 :]:
        body = line.strip()
        if not body:
            continue
        if not _deeper(line, indent):
            return False
        if not body.startswith("#"):
            return True
    return False
