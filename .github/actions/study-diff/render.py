"""Turn the report of `collect.py` into the Markdown of one pull request comment.

Standard library only, and no GitHub: JSON in, Markdown out. The numbers are those of
`headwater diff --json` (`headwater-diff/2`), formatted as `headwater diff` prints them: scores
and changes with 6 significant digits, percentages with one decimal. Databases come first, then
studies, as `collect.py` orders them.

The comment starts with a hidden marker, which `comment.py` uses to find it again.
"""

import argparse
import json
import re
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

MARKER = "<!-- headwater-study-diff -->"
LIMIT = 60_000
"""Characters. GitHub refuses a comment of more than 65,536."""
SUMMARY_LIMIT = 900_000
"""Characters. A job summary can hold 1 MiB."""
TOP = 10
"""Lines listed, as in `headwater diff`."""
COLUMNS = 5
"""Lines with a column of their own in the table of scores, as in `headwater diff`."""
OPEN = 5
"""Scores that changed shown, in the method's order, before the rest are folded."""

_SHAPES = ((TOP, COLUMNS, OPEN), (3, 3, 3), (1, 1, 1))
"""Lines listed, columns and open scores, the full shape first, then smaller ones for a long
comment."""
_PREFIX = {
    "line": "",
    "override": "override of ",
    "cut_off": "cut-off ",
    "flow_property": "flow property ",
    "no_factor": "no_factor.csv row ",
    "comment": "",
}
_NAMED = ("line", "override", "no_factor", "comment", "product")
"""The kinds whose label names the flow in words, as `headwater diff` does."""
_NOTE = (
    "The comment is limited in length, so it was cut here. "
    "The full results are in the run's summary."
)

Folder = dict[str, Any]


def render(report: dict[str, Any], *, run_url: str | None = None, limit: int = LIMIT) -> str:
    """The comment for a report, at most `limit` characters.

    When the full text is longer, fewer lines are listed and get a column, and fewer scores are
    open (10 lines, 5 columns and 5 scores, then 3 of each, then 1). When that is not enough the
    text is cut at a line, open sections are closed, and a note says where to read the rest.
    """
    body = ""
    for shape in _SHAPES:
        body = _page(report, run_url, shape)
        if len(body) <= limit:
            return body
    return _cut(body, limit)


Shape = tuple[int, int, int]


def _page(report: dict[str, Any], run_url: str | None, top: Shape) -> str:
    blocks = [f"{MARKER}\n## Results diff for changed databases and studies"]
    folders: list[Folder] = report["folders"]
    if not folders:
        blocks.append("No database or study changed in this pull request.")
        return _join(blocks)
    base, head = report["base"], report["head"]
    blocks.append(
        f"Base {_code(base['label'])} ({_code(base['commit'][:12])}), head "
        f"{_code(head['label'])} ({_code(head['commit'][:12])}). "
        "Each database and study is calculated on the release it pins."
    )
    failed = [f for f in folders if not f["ok"]]
    if failed:
        blocks.append("**Failed:** " + ", ".join(_code(f["path"]) for f in failed) + ".")
    unread = report.get("uses_unread") or []
    if unread:
        blocks.append(
            "**These files do not read**, so whether their folders reach a changed database is "
            "not known, and they are not compared:\n\n"
            + "\n".join(
                f"- {_code(u['path'] + '/' + u['file'])}: {_text(u['message'])}" for u in unread
            )
        )
    if report.get("uses_error"):
        blocks += [
            "**The databases and studies that reach the changed ones could not be found**, so "
            "they are not compared here.",
            _fence(report["uses_error"]),
        ]
    for folder in folders:
        blocks += _folder(folder, top)
    footer = _footer(folders, run_url)
    if footer:
        blocks.append(footer)
    return _join(blocks)


def _join(blocks: list[str]) -> str:
    return "\n\n".join(blocks) + "\n"


def _footer(folders: list[Folder], run_url: str | None) -> str:
    parts: list[str] = []
    for folder in folders:
        if folder["diff"] is not None:
            parts.append(f"headwater {folder['diff']['engine_version']}")
            break
    if run_url:
        parts.append(f"[workflow run]({run_url})")
    return f"<sub>{' · '.join(parts)}</sub>" if parts else ""


