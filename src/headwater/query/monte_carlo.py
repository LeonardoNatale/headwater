"""`hw.monte_carlo`: a range instead of one number, for products of the background, of databases
and of studies.

Example, the toy with fabric production buying 3 kWh of electricity per kg, lognormal with a
geometric standard deviation of 1.1:

    mc = hw.monte_carlo(bg, [(hw.open("tshirt/tshirt"), "tshirt:tshirt"), "toy:fabric-production"],
                        iterations=1000, seed=0)
    print(mc.range("tshirt:tshirt"))
    # tshirt:tshirt climate-change: score 1.19205 kg CO2-eq, mean 1.1938, median 1.1918, ...

Every item of one call is scored from the same iterations, and iteration k draws the same amounts
in any call with the same seed. So two products keep their correlation: a difference between
them is as uncertain as it really is.
"""

import multiprocessing
from collections import Counter
from collections.abc import Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from typing import cast

import numpy as np
import pyarrow as pa
import scipy
from numpy.typing import NDArray
from scipy import sparse

import headwater
from headwater.calc import SolverSettings
from headwater.calc.arrays import Vector
from headwater.calc.monte_carlo import (
    GENERATOR,
    MonteCarloSystem,
    Patch,
    Samples,
    StudyPart,
    sample,
)
from headwater.model import InvalidDataError, Problem, did_you_mean
from headwater.query._hash import sha256_of
from headwater.query._internals import internals
from headwater.query._study_draws import StudyDraws, study_draws
from headwater.query._system import ids_in_errors
from headwater.query._uncertainty import UncertainLines, uncertain_lines
from headwater.query.background import Background
from headwater.query.database import Database
from headwater.query.result import MethodRef, counted
from headwater.query.study import Study
from headwater.release import ReleaseRef

type Item = str | tuple[Database | Study, str] | tuple[Study, str, str]
"""A background product, by the id of the process that makes it, a product of a database,
`(database, "tshirt:tshirt")`, a product of a study, its amount of it, `(study,
"tshirt:tshirt")`, or one in a scenario of the study, `(study, "tshirt:tshirt", "clean-grid")`."""

MARKETS = "drawn line by line, as in Brightway: a market's shares do not sum to 1 in a draw"


@dataclass(frozen=True)
class MonteCarloProvenance:
    """What a Monte Carlo run was computed from, and how, so it can be reproduced."""

    seed: int
    iterations: int
    """Iterations 0 to `iterations` - 1 of the seed."""
    generator: str
    """How iteration k draws. The same seed and k draw the same amounts on any run with the same
    numpy and scipy (`versions`)."""
    versions: Mapping[str, str]
    """numpy and scipy, which draw the uniforms and the normal quantiles."""
    engine_version: str
    input_hash: str
    """sha256 of the items (product ids and the records of their databases and studies), the
    background's fingerprint, the indicators, the seed and the iterations."""
    releases: tuple[ReleaseRef, ...]
    method: str
    method_version: str
    method_hash: str
    methods: tuple[MethodRef, ...]
    solver: SolverSettings
    """The settings of every iteration's factorization."""
    uncertain_lines: Mapping[str, int]
    """How many lines of the background were drawn, by distribution: `{"lognormal": 1}`. Every
    other line and every characterisation factor stay fixed."""
    database_lines: Mapping[str, int]
    """How many lines of the databases were drawn by their own key, by distribution, each key
    once. A line a database's copy keeps from the process it copies is drawn as the
    background's."""
    parameters: int
    """How many parameters of the databases were drawn, each key once."""
    opposite_signs: int
    """How many draws, over every line and iteration, have the opposite sign of their line's
    amount: a normal input of 0.1 ± 0.1 drawn at -0.02. They are kept, as Brightway keeps them."""
    largest_condition: float
    """The largest condition estimate of any iteration's factors, those of a scenario's matrix
    and of a database's block too. The run stops above `solver.condition_limit`."""
    residual: float
    """The largest residual of any solve of any iteration."""
    pivoted: int
    """How many iterations needed factors with pivoting."""
    scenario_refactorized: int = 0
    """How many solves of a scenario's background, over every scenario and iteration, factorized
    its A again, above the crossover or when the update failed its check. The others updated the
    iteration's factors, as `calc` does, or used them as they are."""
    markets: str = MARKETS


