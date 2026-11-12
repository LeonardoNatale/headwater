"""Calculate two readings of a study: each product, its amount of it, in its database (D46).

Example: the study `tshirt-and-dye` scores 2 T-shirts and 0.5 kg of dye. Lowering the dye's CO2
from 4 to 3.5 kg per kg moves one T-shirt by -0.005 and one kg of dye by -0.5, so the study's
rows move by 2 × -0.005 = -0.01 and 0.5 × -0.5 = -0.25.

Each product is diffed per unit in its database, as `diff_databases` does, and its scores and
effects are then multiplied by the old amount. A changed amount is a difference of the study, whose
effect alone is the change of the amount times what one unit scored. What the effects do not add up
to is the interaction, as in any diff.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace

import headwater as hw
from headwater.diff._attribute import (
    Driver,
    Named,
    Row,
    StudyDiff,
    _indicators,  # pyright: ignore[reportPrivateUsage]
    _rank,  # pyright: ignore[reportPrivateUsage]
    diff_databases,
)
from headwater.diff._changes import Difference, named_in, study_differences
from headwater.diff._git import DiffError
from headwater.model import HeadwaterError
from headwater.query._internals import internals
from headwater.study import DatabaseFolder, StudyFolder


def diff_study(
    old: StudyFolder | None,
    new: StudyFolder,
    background: hw.Background,
    *,
    old_background: hw.Background | None = None,
    indicators: Sequence[str] | None = None,
    labels: tuple[str, str] = ("the old study", "the new study"),
) -> StudyDiff:
    """Score each product of `old` and `new`, its amount of it, and rank the differences by
    their effect on each score. Every product is listed, in the order of `products:`, then the
    products only `old` has. With no `old`, a new study, `new` is only calculated.

    The differences are those of the databases the products reach, each named with its
    database (`dyes:reactive-dye line e03`, `kwh_per_kg in dyes`), and those of the study:
    the amount of a product, its `no_factor.csv` rows, its scenarios and the fields of
    study.yaml. A diff scores no scenario. `old_background` is the release the old side pins,
    across a release change. Raises `DiffError` when a side cannot be calculated.
    """
    ids = _indicators(background, indicators)
    before = old_background or background
    own = study_differences(old, new) if old is not None else ()
    amounts_before = {} if old is None else {p.process: p.amount for p in old.products}
    amounts_after = {p.process: p.amount for p in new.products}
    order = [*amounts_after, *(p for p in amounts_before if p not in amounts_after)]
    found: dict[str, Difference] = {d.id: d for d in own if d.scored}
    per_product: dict[str, StudyDiff] = {}
    unit_ids: dict[str, dict[str, str]] = {}
    for process_id in order:
        a_old, a_new = amounts_before.get(process_id), amounts_after.get(process_id)
        db_old = None if old is None or a_old is None else old.database_of(process_id)
        db_new = None if a_new is None else new.database_of(process_id)
        if db_new is not None:
            per = diff_databases(
                db_old,
                db_new,
                background,
                old_background=old_background if db_old is not None else None,
                indicators=ids,
                products=[process_id],
                labels=labels,
            )
            home = db_new.database.name
        elif db_old is not None:
            per = _removed(db_old, before, ids, process_id, labels)
            home = db_old.database.name
        else:
            continue  # a product of the background
        per_product[process_id] = per
        unit_ids[process_id] = {}
        for d in per.differences:
            named = d if d.database is not None else named_in(home, d)
            found.setdefault(named.id, named)
            unit_ids[process_id][named.id] = d.id
    found.update({d.id: d for d in own if not d.scored})
    differences = tuple(found.values())
    scored = [d for d in differences if d.scored]
    rows: list[Row] = []
    labels_of: dict[str, tuple[str, str]] = {}
    processes: dict[str, Named] = {}
    flow_names: dict[str, str] = {}
    old_release = old_method_hash = None
    for per in per_product.values():
        labels_of.update(per.labels)
        processes.update(per.processes)
        flow_names.update(per.flow_names)
        old_release = old_release or per.old_release
        old_method_hash = old_method_hash or per.old_method_hash
    by_indicator: dict[str, list[Row]] = {i: [] for i in ids}
    for process_id in order:
        a_old, a_new = amounts_before.get(process_id), amounts_after.get(process_id)
        per = per_product.get(process_id)
        if per is None:
            labels_of.setdefault(process_id, _background_label(process_id, background, before))
            unit_rows = [
                replace(r, product=labels_of[process_id][0])
                for r in _background_rows(process_id, a_old, a_new, before, background, ids)
            ]
        else:
            unit_rows = list(per.rows)
        for row in unit_rows:
            by_indicator[row.indicator].append(
                _scaled(
                    row, a_old, a_new, scored, own_of(process_id, own), unit_ids.get(process_id, {})
                )
            )
    for indicator in ids:
        rows += by_indicator[indicator]
    old_results = _calc(old, before, ids[0], labels[0]) if old is not None else ()
    new_results = _calc(new, background, ids[0], labels[1])
    first = new_results[0]
    inner = internals(background)
    release = str(inner.releases[0]) if inner.releases else None
    return StudyDiff(
        rows=tuple(rows),
        differences=differences,
        method=background.method,
        method_hash=inner.method_hash,
        release=release,
        folder=None if release else str(inner.folder.path),
        old_input_hash=old_results[0].provenance.input_hash if old_results else None,
        new_input_hash=first.provenance.input_hash,
        old_residual=max(r.provenance.residual for r in old_results) if old_results else None,
        new_residual=max(r.provenance.residual for r in new_results),
        engine_version=first.provenance.engine_version,
        solver=_solver(first),
        flow_names=flow_names,
        created=old is None,
        old_release=old_release,
        old_method_hash=old_method_hash,
        processes=processes,
        kind="study",
        labels=labels_of,
        amounts={p: (amounts_before.get(p), amounts_after.get(p)) for p in order},
    )


def own_of(process_id: str, own: Sequence[Difference]) -> Difference | None:
    """The difference of the study that changes the amount of `process_id`, if any."""
    return next((d for d in own if d.kind == "product" and d.item == process_id), None)


def _scaled(
    row: Row,
    a_old: float | None,
    a_new: float | None,
    scored: Sequence[Difference],
    amount: Difference | None,
    unit_ids: Mapping[str, str],
) -> Row:
    """`row`, the scores of one unit, for the amounts of the study. Each effect is multiplied by
    the old amount; a changed amount adds its own effect, the change of the amount times what
    one unit scored before the study's changes (across a release change, on the new release).
    Every scored difference of the study is a driver of every row: 0 when it does not reach the
    product, since another database's lines do not move it. `unit_ids` gives the id each
    difference has in the diff of the product's database, by its id in the study."""
    old = None if row.old is None or a_old is None else a_old * row.old
    new = None if row.new is None or a_new is None else a_new * row.new
    if old is None or new is None or a_old is None or a_new is None or row.old is None:
        return replace(row, old=old, new=new, drivers=(), interaction=None, moved=())
    effects: dict[str, float | None] = {d.difference.id: d.contribution for d in row.drivers}
    base = row.old + (row.background or 0.0)
    if amount is not None:
        effects[amount.id] = (a_new - a_old) * base
    drivers: list[Driver] = []
    for d in scored:
        if d.id == (amount.id if amount is not None else None):
            drivers.append(Driver(d, effects[d.id]))
            continue
        unit_id = unit_ids.get(d.id)
        contribution = 0.0 if unit_id is None else effects.get(unit_id, 0.0)
        drivers.append(Driver(d, None if contribution is None else a_old * contribution))
    background = None if row.background is None else a_old * row.background
    known = sum(d.contribution for d in drivers if d.contribution is not None)
    return replace(
        row,
        old=old,
        new=new,
        drivers=_rank(tuple(drivers)),
        interaction=(new - old - (background or 0.0)) - known,
        background=background,
        moved=tuple(replace(m, effect=a_old * m.effect) for m in row.moved),
    )