# --- one database or study ----------------------------------------------------------------------


def _folder(folder: Folder, top: Shape) -> list[str]:
    status = {"new": " (new)", "removed": " (removed)"}.get(folder["status"], "")
    kind = folder.get("kind", "database")
    blocks = [f"### {kind.capitalize()} {_code(folder['path'])}{status}"]
    used = folder.get("uses") or []
    if used:
        blocks.append(" ".join(_used(u, kind) for u in used))
    if folder["status"] == "removed":
        return [*blocks, "Deleted in this pull request. There is nothing to compare."]
    validation = folder["validation"]
    if validation is not None and validation["exit_code"] != 0:
        return [*blocks, *_invalid(validation)]
    new = folder["status"] == "new"
    diff = folder["diff"]
    if diff is not None and diff.get("created") and not diff["results"]:
        count = len(diff["products"])
        noun = "product" if count == 1 else "products"
        line = (
            f"New database: {count} {noun}, not scored. "
            f"{_code('headwater calc ' + folder['path'])} scores them."
        )
        return [*blocks, line, *_warnings(validation)]
    if new:
        blocks.append(
            "New in this pull request, so there is nothing to compare. Validation: valid."
        )
    if diff is None:
        what = "calculated" if new else "compared"
        blocks += [
            f"**The {kind} could not be {what}.**",
            _fence(folder["diff_error"] or "No message."),
        ]
    else:
        blocks += _results(diff, top)
    return [*blocks, *_warnings(validation)]


_USED = {"changed": "changed", "new": "is new", "removed": "was removed"}


def _used(used: dict[str, Any], kind: str) -> str:
    """Why a folder is in the comment: "Uses `dyes`, which uses `chemicals`, which changed.", and
    for a study "Its products reach `tshirt`, which uses `dyes`, which changed."."""
    way = ", which uses ".join(_code(name) for name in used["chain"])
    status = _USED.get(used["status"], used["status"])
    if kind == "study":
        return f"Its products reach {way}, which {status}."
    return f"Uses {way}, which {status}."


def _invalid(validation: dict[str, Any]) -> list[str]:
    errors = validation["errors"]
    noun = "error" if len(errors) == 1 else "errors"
    blocks = [
        f"**Validation failed** with {len(errors)} {noun}. The results were not compared.",
        _fence("\n".join(errors)),
    ]
    return [*blocks, *_warnings(validation)]


def _warnings(validation: dict[str, Any] | None) -> list[str]:
    warnings = validation["warnings"] if validation is not None else []
    if not warnings:
        return []
    return [f"Warnings ({len(warnings)}):\n\n" + "\n".join(f"- {_text(w)}" for w in warnings)]


# --- the results of a diff ---------------------------------------------------------------------


def _results(diff: dict[str, Any], shape: Shape) -> list[str]:
    """Each line that differs once, in the order of the files, then the scores in the method's
    order, with what each line alone did to each, then the comments that changed and what a score
    does not depend on. With several products, a block of scores for each one that moved, the
    first block open (see `_several`)."""
    top, columns, opened = shape
    rows: list[dict[str, Any]] = diff["results"]
    differences = {d["id"]: d for d in diff["differences"]}
    tolerance = diff["tolerance"]
    release = diff["background"]["release"]
    if release is not None:
        name, _, release_hash = release.partition("@")
        where = f"{name}@{release_hash[:12]}"
    else:
        where = f"folder {diff['background']['folder']}"
    method = diff["method"]
    named = f"{method['id']} {method['version']}" if method else ", ".join(diff.get("methods", []))
    blocks = [f"Background {_code(where)}, method {_code(named)}."]
    if not diff["differences"] and not diff.get("created"):
        blocks.append("No lines differ.")
    products = _products(rows, diff)
    if len(products) > 1:
        blocks += _several(diff, products, shape)
    else:
        rows = products[0]["rows"] if products else rows  # named as the product is
        changed = [r for r in rows if r["changed"]]
        driving = {d["difference"] for r in rows for d in r["drivers"]}
        ids = [d["id"] for d in diff["differences"] if d["id"] in driving]
        keys = short_names([differences[i] for i in ids])
        if ids:
            blocks.append(_lines(ids, changed, differences, keys, top))
        moved = [r for r in rows if _moves(r)]
        same = [r for r in rows if not any(r is m for m in moved)]
        effects = _Effects.of(ids, keys, changed, columns, tolerance)
        if moved:
            if effects.header:
                blocks.append("A line's column is the change that line alone makes.")
            blocks += _open_first(moved, effects, opened)
        elif diff.get("kind") == "study" and products:
            blocks.append(f"{_text(products[0]['name'])}: no change.")  # as a study lists one
            same = []
        else:
            blocks += ["No score changed.", _table(same)] if rows else ["No score changed."]
            same = []
        if same:
            blocks.append(_folded(_did_not_change(same), _table(same)))
    comments = [d for d in diff["differences"] if d["kind"] == "comment"]
    if comments:
        blocks.append("Comments changed:\n\n" + "\n".join(f"- {_label(d)}" for d in comments))
    other = [d for d in diff["differences"] if not d["scored"] and d["kind"] != "comment"]
    if other:
        blocks.append(
            "Other changes, not part of the score:\n\n" + "\n".join(f"- {_label(d)}" for d in other)
        )
    return blocks


