"""A study for Monte Carlo: fixed, as a linear function of the background's scores, or drawn.

Example: the T-shirt with fixed lines is 0.25 kg of fabric and 0.5 kWh times the drawn scores of
fabric and electricity. With its fabric line lognormal (median 0.25 kg, GSD 1.1), its own block
is drawn and solved in each iteration, from a uniform keyed by `tshirt:tshirt` line e02.

A study in a scenario that changes background processes gets a `Patch`: the scenario's
background in each iteration is the drawn background with the scenario's lines in place.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace

import numpy as np
import pyarrow as pa

from headwater.calc import CalculationError, factorize, supply
from headwater.calc.arrays import Vector
from headwater.calc.monte_carlo import (
    DISTRIBUTIONS,
    Patch,
    StudyPart,
    Uncertain,
    draw,
    key_words,
    keyed_uniforms,
    line_key,
    parameter_key,
)
from headwater.model import (
    Catalog,
    Distribution,
    Exchange,
    FormulaError,
    InvalidDataError,
    ParameterSet,
    Problem,
    Scenario,
    Uncertainty,
)
from headwater.query._internals import internals
from headwater.query._scenario import change
from headwater.query._system import Block, StackedIndex, assemble, ids_in_errors, matvec
from headwater.query._uncertainty import UncertainLines
from headwater.query.background import Background
from headwater.query.database import (
    Database,
    _formula_lines,  # pyright: ignore[reportPrivateUsage]
    _input_hash,  # pyright: ignore[reportPrivateUsage]
    _Prepared,  # pyright: ignore[reportPrivateUsage]
)
from headwater.query.result import FormulaLine
from headwater.study import NoFactor


@dataclass(frozen=True, eq=False)
class StudyDraws:
    """One study, in one scenario or none, as Monte Carlo scores it."""

    label: str
    """The process id, with the scenario in brackets: `tshirt:tshirt[clean-grid]`."""
    ids: tuple[str, ...]
    """Its processes and copies, as errors name them."""
    amounts: Vector
    """What one unit of its product buys from each background process, with no draw."""
    direct: Vector
    """Its own characterised emissions with no draw, one per indicator."""
    score: Vector
    """Its score with no draw, one per indicator: what `calc` gives, in its scenario."""
    part: StudyPart | None
    """Its block, drawn in each iteration, when a line or parameter of its own has a
    distribution. None: it is fixed, and `amounts` and `direct` give it in each iteration."""
    patch: Patch | None
    """The scenario's changes to the background, when it changes background processes."""
    lines: Mapping[str, str]
    """The distribution of each of its lines drawn by its own key, by key."""
    parameters: tuple[str, ...]
    """The keys of its drawn parameters."""
    input_hash: str


def study_draws(
    study: Database,
    process_id: str,
    chosen: Scenario | None,
    background: Background,
    found: UncertainLines,
    wanted: Sequence[str],
    *,
    seed: int,
    iterations: int,
    offset: int,
    amount: float = 1.0,
    acknowledged: Sequence[NoFactor] = (),
    hashed: Callable[[str | None], str] | None = None,
) -> StudyDraws:
    """How `study`, in `scenario`, is drawn on `background`, whose uncertain lines are `found`.

    `chosen` is a study's scenario, `amount` its amount of the product and `acknowledged` its
    own no_factor rows; `hashed` gives the input hash of a product of a study. `offset` is where
    its processes start among the ids errors name. Raises what `calc` raises for a database it
    refuses, and InvalidDataError for a scenario that sets a background line by a formula when
    the database draws parameters, or sets a parameter of a database the product uses.
    """
    if chosen is not None and any(c.database != study.name for c in chosen.parameter_changes):
        raise InvalidDataError(
            [
                Problem(
                    "monte-carlo-scenario-parameter",
                    f"scenario {chosen.name}",
                    f"sets a parameter of a database that {process_id} uses: Monte Carlo cannot "
                    "yet draw the parameters of a used database in a scenario. Run it without "
                    "the scenario, or set the lines instead.",
                )
            ]
        )
    prepared = study._prepare(  # pyright: ignore[reportPrivateUsage]
        background, chosen, process_id, amount, acknowledged
    )
    block = prepared.block
    ids = tuple(p.id for p in prepared.processes)
    inner = internals(background)
    factors = np.column_stack([inner.characterisation(block.flows, i) for i in wanted])

    with ids_in_errors(ids):
        settings = inner.solved.factorization.settings
        s = supply(
            factorize(
                prepared.A_ss,
                tolerance=settings.tolerance,
                condition_limit=settings.condition_limit,
            ),
            prepared.f_s,
        ).values
    amounts = -matvec(prepared.A_bs, s)
    direct = factors.T @ matvec(block.B, s)
    solved = inner.solved
    if prepared.everyone:  # the scenario changes the background: its scores, as calc has them
        x = [
            change(solved, inner.index, prepared.everyone, prepared.catalog, i).x.values
            for i in wanted
        ]
    else:
        x = [solved.x[i].values for i in wanted]
    score = direct + amounts @ np.column_stack(x)

    lines, part_lines = _lines(study, prepared, found)
    drawn, formulas = _parameters(study, prepared)
    patch = _patch(prepared, background, chosen, bool(drawn))
    part = None
    if len(part_lines.uncertain) or drawn:
        part = _part(prepared, factors, part_lines, offset)
        if drawn:
            part = _with_formulas(part, prepared, drawn, formulas, seed, iterations)
    name = process_id
    return StudyDraws(
        label=name if chosen is None else f"{name}[{chosen.name}]",
        ids=ids,
        amounts=amounts,
        direct=direct,
        score=score,
        part=part,
        patch=patch,
        lines=lines,
        parameters=tuple(key for key, _, _ in drawn),
        input_hash=(
            hashed(None)
            if hashed is not None
            else _input_hash(study.folder, background, process_id, chosen)
        ),
    )


