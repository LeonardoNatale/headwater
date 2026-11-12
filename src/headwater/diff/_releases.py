"""A study diffed across a release change: the bridge, and what the release did, line by line.

The bridge is the old study with only its suppliers moved to the new release. Each supplier moves
to its successor by the rules that need no person (the same ids, the same dataset); one with no
such successor takes the supplier the new study buys from at the same line, and a line the new
study no longer has is left out. The bridge is scored on the new release.

The study's own processes and amounts are the same in the old study and the bridge, so the score
changes by exactly one term per line: the amount the product needs, times the change of what one
unit of the supplier scores. Example: the toy T-shirt buys 0.25 kg of fabric, which scores 3.5
on toy-1 and 2.75 on toy-2, so the release moves it by 0.25 × (2.75 - 3.5) = -0.1875. An emission
of the study moves by its amount times the change of its factor. Nothing is left to interaction:
the terms add up to the bridge's score minus the old score.
"""

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import cast

import pyarrow as pa

import headwater as hw
from headwater.diff._git import DiffError
from headwater.diff._moved import COPY, FACTOR, NEW_STUDY, NO_SUCCESSOR, Moved
from headwater.model import HeadwaterError, Process
from headwater.query._internals import internals
from headwater.release import Release
from headwater.study import DatabaseFolder
from headwater.upgrade._datasets import Dataset, Datasets
from headwater.upgrade._moves import moved
from headwater.upgrade._plan import moves_for, named
from headwater.upgrade._rules import Successors


@dataclass(frozen=True)
class Bridge:
    folder: DatabaseFolder
    how: Mapping[str, str]
    """How each old supplier moved, by its id."""
    copies_of: Mapping[str, str]
    """The old process each new one stands for, so the copies an override makes pair up."""


def bridge(old: DatabaseFolder, new: DatabaseFolder, before: Release, after: Release) -> Bridge:
    """The old study with its suppliers moved to `after`, pinned as the new study pins it.
    Raises `DiffError` when a line an override or a scenario changes, or a flow, has nowhere to
    go, or when an override changes a process with no successor."""
    olds, news = Datasets(before), Datasets(after)
    rules = Successors(olds, news)
    chosen = _chosen(old, new, olds, news)
    found: dict[str, Dataset] = {}
    how: dict[str, str] = {}
    gone: set[str] = set()
    for old_id in named(old.chain, olds):
        automatic = rules.automatic(olds.by_id[old_id])  # no correspondence: same id or dataset
        if automatic.new is not None:
            found[old_id], how[old_id] = automatic.new, automatic.rule.value
        elif old_id in chosen:
            found[old_id], how[old_id] = chosen[old_id], NEW_STUDY
        else:
            how[old_id] = NO_SUCCESSOR
            gone.add(old_id)
    kept = _without(old, gone)
    moves, problems = moves_for(kept.chain, olds, news, found)
    if problems:
        raise DiffError(
            f"The study at the old ref cannot be moved to {news.pin} line for line:\n"
            + "\n".join(str(p) for p in problems)
        )
    moves = replace(moves, pin=new.database.background)
    copies_of = {new_id: old_id for old_id, new_id in moves.suppliers.items()}
    return Bridge(moved(kept, moves), how, copies_of)


def with_rows_of(folder: DatabaseFolder, new: DatabaseFolder) -> DatabaseFolder:
    """`folder` with the rows of `new`'s no_factor.csv files added, study by study. A row says
    why a flow counts 0 and moves no score, but the new release's method may need it before the
    bridge can be calculated."""
    rows = {f.database.name: f.no_factor for f in new.chain}

    def added(f: DatabaseFolder) -> DatabaseFolder:
        keys = {(r.flow_id, r.method) for r in f.no_factor}
        more = tuple(r for r in rows.get(f.database.name, ()) if (r.flow_id, r.method) not in keys)
        return replace(f, no_factor=(*f.no_factor, *more)) if more else f

    return replace(added(folder), used=tuple(added(u) for u in folder.used))


def _chosen(
    old: DatabaseFolder, new: DatabaseFolder, olds: Datasets, news: Datasets
) -> dict[str, Dataset]:
    """For each old supplier, the supplier of the new release that the new study buys from at
    the first line where the old study bought from it."""
    at = {
        (p.id, e.line_id): e.supplier_id
        for folder in new.chain
        for p in folder.processes
        for e in p.exchanges
        if e.supplier_id in news.by_id
    }
    chosen: dict[str, Dataset] = {}
    for folder in old.chain:
        for p in folder.processes:
            for e in p.exchanges:
                supplier = at.get((p.id, e.line_id))
                if e.supplier_id in olds.by_id and supplier is not None:
                    chosen.setdefault(e.supplier_id, news.by_id[supplier])
    return chosen


def _without(folder: DatabaseFolder, gone: set[str]) -> DatabaseFolder:
    """`folder` without the lines that buy from a supplier in `gone`. An override of such a
    process, or a line of one that buys from it, cannot be moved: `DiffError`."""
    if not gone:
        return folder

    def kept(p: Process) -> Process:
        lines = tuple(e for e in p.exchanges if e.supplier_id not in gone)
        return p if len(lines) == len(p.exchanges) else p.model_copy(update={"exchanges": lines})

    for o in folder.overrides:
        if o.process_id in gone:
            raise DiffError(
                f"an override changes {o.process_id}, which has no successor in the new release "
                "by the rules, and the new study does not say which process it became. Move the "
                "study with `headwater upgrade` and a mapping row, then diff."
            )
        bought = [e for e in o.lines if e.supplier_id in gone]
        if bought:
            raise DiffError(
                f"override of {o.process_id} line {bought[0].line_id} buys from "
                f"{bought[0].supplier_id}, which has no successor in the new release"
            )
    return replace(
        folder,
        processes=tuple(kept(p) for p in folder.processes),
        used=tuple(_without(u, gone) for u in folder.used),
    )


