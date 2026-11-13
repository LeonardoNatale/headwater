from collections.abc import Iterable
from dataclasses import dataclass
from itertools import chain
from pathlib import Path

from headwater.model import (
    DEFAULT_UNITS,
    Catalog,
    CutOff,
    Database,
    Flow,
    FlowProperty,
    Issue,
    Override,
    ParameterChange,
    ParameterSet,
    Pin,
    Process,
    Product,
    Scenario,
    Study,
    Unit,
    Units,
)
from headwater.study._no_factor import NoFactor


@dataclass(frozen=True)
class ProcessFolder:
    """What a folder of process files holds, read into records.

    `units` are the extra units of the folder's own `units.csv`, not the built-in ones.
    """

    path: Path
    namespace: str
    processes: tuple[Process, ...]
    flows: tuple[Flow, ...]
    flow_properties: tuple[FlowProperty, ...]
    units: tuple[Unit, ...]


@dataclass(frozen=True)
class DatabaseFolder(ProcessFolder):
    """A database folder: `database.yaml`, its processes, and what else it says.

    `processes` have their formula amounts worked out with the database's `parameters`, and the
    lines named in `cut_offs` marked as cut off. `overrides` are checked against the database's
    own processes only. `override_sources` maps the label of each overridden line
    (`toy:fabric-production line e02`) to the file and row it was written at, as sorted pairs.
    `no_factor` holds the rows of `no_factor.csv`, in the order of the file: validation checks
    them against the method.

    `used` holds every database this one uses, directly or not, each once, sorted by name: the
    T-shirt that uses `dyes`, which uses `chemicals`, has both. Each one is read as a database of
    its own, and its own `used` is empty, since the whole chain is here.
    """

    database: Database
    parameters: ParameterSet = ParameterSet()
    overrides: tuple[Override, ...] = ()
    cut_offs: tuple[Issue, ...] = ()
    override_sources: tuple[tuple[str, str], ...] = ()
    no_factor: tuple[NoFactor, ...] = ()
    used: tuple["DatabaseFolder", ...] = ()

    def override_source(self, label: str) -> str | None:
        """The file and row an overridden line was written at, such as `overrides/mill.csv:2`."""
        return dict(self.override_sources).get(label)

    @property
    def chain(self) -> tuple["DatabaseFolder", ...]:
        """This database, then every database it uses: what one calculation of it is made of."""
        return (self, *self.used)


def owner_of(chain: Iterable["DatabaseFolder"]) -> dict[str, str]:
    """The name of the database each process of `chain` belongs to, by process id."""
    return {p.id: folder.database.name for folder in chain for p in folder.processes}


def in_used(name: str, location: str | None) -> str:
    """Where a file of the used database `name` is, seen from the database that uses it, a folder
    next to it: `exchanges.csv:3` of `dyes` is `../dyes/exchanges.csv:3`."""
    return f"../{name}/{location}" if location else f"../{name}"


def build_catalog(*folders: ProcessFolder) -> Catalog:
    """One catalog over several folders, for example a database and its background.

    Raises `InvalidDataError` when two folders define the same id in different ways.
    """
    units = DEFAULT_UNITS
    for folder in folders:
        units = units.merge(Units(folder.units))
    return Catalog(
        flows=chain.from_iterable(f.flows for f in folders),
        processes=chain.from_iterable(f.processes for f in folders),
        properties=chain.from_iterable(f.flow_properties for f in folders),
        units=units,
    )


@dataclass(frozen=True)
class StudyFolder:
    """A study, read from its folder or built in memory: the products it scores with their
    amounts, the methods, the scenarios and the `no_factor.csv` rows it adds, and the databases
    its products are in.

    `record` is study.yaml as read, and None for a study in memory. `databases` holds the
    database of each product that is in one, each once, in the order of the products, with the
    databases it uses as `used`. A product in none of them is a product of the background.
    `scenarios` are checked against the databases they reach.
    """

    path: Path | None
    record: Study | None
    name: str | None
    products: tuple[Product, ...]
    methods: tuple[str, ...]
    cut_off: CutOff | None
    background: Pin | None
    databases: tuple[DatabaseFolder, ...] = ()
    scenarios: tuple[Scenario, ...] = ()
    no_factor: tuple[NoFactor, ...] = ()

    def database_of(self, process_id: str) -> DatabaseFolder | None:
        """The database whose own process `process_id` is, or None for a product of the
        background."""
        return next(
            (d for d in self.databases if any(p.id == process_id for p in d.processes)), None
        )

    @property
    def reached(self) -> dict[str, DatabaseFolder]:
        """Every database the products reach, those they use included, each once, by name."""
        found: dict[str, DatabaseFolder] = {}
        for database in self.databases:
            for folder in database.chain:
                found.setdefault(folder.database.name, folder)
        return found

    def changes_background(self, chosen: Scenario) -> bool:
        """Whether `chosen` changes a line of the background: a process of no database the
        study reaches."""
        ours = {p.id for f in self.reached.values() for p in f.processes}
        return any(c.process_id not in ours for c in chosen.line_changes)

    def scenario_for(self, chosen: Scenario, database: DatabaseFolder) -> Scenario:
        """`chosen` as a product of `database` sees it: without the changes to the other
        databases the study reaches. Example: a study of a T-shirt and a bag, in two databases
        that do not use each other; a change to the bag's line leaves the T-shirt as it is."""
        mine = {f.database.name for f in database.chain}
        others = [f for name, f in self.reached.items() if name not in mine]
        names = {f.database.name for f in others}
        theirs = {p.id for f in others for p in f.processes}
        kept = tuple(
            c
            for c in chosen.changes
            if not (
                c.database in names if isinstance(c, ParameterChange) else c.process_id in theirs
            )
        )
        if len(kept) == len(chosen.changes):
            return chosen
        return Scenario.model_construct(
            name=chosen.name, description=chosen.description, changes=kept
        )
