"""Monte Carlo on arrays: draw every uncertain amount, factorize again, solve every indicator.

Example, the toy: fabric production buys 3 kWh of electricity per kg, lognormal with median 3
and a geometric standard deviation of 1.1. Iteration k draws that amount from the uniform of
(seed, k), say 3.21 kWh, puts -3.21 in A, factorizes A, and solves the scores of every product:
fabric 2.1192 + 0.5298 × 3.21 = 3.8198. The T-shirt, which buys 0.25 kg of fabric and 0.5 kWh,
is 0.79470 + 0.13245 × 3.21 = 1.21986 in that iteration.

Each uncertain line of the background has one uniform per iteration, in the order the caller
gives the lines, and its amount is the inverse of its distribution's CDF at that uniform. So the
draw of a line depends only on the seed, the iteration and its place in that order, never on
what is asked. A study's own lines and parameters are drawn from uniforms keyed by their ids
instead (`keyed_uniforms`), so a study draws the same alone or beside others.
"""

import hashlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray
from scipy import sparse
from scipy.special import ndtri

from headwater.calc.arrays import Vector
from headwater.calc.errors import CalculationError, ResidualError, SingularMatrixError
from headwater.calc.solve import Factorization, factorize, scores, supply
from headwater.calc.update import CROSSOVER, choose_route, low_rank_update, refactorize
from headwater.model.uncertainty import Distribution

DISTRIBUTIONS: tuple[Distribution, ...] = tuple(Distribution)
"""The distributions, in the order of the codes of `Uncertain.distribution`."""

_LOGNORMAL, _NORMAL, _UNIFORM, _TRIANGULAR = (
    DISTRIBUTIONS.index(d)
    for d in (
        Distribution.lognormal,
        Distribution.normal,
        Distribution.uniform,
        Distribution.triangular,
    )
)

GENERATOR = (
    "numpy.random.PCG64(SeedSequence(seed, spawn_key=(k,))) for iteration k: one uniform per "
    "uncertain line of the background, by process id then line id; for a study's own line or "
    "parameter, SeedSequence(seed, spawn_key=(k, *the 8 little-endian 32-bit words of the sha256 "
    "of its key))'s first 64-bit state; each amount the inverse CDF of its distribution"
)
"""How iteration k draws, for provenance."""

_STEPS = 2**52


@dataclass(frozen=True, eq=False)
class Uncertain:
    """The distributions of some lines, one entry per line, in the reference unit of each flow.

    Parameters a distribution does not use are 0. Example: 3 kWh with a GSD of 1.1 is a lognormal
    with loc ln 3 and scale ln 1.1.
    """

    distribution: NDArray[np.int8]
    """Index into `DISTRIBUTIONS`."""
    loc: Vector
    """lognormal: the mean of ln|x|. normal: the mean. triangular: the mode."""
    scale: Vector
    """lognormal: the standard deviation of ln|x|. normal: the standard deviation."""
    minimum: Vector
    maximum: Vector
    negative: NDArray[np.bool_]
    """A lognormal of a negative amount, drawn below 0: an emission of -0.2 kg stays negative."""

    def __len__(self) -> int:
        return int(self.distribution.size)


def uniforms(seed: int, k: int, n: int) -> Vector:
    """The `n` uniforms of iteration `k`: the same on any run for the same seed and k.

    They are inside the open interval (0, 1), so no inverse CDF is infinite.
    """
    if seed < 0 or k < 0:
        raise ValueError(f"seed and iteration must be zero or positive, got {seed} and {k}")
    rng = np.random.Generator(np.random.PCG64(np.random.SeedSequence(seed, spawn_key=(k,))))
    return (rng.integers(0, _STEPS, size=n, dtype=np.int64) + 0.5) / _STEPS


def line_key(process_id: str, line_id: str) -> str:
    """What a study's line is drawn by: its process id, which holds its study's name, and its
    line id. `tshirt:tshirt` line e02 draws the same in any study that holds it."""
    return f"line\x1f{process_id}\x1f{line_id}"


def parameter_key(study: str, parameter_id: str) -> str:
    """What a study's parameter is drawn by: the study's name and the parameter's id."""
    return f"parameter\x1f{study}\x1f{parameter_id}"