# --- line by line ------------------------------------------------------------------------------

type _Key = tuple[str, str | None]
"""A line by process and line id, or a copy by `(copy of <old id>, None)`."""


@dataclass(frozen=True)
class _Term:
    """What one line of the study puts in the score: `quantity` of each thing it is valued by,
    a background process (`x`) or an elementary flow (`c`), per unit of the product scored."""

    process_id: str
    line_id: str | None
    flow_id: str
    unit: str
    supplier: str | None
    parts: tuple[tuple[str, str, float], ...]
    """`("x", process id, quantity)` or `("c", flow id, quantity)`."""
    per: float
    """What one unit of the line's supplier is, for showing its value per unit: the quantity of
    a line, the supply of a copy."""


@dataclass(frozen=True)
class Terms:
    """The terms of every line of a study that buys from the background or emits, for one
    product, on one background. They add up to the product's score on each indicator."""

    background: hw.Background
    terms: Mapping[_Key, _Term]

    def value(self, term: _Term, indicator: str) -> float:
        inner = internals(self.background)
        x = inner.solved.x[indicator].values
        index = inner.index
        factors = self.background.method.factors_by_indicator.get(indicator, {})
        return sum(
            q * (float(x[index[ref]]) if kind == "x" else factors.get(ref, 0.0))
            for kind, ref, q in term.parts
        )


def terms(
    folder: DatabaseFolder,
    process_id: str,
    background: hw.Background,
    copies_of: Mapping[str, str] | None = None,
    result: hw.Result | None = None,
) -> Terms:
    """The terms of what `folder`'s process `process_id` makes, on `background`, from `result`,
    a calc of it, or from a new calc. `copies_of` names a copy by the old process it copies, so
    the copies of the old database and of the bridge pair up."""
    if result is None:
        try:
            # `_calc`: a diff names no commit of the folders it reads at its refs
            result = hw.Database(folder)._calc(  # pyright: ignore[reportPrivateUsage]
                background, process_id, background.method.indicators[0].id
            )
        except HeadwaterError as error:
            raise DiffError(f"{folder.database.name} cannot be calculated:\n{error}") from error
    system = internals(result)
    n = system.A_ss.shape[0]
    block = system.parts[0].block
    found: dict[_Key, _Term] = {}
    copies: dict[int, list[tuple[str, str, float]]] = defaultdict(list)

    def add(col: int, line_id: str, part: tuple[str, str, float], flow: str, unit: str) -> None:
        if part[2] == 0.0:
            return  # the product does not need this process, or the line holds nothing
        if col in system.copies:
            copies[col].append(part)
            return
        supplier = part[1] if part[0] == "x" else None
        found[(system.ids[col], line_id)] = _Term(
            system.ids[col], line_id, flow, unit, supplier, (part,), part[2]
        )

    lines = block.lines
    for k, line_id in enumerate(_ids(lines.line_id)):
        row, col = int(lines.row[k]), int(lines.col[k])
        if row >= n:  # a line bought from the study itself counts through that process's lines
            quantity = -float(lines.sign[k]) * float(lines.amount[k]) * float(system.s[col])
            part = ("x", system.ids[row], quantity)
            add(col, line_id, part, system.flow_ids[row], system.units[row])
    emissions = block.emissions
    for k, line_id in enumerate(_ids(emissions.line_id)):
        row, col = int(emissions.row[k]), int(emissions.col[k])
        quantity = float(emissions.amount[k]) * float(system.s[col])
        add(
            col, line_id, ("c", block.flows[row], quantity), block.flows[row], block.flow_units[row]
        )
    for col, parts in copies.items():
        copied = system.copies[col]
        name = (copies_of or {}).get(copied, copied)
        found[(f"copy of {name}", None)] = _Term(
            system.ids[col],
            None,
            system.flow_ids[col],
            system.units[col],
            copied,
            tuple(parts),
            float(system.s[col]),
        )
    return Terms(background, found)


def _ids(line_ids: pa.StringArray) -> list[str]:
    """The line ids of a block. None is empty: loading refused a line with no id."""
    return cast(list[str], line_ids.to_pylist())


def moved_lines(
    old: Terms, bridged: Terms, how: Mapping[str, str], indicator: str
) -> tuple[Moved, ...]:
    """What the release change did to each line, for one indicator, the largest effect first."""
    found: list[Moved] = []
    for key in [*old.terms, *(k for k in bridged.terms if k not in old.terms)]:
        before, after = old.terms.get(key), bridged.terms.get(key)
        term = before or after
        assert term is not None
        was = old.value(before, indicator) if before else 0.0
        now = bridged.value(after, indicator) if after else 0.0
        if term.supplier is None:
            kind = FACTOR
        elif term.line_id is None:
            kind = COPY
        else:
            kind = how.get(term.supplier, NO_SUCCESSOR) if before else NEW_STUDY
        found.append(
            Moved(
                process_id=term.process_id,
                line_id=term.line_id,
                flow_id=term.flow_id,
                unit=term.unit,
                old_supplier=before.supplier if before else None,
                new_supplier=after.supplier if after else None,
                old_value=was / before.per if before and before.per else 0.0,
                new_value=now / after.per if after and after.per else 0.0,
                effect=now - was,
                how=kind,
            )
        )
    return tuple(sorted(found, key=lambda m: -abs(m.effect)))