@dataclass(frozen=True)
class Range:
    """The distribution of one item for one indicator, from the samples of a run."""

    item: str
    indicator: str
    unit: str
    score: float
    """The score with no draw, as `calc` gives it."""
    mean: float
    median: float
    sd: float
    """The sample standard deviation."""
    percentiles: Mapping[float, float]
    """By percent: `{2.5: 1.1244, 97.5: 1.2737}`."""
    iterations: int

    def __str__(self) -> str:
        parts = [
            f"mean {self.mean:.6g}",
            f"median {self.median:.6g}",
            f"sd {self.sd:.3g}",
            *(f"{p:g}% {v:.6g}" for p, v in self.percentiles.items()),
        ]
        return f"{self.item} {self.indicator}: score {self.score:.6g} {self.unit}, " + ", ".join(
            parts
        )


@dataclass(frozen=True, eq=False)
class MonteCarlo:
    """The samples of a Monte Carlo run: one value per item, indicator and iteration."""

    items: tuple[str, ...]
    """Each item by name: a product by the id of the process that makes it,
    `toy:fabric-production`, a study by the name of its folder, `tshirt-waste`."""
    indicators: tuple[str, ...]
    units: tuple[str, ...]
    """The unit of each indicator."""
    scores: NDArray[np.float64]
    """The score with no draw, one per item and indicator."""
    samples: NDArray[np.float64]
    """One per item, indicator and iteration: `samples[i, j, k]`."""
    conditions: Vector
    """The condition estimate of each iteration's factors."""
    percentiles: tuple[float, ...]
    provenance: MonteCarloProvenance

    def range(self, item: Item, indicator: str | None = None) -> Range:
        """The distribution of `item` for `indicator`, which may be left out when there is one.

        `item` is an item of the run, or its label: a process id, with the scenario in
        brackets, `tshirt:tshirt[clean-grid]`."""
        i, j = self._item(item), self._indicator(indicator)
        return self._range(self.items[i], j, float(self.scores[i, j]), self.samples[i, j])

    def difference(self, a: Item, b: Item, indicator: str | None = None) -> Range:
        """The distribution of `a` minus `b`, iteration by iteration. Both were drawn with the same
        iterations, so what they share cancels: the T-shirt minus the same T-shirt that also sends
        0.05 kg to incineration is -0.06 in every iteration, with a standard deviation of 0."""
        i, k, j = self._item(a), self._item(b), self._indicator(indicator)
        score = float(self.scores[i, j] - self.scores[k, j])
        name = f"{self.items[i]} - {self.items[k]}"
        return self._range(name, j, score, self.samples[i, j] - self.samples[k, j])

    def ranges(self) -> tuple[Range, ...]:
        """Every item for every indicator, item by item."""
        return tuple(
            self._range(name, j, float(self.scores[i, j]), self.samples[i, j])
            for i, name in enumerate(self.items)
            for j in range(len(self.indicators))
        )

    def to_arrow(self) -> pa.Table:
        """`ranges()` as a table: item, indicator, unit, score, mean, median, sd, then one column
        per percentile, `p2.5` and `p97.5`."""
        ranges = self.ranges()
        columns: dict[str, list[str] | list[float]] = {
            "item": [r.item for r in ranges],
            "indicator": [r.indicator for r in ranges],
            "unit": [r.unit for r in ranges],
            "score": [r.score for r in ranges],
            "mean": [r.mean for r in ranges],
            "median": [r.median for r in ranges],
            "sd": [r.sd for r in ranges],
        }
        for p in self.percentiles:
            columns[f"p{p:g}"] = [r.percentiles[p] for r in ranges]
        return pa.Table.from_pydict(columns)

    def __repr__(self) -> str:
        """Example: `MonteCarlo(2 items, 1 indicator, 2000 iterations, seed 0)`. `print(mc)`
        shows every range."""
        p = self.provenance
        return (
            f"MonteCarlo({counted(len(self.items), 'item', 'items')}, "
            f"{counted(len(self.indicators), 'indicator', 'indicators')}, "
            f"{p.iterations} iterations, seed {p.seed})"
        )

    def __str__(self) -> str:
        p = self.provenance
        header = f"Monte Carlo: {p.iterations} iterations, seed {p.seed}"
        return "\n".join([header, *(str(r) for r in self.ranges())])

    def _range(self, name: str, j: int, score: float, values: Vector) -> Range:
        found = np.percentile(values, self.percentiles)
        return Range(
            item=name,
            indicator=self.indicators[j],
            unit=self.units[j],
            score=score,
            mean=float(values.mean()),
            median=float(np.median(values)),
            sd=float(values.std(ddof=1)),
            percentiles={p: float(v) for p, v in zip(self.percentiles, found, strict=True)},
            iterations=int(values.size),
        )

    def _item(self, item: Item) -> int:
        key = _label(item)
        if key in self.items:
            return self.items.index(key)
        raise ValueError(f"{key!r} is not an item of this run: {', '.join(self.items)}")

    def _indicator(self, indicator: str | None) -> int:
        if indicator is None:
            if len(self.indicators) == 1:
                return 0
            raise ValueError(f"name one indicator of this run: {', '.join(self.indicators)}")
        if indicator not in self.indicators:
            raise ValueError(
                f"{indicator!r} is not an indicator of this run: {', '.join(self.indicators)}"
            )
        return self.indicators.index(indicator)