def key_words(keys: Sequence[str]) -> NDArray[np.uint32]:
    """The sha256 of each key's UTF-8 text, as 8 little-endian 32-bit words: one row per key."""
    words = np.empty((len(keys), 8), dtype=np.uint32)
    for i, key in enumerate(keys):
        words[i] = np.frombuffer(hashlib.sha256(key.encode()).digest(), dtype="<u4")
    return words


def keyed_uniforms(seed: int, k: int, words: NDArray[np.uint32]) -> Vector:
    """One uniform of iteration `k` per key, from `key_words`, inside (0, 1).

    Each comes from `SeedSequence(seed, spawn_key=(k, *words))`, a stream of its own: it depends
    on the seed, k and the key only, never on the other keys asked or on the background's
    uniforms, whose spawn key is `(k,)`.
    """
    if seed < 0 or k < 0:
        raise ValueError(f"seed and iteration must be zero or positive, got {seed} and {k}")
    out = np.empty(len(words), dtype=np.float64)
    for i, row in enumerate(words.tolist()):
        state = np.random.SeedSequence(seed, spawn_key=(k, *row)).generate_state(1, np.uint64)
        out[i] = ((int(state[0]) >> 12) + 0.5) / _STEPS
    return out


def draw(uncertain: Uncertain, u: Vector) -> Vector:
    """The amount of each line at its uniform `u`: the inverse of its distribution's CDF.

    - lognormal: exp(loc + scale × z), below 0 for a negative amount, with z the standard normal
      quantile of u
    - normal: loc + scale × z
    - uniform: minimum + u × (maximum - minimum)
    - triangular: the inverse of the triangular CDF with mode loc
    """
    if u.shape != (len(uncertain),):
        raise ValueError(f"u must hold one uniform per line, {len(uncertain)}, got {u.shape}")
    code = uncertain.distribution
    loc, scale = uncertain.loc, uncertain.scale
    low, high = uncertain.minimum, uncertain.maximum
    out = np.empty(u.size, dtype=np.float64)

    i = code == _LOGNORMAL
    out[i] = np.exp(loc[i] + scale[i] * ndtri(u[i]))
    out[i & uncertain.negative] *= -1.0
    i = code == _NORMAL
    out[i] = loc[i] + scale[i] * ndtri(u[i])
    i = code == _UNIFORM
    out[i] = low[i] + u[i] * (high[i] - low[i])
    i = code == _TRIANGULAR
    a, b, c, q = low[i], high[i], loc[i], u[i]
    width = b - a
    with np.errstate(divide="ignore", invalid="ignore"):
        split = np.where(width > 0, (c - a) / width, 0.5)
    left = a + np.sqrt(q * width * (c - a))
    right = b - np.sqrt((1.0 - q) * width * (b - c))
    out[i] = np.where(q < split, left, right)
    return out


@dataclass(frozen=True, eq=False)
class Patch:
    """A scenario's changes to the lines of the background: in each iteration, the background is
    drawn once and the scenario's background is the drawn one with these lines changed.

    Example, `clean-grid`: electricity's 0.5 kg of CO2 per kWh becomes 0.3, fixed, in B. A line
    whose supplier alone changes keeps its draw and moves to the new supplier's row.
    """

    a_position: NDArray[np.intp]
    """The lines of A the scenario changes, as indices among the lines of A."""
    a_rows: NDArray[np.intp]
    """Their rows in the scenario: a new supplier moves a line to its row."""
    a_amounts: Vector
    a_fixed: NDArray[np.bool_]
    """Lines whose amount the scenario sets, to `a_amounts`. The others keep their draw."""
    b_position: NDArray[np.intp]
    """The lines of B the scenario changes, each set to its amount in `b_amounts`."""
    b_amounts: Vector


