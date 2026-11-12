from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from functools import cached_property
from typing import TYPE_CHECKING, Any, Protocol, cast

import numpy as np
import pyarrow as pa
from numpy.typing import NDArray

from headwater import calc
from headwater.calc import CalculationError, Node, SolverSettings, Step
from headwater.calc.arrays import Vector
from headwater.calc.cancellation import Cancellation
from headwater.calc.explain import largest, sums_by_group
from headwater.model import (
    CutOffCriterion,
    Formula,
    FormulaError,
    Issue,
    ParameterSet,
    Role,
)
from headwater.query._system import REFERENCE_CODE, ROLE_ORDER, Part, System, ids_in_errors
from headwater.release import ReleaseRef

if TYPE_CHECKING:
    import pandas as pd


@dataclass(frozen=True)
class MethodRef:
    """A method by id, version and the hash of its content."""

    id: str
    version: str
    hash: str


@dataclass(frozen=True)
class StudyRef:
    """The study a result is a product of, and whether its files are those of a commit.

    Example: `tshirt-footprint`, saved, at commit `1a2b3c...`, with `changed` False when neither
    the study nor a database it reaches differs from that commit. A study in memory is not saved
    and has no commit: its input hash is what names its inputs."""

    name: str | None
    saved: bool
    commit: str | None
    """The commit the repository that holds the study is at, or None when the study is not
    saved or not in a git repository."""
    changed: bool
    """True when the study or a database it reaches has files that differ from `commit`: the
    commit alone does not give the inputs back."""


@dataclass(frozen=True)
class DatabaseRef:
    """The database a result is a product of, scored on its own, and whether its files are those
    of a commit.

    Example: `tshirt`, at commit `1a2b3c...`, with `changed` False when neither the database nor
    a database it uses differs from that commit. Both are checked at the database's first calc,
    not when it was opened, and kept for its later calcs: the records it scores were read when
    it was opened."""

    name: str
    commit: str | None
    """The commit the repository that holds the database is at, or None when it is not in a git
    repository."""
    changed: bool
    """True when the database or a database it uses has files that differ from `commit`: the
    commit alone does not give the inputs back."""


@dataclass(frozen=True)
class SetRef:
    """The normalisation and weighting set an indicator comes from, by id, version and hash,
    with the method it pins.

    Example: `toy-nw` version 1, for `toy-impacts`, in Pt, leaving out `climate-change-fossil`.
    """

    id: str
    version: str
    hash: str
    method: MethodRef
    """The method the set is for, with the hash it pins."""
    unit: str | None
    """The unit of its weighted scores and of its single score, or None for a set with no
    weights."""
    left_out: tuple[str, ...]
    """The indicators of `method` the set has no row for: they count in none of its scores, as
    EF 3.1's climate sub-indicators count in its total only."""


@dataclass(frozen=True)
class Provenance:
    """What a result was computed from, and how, so it can be reproduced and checked."""

    input_hash: str
    """sha256 of the database, background and method records the result was computed from, and
    for a product of a study of the study's own records."""
    engine_version: str
    method: str
    """The method the background was loaded with. Several methods used together are named by
    their ids joined by `+`: `EF-3.1+IPCC-2021`."""
    method_version: str
    method_hash: str
    """The hash of the method's content, as a release records it. For several methods, the hash
    of their hashes."""
    indicator: str
    indicator_method: MethodRef
    """The method that has the indicator: `method` itself, or with several methods the one of
    them that defines it. Example: IPCC-2021 for climate-change-direct-land-use-change."""
    methods: tuple[MethodRef, ...]
    """Each method the background was loaded with, in order: one, or several used together."""
    pin: str
    """The background the database pins in database.yaml, as written: `toy-background@6864b7dbb65d`.
    For a background read from a release it was checked against that release's name and hash
    before the calculation."""
    solver: SolverSettings
    """How the background was factorized. The database's own processes use the same settings."""
    condition: float
    """The condition estimate of the factors the background was solved with, the ones kept: how
    much a score can move, relative to its own size, when the data moves (docs/calculation.md,
    Factorize). The calculation stops above `solver.condition_limit`."""
    database_condition: float
    """The same estimate for the block of the database's own processes, factorized for this
    result. The two blocks are coupled by the database's purchases, so the condition of the whole
    system is not the larger of them: each is checked against the limit on its own."""
    path: tuple[Step, ...]
    """The steps of every solve, in order: background scores, database scores, database supply,
    background supply."""
    residual: float
    """The largest residual of those solves, each row relative to the size of its terms."""
    exact_zeros: int
    """Background products whose score for this indicator is exactly 0 because nothing upstream
    emits a flow of it. They are set to 0 from the structure of A, not computed."""
    cancellation: Cancellation
    """The positive and negative parts of the score, one term per flow and process that runs
    for the result. `cancellation.cancels` flags a score near 0 because they cancel: -0.0005
    made of +1.5 and -1.5005 (docs/calculation.md, Factorize)."""
    releases: tuple[ReleaseRef, ...] = ()
    """The release the background was read from, by name and hash. Empty for a folder."""
    scenario: str | None = None
    """The named scenario of the study the result was computed in, or None for none."""
    update_route: str | None = None
    """For a scenario that changes the background: how its matrix was solved, and why. Example:
    `refactorized: 140 changed processes, above the crossover of 60`. None outside a scenario of
    that kind."""
    cut_offs: tuple[Issue, ...] = ()
    """The lines the database leaves out on purpose, in the order of its lines, each with its
    reason, criterion, share and the running cumulative share of its criterion."""
    used: tuple[str, ...] = ()
    """The databases the database uses, directly or not, by name: `("dyes",)`. Their records
    are part of `input_hash`."""
    used_cut_offs: tuple[Issue, ...] = ()
    """The lines the databases it uses leave out, database by database: each cumulative share is
    of the database the line belongs to, so `cut_off_shares` does not count them."""
    nw_set: SetRef | None = None
    """For an indicator of a normalisation and weighting set, such as `toy-nw:single-score`: the
    set, with its hash and the indicators of its method it leaves out. None for an indicator of
    a method."""
    study: StudyRef | None = None
    """For a product of a study: the study, saved or not, and its commit. None for a product
    of a database scored on its own."""
    database: DatabaseRef | None = None
    """For a product of a database scored on its own, `Database.calc`: the database and its
    commit. None for a product of a study, whose `study` names the commit and covers the
    databases it reaches."""

    @property
    def cut_off_shares(self) -> dict[CutOffCriterion, float]:
        """The cumulative share the database leaves out, by criterion. Example: the T-shirt's 0.01
        kg of packaging tape is 0.01 / 0.26 = 3.85% of its mass input: `{mass: 0.0385}`."""
        shares: dict[CutOffCriterion, float] = {}
        for issue in self.cut_offs:
            if issue.criterion is not None and issue.cumulative_share is not None:
                shares[issue.criterion] = issue.cumulative_share
        return shares