def monte_carlo(
    background: Background,
    items: Item | Sequence[Item],
    *,
    iterations: int,
    seed: int = 0,
    indicators: Sequence[str] | None = None,
    percentiles: Sequence[float] = (2.5, 97.5),
    workers: int = 1,
) -> MonteCarlo:
    """Draw every uncertain amount of `background` and of the databases asked `iterations` times,
    and score each item in each iteration, for every indicator of the method or for
    `indicators`.

    Iteration k draws every line of the background with a distribution from the uniforms of
    (`seed`, k), refactorizes A once, and solves the scores of every product for each indicator,
    so one iteration serves every item. A database's own lines and parameters with a
    distribution are drawn from uniforms keyed by their ids, so a database draws the same alone
    or beside others; its own processes are then solved again. A database's copy of a
    background process draws the lines it keeps as the background draws them. A product of a
    study in a scenario, `(study, "tshirt:tshirt", "clean-grid")`, uses the drawn background
    with the scenario's lines in place, so it stays paired with the study without it: a line
    the scenario sets is fixed. The
    indicators are every one the background scores, those of its normalisation and weighting
    sets too, by default. A line with no distribution stays fixed, and so do the
    characterisation factors. `workers` runs the iterations in that many processes: the
    samples are the same as with one.

    `background` must be read from a release: Monte Carlo draws the lines of a release. Raises
    InvalidDataError for a folder, when nothing is drawn (no line of the release, of the
    databases or of their parameters has a distribution), and for every database or study
    `calc` refuses.
    Raises ValueError for an unknown product or scenario, an item given twice, fewer than 2
    iterations or a percentile outside 0 to 100. Raises a calc error for the first iteration
    that cannot be solved, naming it and the processes involved: a reference amount drawn at 0
    or below, a matrix singular or nearly so, a residual above the tolerance, in the
    background, a scenario's background or a database's own processes. No iteration is ever
    dropped.
    """
    if iterations < 2:
        raise ValueError(f"iterations must be 2 or more, got {iterations}")
    if workers < 1:
        raise ValueError(f"workers must be 1 or more, got {workers}")
    if seed < 0:
        raise ValueError(f"seed must be zero or positive, got {seed}")
    chosen = tuple(float(p) for p in percentiles)
    if any(not 0 <= p <= 100 for p in chosen):
        raise ValueError(f"percentiles must be between 0 and 100, got {list(chosen)}")
    inner = internals(background)
    release = background.release
    if release is None:
        raise InvalidDataError(
            [
                Problem(
                    "needs-a-release",
                    f"background {inner.folder.path}",
                    "is a folder of process files. Monte Carlo needs a release, whose lines it "
                    "draws: import the data with `headwater release import`.",
                )
            ]
        )
    wanted = (
        [inner.indicator(i) for i in indicators]
        if indicators is not None
        else [i.id for i in background.indicators]
    )
    solved = inner.solved
    block = solved.block
    found = uncertain_lines(release, block)
    asked = _items(background, items, wanted, found, seed=seed, iterations=iterations)
    if not len(found.uncertain) and not asked.studies:
        raise InvalidDataError(
            [
                Problem(
                    "no-uncertainty",
                    f"release {release.name}",
                    "has no line with a distribution, and no study asked has a line or a "
                    "parameter with one, so every iteration would give the same number",
                )
            ]
        )
    settings = solved.factorization.settings
    system = MonteCarloSystem(
        n=len(block.ids),
        a_rows=block.lines.row,
        a_cols=block.lines.col,
        a_signs=block.lines.sign,
        a_amounts=block.lines.amount,
        b_rows=block.emissions.row,
        b_cols=block.emissions.col,
        b_amounts=block.emissions.amount,
        flows=block.B.shape[0],
        uncertain=found.uncertain,
        in_a=found.in_a,
        position=found.position,
        reference=found.reference,
        factors=np.column_stack([solved.c[i] for i in wanted]),
        outputs=asked.outputs,
        offsets=asked.offsets,
        tolerance=settings.tolerance,
        condition_limit=settings.condition_limit,
        worlds=asked.worlds,
        item_world=asked.item_world,
        studies=asked.studies,
    )
    with ids_in_errors((*block.ids, *asked.ids)):
        samples = _run(system, seed, iterations, workers)
    method = background.method
    method_hashes = inner.method_hashes
    provenance = MonteCarloProvenance(
        seed=seed,
        iterations=iterations,
        generator=GENERATOR,
        versions={"numpy": np.__version__, "scipy": scipy.__version__},
        engine_version=headwater.__version__,
        input_hash=sha256_of(
            {
                "items": asked.hashes,
                "background": inner.fingerprint,
                "indicators": wanted,
                "seed": seed,
                "iterations": iterations,
            }
        ),
        releases=inner.releases,
        method=method.id,
        method_version=method.version,
        method_hash=inner.method_hash,
        methods=tuple(MethodRef(m.id, m.version, method_hashes[m.id]) for m in background.methods),
        solver=settings,
        uncertain_lines=found.counts,
        database_lines=dict(sorted(Counter(asked.lines.values()).items())),
        parameters=len(asked.parameters),
        opposite_signs=samples.opposite_signs,
        largest_condition=max(float(samples.conditions.max()), samples.other_condition),
        residual=samples.residual,
        pivoted=samples.pivoted,
        scenario_refactorized=samples.refactorized,
    )
    units = {i.id: i.unit for i in background.indicators}
    return MonteCarlo(
        items=asked.labels,
        indicators=tuple(wanted),
        units=tuple(units[i] for i in wanted),
        scores=asked.scores,
        samples=samples.values,
        conditions=samples.conditions,
        percentiles=chosen,
        provenance=provenance,
    )