@dataclass(frozen=True, eq=False)
class StudyPart:
    """A study whose own lines or parameters are drawn: its processes, those of the studies it
    uses and its copies of background processes, solved again in each iteration.

    Rows and columns 0 to n - 1 are its processes; rows from n on are the background's. In each
    iteration its lines are drawn, its block factorized, and how much each of its processes runs
    for one unit of its reference flow solved. Its score is then its direct emissions plus what
    it buys from the background times the background's scores in that iteration.
    """

    n: int
    offset: int
    """Where its processes start among the ids that errors name, after the background's."""
    a_rows: NDArray[np.intp]
    a_cols: NDArray[np.intp]
    a_signs: Vector
    a_amounts: Vector
    b_rows: NDArray[np.intp]
    b_cols: NDArray[np.intp]
    b_amounts: Vector
    factors: NDArray[np.float64]
    """Characterisation factors of its elementary flows, one column per indicator."""
    demand: Vector
    """One unit of its reference flow."""
    uncertain: Uncertain
    in_a: NDArray[np.bool_]
    position: NDArray[np.intp]
    reference: NDArray[np.bool_]
    keyed: NDArray[np.intp]
    """The uncertain lines drawn from their own keys, in the order of `words`."""
    words: NDArray[np.uint32]
    shared: NDArray[np.intp]
    """The uncertain lines of its copies drawn as the background's own line: same uniform."""
    shared_from: NDArray[np.intp]
    """For each of `shared`, its line's index among the background's uncertain lines."""
    formula_in_a: NDArray[np.bool_] = field(default_factory=lambda: np.zeros(0, dtype=np.bool_))
    formula_position: NDArray[np.intp] = field(default_factory=lambda: np.zeros(0, dtype=np.intp))
    formula_amounts: NDArray[np.float64] = field(
        default_factory=lambda: np.zeros((0, 0), dtype=np.float64)
    )
    """For lines whose amount is a formula of drawn parameters: their amount in iteration k is
    `formula_amounts[k]`, worked out by the caller."""


@dataclass(frozen=True, eq=False)
class MonteCarloSystem:
    """Everything one iteration needs, as arrays: the lines of A and B, the uncertain ones among
    them, the characterisation factors, and what to report.

    What is reported for an item with no drawn line of its own is linear in the scores of the
    products: item i of indicator j is `outputs[i] @ x[:, j] + offsets[i, j]`, with x the scores
    of the background of its world (the base, or a scenario's). For a background product its row
    of `outputs` is the sign of its reference at its own index (so a treatment's score is
    positive) and its offset 0. For such a study it is what the study buys of each product, and
    the offset is its own direct emissions. A study with drawn lines or parameters is a
    `StudyPart`, solved in each iteration.
    """

    n: int
    """The number of processes."""
    a_rows: NDArray[np.intp]
    a_cols: NDArray[np.intp]
    a_signs: Vector
    """The matrix entry of one unit of each line of A: -1 for an input."""
    a_amounts: Vector
    """Each line of A in the reference unit of its flow, positive as in the files."""
    b_rows: NDArray[np.intp]
    b_cols: NDArray[np.intp]
    b_amounts: Vector
    flows: int
    """The number of rows of B."""
    uncertain: Uncertain
    """The lines drawn, in the order of their uniforms."""
    in_a: NDArray[np.bool_]
    """For each uncertain line: in A, or else in B."""
    position: NDArray[np.intp]
    """For each uncertain line: its index among the lines of A, or of B."""
    reference: NDArray[np.bool_]
    """For each uncertain line: a reference line, which must stay above 0."""
    factors: NDArray[np.float64]
    """Characterisation factors, one row per flow of B, one column per indicator."""
    outputs: sparse.csr_array
    offsets: NDArray[np.float64]
    tolerance: float
    condition_limit: float
    worlds: tuple[Patch, ...] = ()
    """The backgrounds of scenarios: world w + 1 is `worlds[w]`, world 0 the base."""
    item_world: NDArray[np.intp] | None = None
    """The world of each item. None: every item is in the base."""
    studies: tuple[tuple[int, StudyPart], ...] = ()
    """The items whose own lines or parameters are drawn, by index, with their parts."""


@dataclass(frozen=True, eq=False)
class Samples:
    """What `sample` found for some iterations."""

    values: NDArray[np.float64]
    """One value per item, indicator and iteration, in the order of the iterations asked."""
    conditions: Vector
    """The condition estimate of each iteration's factors."""
    residual: float
    """The largest residual of every solve, each row relative to the size of its terms."""
    pivoted: int
    """How many iterations needed factors with pivoting."""
    opposite_signs: int
    """How many draws, over every line and iteration, have the opposite sign of the line's
    amount: a normal input of 0.1 ± 0.1 drawn at -0.02."""
    other_condition: float = 0.0
    """The largest condition estimate of a scenario's matrix or a study's block, in any
    iteration."""
    refactorized: int = 0
    """How many solves of a scenario's background, over every scenario and iteration, factorized
    its A again: above the crossover, or when the update of the iteration's factors failed its
    check. The others updated those factors, or used them as they are."""