@dataclass(frozen=True)
class Contribution:
    """One process in a contribution tree: score = direct + the children's scores + rest.

    Example, the T-shirt's fabric: 0.25 kg × 3.7086 per kg = 0.92715, of which 0.5298 is
    direct and 0.39735 is the 0.75 kWh of electricity it buys. A waste line is positive too:
    0.05 kg of scraps sent × 1.2 per kg treated = 0.06.
    """

    process_id: str
    name: str
    location: str
    """The code of the process's location, as its record holds it: `GLO`, `RER`."""
    flow_id: str
    """The flow the process makes, or the waste it treats."""
    amount: float
    """Units of the flow bought, or sent to treatment, in its reference unit."""
    unit: str
    per_unit: float
    """Score per unit of the flow: per kg made, or per kg treated."""
    score: float
    """amount × per_unit."""
    direct: float
    """Direct emissions of the process for this amount, characterised."""
    rest: float
    """Purchases left out by `min_share` or `max_depth`, summed. At `max_depth` it is everything
    the process buys, which its score already holds: the tree prints no rest line there."""
    children: tuple["Contribution", ...]
    """Purchases kept, largest first."""
    copy_of: str | None = None
    """For the database's own copy of a background process, made by an override, the id of that
    process. The copy has its own id, `<database>:copy:<background id>`, and the override's lines
    in place of the background's. None for every other process."""
    database: str | None = None
    """For a process of a database that the database scored uses, that database's name: `dyes`
    for `dyes:reactive-dye`. None for the scored database's own processes and the
    background's."""
    _at_max_depth: bool = field(default=False, repr=False, compare=False)
    """True for a node at the tree's `max_depth`: a leaf, whose score holds what it buys."""

    def __str__(self) -> str:
        """A tree, one line per process with its share of this node's score, and a `rest` line
        for what `min_share` left out, so that every node visibly adds up. A node at `max_depth`
        is a leaf: its score holds what it buys, and no rest line is printed under it.

        Example, the T-shirt::

            1 unit of T-shirt: 1.19205 (100.0%), direct 0.00000
              0.25 kg of cotton fabric production: 0.92715 (77.8%), direct 0.52980
                0.75 kWh of electricity production: 0.39735 (33.3%), direct 0.39735
              0.5 kWh of electricity production: 0.26490 (22.2%), direct 0.26490

        A score of exactly 0 has no shares. The database's copy of a background process, made by
        an override, is marked `(local copy)`. A process of a database it uses shows its id, so a
        reader sees which database it belongs to: `reactive dye production (dyes:reactive-dye)`.
        """
        return "\n".join(self._lines(self.score))

    def _repr_pretty_(self, printer: Any, cycle: bool) -> None:
        """What a notebook shows for a bare `result.contributions()`: the tree, not the
        dataclass."""
        printer.text(str(self))

    def to_dataframe(self) -> "pd.DataFrame":
        """The tree as a pandas DataFrame, one row per process in the order it prints, this node
        first: `depth`, `process_id`, `name`, `location`, `amount`, `unit`, `score`, `share` (of
        this node's score), `direct` and `rest`. At each row, `direct`, `rest` and the scores of
        the rows one level below it, up to the next row at its level, add up to its `score`.

        Example, the T-shirt: `df = result.contributions().to_dataframe()` has 4 rows, the
        T-shirt at depth 0 with 1.19205, the fabric at depth 1 with 0.92715, its electricity at
        depth 2 with 0.39735, the T-shirt's own electricity at depth 1 with 0.2649. The first
        row is the total, so the table adds up as the tree does. Its inputs alone are
        `df[df.depth == 1]`: the fabric and the electricity, which add up to 1.19205. Needs
        pandas: `pip install 'headwater[pandas]'`.
        """
        pandas = _pandas()
        rows = [
            [
                depth,
                node.process_id,
                node.name,
                node.location,
                node.amount,
                node.unit,
                node.score,
                _ratio(node.score, self.score),
                node.direct,
                node.rest,
            ]
            for depth, node in self._walk()
        ]
        columns = ["depth", "process_id", "name", "location", "amount", "unit", "score"]
        return pandas.DataFrame(rows, columns=[*columns, "share", "direct", "rest"])

    def _walk(self, depth: int = 0) -> Iterator[tuple[int, "Contribution"]]:
        yield depth, self
        for child in self.children:
            yield from child._walk(depth + 1)

    def _lines(self, total: float, depth: int = 0) -> Iterator[str]:
        pad = "  " * depth
        name = _named(self.name, self.process_id, self.copy_of, self.database)
        yield (
            f"{pad}{self.amount:g} {self.unit} of {name}: "
            f"{_fixed(self.score)}{_share(self.score, total)}, direct {_fixed(self.direct)}"
        )
        for child in self.children:
            yield from child._lines(total, depth + 1)
        if self.rest != 0.0 and not self._at_max_depth:
            yield f"{pad}  rest: {_fixed(self.rest)}{_share(self.rest, total)}"