@dataclass(frozen=True, eq=False)
class _Asked:
    """The items of a run, as `MonteCarloSystem` takes them, with what names and hashes them."""

    labels: tuple[str, ...]
    outputs: sparse.csr_array
    offsets: NDArray[np.float64]
    scores: NDArray[np.float64]
    """Each item's score with no draw, one per indicator: what `calc` gives."""
    worlds: tuple[Patch, ...]
    item_world: NDArray[np.intp]
    studies: tuple[tuple[int, StudyPart], ...]
    ids: tuple[str, ...]
    """The processes of the study parts, in their order: errors name them after the
    background's."""
    hashes: list[str]
    lines: dict[str, str]
    """The distribution of each study line drawn by its own key, by key."""
    parameters: set[str]


def _label(item: Item) -> str:
    """`tshirt:tshirt`, or `tshirt:tshirt[clean-grid]` in a scenario."""
    if isinstance(item, str):
        return item
    if len(item) == 3:
        return f"{item[1]}[{item[2]}]"
    return item[1]


def _items(
    background: Background,
    items: Item | Sequence[Item],
    wanted: Sequence[str],
    found: UncertainLines,
    *,
    seed: int,
    iterations: int,
) -> _Asked:
    """Each item's name, row of outputs and offsets (see `MonteCarloSystem`), its world and its
    study part, and what its input hash is made of."""
    # A tuple is a sequence of items: a database's product goes inside it, `[(db, "x")]`.
    given: list[object] = (
        [items] if isinstance(items, str | Database | Study) else list(items)  # pyright: ignore[reportUnnecessaryIsInstance]
    )
    inner = internals(background)
    if any(isinstance(item, Database | Study) for item in given):
        raise ValueError(
            "a product of a database or a study is the pair (database or study, process id), "
            "inside the list of items: [(database, 'tshirt:tshirt')], [(study, 'tshirt:tshirt', "
            "'clean-grid')] in a scenario of the study"
        )
    listed = cast(list[Item], given)
    solved = inner.solved
    n = len(inner.index)
    sign = solved.block.reference_sign
    x = np.column_stack([solved.x[i].values for i in wanted])  # for the scores with no draw
    labels: list[str] = []
    rows: list[Vector] = []
    offsets: list[Vector] = []
    scores: list[Vector] = []
    worlds: list[Patch] = []
    item_world: list[int] = []
    studies: list[tuple[int, StudyPart]] = []
    ids: list[str] = []
    hashes: list[str] = []
    lines: dict[str, str] = {}
    parameters: set[str] = set()
    for at, item in enumerate(listed):
        label = _label(item)
        if label in labels:
            raise ValueError(f"{label!r} is asked twice")
        labels.append(label)
        if isinstance(item, str):
            row = inner.index.get(item)
            if row is None:
                raise ValueError(
                    f"{item!r} is not the id of a process of this background."
                    f"{did_you_mean(item, inner.index)}"
                )
            unit = np.zeros(n)
            unit[row] = sign[row]
            rows.append(unit)
            offsets.append(np.zeros(len(wanted)))
            scores.append(unit @ x)
            item_world.append(0)
            hashes.append(item)
            continue
        owner, process_id = item[0], item[1]
        scenario = item[2] if len(item) == 3 else None
        if isinstance(owner, Study):
            draws = _study_product(
                owner, process_id, scenario, background, found, wanted, seed, iterations
            )(n + len(ids))
        elif scenario is not None:
            raise ValueError(
                f"database {owner.name} has no scenarios: a scenario is a study's, and its item "
                f"is (study, {process_id!r}, {scenario!r})"
            )
        else:
            draws = study_draws(
                owner,
                process_id,
                None,
                background,
                found,
                wanted,
                seed=seed,
                iterations=iterations,
                offset=n + len(ids),
            )
        world = 0
        if draws.patch is not None:
            worlds.append(draws.patch)
            world = len(worlds)
        item_world.append(world)
        scores.append(draws.score)
        if draws.part is None:
            rows.append(draws.amounts)
            offsets.append(draws.direct)
        else:
            rows.append(np.zeros(n))
            offsets.append(np.zeros(len(wanted)))
            studies.append((at, draws.part))
            ids.extend(draws.ids)
        lines.update(draws.lines)
        parameters.update(draws.parameters)
        hashes.append(draws.input_hash)
    return _Asked(
        labels=tuple(labels),
        outputs=sparse.csr_array(np.vstack(rows)),
        offsets=np.array(offsets, dtype=np.float64),
        scores=np.array(scores, dtype=np.float64),
        worlds=tuple(worlds),
        item_world=np.array(item_world, dtype=np.intp),
        studies=tuple(studies),
        ids=tuple(ids),
        hashes=hashes,
        lines=lines,
        parameters=parameters,
    )