def sample(system: MonteCarloSystem, seed: int, iterations: Iterable[int]) -> Samples:
    """Draw, factorize and solve each iteration k of `iterations`, in order.

    Raises CalculationError for the first iteration that cannot be solved: a reference line
    drawn at 0 or below, a matrix that is singular or nearly so (condition above the limit),
    or a residual above the tolerance, in the background, a scenario's background or a study's
    block. The message starts with `iteration k of seed s` and the error names the processes
    involved, as indices: the background's, then each study part's from its `offset`.
    """
    ks = list(iterations)
    a_drawn = system.position[system.in_a]
    b_drawn = system.position[~system.in_a]
    deterministic = _deterministic(system.in_a, system.position, system.a_amounts, system.b_amounts)
    diagonal_sign = np.sign(_matrix(system, system.a_rows, system.a_amounts).diagonal())
    m = system.factors.shape[1]
    values = np.empty((system.outputs.shape[0], m, len(ks)), dtype=np.float64)
    conditions = np.empty(len(ks), dtype=np.float64)
    residual = 0.0
    other = 0.0
    pivoted = 0
    opposite = 0
    refactorized = 0
    worlds = _items_by_world(system)
    signs = [_diagonal_sign(part) for _, part in system.studies]
    for at, k in enumerate(ks):
        u = uniforms(seed, k, len(system.uncertain))
        drawn = draw(system.uncertain, u)
        opposite += int(np.count_nonzero(drawn * deterministic < 0))
        try:
            _check_references(system.reference, system.position, system.a_cols, drawn, 0)
            a = system.a_amounts.copy()
            a[a_drawn] = drawn[system.in_a]
            b = system.b_amounts.copy()
            b[b_drawn] = drawn[~system.in_a]
            A = _matrix(system, system.a_rows, a)
            _check_diagonal(A, diagonal_sign)
            x, factorization, largest = _solve(system, A, b)
            values[:, :, at] = system.outputs @ x + system.offsets
            backgrounds = [x]
            for patch, rows in zip(system.worlds, worlds, strict=True):
                found, condition, largest_w, fresh = _scenario(
                    system, patch, a, b, factorization, x, diagonal_sign
                )
                refactorized += int(fresh)
                backgrounds.append(found)
                values[rows, :, at] = system.outputs[rows] @ found + system.offsets[rows]
                other = max(other, condition)
                largest = max(largest, largest_w)
            item_world = system.item_world
            for (item, part), expected in zip(system.studies, signs, strict=True):
                world = int(item_world[item]) if item_world is not None else 0
                found, condition, largest_s, flipped = _study(
                    system, part, expected, seed, k, u, backgrounds[world]
                )
                values[item, :, at] = found
                other = max(other, condition)
                largest = max(largest, largest_s)
                opposite += flipped
        except CalculationError as error:
            raise _in_iteration(error, seed, k) from error
        conditions[at] = factorization.condition
        residual = max(residual, largest)
        pivoted += int(factorization.pivoted)
    return Samples(values, conditions, residual, pivoted, opposite, other, refactorized)


def _items_by_world(system: MonteCarloSystem) -> list[NDArray[np.intp]]:
    """The items of each scenario's world, w = 1 onwards, that are not study parts."""
    if not system.worlds:
        return []
    assert system.item_world is not None
    parts = {item for item, _ in system.studies}
    return [
        np.array(
            [i for i in np.flatnonzero(system.item_world == w).tolist() if i not in parts],
            dtype=np.intp,
        )
        for w in range(1, len(system.worlds) + 1)
    ]


def _deterministic(
    in_a: NDArray[np.bool_], position: NDArray[np.intp], a: Vector, b: Vector
) -> Vector:
    """The amount with no draw of each uncertain line."""
    out = np.empty(in_a.size, dtype=np.float64)
    out[in_a] = a[position[in_a]]
    out[~in_a] = b[position[~in_a]]
    return out