def _named(name: str, process_id: str, copy_of: str | None, database: str | None) -> str:
    """A process as `contributions` prints it: `electricity production (local copy)` for the
    database's own copy, `reactive dye production (dyes:reactive-dye)` for a process of a database
    it uses."""
    if database is not None:
        return f"{name} ({'local copy, ' if copy_of is not None else ''}{process_id})"
    return f"{name} (local copy)" if copy_of is not None else name


def _fixed(value: float) -> str:
    """Five decimals. The score of a waste treatment that emits nothing is -1 × 0, a negative
    zero: it prints as 0."""
    return f"{value + 0.0:.5f}"


def _share(value: float, total: float) -> str:
    return f" ({value / total:.1%})" if total != 0 else ""


def _ratio(value: float, total: float) -> float | None:
    """`value / total`, or None for a total of exactly 0: a share of nothing."""
    return value / total if total != 0 else None


def _pandas() -> Any:
    """The pandas module, or an ImportError that says how to install it."""
    try:
        import pandas
    except ImportError:
        raise ImportError(
            "a table needs pandas, which headwater does not install by default: "
            "pip install 'headwater[pandas]'"
        ) from None
    return pandas


def counted(n: int, noun: str, plural: str) -> str:
    """`1 process`, `3 processes`: how the objects of this package count when they print."""
    return f"{n:,} {noun if n == 1 else plural}"


@dataclass(frozen=True, eq=False)
class Locations:
    """The location of each process of a calculation, as codes: process k is in
    `codes[group[k]]`. Built once for a background, and stacked under a database's own processes,
    so that `where` does not sort every location again."""

    codes: tuple[str, ...]
    group: NDArray[np.intp]

    @classmethod
    def of(cls, locations: Sequence[str]) -> "Locations":
        """Example: `of(["GLO", "CN", "GLO"])` has codes CN and GLO, and groups 1, 0, 1."""
        codes, group = np.unique(np.asarray(locations, dtype=str), return_inverse=True)
        return cls(tuple(str(code) for code in codes), np.asarray(group, dtype=np.intp).ravel())

    def under(self, locations: Sequence[str]) -> "Locations":
        """`locations` first, for the database's own processes, then these. A code these do not
        have is added after theirs."""
        index = {code: k for k, code in enumerate(self.codes)}
        codes = list(self.codes)
        top: list[int] = []
        for location in locations:
            if location not in index:
                index[location] = len(codes)
                codes.append(location)
            top.append(index[location])
        return Locations(tuple(codes), np.concatenate([np.array(top, dtype=np.intp), self.group]))

    def __len__(self) -> int:
        return int(self.group.size)


@dataclass(frozen=True)
class Emitter:
    """A process, and the part of the score its own emissions make, for everything upstream that
    runs it.

    Example: for one T-shirt, electricity production runs 1.25 kWh, 0.5 for the T-shirt and 0.75
    for its fabric, and emits 0.5298 per kWh: 0.66225 of the 1.19205.
    """

    process_id: str
    name: str
    location: str
    amount: float
    """How much of its reference flow the process makes, or treats, for this score, in its
    reference unit: its runs times what one run makes. Example: 1.25 kWh of electricity."""
    unit: str
    score: float
    """The process's direct emissions for that amount, characterised."""
    copy_of: str | None = None
    """For the database's own copy of a background process, made by an override, the id of that
    process. None for every other process."""
    database: str | None = None
    """For a process of a database that the database scored uses, that database's name. None
    otherwise."""


@dataclass(frozen=True)
class Place:
    """A location, and the part of the score emitted there: its processes' parts, summed."""

    location: str
    """The code of the processes' location, as their records hold it, such as `GLO` or `CN`.
    A region is not split into the places it contains."""
    score: float
    processes: int
    """How many processes there emit something for this score."""


