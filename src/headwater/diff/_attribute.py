"""Calculate two readings of a database, and find what each difference did.

The effect of a difference is what the score would be if only that difference were made to the old
database, minus the old score. The differences are not independent: lines in series multiply. What
the single effects do not add up to is reported as the interaction, so every row adds up. A
database with no old reading, one that is new, has its products counted and not scored.

Across a release change, the old database is first moved to the new release line for line, the
bridge (`_releases.py`). What the release did is the bridge's score minus the old score, one term
per line. The differences are then found and ranked from the bridge to the new database, on the
new release, as above.

A study is diffed product by product, each in its database (`diff_study`).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any, Literal

import headwater as hw
from headwater.diff._changes import Difference, Names, no_effect
from headwater.diff._changes import differences as find_differences
from headwater.diff._git import DiffError
from headwater.diff._moved import Moved
from headwater.model import HeadwaterError, InvalidDataError, Method, Process, did_you_mean
from headwater.query._internals import internals
from headwater.study import DatabaseFolder

if TYPE_CHECKING:
    from headwater.diff._releases import Bridge, Terms

UNCHANGED_RTOL = 1e-9
"""Two scores are the same when they differ by less than this share of the larger one."""


@dataclass(frozen=True)
class Driver:
    """A difference and the change of the score it causes alone, or None when it cannot be
    applied to the old study on its own."""

    difference: Difference
    contribution: float | None


@dataclass(frozen=True)
class Row:
    """The score of one product on one indicator, in the old and the new folder."""

    product_id: str
    """The id of the process that makes the product."""
    product: str
    """What the process makes, in words: `T-shirt`."""
    indicator: str
    indicator_name: str
    unit: str
    old: float | None
    new: float | None
    drivers: tuple[Driver, ...]
    """The scored differences, the largest effect first. Empty unless both scores exist."""
    interaction: float | None
    """The change that the effects of the differences alone do not add up to."""
    background: float | None = None
    """Across a release change, what the release changed: the score of the old study with only
    its suppliers moved, minus the old score. None on one release."""
    moved: tuple[Moved, ...] = ()
    """The lines the release change moved, the largest effect first. They add up to
    `background`. The differences and the interaction then explain the rest of the change."""

    @property
    def change(self) -> float | None:
        if self.old is None or self.new is None:
            return None
        return self.new - self.old

    @property
    def percent(self) -> float | None:
        if self.old is None or self.new is None or self.old == 0:
            return None
        return 100 * (self.new - self.old) / abs(self.old)

    @property
    def changed(self) -> bool:
        """False when a score is missing on one side, or the two are the same within
        `UNCHANGED_RTOL`."""
        if self.old is None or self.new is None:
            return False
        return abs(self.new - self.old) > UNCHANGED_RTOL * max(abs(self.old), abs(self.new))


@dataclass(frozen=True)
class Product:
    """One product of a database or a study and its scores, one row per indicator in the
    method's order. Example: the process `tshirt:bag-sewing`, named `bag sewing`, which makes
    `bag`."""

    id: str
    """The id of the process that makes it."""
    name: str
    """What it makes, in words."""
    process_name: str
    status: Literal["both", "new", "removed"]
    """`new` when only the new side has it, `removed` when only the old one did."""
    rows: tuple[Row, ...]
    """Empty for a product of a new database, which is counted and not scored."""
    amount: tuple[float | None, float | None] | None = None
    """For a product of a study, its amount on each side, None on a side that does not have it.
    None for a database, which scores one unit of each product."""

    @property
    def label(self) -> str:
        """`bag sewing | bag (tshirt:bag-sewing)`, and for a study the amount first:
        `2 × T-shirt (tshirt:tshirt)`. A process with the name of its product is not named
        twice."""
        from headwater.diff._render import product_label

        text = product_label(self.process_name, self.name, self.id)
        if self.amount is None:
            return text
        old, new = self.amount
        shown = new if new is not None else old
        return f"{shown:g} × {text}"

    @property
    def differs(self) -> bool:
        """True when a score changed, or the product is on one side only."""
        return any(r.changed or r.old is None or r.new is None for r in self.rows)


@dataclass(frozen=True)
class Named:
    """A process in words: `electricity production`, in `GLO`."""

    name: str
    location: str

    def __str__(self) -> str:
        return f"{self.name} ({self.location})"


@dataclass(frozen=True)
class StudyDiff:
    """Both readings of a database or a study calculated, with what differs."""

    rows: tuple[Row, ...]
    differences: tuple[Difference, ...]
    method: Method
    method_hash: str
    release: str | None
    """`name@hash` of the release the background was read from."""
    folder: str | None
    """The folder the background was read from, when it was not a release."""
    old_input_hash: str | None
    """None for a new folder, and for an old side that makes none of the products scored."""
    new_input_hash: str | None
    """None for a new database, which is not scored."""
    old_residual: float | None
    new_residual: float | None
    engine_version: str
    solver: dict[str, Any]
    """How the background and the study blocks were factorized: `SolverSettings` as a dict."""
    flow_names: dict[str, str]
    """The names of the flows that the products and differences refer to, by id."""
    created: bool = False
    """True when there was no old folder: every row has only a new score, and a new database
    has no rows."""
    old_release: str | None = None
    """Across a release change, `name@hash` of the release the old study was scored on."""
    old_method_hash: str | None = None
    processes: dict[str, Named] = field(default_factory=dict[str, Named])
    """The name and location of each process that the lines of either side buy from, by id:
    from its release, or from its study for a study's own process."""
    kind: Literal["database", "study"] = "database"
    labels: dict[str, tuple[str, str]] = field(default_factory=dict[str, tuple[str, str]])
    """What each product makes and the name of its process, by the process id: of the new side,
    or of the old one for a product only it has."""
    unscored: tuple[str, ...] = ()
    """The products of a new database, counted and not scored."""
    amounts: dict[str, tuple[float | None, float | None]] = field(
        default_factory=dict[str, tuple[float | None, float | None]]
    )
    """For a study, the amount of each product on each side, by the process id."""

    @property
    def names(self) -> Names:
        """Words for the ids that the differences name."""
        return Names(self.flow_names, {i: str(p) for i, p in self.processes.items()})

    @property
    def products(self) -> tuple[Product, ...]:
        """The products, in the order of `rows`: a database's processes, the old side's before
        the new side's, or a study's products in the order of `products:`. Then those of a new
        database, which have no rows."""
        grouped: dict[str, list[Row]] = {}
        for row in self.rows:
            grouped.setdefault(row.product_id, []).append(row)
        for product in self.unscored:
            grouped.setdefault(product, [])
        return tuple(
            Product(
                id=product,
                name=self.labels.get(product, (product, product))[0],
                process_name=self.labels.get(product, (product, product))[1],
                status=(
                    "new"
                    if self.created or (rows and all(r.old is None for r in rows))
                    else "removed"
                    if rows and all(r.new is None for r in rows)
                    else "both"
                ),
                rows=tuple(rows),
                amount=self.amounts.get(product) if self.kind == "study" else None,
            )
            for product, rows in grouped.items()
        )


