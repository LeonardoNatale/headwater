"""A diff as text for people, and as JSON for CI."""

from collections.abc import Collection, Sequence
from typing import Any

from headwater.diff._attribute import UNCHANGED_RTOL, Product, Row, StudyDiff
from headwater.diff._changes import Difference, Names
from headwater.diff._git import Source
from headwater.diff._moved import FACTOR, NO_SUCCESSOR, Moved

FORMAT = "headwater-diff/2"
"""The `format` of the JSON. Within version 2 keys may be added, none is renamed or removed."""

DEFAULT_TOP = 10
COLUMNS = 5
"""Lines that get a column of their own in a table of effects: the rest share one."""


def render_text(
    diff: StudyDiff,
    *,
    path: str,
    old: Source,
    new: Source,
    top: int = DEFAULT_TOP,
    sides: tuple[str, str] | None = None,
) -> str:
    """The diff of the database or study at `path`, for a terminal.

    The scores come first, in the method's order. When one score changed, the lines that differ
    follow, ranked by their effect on it. When several did, each line that differs is listed
    once, in the order of the files, then a table gives what each did alone to each score that
    changed. Then the comments that changed, and what a score does not depend on. At most `top`
    lines are listed. Across a release change, each changed score says how much is the release's
    and how much the study's, and the lines the release moved come before the lines that differ,
    for the first score that changed. `sides` names the two sides instead of their sources, such
    as the two releases of an upgrade.
    """
    named = sides or (old.label, new.label)
    kind = diff.kind.capitalize()
    lines = [f"{kind} {path}: {named[0]} → {named[1]}", f"Background: {_background(diff)}"]
    lines[-1] += f", method {diff.method.id} {diff.method.version}"
    if diff.old_release is not None:
        lines[-1] += f"; before: {_short(diff.old_release)}"
    products = diff.products
    if diff.created and not diff.rows:
        return "\n".join([*lines, "", new_database(len(products), path)])
    if diff.created:
        lines += [
            "",
            f"New {diff.kind}: it does not exist at {old.label}, so there is nothing to compare "
            "with.",
        ]
    several = len(products) > 1
    shown = [p for p in products if diff.kind == "study" or p.differs]
    if several:
        lines += _product_blocks(shown, len(products) - len(shown), diff, old, new)
    else:
        for product in products:
            lines += ["", product.label, *_score_lines(list(product.rows), old, new, diff.kind)]
    if not diff.differences and not diff.created:
        lines += ["", "No lines differ."]
    changed = [r for r in diff.rows if r.changed]
    first = next(iter(changed or [r for r in diff.rows if r.drivers]), None)
    if first is not None and first.moved:
        lines += ["", *_release_section(first, diff, top, several)]
    found = [d for d in diff.differences if first is not None and d.id in _ids(first)]
    if several:
        lines += _products_effects(shown, diff, top)
    elif len(changed) > 1 and found:
        keys = _short_names(found)
        lines += ["", *_listed(found, changed, diff, keys, top)]
        lines += ["", *_effects(found, changed, keys)]
    elif first is not None and (first.drivers or not first.moved or _interacts(first)):
        lines += ["", *_section(first, diff, top)]
    comments = [d for d in diff.differences if d.kind == "comment"]
    if comments:
        lines += ["", "Comments changed:", *(f"  {_label(d, diff)}" for d in comments)]
    other = [d for d in diff.differences if not d.scored and d.kind != "comment"]
    if other:
        lines += ["", "Other changes, not part of the score:"]
        lines += [f"  {_label(d, diff)}" for d in other]
    return "\n".join(lines)


def product_label(process: str, name: str, process_id: str) -> str:
    """A product in words: the name of the process that makes it, its own name and the id of the
    process, as `bag sewing | bag (tshirt:bag-sewing)`. A process with the product's name is not
    named again: `T-shirt (tshirt:tshirt)`."""
    named = name if process == name else f"{process} | {name}"
    return f"{named} ({process_id})"


def new_database(products: int, path: str) -> str:
    """The one line of a new database: its products counted, not scored (D43)."""
    noun = "product" if products == 1 else "products"
    return f"New database: {products} {noun}, not scored. `headwater calc {path}` scores them."


def _ids(row: Row) -> set[str]:
    return {d.difference.id for d in row.drivers}