def _moves(row: dict[str, Any]) -> bool:
    """True for a score that changed, or that exists on one side only."""
    return row["changed"] or row["old"] is None or row["new"] is None


def _did_not_change(rows: list[dict[str, Any]]) -> str:
    return f"{len(rows)} {'score did' if len(rows) == 1 else 'scores did'} not change"


def _products(rows: list[dict[str, Any]], diff: dict[str, Any]) -> list[dict[str, Any]]:
    """The products of the diff in the order of the report, each with its `rows` and its
    `status` (`both`, `new` or `removed`), from the report's `products`. A product is named by
    the process that makes it as well, `bag sewing | bag`, when the process has another name,
    and a product of a study by its amount first, `2 × T-shirt`. A name that two products share
    is followed by the id, in the rows too."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row["product"]["id"], []).append(row)
    listed = {p["id"]: p for p in diff.get("products") or []}
    found: list[dict[str, Any]] = []
    for product, product_rows in grouped.items():
        info = listed.get(product) or {}
        status = info.get("status") or (
            "new"
            if all(r["old"] is None for r in product_rows)
            else "removed"
            if all(r["new"] is None for r in product_rows)
            else "both"
        )
        name = product_rows[0]["product"]["name"]
        process = info.get("process") or product_rows[0]["product"].get("process")
        if process and process != name:
            name = f"{process} | {name}"
        amount = info.get("amount")
        if amount:
            shown = amount["new"] if amount["new"] is not None else amount["old"]
            name = f"{shown:g} × {name}"
        product_rows = [{**r, "product": {**r["product"], "name": name}} for r in product_rows]
        found.append({"id": product, "name": name, "status": status, "rows": product_rows})
    shared = {n for n, c in Counter(p["name"] for p in found).items() if c > 1}
    for product in found:
        if product["name"] in shared:
            label = f"{product['name']} ({product['id']})"
            product["rows"] = [
                {**row, "product": {**row["product"], "name": label}} for row in product["rows"]
            ]
            product["name"] = label
    return found


def _several(diff: dict[str, Any], products: list[dict[str, Any]], shape: Shape) -> list[str]:
    """What follows the background line of a folder with several products: each line that
    differs once, then a block for each product that moved, in the order of the folder. A
    database's products that did not move are counted; each of a study's is one line. Each block
    has the columns of the lines that moved that product. The first block of scores is open."""
    top, columns, opened = shape
    differences = {d["id"]: d for d in diff["differences"]}
    tolerance = diff["tolerance"]
    study = diff.get("kind") == "study"
    shown = [p for p in products if study or any(_moves(r) for r in p["rows"])]
    changed = {p["id"]: [r for r in p["rows"] if r["changed"]] for p in shown}
    # a line is listed even when it moves no product
    driving = {d["difference"] for p in products for r in p["rows"] for d in r["drivers"]}
    ids = [d["id"] for d in diff["differences"] if d["id"] in driving]
    keys = short_names([differences[i] for i in ids])
    effects = {
        p["id"]: _Effects.of(
            _relevant(ids, changed[p["id"]], tolerance), keys, changed[p["id"]], columns, tolerance
        )
        for p in shown
    }
    blocks: list[str] = []
    if ids:
        every = [r for rows in changed.values() for r in rows]
        # a line with a column in a table is listed, whatever its share of all the scores
        keep = {i for e in effects.values() for i in e.own}
        blocks.append(_lines(ids, every, differences, keys, top, keep))
    if any(e.header for e in effects.values()):
        blocks.append("A line's column is the change that line alone makes.")
    first = True
    for product in shown:
        moved = [r for r in product["rows"] if _moves(r)]
        if not moved:
            blocks.append(f"{_text(product['name'])}: no change.")
            continue
        trim = opened < OPEN and not first
        blocks.append(_block(product, moved, effects[product["id"]], opened, first, diff, trim))
        first = False
    rest = len(products) - len(shown)
    if rest:
        blocks.append(f"{rest} other {'product' if rest == 1 else 'products'}, no change.")
    return blocks


def _block(
    product: dict[str, Any],
    moved: list[dict[str, Any]],
    effects: "_Effects",
    opened: int,
    first: bool,
    diff: dict[str, Any],
    trim: bool,
) -> str:
    """The scores of one product that moved in a `<details>` section, open when `first`. The
    first shows the first `opened` scores and folds the rest. The scores that did not change are
    folded below. With `trim`, for a long comment, only the first `opened` scores are listed and
    the rest are counted."""
    status = product["status"]
    created = diff.get("created")
    tag = f" ({status} product)" if status != "both" and not created else ""
    noun = "score" if len(moved) == 1 else "scores"
    same = [r for r in product["rows"] if not any(r is m for m in moved)]
    if trim:
        inner = [_table(moved[:opened], effects)]
        if len(moved) > opened:
            more = len(moved) - opened
            inner.append(
                f"and {more} more {'score' if more == 1 else 'scores'}, in the job summary."
            )
    else:
        inner = _open_first(moved, effects, opened) if first else [_table(moved, effects)]
        if same:
            inner.append(_folded(_did_not_change(same), _table(same)))
    summary = f"{_text(product['name'])}{tag}: {len(moved)} {noun}"
    tag_open = "<details open>" if first else "<details>"
    return f"{tag_open}\n<summary>{summary}</summary>\n\n" + "\n\n".join(inner) + "\n\n</details>"


def _relevant(ids: list[str], rows: list[dict[str, Any]], tolerance: float) -> list[str]:
    """The differences of `ids` that move one of `rows`: an effect above the tolerance, or one
    that could not be worked out. All of `ids` when none does."""
    moving: set[str] = set()
    for row in rows:
        scale = max(abs(row["old"] or 0.0), abs(row["new"] or 0.0))
        for d in row["drivers"]:
            if d["contribution"] is None or abs(d["contribution"]) > tolerance * scale:
                moving.add(d["difference"])
    return [i for i in ids if i in moving] or list(ids)


def _open_first(rows: list[dict[str, Any]], effects: "_Effects", opened: int) -> list[str]:
    blocks = [_table(rows[:opened], effects)]
    folded = rows[opened:]
    if folded:
        noun = "score" if len(folded) == 1 else "scores"
        blocks.append(_folded(f"{len(folded)} more {noun}", _table(folded, effects)))
    return blocks


def short_names(found: list[dict[str, Any]]) -> dict[str, str]:
    """A short name for each difference, for the header of a column: the line id of a line,
    `e02`, the item of anything else. When two would be the same, all are numbered from 1."""
    names = {d["id"]: _short_name(d) for d in found}
    if len(set(names.values())) < len(names):
        return {d["id"]: str(i) for i, d in enumerate(found, 1)}
    return names


def _short_name(difference: dict[str, Any]) -> str:
    item: str = difference["item"]
    database = difference.get("database")
    item = item.removesuffix(f" in {database}") if database else item
    _, line, line_id = item.rpartition(" line ")
    return line_id if line else item


def chosen(ids: list[str], rows: list[dict[str, Any]], most: int) -> list[str]:
    """The `most` differences of `ids` with the largest share of any score's change, in the order
    of `ids`, as `headwater diff` chooses them: the share of a difference in a score is the size
    of its effect over the sizes of all of them."""
    if len(ids) <= most:
        return list(ids)
    share = dict.fromkeys(ids, 0.0)
    for row in rows:
        effects = {d["difference"]: abs(d["contribution"] or 0.0) for d in row["drivers"]}
        total = sum(effects.values())
        for i in ids:
            if total > 0:
                share[i] = max(share[i], effects.get(i, 0.0) / total)
    largest = set(sorted(ids, key=lambda i: -share[i])[:most])
    return [i for i in ids if i in largest]


def _lines(
    ids: list[str],
    rows: list[dict[str, Any]],
    differences: dict[str, dict[str, Any]],
    keys: dict[str, str],
    top: int,
    keep: set[str] | None = None,
) -> str:
    """Each line that differs once, in the order of the files: the `top` with the largest share
    of a score's change when there are more, and those of `keep`."""
    wanted = set(chosen(ids, rows, top)) | (keep or set())
    shown = [i for i in ids if i in wanted]
    table = ["| | Lines that differ |", "|---|---|"]
    table += [_cells([keys[i], _label(differences[i])]) for i in shown]
    rest = len(ids) - len(shown)
    if rest > 0:
        table.append(f"\nand {rest} more {'line differs' if rest == 1 else 'lines differ'}.")
    return "\n".join(table)