@dataclass(frozen=True, eq=False)
class _PartLines:
    """A study's lines with a distribution, in its block."""

    uncertain: Uncertain
    in_a: np.ndarray
    position: np.ndarray
    reference: np.ndarray
    keyed: np.ndarray
    words: np.ndarray
    shared: np.ndarray
    shared_from: np.ndarray


def _lines(
    study: Database, prepared: _Prepared, found: UncertainLines
) -> tuple[dict[str, str], _PartLines]:
    """Every line of the study's block with a distribution, and what it is drawn by: its own key
    for a line of a study process, or of a copy that its override gives a distribution; the
    background line's uniform for a line a copy keeps from the process it copies."""
    block, catalog = prepared.block, prepared.catalog
    where = _positions(block)
    own_rows = {
        (f"{folder.database.name}:copy:{o.process_id}", e.line_id)
        for folder in study.folder.chain
        for o in folder.overrides
        for e in o.lines
        if e.uncertainty is not None
    }
    rows: list[tuple[Exchange, Uncertainty, bool, int]] = []
    keys: list[str] = []
    originals: list[tuple[str, str]] = []
    for col, process in enumerate(prepared.processes):
        original = prepared.copy_of.get(process.id)
        for e in process.exchanges:
            if e.uncertainty is None:
                continue
            at = where.get((col, e.line_id))
            if at is None:
                continue  # cut off: no place in the matrices
            rows.append((e, e.uncertainty, *at))
            if original is None or (process.id, e.line_id) in own_rows:
                keys.append(line_key(process.id, e.line_id))
                originals.append(("", ""))
            else:
                keys.append("")
                originals.append((original, e.line_id))
    shared_from = found.indices([o for o in originals if o[0]])
    keyed: list[int] = []
    shared: list[int] = []
    sources: list[int] = []
    shared_iter = iter(shared_from)
    for i, (key, origin) in enumerate(zip(keys, originals, strict=True)):
        if key:
            keyed.append(i)
            continue
        source = next(shared_iter)
        if source is None:  # the background does not draw it: keyed as the original
            keys[i] = line_key(*origin)
            keyed.append(i)
        else:
            shared.append(i)
            sources.append(source)
    uncertain = _converted([(e, u) for e, u, _, _ in rows], catalog)
    by_key = {keys[i]: rows[i][1].distribution.value for i in keyed}
    return by_key, _PartLines(
        uncertain=uncertain,
        in_a=np.array([in_a for _, _, in_a, _ in rows], dtype=np.bool_),
        position=np.array([p for _, _, _, p in rows], dtype=np.intp),
        reference=np.array([e.role.value == "reference" for e, _, _, _ in rows], dtype=np.bool_),
        keyed=np.array(keyed, dtype=np.intp),
        words=key_words([keys[i] for i in keyed]),
        shared=np.array(shared, dtype=np.intp),
        shared_from=np.array(sources, dtype=np.intp),
    )


def _positions(block: Block) -> dict[tuple[int, str], tuple[bool, int]]:
    """Where each line of a block sits: in A or B, and its index among their lines."""
    where: dict[tuple[int, str], tuple[bool, int]] = {}
    for in_a, cols, line_ids in (
        (True, block.lines.col, block.lines.line_id),
        (False, block.emissions.col, block.emissions.line_id),
    ):
        for i, (col, line) in enumerate(zip(cols.tolist(), line_ids.to_pylist(), strict=True)):
            where[(int(col), str(line))] = (in_a, i)
    return where