@dataclass(frozen=True)
class Where:
    """Where a score is emitted: by process and by location, largest first.

    Example, the T-shirt: of 1.19205, electricity production emits 0.66225 (1.25 kWh × 0.5298)
    and fabric production 0.52980 (0.25 kg × 2.1192), all in GLO. `contributions` says what the
    T-shirt buys; this says which processes, anywhere upstream, emit.

    Rows are kept up to `top`; the others are summed into `rest`, so the rows and the rest add up
    to `score` in each grouping. A process that emits nothing has no row and is not counted.
    """

    process_id: str
    """The process whose score is split: the database's process, or the background product."""
    name: str
    amount: float
    """Units of its reference flow, in its reference unit: 1."""
    unit: str
    score: float
    """The sum of every process's part: h · s, the forward road. It equals the score from the
    stored scores within the solve's tolerance."""
    processes: tuple[Emitter, ...]
    """Largest |score| first."""
    rest: float
    """The processes left out by `top`, summed."""
    rest_count: int
    locations: tuple[Place, ...]
    """Largest |score| first. Ties are in the order of the codes."""
    locations_rest: float
    """The locations left out by `top`, summed."""
    locations_rest_count: int

    def __str__(self) -> str:
        """Each process with how much it runs, its location, its part and its share, then each
        location. Example, the T-shirt::

            1 unit of T-shirt: 1.19205
            By process:
              1.25 kWh of electricity production (GLO): 0.66225 (55.6%)
              0.25 kg of cotton fabric production (GLO): 0.52980 (44.4%)
            By location:
              GLO: 1.19205 (100.0%), 2 processes

        A `rest` line shows what `top` left out. A score of exactly 0 has no shares.
        """
        total = self.score
        lines = [f"{self.amount:g} {self.unit} of {self.name}: {_fixed(total)}", "By process:"]
        for e in self.processes:
            where = f"local copy, {e.location}" if e.copy_of is not None else e.location
            if e.database is not None:
                where = f"{e.process_id}, {where}"
            lines.append(
                f"  {e.amount:g} {e.unit} of {e.name} ({where}): "
                f"{_fixed(e.score)}{_share(e.score, total)}"
            )
        lines += self._rest(self.processes, self.rest, self.rest_count, "process", "processes")
        lines.append("By location:")
        lines += [
            f"  {p.location}: {_fixed(p.score)}{_share(p.score, total)}, "
            f"{counted(p.processes, 'process', 'processes')}"
            for p in self.locations
        ]
        lines += self._rest(
            self.locations, self.locations_rest, self.locations_rest_count, "location", "locations"
        )
        return "\n".join(lines)

    def _rest(
        self, rows: tuple[object, ...], rest: float, count: int, noun: str, plural: str
    ) -> list[str]:
        if count:
            share = _share(rest, self.score)
            return [f"  rest: {_fixed(rest)}{share}, {counted(count, noun, plural)}"]
        return [] if rows else ["  none"]

    def _repr_pretty_(self, printer: Any, cycle: bool) -> None:
        """What a notebook shows for a bare `result.where()`: the text, not the dataclass."""
        printer.text(str(self))

    def to_dataframe(self) -> "pd.DataFrame":
        """The processes kept as a pandas DataFrame, largest first: `process_id`, `name`,
        `location`, `amount`, `unit`, `score` and `share` (of `score`). With `rest`, the scores
        add up to `score`. Needs pandas: `pip install 'headwater[pandas]'`.
        """
        pandas = _pandas()
        columns = ["process_id", "name", "location", "amount", "unit", "score", "share"]
        rows = [
            [
                e.process_id,
                e.name,
                e.location,
                e.amount,
                e.unit,
                e.score,
                _ratio(e.score, self.score),
            ]
            for e in self.processes
        ]
        return pandas.DataFrame(rows, columns=columns)


@dataclass(frozen=True)
class NotCharacterised:
    """An elementary flow that the method has no factor for, in any indicator.

    It counts 0 in every score. Example: nitrous oxide in `toy-climate`, which only knows carbon
    dioxide and methane. A flow that the method covers for some indicators is not listed: it
    counts 0 only for the others.
    """

    flow_id: str
    name: str
    processes: tuple[str, ...]
    """The processes that emit or take the flow, sorted. They may not all be part of a result."""
    in_database: bool = False
    """True when a process of a database the result reaches emits or takes the flow, not only
    the background. That database's `no_factor.csv`, or the study's, then has a row for it,
    which says why it counts 0."""
    reason: str | None = None
    """For a flow of a database, the reason its `no_factor.csv` row gives. None otherwise."""


@dataclass(frozen=True)
class Sensitivity:
    """One line of a process that a person can change, and how much the score moves with it.

    Example, fabric production's line e02, 3 kWh of electricity per kg of fabric: one more kWh
    per kg adds 0.13245 to the T-shirt (sensitivity), and 1% more adds 1/3 % (elasticity). An
    emission has one too: one more kg of CO2 per kWh of electricity adds 1.25.
    """

    process_id: str
    name: str
    """The process's name: `T-shirt`, `electricity production`."""
    line_id: str
    role: Role
    flow_id: str
    supplier_id: str | None
    """The process bought from, or sent to. None for an emission or a resource."""
    supplier_name: str | None
    """Its name: `cotton fabric production`. None for an emission or a resource."""
    amount: float
    """Per run of the process, in the flow's reference unit, as in the file."""
    unit: str
    sensitivity: float
    """Change of the score per one more unit of `amount`."""
    elasticity: float
    """% change of the score per 1% change of `amount`."""
    copy_of: str | None = None
    """For a line of the database's own copy of a background process, made by an override, the
    id of that process. The copy's own id is `<database>:copy:<background id>`."""

    def __repr__(self) -> str:
        """One line: `Sensitivity(tshirt:tshirt e02, T-shirt: input 0.25 kg, elasticity 0.7778,
        sensitivity 3.7086)`. `supplier_name` and `flow_id` say what the line buys or emits."""
        return (
            f"Sensitivity({self.process_id} {self.line_id}, {self.name}: {self.role.value} "
            f"{self.amount:g} {self.unit}, elasticity {self.elasticity:.4g}, "
            f"sensitivity {self.sensitivity:.6g})"
        )