def _product_blocks(
    shown: Sequence[Product], rest: int, diff: StudyDiff, old: Source, new: Source
) -> list[str]:
    """One block of scores for each product of `shown`, those with a score that changed, or that
    only one side makes, and `rest` products that did not move, counted."""
    lines: list[str] = []
    for product in shown:
        tag = ""
        if not diff.created and product.status != "both":
            tag = f", {product.status} product"
        lines += ["", f"{product.label}{tag}"]
        lines += (
            _score_lines(list(product.rows), old, new, diff.kind)
            if product.differs
            else ["  no change"]
        )
    if rest:
        lines += ["", f"{rest} other {'product' if rest == 1 else 'products'}, no change"]
    return lines


def _relevant(found: Sequence[Difference], rows: Sequence[Row]) -> list[Difference]:
    """The differences of `found` that move one of `rows`: an effect above the tolerance, or one
    that cannot be worked out. All of `found` when none does."""
    moving = {
        d.difference.id
        for row in rows
        for d in row.drivers
        if d.contribution is None or abs(d.contribution) > UNCHANGED_RTOL * _scale(row)
    }
    return [d for d in found if d.id in moving] or list(found)


def _products_effects(shown: Sequence[Product], diff: StudyDiff, top: int) -> list[str]:
    """Each line that differs once, then for each product of `shown` with a score that changed
    what each line did alone to it, with the columns of the lines that moved that product. A
    line that moves no product is listed too."""
    changed = [(p, [r for r in p.rows if r.changed]) for p in shown]
    changed = [(p, rows) for p, rows in changed if rows]
    every = [r for _, rows in changed for r in rows]
    ids = {i for p in diff.products for r in p.rows for i in _ids(r)}
    found = [d for d in diff.differences if d.id in ids]
    if not found:
        return []
    keys = _short_names(found)
    own = {p.id: _relevant(found, rows) for p, rows in changed}
    # a line with a column in a table is listed, whatever its share of all the scores
    keep = {i for p, rows in changed for i in _chosen([d.id for d in own[p.id]], rows, COLUMNS)}
    lines = ["", *_listed(found, every, diff, keys, top, keep)]
    for p, rows in changed:
        title = f"Effect of each line alone on each score of {p.label} that changed:"
        lines += ["", *_effects(own[p.id], rows, keys, title)]
    return lines


def _short_names(found: Sequence[Difference]) -> dict[str, str]:
    """A short name for each difference, for the header of a table: the line id of a line,
    `e02`, and the item of anything else, `fabric_kg`. When two would be the same, all are
    numbered from 1 instead."""
    names = {d.id: _short_name(d) for d in found}
    if len(set(names.values())) < len(names):
        return {d.id: str(i) for i, d in enumerate(found, 1)}
    return names


def _short_name(d: Difference) -> str:
    item = d.item.removesuffix(f" in {d.database}") if d.database else d.item
    _, line, line_id = item.rpartition(" line ")
    return line_id if line else item


def _chosen(ids: Sequence[str], rows: Sequence[Row], most: int) -> list[str]:
    """The `most` differences of `ids` with the largest share of any score's change, in the order
    of `ids`. The share of a difference in a score is its effect over the effects of all of
    them, each counted as a size: 0.9 for +0.9 next to -0.1. One with no effect that can be
    worked out has no share."""
    if len(ids) <= most:
        return list(ids)
    share = dict.fromkeys(ids, 0.0)
    for row in rows:
        effects = {d.difference.id: abs(d.contribution or 0.0) for d in row.drivers}
        total = sum(effects.values())
        for i in ids:
            if total > 0:
                share[i] = max(share[i], effects.get(i, 0.0) / total)
    largest = set(sorted(ids, key=lambda i: -share[i])[:most])
    return [i for i in ids if i in largest]


def _listed(
    found: Sequence[Difference],
    rows: Sequence[Row],
    diff: StudyDiff,
    keys: dict[str, str],
    top: int,
    keep: Collection[str] = (),
) -> list[str]:
    """Each line that differs once, by its short name, in the order of the files: the `top` with
    the largest share of a score's change when there are more, and those of `keep`."""
    shown = set(_chosen([d.id for d in found], rows, top)) | set(keep)
    listed = [d for d in found if d.id in shown]
    width = max(len(keys[d.id]) for d in listed)
    text = ["Lines that differ:"]
    text += [f"  {keys[d.id]:<{width}}  {_label(d, diff)}" for d in listed]
    rest = len(found) - len(listed)
    if rest > 0:
        text.append(f"  and {rest} more {'line differs' if rest == 1 else 'lines differ'}")
    return text