def _converted(lines: Sequence[tuple[Exchange, Uncertainty]], catalog: Catalog) -> Uncertain:
    """The distributions, taken from the unit of each line to the reference unit of its flow, by
    the factor that converts its amount: 10.8 MJ with loc ln 10.8 is 3 kWh with loc ln 3."""
    size = len(lines)
    code = np.empty(size, dtype=np.int8)
    loc, scale = np.zeros(size), np.zeros(size)
    minimum, maximum = np.zeros(size), np.zeros(size)
    negative = np.zeros(size, dtype=np.bool_)
    for i, (e, u) in enumerate(lines):
        k = catalog.convert(e.model_copy(update={"amount": 1.0}))
        if not k > 0:
            raise ValueError(f"the unit factor of {e.label} is not above 0")
        lognormal = u.distribution is Distribution.lognormal
        code[i] = DISTRIBUTIONS.index(u.distribution)
        loc[i] = (u.loc or 0.0) + np.log(k) if lognormal else (u.loc or 0.0) * k
        scale[i] = (u.scale or 0.0) * (1.0 if lognormal else k)
        minimum[i], maximum[i] = (u.minimum or 0.0) * k, (u.maximum or 0.0) * k
        negative[i] = lognormal and e.amount < 0
    return Uncertain(code, loc, scale, minimum, maximum, negative)


def _part(prepared: _Prepared, factors: np.ndarray, lines: _PartLines, offset: int) -> StudyPart:
    block = prepared.block
    return StudyPart(
        n=prepared.n,
        offset=offset,
        a_rows=block.lines.row,
        a_cols=block.lines.col,
        a_signs=block.lines.sign,
        a_amounts=block.lines.amount,
        b_rows=block.emissions.row,
        b_cols=block.emissions.col,
        b_amounts=block.emissions.amount,
        factors=np.asarray(factors, dtype=np.float64),
        demand=prepared.f_s,
        uncertain=lines.uncertain,
        in_a=lines.in_a,
        position=lines.position,
        reference=lines.reference,
        keyed=lines.keyed,
        words=lines.words,
        shared=lines.shared,
        shared_from=lines.shared_from,
    )


# --- parameters ----------------------------------------------------------------------------


type _Drawn = list[tuple[str, str, ParameterSet]]
"""Each drawn parameter: its key, its id, and the parameters of the study it belongs to."""


def _parameters(
    study: Database, prepared: _Prepared
) -> tuple[_Drawn, list[tuple[ParameterSet, FormulaLine]]]:
    """The drawn parameters of the study and of the studies it uses, each with its own key, and
    every formula line of those studies with the parameters it is worked out with."""
    folders = [(study.name, prepared.parameters, prepared.mine)]
    for used in study.folder.used:
        rows = tuple(i for i, owner in prepared.used_rows.items() if owner == used.database.name)
        folders.append((used.database.name, used.parameters, rows))
    drawn: _Drawn = []
    formulas: list[tuple[ParameterSet, FormulaLine]] = []
    for name, parameters, rows in folders:
        mine = [(parameter_key(name, p.id), p.id, parameters) for p in parameters if p.uncertainty]
        if not mine:
            continue
        drawn.extend(mine)
        processes = [prepared.processes[i] for i in rows]
        found = _formula_lines(
            processes, rows, prepared.block, 0, prepared.catalog, prepared.copy_of
        )
        formulas.extend((parameters, f) for f in found)
    return drawn, formulas


def _with_formulas(
    part: StudyPart,
    prepared: _Prepared,
    drawn: _Drawn,
    formulas: list[tuple[ParameterSet, FormulaLine]],
    seed: int,
    iterations: int,
) -> StudyPart:
    """The part with the amount of each formula line in each iteration, worked out with that
    iteration's drawn parameters. Raises CalculationError naming the iteration and the line
    when a formula cannot be worked out with them."""
    uncertain = _parameter_distributions(drawn)
    words = key_words([key for key, _, _ in drawn])
    amounts = np.empty((iterations, len(formulas)), dtype=np.float64)
    for k in range(iterations):
        values = draw(uncertain, keyed_uniforms(seed, k, words))
        changed: dict[int, dict[str, float | str]] = {}
        sets: dict[int, ParameterSet] = {}
        for (_, parameter_id, parameters), value in zip(drawn, values.tolist(), strict=True):
            changed.setdefault(id(parameters), {})[parameter_id] = value
            sets[id(parameters)] = parameters
        worked = {i: sets[i].with_changes(c) for i, c in changed.items()}
        for j, (parameters, line) in enumerate(formulas):
            current = worked.get(id(parameters), parameters)
            try:
                amounts[k, j] = line.factor * current.evaluate(line.formula, line.scope)
            except FormulaError as exc:
                raise CalculationError(
                    f"iteration {k} of seed {seed}: the formula {line.formula!r} of "
                    f"{line.label} cannot be worked out with the drawn parameters: {exc}"
                ) from exc
    return replace(
        part,
        formula_in_a=np.array([not line.biosphere for _, line in formulas], dtype=np.bool_),
        formula_position=np.array([line.position for _, line in formulas], dtype=np.intp),
        formula_amounts=amounts,
    )


