"""`headwater upgrade`: move databases and studies to a new release, say how each supplier mapped,
and diff."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

import typer

import headwater as hw
from headwater.cli._common import (
    IndicatorOption,
    MethodOption,
    check_folder,
    fail,
    load_background,
    pick_indicators,
    pinned_record,
    print_validation,
    validation_data,
    with_pull_hint,
)
from headwater.cli.diff import OldMethodOption
from headwater.diff import Source, diff_databases, render_json, render_text
from headwater.diff._render import DEFAULT_TOP
from headwater.model import HeadwaterError, InvalidDataError, Problem, Report
from headwater.study import read_database, read_study

if TYPE_CHECKING:
    from headwater.upgrade import Dataset, Flag, Match, Plan

FORMAT = "headwater-upgrade/2"
"""The `format` of `upgrade --json`. Within version 2 keys may be added, none is renamed or
removed."""

_RULES = {
    "mapping": "mapping row",
    "same-id": "same id",
    "correspondence": "correspondence",
    "same-dataset": "same dataset",
    "unmapped": "not mapped",
}
"""Each rule of `headwater.upgrade.Rule`, by its value, as the report names it."""


def upgrade_command(
    folders: Annotated[
        list[Path],
        typer.Argument(
            metavar="FOLDER...",
            help="Database and study folders to move together. The databases they use, and the "
            "databases a study's products are in, move with them.",
        ),
    ],
    to: Annotated[
        str,
        typer.Option(
            "--to", help="The release to move to: name@hash, with at least 12 characters."
        ),
    ],
    mapping: Annotated[
        Path | None,
        typer.Option(
            "--mapping",
            help="Reviewed rows that decide suppliers, in the format of the supplier mapping: "
            "supplier_id,name,product,location,unit,supplier,reason,who,when,comment.",
        ),
    ] = None,
    method: MethodOption = None,
    old_method: OldMethodOption = None,
    indicator: IndicatorOption = None,
    top: Annotated[
        int,
        typer.Option("--top", min=1, help="Lines to list for each indicator, the largest first."),
    ] = DEFAULT_TOP,
    check: Annotated[
        bool, typer.Option("--check", help="Write nothing: report and diff only.")
    ] = False,
    as_json: Annotated[
        bool, typer.Option("--json", help="Print JSON, for CI, instead of text.")
    ] = False,
) -> None:
    """Move databases and studies from the release they pin to another, and show what moved.

    Each supplier is mapped by the first rule that decides it: a row of --mapping, the same ids,
    the same dataset (name, product and location). What no rule maps is listed with its reason,
    the processes it might be, and a row to fill in. Links to RoW and markets that changed are
    listed to review. When everything maps, only the cells that name the old release are
    written, in place, and each database is diffed across the release change. A study's
    scenarios move with it, and so do the products and the pin of a study of release products;
    each study is validated on the new release.

    Exit code 0 when written (or, with --check, when it could be), 1 when something is not
    mapped (nothing is written) or an upgraded folder does not validate, and 2 when the
    upgrade cannot run.
    """
    from headwater.upgrade import plan  # it reads releases: loaded when the command runs

    try:
        found = plan(folders, to=to, mapping=mapping)
    except InvalidDataError as error:
        fail(with_pull_hint(error), code=2)
    except HeadwaterError as error:
        fail(error, code=2)
    report: dict[str, Any] = _report(found, check=check)
    if not found.complete:
        _show(report, found, as_json)
        raise typer.Exit(code=1)
    record = (
        found.databases[0].database
        if found.databases
        else pinned_record(hw.Study._of(found.studies[0]))  # pyright: ignore[reportPrivateUsage]
    )
    try:  # every option is checked before anything is written
        said: set[str] = set()  # both releases may take a method from the same folder
        before = load_background(record, release=None, method=old_method, namespace=None, said=said)
        after = load_background(
            record, release=found.new.pin, method=method, namespace=None, said=said
        )
    except HeadwaterError as error:  # load_background says how to pull a release
        fail(error, code=2)
    indicators = pick_indicators(after, indicator, one=False)
    invalid: list[tuple[str, Report, str]] = []
    texts: list[str] = []
    studies = {s.path.resolve() for s in found.studies if s.path is not None}
    with tempfile.TemporaryDirectory(prefix="headwater-upgrade-") as temp:
        into = Path(temp) if check else None
        written = found.write(into=into)
        report["written"] = [] if check else sorted(_relative(p, found) for p in written)
        diffed = [p for p in folders if p.resolve() not in studies]
        for study in found.studies:  # its databases are diffed once, as if given
            for database in study.databases:
                if database.path.resolve() not in {p.resolve() for p in diffed}:
                    diffed.append(database.path)
        for path in diffed:
            new = read_database((into or path.parent) / path.name)
            checked = check_folder(hw.Database(new), after)
            report["validation"].append(validation_data(str(path), after, checked, "database"))
            if checked.errors:
                invalid.append((new.database.name, checked, "Database"))
                continue
            old = next(f for f in found.databases if f.path.name == path.name)
            try:
                result = diff_databases(
                    old, new, after, old_background=before, indicators=indicators
                )
            except HeadwaterError as error:
                fail(error, code=2)
            source = Source(None, None)
            sides = (found.old.pin, found.new.pin)
            report["diffs"].append(render_json(result, path=str(path), old=source, new=source))
            texts.append(
                render_text(result, path=str(path), old=source, new=source, top=top, sides=sides)
            )
        for study in found.studies:
            assert study.path is not None
            moved = hw.Study._of(read_study((into or study.path.parent) / study.path.name))  # pyright: ignore[reportPrivateUsage]
            checked = check_folder(moved, after)
            report["validation"].append(validation_data(str(study.path), after, checked, "study"))
            if checked.errors:
                invalid.append((study.name or study.path.name, checked, "Study"))
            else:
                texts.append(f"Study {study.name}: valid.")
    _show(report, found, as_json, texts)
    if invalid and not as_json:
        for name, checked, kind in invalid:
            print_validation(name, after, checked, kind)
    if invalid:
        raise typer.Exit(code=1)


def _show(
    report: dict[str, Any], found: Plan, as_json: bool, texts: list[str] | None = None
) -> None:
    if as_json:
        typer.echo(json.dumps(report, indent=2, allow_nan=False, ensure_ascii=False))
        return
    typer.echo("\n".join(_lines(found, report)))
    for text in texts or ():
        typer.echo("")
        typer.echo(text)


def _lines(found: Plan, report: dict[str, Any]) -> list[str]:
    """The report of the mapping as text: counts per rule, what a mapping row decided, what to
    review, what did not map, and what was written."""
    names = ", ".join(
        [*(s.database.name for s in found.databases), *(s.name or "" for s in found.studies)]
    )
    lines = [f"Upgrade {names}: {found.old.pin} → {found.new.pin}", ""]
    lines.append("Suppliers, by the rule that mapped them:")
    for rule, (suppliers, places) in found.counts().items():
        lines.append(
            f"  {_RULES[rule]:<16} {_count(suppliers, 'supplier')}, {_count(places, 'line')}"
        )
    decided = [m for m in found.matches if m.rule == "mapping"]
    if decided:
        lines += ["", "Decided by a mapping row:"]
        lines += [f"  {_decided(m)}" for m in decided]
    if found.flags:
        lines += ["", f"To review ({len(found.flags)}):"]
        lines += [f"  {_flag(f)}" for f in found.flags]
    unmapped = found.unmapped
    if unmapped:
        places = sum(len(m.lines) for m in unmapped)
        lines += ["", f"Not mapped: {_count(len(unmapped), 'supplier')}, {_count(places, 'line')}"]
        for m in unmapped:
            lines.append(f"- [{m.code}] {m.old.label}: {m.reason}")
            lines += [f"    maybe: {s.id} ({s})" for s in m.suggestions]
            lines += [f"    at {place}" for place in m.lines]
            lines.append(f"    row: {m.row}")
    lines += [f"error: {p}" for p in found.problems]
    lines += [f"warning: {p}" for p in found.warnings]
    if not found.complete:
        lines += [
            "",
            "Nothing was written. Decide each supplier in a mapping file (--mapping), change "
            "what is listed, and run again.",
        ]
    elif report["check"]:
        lines += ["", "--check: nothing was written."]
    else:
        lines += ["", f"Written: {', '.join(report['written']) or 'nothing'}"]
    return lines


def _decided(m: Match) -> str:
    assert m.new is not None and m.decision is not None
    text = (
        f"{m.old.label} → {m.new.label}, by {m.decision.who} on "
        f"{m.decision.when.date().isoformat()} ({m.decision.source})"
    )
    if m.automatic is not None:
        text += f"; the rules give {m.automatic.label}"
    return text


def _flag(f: Flag) -> str:
    where = f". At {', '.join(f.lines)}" if f.lines else ""
    label = "RoW" if f.kind == "row" else f.kind
    return f"[{label}] {f.old} ({f.old.id} → {f.new.id}): {f.message}{where}"


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}{'' if n == 1 else 's'}"


def _relative(path: Path, found: Plan) -> str:
    """A written file from the folder that holds the databases and studies:
    `tshirt/exchanges.csv`."""
    folders = [d.path for d in found.databases] + [s.path for s in found.studies if s.path]
    parent = os.path.commonpath([f.resolve().parent for f in folders])
    return Path(os.path.relpath(path.resolve(), parent)).as_posix()


# --- JSON --------------------------------------------------------------------------------------


def _report(found: Plan, *, check: bool) -> dict[str, Any]:
    return {
        "format": FORMAT,
        "databases": [str(s.path) for s in found.databases],
        "studies": [str(s.path) for s in found.studies],
        "from": found.old.pin,
        "to": found.new.pin,
        "check": check,
        "complete": found.complete,
        "counts": {
            rule.value: {"suppliers": suppliers, "lines": places}
            for rule, (suppliers, places) in found.counts().items()
        },
        "matches": [_match(m) for m in found.matches],
        "flags": [asdict(f) for f in found.flags],
        "problems": [_problem(p) for p in found.problems],
        "warnings": [_problem(p) for p in found.warnings],
        "written": [],
        "validation": [],
        "diffs": [],
    }


def _dataset(d: Dataset | None) -> dict[str, str] | None:
    return None if d is None else asdict(d)


def _match(m: Match) -> dict[str, Any]:
    decision = m.decision
    return {
        "old": _dataset(m.old),
        "rule": m.rule.value,
        "new": _dataset(m.new),
        "code": m.code,
        "reason": m.reason,
        "decision": (
            None
            if decision is None
            else {
                "who": decision.who,
                "when": decision.when.isoformat(),
                "source": decision.source,
                "comment": decision.comment,
            }
        ),
        "automatic": _dataset(m.automatic),
        "suggestions": [_dataset(s) for s in m.suggestions],
        "lines": list(m.lines),
    }


def _problem(p: Problem) -> dict[str, Any]:
    return {"code": p.code, "item": p.item, "message": p.message, "text": str(p)}