class _Effects:
    """The columns of effects in a table of scores: one per line, in the order of the files, at
    most `columns` (those with the largest share of a score's change), then the other lines
    together, then the interaction when a score needs it."""

    def __init__(
        self,
        own: list[str],
        others: list[str],
        keys: dict[str, str],
        interaction: bool,
        tolerance: float,
    ) -> None:
        self.own, self.others, self.interaction = own, others, interaction
        self.tolerance = tolerance
        self.header = [keys[i] for i in own]
        self.header += [f"{len(others)} others"] if others else []
        self.header += ["interaction"] if interaction else []

    @classmethod
    def of(
        cls,
        ids: list[str],
        keys: dict[str, str],
        changed: list[dict[str, Any]],
        columns: int,
        tolerance: float,
    ) -> "_Effects":
        if not changed or not ids:
            return cls([], [], keys, False, tolerance)
        own = chosen(ids, changed, columns)
        others = [i for i in ids if i not in own]
        interacts = any(_interacts(r, tolerance) for r in changed)
        return cls(own, others, keys, interacts, tolerance)

    def cells(self, row: dict[str, Any]) -> list[str]:
        if not self.header:
            return []
        if not row["changed"]:
            return [""] * len(self.header)
        scale = max(abs(row["old"] or 0.0), abs(row["new"] or 0.0))
        cells = [_effect(_sum(row, [i]), scale, self.tolerance) for i in self.own]
        if self.others:
            cells.append(_effect(_sum(row, self.others), scale, self.tolerance))
        if self.interaction:
            cells.append(_signed(row["interaction"]) if _interacts(row, self.tolerance) else "")
        return cells