def diff_databases(
    old: DatabaseFolder | None,
    new: DatabaseFolder,
    background: hw.Background,
    *,
    old_background: hw.Background | None = None,
    indicators: Sequence[str] | None = None,
    products: Sequence[str] | Literal["all"] | None = None,
    differences: Sequence[Difference] | None = None,
    labels: tuple[str, str] = ("the old database", "the new database"),
) -> StudyDiff:
    """Calculate `old` and `new` on `background`, for `indicators` (all by default), and rank the
    differences by their effect on each score. With no `old`, a new database, its products are
    counted and not scored, unless `products` names some: those are only calculated.

    `old_background`, a release other than `background`'s, scores the old database across a
    release change: each row then has what the release changed (`Row.background`), line by line
    (`Row.moved`), and the differences are found from the old database moved to the new release.

    `products` are the ids of the processes to score: by default, or with `"all"`, every process
    of either side, each a product, scored per unit of what it makes. `labels` name the two
    sides in errors. Raises `DiffError` when a side pins another release than the one it is
    scored on, cannot be calculated, or when a product asked for is a process of neither side:
    the message names the side and what is wrong, or lists the products. A difference that
    cannot be applied to the old database on its own gets a contribution of None.
    """
    ids = _indicators(background, indicators)
    _require_products(old, new, products)
    if old is None and products is None:
        return _counted(new, background)
    bridged: Bridge | None = None
    compared = base = old
    if old is not None and old_background is not None and _apart(old_background, background):
        # the bridge reads both releases: loaded only for a diff across them
        from headwater.diff._releases import bridge, with_rows_of

        before = old_background
        _indicators(before, ids)
        _require_pins(old, new, before, background, labels)
        assert before.release is not None and background.release is not None
        bridged = bridge(old, new, before.release, background.release)
        compared, base = bridged.folder, with_rows_of(bridged.folder, new)
    else:
        before = background
        if old is not None:
            require_same_pin(old, new, background, labels)
    across = bridged is not None
    found: tuple[Difference, ...] = ()
    if compared is not None:
        found = tuple(differences) if differences is not None else find_differences(compared, new)
    wanted = _products(old, new, products)
    old_first = None if old is None else _first(old, wanted)
    new_first = _first(new, wanted)
    if new_first is None and old_first is None:
        raise DiffError(f"The database at {labels[1]} has no process to score")
    old_results = (
        {}
        if old is None or old_first is None
        else {i: _calc(old, old_first, before, i, labels[0]) for i in ids}
    )
    new_results = (
        {}
        if new_first is None
        else {i: _calc(new, new_first, background, i, labels[1]) for i in ids}
    )
    old_scores = _scores(old, before, ids, wanted, labels[0], old_first, old_results)
    new_scores = _scores(new, background, ids, wanted, labels[1], new_first, new_results)
    base_scores = (
        _scores(base, background, ids, wanted, labels[0], None, {}) if across else old_scores
    )
    variants = (
        {}
        if base is None
        else {
            product: _score_variants(base, product, found, background, ids)
            for product in base_scores
        }
    )
    release_terms: dict[str, tuple[Terms, Terms]] = {}
    how: Mapping[str, str] = {}
    if bridged is not None and old is not None and base is not None:
        from headwater.diff._releases import terms

        how = bridged.how
        for product in old_scores.keys() & base_scores.keys():
            calculated = old_results[ids[0]] if product == old_first else None
            release_terms[product] = (
                terms(old, product, before, result=calculated),
                terms(base, product, background, bridged.copies_of),
            )
    sides = [(new, background), *([] if old is None else [(old, before)])]
    if across and base is not None:
        sides.append((base, background))  # the old database on the new release
    names, processes = _names(sides, found)
    made = _labels(old, new, names)
    rows: list[Row] = []
    method_ids = {i.id: i for i in background.indicators}
    for indicator in ids:
        info = method_ids[indicator]
        for product in wanted:
            o = old_scores.get(product, {}).get(indicator)
            b = base_scores.get(product, {}).get(indicator)
            n = new_scores.get(product, {}).get(indicator)
            drivers: tuple[Driver, ...] = ()
            interaction: float | None = None
            if b is not None and n is not None:
                drivers = _rank(
                    tuple(
                        Driver(d, _effect(d, variants[product], indicator, b))
                        for d in found
                        if d.scored
                    )
                )
                known = sum(d.contribution for d in drivers if d.contribution is not None)
                interaction = (n - b) - known
            release_part: float | None = None
            moved: tuple[Moved, ...] = ()
            if product in release_terms and o is not None and b is not None:
                release_part = b - o
                from headwater.diff._releases import moved_lines

                moved = moved_lines(*release_terms[product], how, indicator)
            rows.append(
                Row(
                    product_id=product,
                    product=made[product][0],
                    indicator=indicator,
                    indicator_name=info.name,
                    unit=info.unit,
                    old=o,
                    new=n,
                    drivers=drivers,
                    interaction=interaction,
                    background=release_part,
                    moved=moved,
                )
            )
    first = (new_results or old_results)[ids[0]]
    inner = internals(background)
    release = str(inner.releases[0]) if inner.releases else None
    return StudyDiff(
        rows=tuple(rows),
        differences=found,
        method=background.method,
        method_hash=inner.method_hash,
        release=release,
        folder=None if release else str(inner.folder.path),
        old_input_hash=old_results[ids[0]].provenance.input_hash if old_results else None,
        new_input_hash=new_results[ids[0]].provenance.input_hash if new_results else None,
        old_residual=_largest(old_results),
        new_residual=_largest(new_results),
        engine_version=first.provenance.engine_version,
        solver=asdict(first.provenance.solver),
        flow_names=names,
        created=old is None,
        old_release=str(internals(before).releases[0])
        if across and internals(before).releases
        else None,
        old_method_hash=internals(before).method_hash if across else None,
        processes=processes,
        labels=made,
    )