def render_json(
    diff: StudyDiff,
    *,
    path: str,
    old: Source,
    new: Source,
    validation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The diff as plain data: `json.dumps` of it is the output of `headwater diff --json`.

    The keys are listed in docs/workflows.md and tested. Numbers are floats, and a score or a
    percentage that does not exist is null. `validation` is the object of `validate --json` for
    the new side, which the command made before it compared.
    """
    return {
        "format": FORMAT,
        "path": path,
        "kind": diff.kind,
        "created": diff.created,
        "old": _side(old, diff.old_input_hash, diff.old_residual),
        "new": _side(new, diff.new_input_hash, diff.new_residual),
        "engine_version": diff.engine_version,
        "solver": diff.solver,
        "background": {"release": diff.release, "folder": diff.folder},
        # Several methods used together have a joined id, which is not written to a file: `method`
        # is null for them, and `methods` lists the ids.
        "method": (
            {"id": diff.method.id, "version": diff.method.version, "hash": diff.method_hash}
            if len(diff.method.ids) == 1
            else None
        ),
        "methods": list(diff.method.ids),
        "tolerance": UNCHANGED_RTOL,
        "old_background": (
            None
            if diff.old_release is None
            else {"release": diff.old_release, "folder": None, "method_hash": diff.old_method_hash}
        ),
        "products": [_product(p) for p in diff.products],
        "results": [_result(row, diff.labels) for row in diff.rows],
        "differences": [_difference(d, diff.flow_names, diff.names) for d in diff.differences],
        "processes": {
            i: {"name": p.name, "location": p.location} for i, p in sorted(diff.processes.items())
        },
        "validation": validation,
    }


# --- text --------------------------------------------------------------------------------------


def _number(value: float) -> str:
    return f"{value:.6g}"


def _signed(value: float) -> str:
    return f"{value + 0.0:+.6g}"


def _background(diff: StudyDiff) -> str:
    if diff.release is not None:
        return _short(diff.release)
    return f"folder {diff.folder}"


def _short(release: str) -> str:
    """`toy-1@b24ebd7d98cc`, from a release written with its full hash."""
    name, _, release_hash = release.partition("@")
    return f"{name}@{release_hash[:12]}"


def _score_lines(rows: list[Row], old: Source, new: Source, kind: str = "database") -> list[str]:
    """One line per indicator, columns aligned: name, old, new, unit, change. Across a release
    change, the change is split into the release's part and the `kind`'s, the folder's."""
    table: list[list[str]] = []
    for row in rows:
        name = f"{row.indicator_name}:"
        unit = f"{row.unit}," if row.unit else ""  # a normalised score has no unit
        if row.old is None and row.new is not None:
            table.append([name, _number(row.new), "", unit, f"only in {new.label}"])
        elif row.new is None and row.old is not None:
            table.append([name, _number(row.old), "", unit, f"only in {old.label}"])
        else:
            assert row.old is not None and row.new is not None and row.change is not None
            if not row.changed:
                table.append([name, _number(row.old), "", unit, "no change"])
                continue
            change = f"change {_signed(row.change)}"
            if row.percent is not None:
                change += f" ({row.percent:+.1f}%)"
            if row.background is not None:
                change += f": release {_signed(row.background)}"
                change += f", {kind} {_signed(row.change - row.background)}"
            table.append([name, _number(row.old), f"→ {_number(row.new)}", unit, change])
    widths = [max(len(r[i]) for r in table) for i in range(5)]
    aligned = [
        " ".join(
            (cell.ljust(w) if i in (0, 2, 3, 4) else cell.rjust(w))
            for i, (cell, w) in enumerate(zip(r, widths, strict=True))
            if w > 0
        )
        for r in table
    ]
    return [f"  {line.rstrip()}" for line in aligned]


def _named(row: Row) -> str:
    """`Climate change (kg CO2-eq)`, or the name alone for a score with no unit."""
    return f"{row.indicator_name} ({row.unit})" if row.unit else row.indicator_name


def _section(row: Row, diff: StudyDiff, top: int) -> list[str]:
    """The lines that differ, ranked by their effect on `row`."""
    scale = _scale(row)
    shown = row.drivers[:top]
    rest = len(row.drivers) - len(shown)
    last = None
    if _interacts(row):
        assert row.interaction is not None
        last = (_signed(row.interaction), "interaction between the changes")
    return _ranked(
        f"Lines that differ, ranked by their effect on {_named(row)}:",
        [(_effect(d.contribution, scale), _label(d.difference, diff)) for d in shown],
        f"and {rest} more {'line differs' if rest == 1 else 'lines differ'}" if rest > 0 else None,
        last,
    )


def _scale(row: Row) -> float:
    return max(abs(row.old or 0.0), abs(row.new or 0.0))


def _effect_of(row: Row, ids: Sequence[str]) -> float | None:
    """What the differences `ids` do together to `row`, each alone, or None when one of them
    cannot be worked out."""
    by_id = {d.difference.id: d.contribution for d in row.drivers}
    effects = [by_id.get(i) for i in ids]
    return None if any(e is None for e in effects) else sum(e for e in effects if e is not None)


def _effects(
    found: Sequence[Difference],
    rows: Sequence[Row],
    keys: dict[str, str],
    title: str = "Effect of each line alone on each score that changed:",
) -> list[str]:
    """What each line that differs did alone to each score of `rows`: one column per line, in the
    order of the files, at most `COLUMNS` (those with the largest share of a score's change),
    then the others together, then the interaction when a score needs it."""
    ids = [d.id for d in found]
    own = _chosen(ids, rows, COLUMNS)
    others = [i for i in ids if i not in own]
    interacts = any(_interacts(r) for r in rows)
    header = [keys[i] for i in own]
    header += [f"{len(others)} others"] if others else []
    header += ["interaction"] if interacts else []
    several = len({r.product_id for r in rows}) > 1
    table = [["", *header]]
    for row in rows:
        scale = _scale(row)
        name = f"{row.product}, {_named(row)}:" if several else f"{_named(row)}:"
        cells = [_effect(_effect_of(row, [i]), scale) for i in own]
        if others:
            cells.append(_effect(_effect_of(row, others), scale))
        if interacts:
            cells.append("" if not _interacts(row) else _signed(row.interaction or 0.0))
        table.append([name, *cells])
    widths = [max(len(r[i]) for r in table) for i in range(len(table[0]))]
    aligned = [
        "  ".join(
            cell.ljust(w) if i == 0 else cell.rjust(w)
            for i, (cell, w) in enumerate(zip(r, widths, strict=True))
        )
        for r in table
    ]
    return [title, *(f"  {line.rstrip()}" for line in aligned)]


def _release_section(row: Row, diff: StudyDiff, top: int, several: bool = False) -> list[str]:
    """The lines the release moved, the largest effect first. With several products, the title
    names the product."""
    shown = row.moved[:top]
    rest = len(row.moved) - len(shown)
    on = f"{row.product}, {_named(row)}" if several else _named(row)
    return _ranked(
        f"Release change, line by line, on {on}:",
        [(_signed(m.effect), _moved_label(m, diff)) for m in shown],
        f"and {rest} more {'line' if rest == 1 else 'lines'}" if rest > 0 else None,
    )


def _ranked(
    title: str,
    ranked: list[tuple[str, str]],
    more: str | None,
    last: tuple[str, str] | None = None,
) -> list[str]:
    """`title`, then each effect and its label, effects aligned, then `more` and `last`."""
    rows = [*ranked, *([last] if last else [])]
    width = max((len(effect) for effect, _ in rows), default=0)
    lines = [title, *(f"  {effect:>{width}}  {label}" for effect, label in ranked)]
    if more is not None:
        lines.append(f"  {more}")
    if last is not None:
        lines.append(f"  {last[0]:>{width}}  {last[1]}")
    return lines


_HOW = {
    "same-id": "same id",
    "same-dataset": "same dataset",
    "new-study": "as in the new database",
}
"""How a moved line is named in text, where its JSON name is not plain words."""


def _moved_label(m: Moved, diff: StudyDiff) -> str:
    """`tshirt:tshirt line e04 (reactive dye): 4 → 3 per kg, as in the new database, dye production
    (GLO) → reactive dye production (GLO)`. A supplier with the same name and location in both
    releases is named once, with its two ids."""
    names = diff.names.processes
    name = diff.flow_names.get(m.flow_id)
    where = f"{m.process_id} line {m.line_id}" if m.line_id else m.process_id
    where += f" ({name})" if name else ""
    if m.how == FACTOR:
        return f"{where}: factor {_number(m.old_value)} → {_number(m.new_value)}"
    old_supplier = None if m.old_supplier is None else names.get(m.old_supplier, m.old_supplier)
    if m.how == NO_SUCCESSOR:
        return (
            f"{where}: {_number(m.old_value)} per {m.unit}, {old_supplier} has no successor "
            "and the new study no longer has the line"
        )
    new_supplier = None if m.new_supplier is None else names.get(m.new_supplier, m.new_supplier)
    if old_supplier == new_supplier and m.old_supplier in names:
        moved = f"{old_supplier}, {m.old_supplier} → {m.new_supplier}"
    else:
        moved = f"{old_supplier} → {new_supplier}"
    return (
        f"{where}: {_number(m.old_value)} → {_number(m.new_value)} per {m.unit}, "
        f"{_HOW.get(m.how, m.how)}, {moved}"
    )


def _interacts(row: Row) -> bool:
    """True when the interaction is too large to leave out."""
    scale = max(abs(row.old or 0.0), abs(row.new or 0.0))
    return row.interaction is not None and abs(row.interaction) > UNCHANGED_RTOL * scale


def _effect(contribution: float | None, scale: float) -> str:
    if contribution is None:
        return "n/a"
    if abs(contribution) <= UNCHANGED_RTOL * scale:
        return "+0"
    return _signed(contribution)


_PREFIX = {
    "line": "",
    "override": "override of ",
    "cut_off": "cut-off ",
    "flow_property": "flow property ",
    "no_factor": "no_factor.csv row ",
}
_NAMED = ("line", "override", "no_factor", "comment", "product")
"""The kinds whose label names the flow in words: its id alone is of little use to a reader."""


def _label(d: Difference, diff: StudyDiff) -> str:
    """`tshirt:tshirt line e02 (cotton fabric): amount 0.25 → 0.22 kg`."""
    prefix = "" if d.kind == "comment" else _PREFIX.get(d.kind, f"{d.kind} ")
    name = diff.flow_names.get(d.flow_id) if d.flow_id else None
    named = f" ({name})" if name and name != d.item and d.kind in _NAMED else ""
    return f"{prefix}{d.item}{named}: {d.described(diff.names)}"


# --- JSON --------------------------------------------------------------------------------------


def _side(source: Source, input_hash: str | None, residual: float | None) -> dict[str, Any]:
    return {
        "ref": source.ref,
        "commit": source.commit,
        "input_hash": input_hash,
        "residual": residual,
    }


def _product(product: Product) -> dict[str, Any]:
    amount = product.amount
    return {
        "id": product.id,
        "name": product.name,
        "process": product.process_name,
        "status": product.status,
        "amount": None if amount is None else {"old": amount[0], "new": amount[1]},
    }


def _result(row: Row, labels: dict[str, tuple[str, str]]) -> dict[str, Any]:
    return {
        "product": {
            "id": row.product_id,
            "name": row.product,
            "process": labels.get(row.product_id, (row.product, row.product))[1],
        },
        "indicator": {"id": row.indicator, "name": row.indicator_name, "unit": row.unit},
        "old": row.old,
        "new": row.new,
        "change": row.change,
        "percent": row.percent,
        "changed": row.changed,
        "drivers": [
            {"difference": d.difference.id, "contribution": d.contribution} for d in row.drivers
        ],
        "interaction": row.interaction,
        "background": row.background,
        "moved": [_moved(m) for m in row.moved],
    }


def _moved(m: Moved) -> dict[str, Any]:
    return {
        "process": m.process_id,
        "line": m.line_id,
        "flow": m.flow_id,
        "unit": m.unit,
        "old_supplier": m.old_supplier,
        "new_supplier": m.new_supplier,
        "old_value": m.old_value,
        "new_value": m.new_value,
        "effect": m.effect,
        "how": m.how,
    }


def _difference(d: Difference, flow_names: dict[str, str], names: Names) -> dict[str, Any]:
    flow = None if d.flow_id is None else {"id": d.flow_id, "name": flow_names.get(d.flow_id)}
    return {
        "id": d.id,
        "kind": d.kind,
        "change": d.change.value,
        "item": d.item,
        "description": d.description,
        "text": d.described(names),
        "fields": [{"name": f.name, "old": f.old, "new": f.new} for f in d.fields],
        "affects": list(d.affects),
        "scored": d.scored,
        "flow": flow,
        "database": d.database,
    }