@dataclass(frozen=True)
class ParameterSensitivity:
    """A named parameter of the database, and how much the score moves with it.

    Example: `fabric_kg`, 0.25 kg of fabric per T-shirt. One more kg adds 3.7086 to the score
    (sensitivity), and 1% more adds 0.7778% (elasticity).
    """

    parameter_id: str
    """The name, or `<process id>/<name>` for a parameter of one process."""
    name: str
    scope: str | None
    """The process the parameter belongs to, or None for the whole database."""
    value: float
    """As the result was computed: in a scenario, the scenario's value."""
    formula: str | None
    """The formula the value comes from, or None for a value."""
    unit: str | None
    sensitivity: float
    """Change of the score per one more unit of the value. For a parameter that is a formula,
    the parameters it is computed from are held as they are, as a scenario setting its value
    would."""
    elasticity: float
    """% change of the score per 1% change of the value."""

    def __repr__(self) -> str:
        """One line: `ParameterSensitivity(fabric_kg = 0.25 kg, elasticity 0.7778, sensitivity
        3.7086)`. A parameter that is a formula shows it: `fabric_gross = fabric_kg * (1 + loss)
        = 0.2625 kg`."""
        formula = f"{self.formula} = " if self.formula is not None else ""
        unit = f" {self.unit}" if self.unit else ""
        return (
            f"ParameterSensitivity({self.parameter_id} = {formula}{self.value:g}{unit}, "
            f"elasticity {self.elasticity:.4g}, sensitivity {self.sensitivity:.6g})"
        )


@dataclass(frozen=True)
class FormulaLine:
    """A line of a calculation whose amount is a formula, and where it sits in the system."""

    label: str
    """The line, as messages name it: `tshirt:tshirt line e02`."""
    formula: str
    scope: str
    """The process the line belongs to: the parameters of that process are visible to it."""
    part: int
    """The index of the system's part that holds the line."""
    biosphere: bool
    """True for an emission or a resource, in B. Else a line of A."""
    position: int
    """The line's position in its part's `lines`, or `emissions`."""
    factor: float
    """The amount in the reference unit of the flow, per unit of what the formula gives: 0.001
    for a line written in g of a flow counted in kg."""