def _run(system: MonteCarloSystem, seed: int, iterations: int, workers: int) -> Samples:
    """The iterations 0 to `iterations` - 1, in `workers` processes, each a block of them in
    order. The first error raised is that of the first iteration that fails."""
    if workers == 1:
        return sample(system, seed, range(iterations))
    blocks = [b.tolist() for b in np.array_split(np.arange(iterations), workers) if b.size]
    context = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=len(blocks), mp_context=context) as pool:
        parts = list(pool.map(sample, [system] * len(blocks), [seed] * len(blocks), blocks))
    return Samples(
        values=np.concatenate([p.values for p in parts], axis=2),
        conditions=np.concatenate([p.conditions for p in parts]),
        residual=max(p.residual for p in parts),
        pivoted=sum(p.pivoted for p in parts),
        opposite_signs=sum(p.opposite_signs for p in parts),
        other_condition=max(p.other_condition for p in parts),
        refactorized=sum(p.refactorized for p in parts),
    )


def _study_product(
    study: Study,
    process_id: str,
    scenario: str | None,
    background: Background,
    found: UncertainLines,
    wanted: Sequence[str],
    seed: int,
    iterations: int,
):  # -> Callable[[int], StudyDraws]
    """How a product of a study, in its scenario if the item names one, is drawn: its amount of
    it, from the database it is in, with the study's scenario and no_factor rows. Raises
    ValueError for a product the study does not have, or one of the background, which is drawn
    as itself, by its id, and for a scenario the study does not have."""
    from headwater.query.study import study_input_hash

    chosen = study._scenario(scenario)  # pyright: ignore[reportPrivateUsage]
    product = next((p for p in study.products if p.process == process_id), None)
    if product is None and process_id in {s.name for s in study.folder.scenarios}:
        first = study.products[0].process
        raise ValueError(
            f"{process_id!r} is a scenario of study {study.name}: a product in a scenario is "
            f"(study, process id, scenario), such as (study, {first!r}, {process_id!r})"
        )
    if product is None:
        raise ValueError(
            f"{process_id!r} is not a product of study {study.name}."
            f"{did_you_mean(process_id, [p.process for p in study.products])}"
        )
    database = study.folder.database_of(process_id)
    if database is None:
        raise ValueError(
            f"{process_id} is a product of the background: draw it as the item {process_id!r}"
        )
    study._study_problems(background).raise_if_errors()  # pyright: ignore[reportPrivateUsage]

    def hashed(nw_set: str | None) -> str:
        return study_input_hash(study.folder, background, process_id, chosen, nw_set)

    def draws(offset: int) -> StudyDraws:
        return study_draws(
            Database(database),
            process_id,
            None if chosen is None else study.folder.scenario_for(chosen, database),
            background,
            found,
            wanted,
            seed=seed,
            iterations=iterations,
            offset=offset,
            amount=product.amount,
            acknowledged=study.folder.no_factor,
            hashed=hashed,
        )

    return draws