def _sum(row: dict[str, Any], ids: list[str]) -> float | None:
    """What the differences `ids` each did alone to `row`, added up, or None when one of them
    could not be worked out."""
    by_id = {d["difference"]: d["contribution"] for d in row["drivers"]}
    found = [by_id.get(i) for i in ids]
    return None if any(c is None for c in found) else sum(c for c in found if c is not None)


def _interacts(row: dict[str, Any], tolerance: float) -> bool:
    scale = max(abs(row["old"] or 0.0), abs(row["new"] or 0.0))
    interaction = row["interaction"]
    return interaction is not None and abs(interaction) > tolerance * scale


def _folded(summary: str, body: str) -> str:
    return f"<details>\n<summary>{summary}</summary>\n\n{body}\n\n</details>"


def _table(rows: list[dict[str, Any]], effects: _Effects | None = None) -> str:
    header = effects.header if effects is not None else []
    lines = [
        _cells(["Product", "Indicator", "Old", "New", "Change", "%", *header]),
        "|---|---|" + "---:|" * (4 + len(header)),
    ]
    for row in rows:
        lines.append(_row(row, effects.cells(row) if effects is not None else []))
    return "\n".join(lines)


def _row(row: dict[str, Any], effects: list[str]) -> str:
    indicator = row["indicator"]
    old, new = row["old"], row["new"]
    if old is None:
        change, percent = "only in head", ""
    elif new is None:
        change, percent = "only in base", ""
    elif not row["changed"]:
        change, percent = "no change", ""
    else:
        change = _signed(row["change"])
        percent = "" if row["percent"] is None else f"{row['percent']:+.1f}%"
    cells = [
        _text(row["product"]["name"]),
        _text(f"{indicator['name']} ({indicator['unit']})"),
        "-" if old is None else _number(old),
        "-" if new is None else _number(new),
        change,
        percent,
        *effects,
    ]
    return _cells(cells)