def _largest(results: Mapping[str, hw.Result]) -> float | None:
    return float(max(r.provenance.residual for r in results.values())) if results else None


def _counted(new: DatabaseFolder, background: hw.Background) -> StudyDiff:
    """A new database: its products counted, in the order of its processes, and not scored. A
    pull request that adds a database of 40 products would otherwise show 1,000 scores with
    nothing to compare them with (D43)."""
    import headwater

    inner = internals(background)
    release = str(inner.releases[0]) if inner.releases else None
    names, processes = _names([(new, background)], ())
    return StudyDiff(
        rows=(),
        differences=(),
        method=background.method,
        method_hash=inner.method_hash,
        release=release,
        folder=None if release else str(inner.folder.path),
        old_input_hash=None,
        new_input_hash=None,
        old_residual=None,
        new_residual=None,
        engine_version=headwater.__version__,
        solver=asdict(inner.solved.factorization.settings),
        flow_names=names,
        created=True,
        processes=processes,
        labels=_labels(None, new, names),
        unscored=tuple(p.id for p in new.processes),
    )


def _labels(
    old: DatabaseFolder | None, new: DatabaseFolder, flow_names: Mapping[str, str]
) -> dict[str, tuple[str, str]]:
    """What each process makes, in words, and its own name, by its id: of the new side first,
    then of the old one for a process only it has."""
    found: dict[str, tuple[str, str]] = {}
    for side in (new,) if old is None else (new, old):
        for process in side.processes:
            flow = process.reference_exchange.flow_id
            found.setdefault(process.id, (flow_names.get(flow, flow), process.name))
    return found