def _parameter_distributions(drawn: _Drawn) -> Uncertain:
    """The drawn parameters' distributions, in their own units: a parameter has no flow."""
    found = [parameters.get(parameter_id) for _, parameter_id, parameters in drawn]
    distributions = [p.uncertainty for p in found if p is not None and p.uncertainty is not None]
    assert len(distributions) == len(drawn)

    def numbers(name: str) -> np.ndarray:
        return np.array([getattr(u, name) or 0.0 for u in distributions], dtype=np.float64)

    values = np.array([p.value or 0.0 for p in found if p is not None], dtype=np.float64)
    lognormal = np.array([u.distribution is Distribution.lognormal for u in distributions])
    return Uncertain(
        distribution=np.array(
            [DISTRIBUTIONS.index(u.distribution) for u in distributions], dtype=np.int8
        ),
        loc=numbers("loc"),
        scale=numbers("scale"),
        minimum=numbers("minimum"),
        maximum=numbers("maximum"),
        negative=lognormal & (values < 0),
    )


# --- scenarios -----------------------------------------------------------------------------


def _patch(
    prepared: _Prepared, background: Background, chosen: Scenario | None, draws_parameters: bool
) -> Patch | None:
    """The scenario's changes to the lines of the background, or None when it changes none.

    A line whose amount or unit the scenario sets is fixed at it; a line whose supplier alone
    changes keeps its draw and moves to its new supplier's row.
    """
    everyone = prepared.everyone
    if not everyone or chosen is None:
        return None
    changes = [c for c in chosen.line_changes if c.process_id in {p.id for p in everyone}]
    by_formula = [c for c in changes if c.formula is not None]
    if by_formula and draws_parameters:
        raise InvalidDataError(
            Problem(
                "not-supported-yet",
                f"{c.process_id} line {c.line_id}",
                f"scenario {chosen.name} sets this background line by the formula "
                f"{c.formula!r}, and the study draws parameters: Monte Carlo cannot yet work "
                "out a background line in each iteration",
            )
            for c in by_formula
        )
    inner = internals(background)
    bg = inner.solved.block
    index = inner.index
    ids = [p.id for p in everyone]
    columns = np.array([index[i] for i in ids], dtype=np.intp)
    patch = assemble(
        list(everyone),
        prepared.catalog,
        StackedIndex({i: k for k, i in enumerate(ids)}, index),
    )
    rows = np.concatenate([columns, np.arange(len(bg.ids), dtype=np.intp)])
    a_position: list[int] = []
    a_rows: list[int] = []
    a_amounts: list[float] = []
    a_fixed: list[bool] = []
    b_position: list[int] = []
    b_amounts: list[float] = []
    for line_change in changes:
        local = ids.index(line_change.process_id)
        col = int(columns[local])
        fixed = line_change.amount is not None or line_change.unit is not None
        new = _find(patch.lines.col, patch.lines.line_id, local, line_change.line_id)
        if new is not None:
            base = _find(bg.lines.col, bg.lines.line_id, col, line_change.line_id)
            if base is None:
                continue
            a_position.append(base)
            a_rows.append(int(rows[patch.lines.row[new]]))
            a_amounts.append(float(patch.lines.amount[new]))
            a_fixed.append(fixed)
            continue
        new = _find(patch.emissions.col, patch.emissions.line_id, local, line_change.line_id)
        base = _find(bg.emissions.col, bg.emissions.line_id, col, line_change.line_id)
        if new is not None and base is not None and fixed:
            b_position.append(base)
            b_amounts.append(float(patch.emissions.amount[new]))
    return Patch(
        a_position=np.array(a_position, dtype=np.intp),
        a_rows=np.array(a_rows, dtype=np.intp),
        a_amounts=np.array(a_amounts, dtype=np.float64),
        a_fixed=np.array(a_fixed, dtype=np.bool_),
        b_position=np.array(b_position, dtype=np.intp),
        b_amounts=np.array(b_amounts, dtype=np.float64),
    )


def _find(cols: np.ndarray, line_ids: pa.StringArray, col: int, line_id: str) -> int | None:
    """The index of the line `line_id` of column `col`, or None."""
    at = np.flatnonzero(cols == col)
    if not at.size:
        return None
    names = line_ids.take(pa.array(at, pa.int64())).to_pylist()
    hit = [i for i, name in enumerate(names) if name == line_id]
    return int(at[hit[0]]) if hit else None