def _check_references(
    reference: NDArray[np.bool_],
    position: NDArray[np.intp],
    a_cols: NDArray[np.intp],
    drawn: Vector,
    offset: int,
) -> None:
    low = reference & (drawn <= 0)
    if low.any():
        lines = np.flatnonzero(low)
        cols = a_cols[position[lines]] + offset
        shown = ", ".join(f"{drawn[i]:.6g}" for i in lines[:3])
        raise CalculationError(
            f"a reference amount drew 0 or below ({shown}), which cannot scale its process; "
            "processes",
            np.unique(cols),
        )


def _matrix(system: MonteCarloSystem, rows: NDArray[np.intp], a: Vector) -> sparse.csc_array:
    n = system.n
    return sparse.csc_array((system.a_signs * a, (rows, system.a_cols)), shape=(n, n))


def _check_diagonal(A: sparse.csc_array, expected: Vector, offset: int = 0) -> None:
    """An own use drawn above its reference flips a process's net output: 1 kWh made and 1.2
    used is -0.2 on the diagonal. Stop rather than scale the process the wrong way."""
    flipped = np.flatnonzero(np.sign(A.diagonal()) != expected)
    if flipped.size:
        raise CalculationError(
            "a draw made the net output of a process 0 or of the opposite sign, its own use "
            "above its reference; processes",
            flipped + offset,
        )


def _solve(
    system: MonteCarloSystem, A: sparse.csc_array, b: Vector
) -> tuple[NDArray[np.float64], Factorization, float]:
    """Factorize the drawn A and solve the scores of every indicator, checked as `scores` is."""
    n = system.n
    B = sparse.csc_array((b, (system.b_rows, system.b_cols)), shape=(system.flows, n))
    factorization = factorize(A, tolerance=system.tolerance, condition_limit=system.condition_limit)
    H = np.asarray(B.T @ system.factors, dtype=np.float64)
    x = np.empty((n, H.shape[1]), dtype=np.float64)
    largest = 0.0
    for j in range(H.shape[1]):
        solution = scores(factorization, H[:, j])
        if solution.fallback is not None:  # the solve needed pivoting: keep those factors
            factorization = solution.fallback
        x[:, j] = solution.values
        largest = max(largest, solution.residual)
    return x, factorization, largest


def _scenario(
    system: MonteCarloSystem,
    patch: Patch,
    a: Vector,
    b: Vector,
    factorization: Factorization,
    x: NDArray[np.float64],
    diagonal_sign: Vector,
) -> tuple[NDArray[np.float64], float, float, bool]:
    """The scores of a scenario's background in this iteration: the drawn background with the
    scenario's lines in place. Changed columns of A are solved as `calc` solves a scenario, by a
    low-rank update of this iteration's factors up to the crossover, and by factorizing again
    above it or when the update fails its check."""
    a_w = a.copy()
    fixed = patch.a_position[patch.a_fixed]
    a_w[fixed] = patch.a_amounts[patch.a_fixed]
    rows = system.a_rows.copy()
    rows[patch.a_position] = patch.a_rows
    b_w = b.copy()
    b_w[patch.b_position] = patch.b_amounts
    B = sparse.csc_array((b_w, (system.b_rows, system.b_cols)), shape=(system.flows, system.n))
    H = np.asarray(B.T @ system.factors, dtype=np.float64)
    same_h = patch.b_position.size == 0
    columns = np.unique(system.a_cols[patch.a_position]).tolist()
    out = np.empty_like(x)
    largest = 0.0
    if not columns:
        for j in range(H.shape[1]):
            solution = scores(factorization, H[:, j])
            factorization = solution.fallback or factorization
            out[:, j] = solution.values
            largest = max(largest, solution.residual)
        return out, factorization.condition, largest, False

    A_w = _matrix(system, rows, a_w)
    _check_diagonal(A_w, diagonal_sign)
    new = A_w[:, columns]
    if not choose_route(len(columns), CROSSOVER).refactorized:
        try:
            update = low_rank_update(factorization, columns, new)
            for j in range(H.shape[1]):
                solution = update.scores(H[:, j], start=x[:, j] if same_h else None)
                out[:, j] = solution.values
                largest = max(largest, solution.residual)
            return out, update.condition, largest, False
        except (ResidualError, SingularMatrixError):
            pass  # as in calc: factorizing the scenario's A decides, and raises if it is singular
    fresh = refactorize(factorization, columns, new)
    largest = 0.0
    for j in range(H.shape[1]):
        solution = scores(fresh, H[:, j])
        fresh = solution.fallback or fresh
        out[:, j] = solution.values
        largest = max(largest, solution.residual)
    return out, fresh.condition, largest, True