def _indicators(background: hw.Background, indicators: Sequence[str] | None) -> list[str]:
    """The indicator ids asked for, all of the method's and its sets' by default. Raises
    `DiffError` for one the method does not have."""
    known = [i.id for i in background.indicators]
    ids = list(indicators) if indicators is not None else known
    if not ids:
        raise DiffError(f"method {background.method.id} has no indicators to compare")
    unknown = [i for i in ids if i not in known]
    if unknown:
        raise DiffError(
            f"method {background.method.id} has no indicator {unknown[0]!r}. "
            f"Its indicators: {', '.join(known)}"
        )
    return ids


def _apart(old: hw.Background, new: hw.Background) -> bool:
    """True when the two backgrounds are two releases. Raises `DiffError` when one of two
    different backgrounds is a folder: only releases can be compared line for line."""
    if old is new:
        return False
    if old.release is None or new.release is None:
        raise DiffError(
            "a diff across a release change needs two releases: a folder of process files has "
            "no version to move from"
        )
    return old.release.hash != new.release.hash


def _require_pins(
    old: DatabaseFolder,
    new: DatabaseFolder,
    before: hw.Background,
    after: hw.Background,
    labels: tuple[str, str],
) -> None:
    for side, background, label in ((old, before, labels[0]), (new, after, labels[1])):
        try:
            internals(background).require_pin(side.database.background, side.database.name)
        except InvalidDataError as error:
            raise DiffError(f"The study at {label} pins another release:\n{error}") from None