class Result:
    """The score of one unit of what a database's process makes, for one indicator.

    Example: the T-shirt scores 1.19205 kg CO2-eq. `contributions` says why, `where` which processes
    emit it and `sensitivity` which lines matter most, all from what was solved here, with no new
    solve.
    """

    def __init__(
        self,
        *,
        score: float,
        forward_score: float,
        indicator: str,
        unit: str,
        provenance: Provenance,
        system: System,
        uncovered: tuple[NotCharacterised, ...] = (),
        locations: Locations,
        parameters: ParameterSet | None = None,
        formula_lines: tuple[FormulaLine, ...] = (),
        amount: float = 1.0,
    ) -> None:
        """`locations` holds the location of each process of `system`, in its order."""
        if len(locations) != len(system.ids):
            raise ValueError(
                f"locations must name one location per process: {len(system.ids)}, "
                f"got {len(locations)}"
            )
        self.score = score
        """x · f: the backward road, from the stored scores."""
        self.forward_score = forward_score
        """h · s from one forward solve: the same number by the other road."""
        self.indicator = indicator
        self.unit = unit
        self.amount = amount
        """How much of what `process_id` makes is scored: 1, or a study's amount of it."""
        self.provenance = provenance
        self._system = system
        self._uncovered = uncovered
        self._parameters = parameters or ParameterSet()
        self._formula_lines = formula_lines
        self._locations = locations

    @cached_property
    def not_characterised(self) -> tuple[NotCharacterised, ...]:
        """Flows that this result reaches and the method has no factor for in any indicator,
        sorted by id. They count 0 in the score, and are listed here so that nothing is dropped
        without saying so. A flow that the method covers for another indicator counts 0 here
        too, but is not listed. A flow the databases' own processes emit or take is
        `in_database`, with the reason of its row in a `no_factor.csv`, and is listed once with the
        background's processes that use it too.

        Computed on first use from the supply already solved for this result: a process is
        reached when it runs for the result. Each entry names only the processes that run, so it
        can be shorter than the same flow in `Background.not_characterised`. The database's copy
        of a background process is named by its own id when it runs.
        """
        s = self._system
        reached = {pid for pid, runs in zip(s.ids, s.s, strict=True) if runs != 0.0}
        copies: dict[str, list[str]] = {}
        for index, background_id in s.copies.items():
            if s.ids[index] in reached:
                copies.setdefault(background_id, []).append(s.ids[index])
        found: list[NotCharacterised] = []
        for entry in self._uncovered:
            running = [p for p in entry.processes if p in reached]
            running += [c for p in entry.processes for c in copies.get(p, ())]
            processes = tuple(sorted(running))
            if processes:
                found.append(replace(entry, processes=processes))
        return tuple(found)

    def __repr__(self) -> str:
        p = self.provenance
        text = (
            f"Result({'' if self.amount == 1 else f'{self.amount:g} × '}{self.process_id} = "
            f"{self.score:.6g} {self.unit}, "
            f"indicator {self.indicator}, method {p.method} {p.method_version}"
        )
        if p.scenario is not None:
            text += f", scenario {p.scenario}"
        if p.nw_set is not None:
            text += f", set {p.nw_set.id} {p.nw_set.version}"
            if p.nw_set.left_out:
                n = len(p.nw_set.left_out)
                text += f", {n} {'indicator' if n == 1 else 'indicators'} left out"
        if p.cancellation.cancels:
            text += f", cancels: {p.cancellation.positive:+.6g} and {p.cancellation.negative:+.6g}"
        if self.not_characterised:
            n = len(self.not_characterised)
            text += f", {n} {'flow' if n == 1 else 'flows'} not characterised"
        return text + ")"

    @property
    def process_id(self) -> str:
        """The process scored: one unit of what it makes."""
        return self._system.ids[self._system.target]

    def contributions(self, *, min_share: float = 0.03, max_depth: int = 3) -> Contribution:
        """Contribution analysis: what the score is made of, direct emissions plus amount × score
        of each purchase, as a tree.

        Purchases below `min_share` of the score, and everything below `max_depth`, are summed
        into `rest`, so every node still adds up to its score. `str(tree)` prints it with
        shares.
        """
        s = self._system
        with ids_in_errors(s.ids):
            root = calc.explain(
                s.A,
                s.h,
                s.x,
                s.target,
                amount=float(s.f[s.target]),
                min_share=min_share,
                max_depth=max_depth,
            )
        return contribution_of(root, s, s.x, self._locations, s.copies, s.used, max_depth=max_depth)

    def direct_and_upstream(self) -> tuple[float, float]:
        """The score split as Brightway splits it: what the database's process that makes the
        reference flow emits itself, loops included, and the rest, upstream.

        `direct` is that process's own row of `where(top=None)`, and `upstream` is `score` minus
        `direct`. The T-shirt emits nothing itself: 0 direct and 1.19205 upstream. No solve: it
        reads what `calc` solved.
        """
        s = self._system
        direct = float(s.h[s.target] * s.s[s.target])
        return direct, self.score - direct

    def where(self, *, top: int | None = 10) -> Where:
        """Where the score is emitted: the part of each process that runs for the result, its
        own direct emissions times how much it runs, and the same parts summed by location.

        The `top` largest processes and locations are kept, by size, and the others summed into
        a rest. `top=None` keeps every process that emits something. Example, the T-shirt:
        electricity production 0.66225 and fabric production 0.52980, all in GLO.
        """
        return where_of(self._system, self._locations, top=top)

    def sensitivity(
        self, *, top: int | None = None, include_reference: bool = False
    ) -> tuple[Sensitivity, ...]:
        """Sensitivity analysis: how much the score moves with each line a person can change,
        inputs, wastes, emissions and resources, largest elasticity first, in the database and in
        the background it reaches.

        The reference line of a process, what it makes per run, is left out: it is how the
        process is defined, not a choice. `include_reference` keeps it. Lines with no matrix
        entry have no sensitivity: a recyclable line, an amount of zero, or lines that cancel each
        other out.
        """
        return rank_sensitivity(
            self._system, self.score, top=top, include_reference=include_reference
        )

    def parameters(self, *, top: int | None = None) -> tuple[ParameterSensitivity, ...]:
        """Every named parameter of the database, with its effect on the score, largest elasticity
        first. A parameter that no line uses has a sensitivity of 0.

        Worked out by the chain rule from what was solved here, with no new solve: the
        sensitivity of each line whose amount is a formula, times the exact derivative of the
        formula with respect to each parameter it uses, then back through the formulas of the
        parameters themselves. Example: `fabric_gross = fabric_kg * (1 + loss)` passes 1.05
        times its own sensitivity on to `fabric_kg`.

        Raises CalculationError naming the line or the parameter whose formula has no finite
        derivative where it is used, such as `sqrt(x)` at 0, and when the score is 0, as
        elasticities are relative to it.
        """
        if top is not None and top < 0:
            raise ValueError(f"top must be zero or positive, got {top}")
        s = self._system
        if self._parameters and self.score == 0:
            raise CalculationError(
                f"score of {s.ids[s.target]} is zero, so elasticities are undefined"
            )
        sensitivity = parameter_sensitivities(
            self._parameters, self._formula_lines, line_sensitivities(s, self._formula_lines)
        )
        found = [
            ParameterSensitivity(
                parameter_id=p.id,
                name=p.name,
                scope=p.scope,
                value=self._parameters.values[p.id],
                formula=p.formula,
                unit=p.unit,
                sensitivity=sensitivity[p.id],
                elasticity=sensitivity[p.id] * self._parameters.values[p.id] / self.score,
            )
            for p in self._parameters
        ]
        found.sort(key=lambda p: (-abs(p.elasticity), p.parameter_id))
        return tuple(found if top is None else found[:top])


def line_sensitivities(s: System, lines: Sequence[FormulaLine]) -> Vector:
    """Change of the score per one more unit of what each line's formula gives.

    A line of A at (row, col) moves the score by -x[row] × s[col] × sign per unit of its amount,
    and an emission by c[flow] × s[col], whether its cell holds anything or not: a line of
    amount 0 still has a derivative. Then times the line's unit factor.
    """
    found = np.zeros(len(lines))
    for k, line in enumerate(lines):
        part = s.parts[line.part]
        block, at, offset = part.block, line.position, part.offset
        if line.biosphere:
            e = block.emissions
            found[k] = part.c[e.row[at]] * s.s[offset + e.col[at]]
        else:
            t = block.lines
            found[k] = -s.x[offset + t.row[at]] * s.s[offset + t.col[at]] * t.sign[at]
        found[k] *= line.factor
    return found