def _diagonal_sign(part: StudyPart) -> Vector:
    """The sign of the net output of each of a study's processes, with no draw."""
    A = sparse.csc_array(
        (part.a_signs * part.a_amounts, (part.a_rows, part.a_cols)),
        shape=(int(part.a_rows.max(initial=part.n - 1)) + 1, part.n),
    )
    return np.sign(A[: part.n, :].diagonal())


def _study(
    system: MonteCarloSystem,
    part: StudyPart,
    expected: Vector,
    seed: int,
    k: int,
    u_background: Vector,
    x: NDArray[np.float64],
) -> tuple[Vector, float, float, int]:
    """One iteration of a study whose own lines or parameters are drawn: its score for each
    indicator, the condition of its block, the residual and how many draws flipped a sign."""
    u = np.empty(len(part.uncertain), dtype=np.float64)
    u[part.keyed] = keyed_uniforms(seed, k, part.words)
    u[part.shared] = u_background[part.shared_from]
    drawn = draw(part.uncertain, u)
    a = part.a_amounts.copy()
    b = part.b_amounts.copy()
    flipped = 0
    if part.formula_position.size:
        amounts = part.formula_amounts[k]
        at_a = part.formula_position[part.formula_in_a]
        at_b = part.formula_position[~part.formula_in_a]
        flipped += int(np.count_nonzero(amounts[part.formula_in_a] * part.a_amounts[at_a] < 0))
        flipped += int(np.count_nonzero(amounts[~part.formula_in_a] * part.b_amounts[at_b] < 0))
        a[part.formula_position[part.formula_in_a]] = amounts[part.formula_in_a]
        b[part.formula_position[~part.formula_in_a]] = amounts[~part.formula_in_a]
    flipped += int(
        np.count_nonzero(
            drawn * _deterministic(part.in_a, part.position, part.a_amounts, part.b_amounts) < 0
        )
    )
    _check_references(part.reference, part.position, part.a_cols, drawn, part.offset)
    a[part.position[part.in_a]] = drawn[part.in_a]
    b[part.position[~part.in_a]] = drawn[~part.in_a]

    n = part.n
    A = sparse.csc_array((part.a_signs * a, (part.a_rows, part.a_cols)), shape=(n + x.shape[0], n))
    A_ss = sparse.csc_array(A[:n, :])
    A_bs = sparse.csc_array(A[n:, :])
    try:
        _check_diagonal(A_ss, expected)
        factors = factorize(
            A_ss, tolerance=system.tolerance, condition_limit=system.condition_limit
        )
        s = supply(factors, part.demand)
    except CalculationError as error:
        raise _shifted(error, part.offset) from error
    flows = part.factors.shape[0]
    B = sparse.csc_array((b, (part.b_rows, part.b_cols)), shape=(flows, n))
    direct = part.factors.T @ np.asarray(B @ s.values, dtype=np.float64)
    bought = -np.asarray(A_bs @ s.values, dtype=np.float64)
    condition = (s.fallback or factors).condition
    return direct + bought @ x, condition, s.residual, flipped


def _shifted(error: CalculationError, offset: int) -> CalculationError:
    """The same error, naming a study part's processes by their place among all the ids."""
    indices = tuple(int(i) + offset for i in error.indices)
    if isinstance(error, ResidualError):
        return ResidualError(error.message, indices, error.residual)
    if isinstance(error, SingularMatrixError):
        return SingularMatrixError(error.message, indices)
    return CalculationError(error.message, indices)


def _in_iteration(error: CalculationError, seed: int, k: int) -> CalculationError:
    """The same error, its message starting with the iteration and the seed."""
    message = f"iteration {k} of seed {seed}: {error.message}"
    if isinstance(error, ResidualError):
        return ResidualError(message, error.indices, error.residual)
    if isinstance(error, SingularMatrixError):
        return SingularMatrixError(message, error.indices)
    return CalculationError(message, error.indices)