def _products(
    old: DatabaseFolder | None, new: DatabaseFolder, products: Sequence[str] | Literal["all"] | None
) -> list[str]:
    """The processes to score: those `products` names, else every process of either side, the
    old side's first, each in the order of its processes."""
    sides = (new,) if old is None else (old, new)
    if products is not None and products != "all":
        return list(dict.fromkeys(products))
    return list(dict.fromkeys(p.id for s in sides for p in s.processes))


def _has(folder: DatabaseFolder, process_id: str) -> bool:
    return any(p.id == process_id for p in folder.processes)


def _first(folder: DatabaseFolder, wanted: Sequence[str]) -> str | None:
    """The first of `wanted` that is a process of `folder`: it is calculated in full, for its
    provenance. None when `folder` has none of them."""
    return next((p for p in wanted if _has(folder, p)), None)


def _require_products(
    old: DatabaseFolder | None, new: DatabaseFolder, products: Sequence[str] | Literal["all"] | None
) -> None:
    """Raises `DiffError` when one of `products` is a process of neither side. When it is the
    flow a process makes, the message names the process; else it lists the processes."""
    if products is None or products == "all":
        return
    sides = (new,) if old is None else (old, new)
    for product in products:
        message = unknown_product(product, [p for s in sides for p in s.processes])
        if message is not None:
            raise DiffError(message)


def unknown_product(product: str, processes: Sequence[Process]) -> str | None:
    """None when `product` is the id of one of `processes`, else what to say: the processes that
    make it, when it is the id of a flow they make, or the ids to choose from. Example:
    `tshirt:bag is the flow of the process tshirt:bag-sewing: give its id`."""
    ids = list(dict.fromkeys(p.id for p in processes))
    if product in ids:
        return None
    makers = list(dict.fromkeys(p.id for p in processes if p.reference_exchange.flow_id == product))
    if len(makers) == 1:
        return f"{product} is the flow of the process {makers[0]}: give its id, {makers[0]}."
    if makers:
        return (
            f"{product} is the flow of the processes {', '.join(makers)}: give the id of one "
            "of them."
        )
    return (
        f"There is no process {product!r}.{did_you_mean(product, ids)} A product is named by "
        f"the id of its process: {', '.join(ids)}"
    )


def _scores(
    folder: DatabaseFolder | None,
    background: hw.Background,
    ids: Sequence[str],
    products: Sequence[str],
    label: str,
    first: str | None,
    results: Mapping[str, hw.Result],
) -> dict[str, dict[str, float]]:
    """The scores of each of `products` that is a process of `folder`, by product and
    indicator. Those of `first` are taken from `results`, its calc for each indicator, when
    given."""
    if folder is None:
        return {}
    found: dict[str, dict[str, float]] = {}
    if results and first is not None:
        found[first] = {i: r.score for i, r in results.items()}
    for product in products:
        if not _has(folder, product) or product in found:
            continue
        try:
            found[product] = hw.Database(folder).scores(background, product, indicators=ids)
        except HeadwaterError as error:
            raise DiffError(f"The database at {label} cannot be calculated:\n{error}") from error
    return found