def parameter_sensitivities(
    parameters: ParameterSet, lines: Sequence[FormulaLine], line_sensitivity: Vector
) -> dict[str, float]:
    """The sensitivity of the score to each parameter, by id, by the chain rule.

    Each line passes its sensitivity on to the parameters its formula uses, times the formula's
    derivative. Then each parameter that is a formula passes its own on to the parameters it
    uses, last first: parameters are in dependency order, so every user of a parameter has
    passed its share on before the parameter does.
    """
    values = parameters.values
    found = dict.fromkeys((p.id for p in parameters), 0.0)

    def pass_on(text: str, scope: str | None, amount: float, what: str) -> None:
        visible = parameters.visible(scope)
        formula = Formula.parse(text)
        at = {name: values[visible[name]] for name in formula.names if name in visible}
        try:
            derivatives = formula.derivatives(at)
        except FormulaError as error:
            raise CalculationError(
                f"the sensitivity of {what} cannot be computed: {error}"
            ) from None
        for name, derivative in derivatives.items():
            found[visible[name]] += amount * derivative

    for line, sensitivity in zip(lines, line_sensitivity.tolist(), strict=True):
        if sensitivity != 0.0:
            pass_on(line.formula, line.scope, sensitivity, line.label)
    for parameter in reversed(parameters.parameters):
        if parameter.formula is not None and found[parameter.id] != 0.0:
            pass_on(
                parameter.formula, parameter.scope, found[parameter.id], f"parameter {parameter.id}"
            )
    return found


class _Labelled(Protocol):
    """What names the processes of a calculation: a `System`, or the `Block` of a background."""

    @property
    def ids(self) -> tuple[str, ...]: ...
    @property
    def names(self) -> tuple[str, ...]: ...
    @property
    def flow_ids(self) -> tuple[str, ...]: ...
    @property
    def units(self) -> tuple[str, ...]: ...
    @property
    def reference_sign(self) -> Vector: ...


def contribution_of(
    node: Node,
    labels: _Labelled,
    x: Vector,
    locations: Locations,
    copies: Mapping[int, str] | None = None,
    used: Mapping[int, str] | None = None,
    *,
    max_depth: int,
    depth: int = 0,
) -> Contribution:
    """The "why" tree of `calc.explain` with its processes named and placed. `x` holds the
    stored scores, `locations` the location of each process, and `copies` the background id of
    each of the database's copies, by index. `max_depth` is the one `explain` was given: the nodes
    at that depth are leaves."""
    copies = copies or {}
    used = used or {}
    # calc counts a waste sent as a negative amount of a product with a negative score. The
    # sign of one unit of the reference turns both positive: kg sent and burden per kg.
    sign = float(labels.reference_sign[node.index])
    return Contribution(
        process_id=labels.ids[node.index],
        name=labels.names[node.index],
        location=locations.codes[int(locations.group[node.index])],
        flow_id=labels.flow_ids[node.index],
        amount=node.amount * sign,
        unit=labels.units[node.index],
        per_unit=float(x[node.index]) * sign,
        score=node.score,
        direct=node.direct,
        rest=node.rest,
        children=tuple(
            contribution_of(
                child, labels, x, locations, copies, used, max_depth=max_depth, depth=depth + 1
            )
            for child in node.children
        ),
        copy_of=copies.get(node.index),
        database=used.get(node.index),
        _at_max_depth=depth == max_depth,
    )


def where_of(s: System, locations: Locations, *, top: int | None) -> Where:
    """Where the score of `s` is emitted, by process and by location. `locations` holds the
    location of each process of `s`, in its order.

    Each process's part is h × s from what was solved. Only the `top` rows of each grouping
    become objects.
    """
    if top is not None and top < 0:
        raise ValueError(f"top must be zero or positive, got {top}")
    parts = calc.where(s.h, s.s)
    kept = largest(parts, top)
    sums, counts = sums_by_group(parts, locations.group, len(locations.codes))
    places = largest(sums, top)
    rows: list[int] = kept.index.tolist()
    groups: list[int] = places.index.tolist()
    made = _reference_amounts(s)
    unknown = [s.ids[j] for j in rows if not np.isfinite(made[j])]
    if unknown:
        raise CalculationError(
            f"no reference line, so what they make is unknown, in processes: {', '.join(unknown)}"
        )
    target = s.target
    return Where(
        process_id=s.ids[target],
        name=s.names[target],
        amount=float(s.f[target] * s.reference_sign[target]),
        unit=s.units[target],
        score=float(parts.sum()),
        processes=tuple(
            Emitter(
                process_id=s.ids[j],
                name=s.names[j],
                location=locations.codes[int(locations.group[j])],
                amount=float(s.s[j] * made[j]),
                unit=s.units[j],
                score=float(parts[j]),
                copy_of=s.copies.get(j),
                database=s.used.get(j),
            )
            for j in rows
        ),
        rest=kept.rest,
        rest_count=kept.rest_count,
        locations=tuple(
            Place(location=locations.codes[k], score=float(sums[k]), processes=int(counts[k]))
            for k in groups
        ),
        locations_rest=places.rest,
        locations_rest_count=places.rest_count,
    )