def _removed(
    database: DatabaseFolder,
    background: hw.Background,
    ids: Sequence[str],
    process_id: str,
    labels: tuple[str, str],
) -> StudyDiff:
    """A product only the old study has: its scores on the old side, as a row with no new
    score."""
    alone = diff_databases(
        None,
        database,
        background,
        indicators=ids,
        products=[process_id],
        labels=(labels[1], labels[0]),  # the old side is scored here: errors name its ref
    )
    rows = tuple(replace(r, old=r.new, new=None) for r in alone.rows)
    return replace(alone, rows=rows)


def _background_rows(
    process_id: str,
    a_old: float | None,
    a_new: float | None,
    before: hw.Background,
    background: hw.Background,
    ids: Sequence[str],
) -> list[Row]:
    """The rows of one unit of a product of the background: its stored score on each side."""
    names = {i.id: i for i in background.indicators}
    rows: list[Row] = []
    for indicator in ids:
        try:
            old = None if a_old is None else before.score(process_id, indicator=indicator)
            new = None if a_new is None else background.score(process_id, indicator=indicator)
        except ValueError as error:
            raise DiffError(f"Product {process_id} cannot be scored: {error}") from error
        rows.append(
            Row(
                product_id=process_id,
                product=process_id,
                indicator=indicator,
                indicator_name=names[indicator].name,
                unit=names[indicator].unit,
                old=old,
                new=new,
                drivers=(),
                interaction=None,
            )
        )
    return rows


def _background_label(
    process_id: str, background: hw.Background, before: hw.Background
) -> tuple[str, str]:
    """What a process of the background makes, in words, and its name."""
    for side in (background, before):
        try:
            process = side.processes([process_id])[process_id]
        except ValueError:
            continue
        made = process.reference_exchange
        return (made.flow_name or process.name, process.name)
    return (process_id, process_id)


def _calc(
    folder: StudyFolder, background: hw.Background, indicator: str, label: str
) -> tuple[hw.Result, ...]:
    """Each product of the study calculated in full, for the provenance of its side."""
    from headwater.query.study import Study

    try:
        return Study._of(folder).calc(background, indicator=indicator)  # pyright: ignore[reportPrivateUsage]
    except HeadwaterError as error:
        raise DiffError(f"The study at {label} cannot be calculated:\n{error}") from error


def _solver(result: hw.Result) -> dict[str, object]:
    from dataclasses import asdict

    return asdict(result.provenance.solver)