def _calc(
    folder: DatabaseFolder, product: str, background: hw.Background, indicator: str, label: str
) -> hw.Result:
    try:
        # `_calc`: a diff names no commit of the folders it reads at its refs
        return hw.Database(folder)._calc(background, product, indicator)  # pyright: ignore[reportPrivateUsage]
    except HeadwaterError as error:
        raise DiffError(f"The database at {label} cannot be calculated:\n{error}") from error


def require_same_pin(
    old: DatabaseFolder, new: DatabaseFolder, background: hw.Background, labels: tuple[str, str]
) -> None:
    """Both sides are calculated on one background, so they must pin it, however they write it.
    Raises `DiffError` naming both pins when they do not."""
    pins = (old.database.background, new.database.background)
    if pins[0] == pins[1] or background.release is None:
        return
    try:
        for pin, side in zip(pins, (old, new), strict=True):
            internals(background).require_pin(pin, side.database.name)
    except InvalidDataError:
        raise DiffError(
            f"The study pins {pins[0]} at {labels[0]} and {pins[1]} at {labels[1]}, and both "
            "sides were given one background. Give each side the release it pins: "
            "`old_background` in Python, or no --release on the command line."
        ) from None


def _score_variants(
    old: DatabaseFolder,
    process_id: str,
    found: Sequence[Difference],
    background: hw.Background,
    indicators: Sequence[str],
) -> dict[str, dict[str, float]]:
    """The score for every one of `indicators` of the old database with only one difference made,
    for each difference that has an effect to calculate. Each variant is assembled and factorized
    once and scored for all indicators together (`Study.scores`).

    A difference is left out when it cannot be made, such as a line that buys from a process only
    the new study has and that nothing else brings along, or when the variant cannot be scored."""
    variants: dict[str, dict[str, float]] = {}
    for d in found:
        if d.apply is None or d.apply is no_effect:
            continue
        try:
            variants[d.id] = hw.Database(d.apply(old)).scores(
                background, process_id, indicators=indicators
            )
        except (ValueError, HeadwaterError):
            pass
    return variants


def _effect(
    difference: Difference,
    variants: dict[str, dict[str, float]],
    indicator: str,
    old: float,
) -> float | None:
    if difference.apply is no_effect:
        return 0.0
    scores = variants.get(difference.id)
    if scores is None:
        return None
    return scores[indicator] - old


def _rank(drivers: tuple[Driver, ...]) -> tuple[Driver, ...]:
    """Largest effect first. Ties keep the order the differences were found in. A difference with
    no effect of its own that could be worked out comes last."""
    return tuple(
        sorted(
            drivers,
            key=lambda d: (d.contribution is None, -abs(d.contribution or 0.0)),
        )
    )


def _names(
    sides: Sequence[tuple[DatabaseFolder, hw.Background]], found: Sequence[Difference]
) -> tuple[dict[str, str], dict[str, Named]]:
    """The names of the flows that the products and differences refer to, and the name and
    location of every supplier of a line, each looked up on the background of its side."""
    wanted = {d.flow_id for d in found if d.flow_id}
    wanted |= {e.flow_id for side, _ in sides for p in side.processes for e in p.exchanges}
    flows: dict[str, str] = {}
    processes: dict[str, Named] = {}
    for side, background in sides:
        catalog = internals(background).study_catalog(side)
        for flow_id in wanted:
            flow = catalog.flow(flow_id)
            if flow is not None:
                flows.setdefault(flow_id, flow.name)
        for supplier in sorted(_suppliers(side)):
            process = catalog.process(supplier)
            if process is not None and supplier not in processes:
                processes[supplier] = Named(process.name, process.location)
    return flows, processes


def _suppliers(folder: DatabaseFolder) -> set[str]:
    """The processes that the lines of `folder`, of the studies it uses and of its overrides buy
    from."""
    lines = [e for f in folder.chain for p in f.processes for e in p.exchanges]
    lines += [e for f in folder.chain for o in f.overrides for e in o.lines]
    return {e.supplier_id for e in lines if e.supplier_id is not None}