def _reference_amounts(s: System) -> Vector:
    """What one run of each process makes, or treats, from its reference line: 1 kWh for a
    toy electricity production, 1 kg treated for a treatment. NaN for a process with none."""
    made = np.full(len(s.ids), np.nan)
    for part in s.parts:
        lines = part.block.lines
        reference = lines.role == REFERENCE_CODE
        made[lines.col[reference] + part.offset] = lines.amount[reference]
    return made


def rank_sensitivity(
    s: System, score: float, *, top: int | None, include_reference: bool
) -> tuple[Sensitivity, ...]:
    """Every line a person can change in `s`, with its effect on `score`, largest elasticity
    first. Lines with the same elasticity keep the order of the database's lines, the
    background's, then the database's emissions and the background's.

    The effects of all lines are worked out as arrays and ranked with one stable sort. A
    `Sensitivity` is made only for the `top` lines returned. Times are in
    docs/appendix/benchmarks.md, under "Study queries on a release".
    """
    if top is not None and top < 0:
        raise ValueError(f"top must be zero or positive, got {top}")
    if score == 0:
        raise CalculationError(f"score of {s.ids[s.target]} is zero, so elasticities are undefined")
    A = s.A
    elasticity: list[Vector] = []
    positions: list[NDArray[np.intp]] = []
    effects: list[calc.LineEffects] = []
    for part in s.parts:
        lines = part.block.lines
        with ids_in_errors(s.ids):
            found = calc.line_levers(
                A,
                s.x,
                s.s,
                s.f,
                lines.row + part.offset,
                lines.col + part.offset,
                lines.sign,
                lines.amount,
            )
        keep = (
            found.present
            if include_reference
            else found.present & (lines.role != ROLE_ORDER.index(Role.reference))
        )
        effects.append(found)
        positions.append(np.flatnonzero(keep))
        elasticity.append(found.elasticity[positions[-1]])
    for part in s.parts:
        emissions = part.block.emissions
        runs = s.s[part.offset : part.offset + part.block.B.shape[1]]
        with ids_in_errors(s.ids[part.offset :]):
            found = calc.emission_line_levers(
                part.block.B,
                part.c,
                runs,
                score,
                emissions.row,
                emissions.col,
                emissions.amount,
            )
        effects.append(found)
        positions.append(np.flatnonzero(found.present))
        elasticity.append(found.elasticity[positions[-1]])

    group = np.concatenate([np.full(len(p), g, dtype=np.intp) for g, p in enumerate(positions)])
    position = np.concatenate(positions)
    order = np.argsort(-np.abs(np.concatenate(elasticity)), kind="stable")
    chosen = order if top is None else order[:top]
    ranked: list[Sensitivity | None] = [None] * len(chosen)
    n_parts = len(s.parts)
    for g, part in enumerate(s.parts * 2):
        where = np.flatnonzero(group[chosen] == g)
        if not where.size:
            continue
        at = position[chosen[where]]
        found = effects[g]
        made = (
            _tech_lines(s, part, at, found) if g < n_parts else _emission_lines(s, part, at, found)
        )
        for k, line in zip(where, made, strict=True):
            ranked[int(k)] = line
    return tuple(line for line in ranked if line is not None)


def _line_ids(ids: pa.StringArray, at: NDArray[np.intp]) -> list[str]:
    """The ids of the lines at positions `at`. None is empty: loading refused a line with no id."""
    return cast(list[str], ids.take(pa.array(at, pa.int64())).to_pylist())


def _tech_lines(
    s: System, part: Part, at: NDArray[np.intp], found: calc.LineEffects
) -> Iterator[Sensitivity]:
    lines = part.block.lines
    line_ids = _line_ids(lines.line_id, at)
    for k, index in enumerate(at.tolist()):
        row, col = int(lines.row[index]) + part.offset, int(lines.col[index]) + part.offset
        has_supplier = bool(lines.has_supplier[index])
        yield Sensitivity(
            process_id=s.ids[col],
            name=s.names[col],
            line_id=line_ids[k],
            role=ROLE_ORDER[int(lines.role[index])],
            flow_id=s.flow_ids[row],
            supplier_id=s.ids[row] if has_supplier else None,
            supplier_name=s.names[row] if has_supplier else None,
            amount=float(lines.amount[index]),
            unit=s.units[row],
            sensitivity=float(found.sensitivity[index]),
            elasticity=float(found.elasticity[index]),
            copy_of=s.copies.get(col),
        )


def _emission_lines(
    s: System, part: Part, at: NDArray[np.intp], found: calc.LineEffects
) -> Iterator[Sensitivity]:
    block = part.block
    emissions = block.emissions
    line_ids = _line_ids(emissions.line_id, at)
    for k, index in enumerate(at.tolist()):
        row = int(emissions.row[index])
        col = int(emissions.col[index]) + part.offset
        yield Sensitivity(
            process_id=s.ids[col],
            name=s.names[col],
            line_id=line_ids[k],
            role=ROLE_ORDER[int(emissions.role[index])],
            flow_id=block.flows[row],
            supplier_id=None,
            supplier_name=None,
            amount=float(emissions.amount[index]),
            unit=block.flow_units[row],
            sensitivity=float(found.sensitivity[index]),
            elasticity=float(found.elasticity[index]),
            copy_of=s.copies.get(col),
        )