def _effect(contribution: float | None, scale: float, tolerance: float) -> str:
    if contribution is None:
        return "n/a"
    if abs(contribution) <= tolerance * scale:
        return "+0"
    return _signed(contribution)


def _label(difference: dict[str, Any]) -> str:
    """`tshirt:tshirt line e02` (cotton fabric): amount 0.25 → 0.22 kg. The name is the flow's,
    from the `flow` key, and the description is its `text`, with suppliers named in words."""
    kind, item = difference["kind"], difference["item"]
    flow = difference.get("flow")
    name = flow["name"] if flow else None
    named = f" ({_text(name)})" if name and name != item and kind in _NAMED else ""
    prefix = _PREFIX.get(kind, f"{kind} ")
    text = difference.get("text") or difference["description"]
    return f"{_code(prefix + item)}{named}: {_text(text)}"


# --- text --------------------------------------------------------------------------------------


def _number(value: float) -> str:
    return f"{value:.6g}"


def _signed(value: float) -> str:
    return f"{value + 0.0:+.6g}"


def _cells(cells: list[str]) -> str:
    """A table row. A pipe in a cell is escaped, also inside a code span."""
    return "| " + " | ".join(c.replace("|", "\\|") for c in cells) + " |"


def _text(text: str) -> str:
    """Text from a database or a study, as one line that cannot start a tag."""
    return " ".join(text.split("\n")).replace("<", "&lt;").replace(">", "&gt;")


def _code(text: str) -> str:
    """An inline code span, with enough backticks for what is inside."""
    text = " ".join(text.split("\n"))
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    ticks = "`" * (longest + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{ticks}{pad}{text}{pad}{ticks}"


def _fence(text: str) -> str:
    """A fenced block, longer than any run of backticks inside."""
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    ticks = "`" * max(3, longest + 1)
    return f"{ticks}text\n{text}\n{ticks}"


# --- the limit ---------------------------------------------------------------------------------


def _cut(body: str, limit: int) -> str:
    """The start of `body` that fits with a note, with open fences and sections closed."""
    room = limit - len(_NOTE) - 100
    head = body[: max(room, 0)]
    head = head[: head.rfind("\n")] if "\n" in head else head
    closers: list[str] = []
    fence: str | None = None
    for line in head.splitlines():
        match = re.match(r"(`{3,})", line)
        if match and fence is None:
            fence = match.group(1)
        elif match and fence is not None and line.strip() == fence:
            fence = None
    if fence is not None:
        closers.append(fence)
    opened = head.count("<details") - head.count("</details>")
    closers += ["</details>"] * opened
    return (
        head.rstrip("\n") + "\n" + "\n".join(closers) + ("\n\n" if closers else "\n") + _NOTE + "\n"
    )


# --- the command -------------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write the pull request comment for a report.")
    parser.add_argument("--report", required=True, help="The JSON report of collect.py.")
    parser.add_argument("--out", required=True, help="Where to write the comment.")
    parser.add_argument("--summary", default="", help="A file to append the full text to.")
    parser.add_argument("--run-url", default="", help="A link to the workflow run.")
    args = parser.parse_args(argv)
    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    url = args.run_url or None
    Path(args.out).write_text(render(report, run_url=url), encoding="utf-8")
    if args.summary:
        with open(args.summary, "a", encoding="utf-8") as handle:
            handle.write(render(report, run_url=url, limit=SUMMARY_LIMIT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
