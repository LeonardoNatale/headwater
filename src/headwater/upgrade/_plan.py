"""Planning an upgrade: every id of the old release a database or a study names, its successor,
and what stops the upgrade from being written."""

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from headwater.link import SupplierMapping, read_supplier_mapping
from headwater.model import (
    Exchange,
    HeadwaterError,
    InvalidDataError,
    Pin,
    Problem,
    Process,
)
from headwater.release import Release, ReleaseKind, find
from headwater.study import DatabaseFolder, StudyFolder, read_database, read_study, users_of
from headwater.study._uses import uses_on_disk
from headwater.study.reader import read_uses
from headwater.upgrade._datasets import Dataset, Datasets
from headwater.upgrade._flags import Flag, market_flag, row_flag
from headwater.upgrade._moves import Moves
from headwater.upgrade._rules import Correspondence, Match, Rule, Successors, reason_rows
from headwater.upgrade._write import write, write_studies

type Given = DatabaseFolder | StudyFolder | Path | str


class UpgradeError(HeadwaterError):
    """An upgrade that cannot be written: a supplier no rule maps, or a problem it names."""


@dataclass(frozen=True)
class Plan:
    """What `headwater upgrade` does to databases and studies, before anything is written.

    Example, the toy T-shirt from toy-1 to toy-2 with the reviewed rows for dye and tape: four
    suppliers, all mapped (two by a mapping row, one by the same id, one by the same dataset),
    a RoW flag on the fabric and a market flag on the electricity, and no problem.
    """

    old: Datasets
    new: Datasets
    databases: tuple[DatabaseFolder, ...]
    """The databases upgraded together: those given, those the studies given reach, and every
    database they use."""
    matches: tuple[Match, ...]
    """One per old process the databases and studies name, in the order they name them."""
    flags: tuple[Flag, ...]
    problems: tuple[Problem, ...]
    """What stops the upgrade besides an unmapped supplier: an override or scenario line with no
    single match, a flow the new release does not hold, a database or a study that reaches one
    of them and stays."""
    warnings: tuple[Problem, ...]
    moves: Moves
    studies: tuple[StudyFolder, ...] = ()
    """The studies given: their scenarios, and the products and pin of a study of release
    products, move with the databases."""

    @property
    def unmapped(self) -> tuple[Match, ...]:
        return tuple(m for m in self.matches if m.rule is Rule.unmapped)

    @property
    def complete(self) -> bool:
        """True when every supplier is mapped and nothing else stops the upgrade."""
        return not self.unmapped and not self.problems

    def counts(self) -> dict[Rule, tuple[int, int]]:
        """Suppliers and places per rule, every rule listed: `{Rule.same_id: (1, 1), ...}`."""
        found = {rule: (0, 0) for rule in Rule}
        for m in self.matches:
            suppliers, lines = found[m.rule]
            found[m.rule] = (suppliers + 1, lines + len(m.lines))
        return found

    def write(self, into: Path | None = None) -> tuple[Path, ...]:
        """Write the upgrade into the files of the databases and studies, in place, or into a
        copy of each folder in `into`, and return the files that changed. Only the cells that
        name the old release change. Raises `UpgradeError`, and writes nothing, unless
        `complete`."""
        if not self.complete:
            what = [f"{m.old.label}: {m.reason}" for m in self.unmapped]
            what += [str(p) for p in self.problems]
            raise UpgradeError(
                f"The upgrade to {self.new.pin} cannot be written: "
                + "; ".join(what)
                + ". Nothing was written."
            )
        return (
            *write(self.databases, self.moves, into),
            *write_studies(self.studies, self.moves, into),
        )


def plan(
    folders: Given | Sequence[Given],
    to: Release | str,
    *,
    mapping: SupplierMapping | Path | str | None = None,
    correspondence: Correspondence | None = None,
) -> Plan:
    """Plan the upgrade of `folders`, databases and studies, to the release `to`. The databases
    a study's products are in move with it, and so does every database they use.

    `to` is a release or its pin, `name@hash` with at least 12 characters of the hash. The
    databases, and a study of release products, must all pin one other release, the one they
    move from. `mapping` is a supplier
    mapping file of reviewed rows, keyed by the old process's id or by its name, product,
    location and unit. Each supplier is mapped by the first rule that decides it: a mapping
    row, the same id, a correspondence row, the same dataset; else it is unmapped, with
    suggestions that are never applied.

    Raises `InvalidDataError` when the folders pin no release in the cache, pin different
    ones, or already pin `to`, and for a mapping file that is wrong or gives a reason row.
    """
    given = [folders] if isinstance(folders, DatabaseFolder | StudyFolder | Path | str) else folders
    studies = tuple(_read_study(g) for g in given if _is_study(g))
    databases = [g for g in given if not _is_study(g)]
    databases += [d for s in studies for d in s.databases]
    folders = _with_used(databases)
    old_release = _pinned(folders, studies)
    new_release = to if isinstance(to, Release) else find(to)
    for release in (old_release, new_release):
        if release.kind is not ReleaseKind.source:
            raise InvalidDataError(
                [
                    Problem(
                        "indicators-release",
                        f"release {release.name}",
                        "holds scores only, with no processes to move to. Name its source "
                        f"release {release.parent}.",
                    )
                ]
            )
    if new_release.hash == old_release.hash:
        raise InvalidDataError(
            [
                Problem(
                    "already-upgraded",
                    _first_name(folders, studies),
                    f"already pins {new_release.name}@{new_release.hash[:12]}",
                )
            ]
        )
    rows = read_supplier_mapping(mapping) if isinstance(mapping, Path | str) else mapping
    if rows is not None:
        reason_rows(rows)
    old, new = Datasets(old_release), Datasets(new_release)
    successors = Successors(old, new, mapping=rows, correspondence=correspondence)
    places = named(folders, old)
    for i, where in named_by_studies(studies, old).items():
        places.setdefault(i, []).extend(where)
    matches = tuple(successors.match(old.by_id[i], tuple(where)) for i, where in places.items())
    found = {m.old.id: m.new for m in matches if m.new is not None}
    moves, problems = moves_for(folders, old, new, found, studies)
    return Plan(
        old=old,
        new=new,
        databases=folders,
        matches=matches,
        flags=_flags(matches, old, new),
        problems=(*problems, *_left_behind(folders, studies, old, new)),
        warnings=successors.unused(),
        moves=moves,
        studies=studies,
    )


def _is_study(item: Given) -> bool:
    if isinstance(item, StudyFolder):
        return True
    if isinstance(item, DatabaseFolder):
        return False
    return (Path(item) / "study.yaml").is_file()


def _read_study(item: Given) -> StudyFolder:
    return item if isinstance(item, StudyFolder) else read_study(Path(str(item)))


def _first_name(databases: Sequence[DatabaseFolder], studies: Sequence[StudyFolder]) -> str:
    if databases:
        return f"database {databases[0].database.name}"
    return f"study {studies[0].name}"


def _scenario_file(study: StudyFolder, scenario: str) -> str:
    """`study tshirt-footprint scenarios/clean-grid.yaml`: where a scenario's change is."""
    return f"study {study.name} scenarios/{scenario}.yaml"


def named_by_studies(studies: Iterable[StudyFolder], old: Datasets) -> dict[str, list[str]]:
    """Every process of the old release that the studies name, with the places that name it:
    a product of the release, the line a scenario changes, the supplier a scenario sets, the
    process whose parameter a scenario sets."""
    places: dict[str, list[str]] = defaultdict(list)

    def add(process_id: str | None, place: str) -> None:
        if process_id in old.by_id and place not in places[process_id]:
            places[process_id].append(place)

    for study in studies:
        for product in study.products:
            add(product.process, f"study {study.name} product {product.process}")
        for scenario in study.scenarios:
            where = _scenario_file(study, scenario.name)
            for change in scenario.line_changes:
                place = f"{where}: {change.process_id} line {change.line_id}"
                add(change.process_id, place)
                add(change.supplier_id, place)
            for change in scenario.parameter_changes:
                if "/" in change.parameter:  # `<process id>/<name>`, a parameter of a process
                    add(change.parameter.rsplit("/", 1)[0], f"{where}: {change.parameter}")
    return dict(places)


def named(folders: Iterable[DatabaseFolder], old: Datasets) -> dict[str, list[str]]:
    """Every process of the old release that the folders name, with the places that name it:
    lines that buy from it, override rows, scenario changes, parameters of its own."""
    places: dict[str, list[str]] = defaultdict(list)

    def add(process_id: str | None, place: str) -> None:
        if process_id in old.by_id and place not in places[process_id]:
            places[process_id].append(place)

    for folder in folders:
        for p in folder.processes:
            for e in p.exchanges:
                add(e.supplier_id, f"{p.id} line {e.line_id}")
        for o in folder.overrides:
            for e in o.lines:
                add(o.process_id, f"override of {o.process_id} line {e.line_id}")
                add(e.supplier_id, f"override of {o.process_id} line {e.line_id}")
        for parameter in folder.parameters:
            add(parameter.scope, f"parameters of {parameter.scope}")
    return dict(places)


def moves_for(
    folders: Sequence[DatabaseFolder],
    old: Datasets,
    new: Datasets,
    found: Mapping[str, Dataset],
    studies: Sequence[StudyFolder] = (),
) -> tuple[Moves, list[Problem]]:
    """The moves that put each old process of `found` at its successor, and the problems that
    stop them: an override or scenario line with no single line to move to, and a flow the new
    release does not hold."""
    suppliers = {i: d.id for i, d in found.items()}
    products = {
        i: (old.by_id[i].product, d.product)
        for i, d in found.items()
        if old.by_id[i].product != d.product
    }
    moves = Moves(Pin.parse(new.pin), suppliers, products, names={}, lines={})
    problems: list[Problem] = []
    lines, line_problems = _lines(folders, old, new, moves, studies)
    problems += line_problems
    flows = _flows(folders, moves, studies)
    names = {f: new.flow_names[f] for f in flows if f in new.flow_names}
    for flow, where in flows.items():
        if flow in old.flow_names and flow not in new.flow_names:
            problems.append(
                Problem(
                    "flow-not-in-release",
                    f"flow {flow} ({old.flow_names[flow]})",
                    f"is not a flow of {new.pin}. Used at {', '.join(where)}: change the lines "
                    "that use it, then upgrade.",
                )
            )
    return replace(moves, names=names, lines=lines), problems


def _lines(
    folders: Sequence[DatabaseFolder],
    old: Datasets,
    new: Datasets,
    moves: Moves,
    studies: Sequence[StudyFolder] = (),
) -> tuple[dict[tuple[str, str], str], list[Problem]]:
    """The successor line of each old line an override or a study's scenario changes.
    ecoinvent's line ids change with every version, so a line is found by its role and flow,
    and, when several fit, by the successor of its supplier."""
    wanted: dict[tuple[str, str], tuple[str, str]] = {}
    for folder in folders:
        for o in folder.overrides:
            for e in o.lines:
                place = ("override-line", f"override of {e.label}")
                wanted.setdefault((o.process_id, e.line_id), place)
    for study in studies:
        for scenario in study.scenarios:
            for change in scenario.line_changes:
                place = (
                    "scenario-line",
                    f"{_scenario_file(study, scenario.name)}: {change.process_id} line "
                    f"{change.line_id}",
                )
                wanted.setdefault((change.process_id, change.line_id), place)
    wanted = {k: v for k, v in wanted.items() if k[0] in moves.suppliers}
    before = old.processes(k[0] for k in wanted)
    after = new.processes(moves.suppliers[k[0]] for k in wanted)
    found: dict[tuple[str, str], str] = {}
    problems: list[Problem] = []
    for (process_id, line_id), (code, place) in wanted.items():
        line = next((e for e in before[process_id].exchanges if e.line_id == line_id), None)
        successor = after[moves.suppliers[process_id]]
        if line is None:
            continue  # not a line of the old process: validation said so before
        chosen, why = _successor_line(line, successor, moves)
        if chosen is not None:
            found[(process_id, line_id)] = chosen
        else:
            problems.append(Problem(code, place, why))
    return found, problems


def _successor_line(line: Exchange, successor: Process, moves: Moves) -> tuple[str | None, str]:
    supplier, flow, _ = moves.line(line.supplier_id, line.flow_id, None)
    fits = [e for e in successor.exchanges if e.role is line.role and e.flow_id == flow]
    if len(fits) > 1:
        fits = [e for e in fits if e.supplier_id == supplier] or fits
    if len(fits) == 1:
        return fits[0].line_id, ""
    what = f"{line.role.value} of {flow}"
    if not fits:
        return None, f"no line of {successor.id} is an {what}. Change or remove the row."
    ids = ", ".join(e.line_id for e in fits)
    return None, (
        f"{len(fits)} lines of {successor.id} are an {what}: {ids}. Write the row again for "
        "one of them."
    )


def _flows(
    folders: Sequence[DatabaseFolder], moves: Moves, studies: Sequence[StudyFolder] = ()
) -> dict[str, list[str]]:
    """Every flow the folders' lines and no_factor.csv rows name once moved, with where."""
    flows: dict[str, list[str]] = defaultdict(list)
    for study in studies:
        for row in study.no_factor:
            flows[row.flow_id].append(f"study {study.name} no_factor.csv")
    for folder in folders:
        lines = [e for p in folder.processes for e in p.exchanges]
        lines += [e for o in folder.overrides for e in o.lines]
        for e in lines:
            flows[moves.line(e.supplier_id, e.flow_id, None)[1]].append(e.label)
        for row in folder.no_factor:
            flows[row.flow_id].append(f"{folder.database.name} no_factor.csv")
    return dict(flows)


def _flags(matches: Sequence[Match], old: Datasets, new: Datasets) -> tuple[Flag, ...]:
    markets = [m for m in matches if m.new is not None and m.old.is_market and m.new.is_market]
    old.processes(m.old.id for m in markets)  # every market's lines, read at once
    new.processes(m.new.id for m in markets if m.new is not None)
    found: list[Flag] = []
    for m in matches:
        if m.new is None:
            continue
        for make in (row_flag, market_flag):
            flag = make(m.old, m.new, old, new)
            if flag is not None:
                found.append(replace(flag, lines=m.lines))
    return tuple(found)


def _with_used(given: Sequence[Given]) -> tuple[DatabaseFolder, ...]:
    """The databases given, read, and every database they use, each once."""
    folders: dict[str, DatabaseFolder] = {}
    for item in given:
        folder = item if isinstance(item, DatabaseFolder) else read_database(Path(str(item)))
        for f in folder.chain:
            known = folders.setdefault(f.database.name, f)
            if known.path.resolve() != f.path.resolve():
                raise InvalidDataError(
                    [
                        Problem(
                            "same-name",
                            f"database {f.database.name}",
                            f"is both {known.path} and {f.path}: upgrade them one at a time",
                        )
                    ]
                )
    return tuple(folders.values())


def _pinned(folders: Sequence[DatabaseFolder], studies: Sequence[StudyFolder] = ()) -> Release:
    pins = {str(f.database.background) for f in folders}
    pins |= {str(s.background) for s in studies if s.background is not None}
    if not pins:
        raise InvalidDataError(
            [
                Problem(
                    "no-pin",
                    _first_name(folders, studies),
                    "pins no release: give the databases its products are in, or pin the "
                    "release of its products in study.yaml",
                )
            ]
        )
    if len(pins) > 1:
        raise InvalidDataError(
            [
                Problem(
                    "several-pins",
                    "databases and studies",
                    f"pin {len(pins)} releases ({', '.join(sorted(pins))}): an upgrade moves "
                    "them from one release",
                )
            ]
        )
    return find(pins.pop())


def _left_behind(
    folders: Sequence[DatabaseFolder],
    studies: Sequence[StudyFolder],
    old: Datasets,
    new: Datasets,
) -> list[Problem]:
    """A database next to one upgraded that uses it, or a study whose products reach it and that
    names an id of the old release, which is not upgraded with it: its databases would pin two
    releases, or its scenarios would name lines the new release does not have."""
    moving = {f.database.name for f in folders}
    moving_studies = {s.path.resolve() for s in studies if s.path is not None}
    problems: list[Problem] = []
    seen: set[Path] = set()
    for folder in folders:
        parent = folder.path.resolve().parent
        uses = uses_on_disk(parent, read_uses)
        for user, _ in users_of(folder.database.name, uses):
            if user not in moving:
                problems.append(
                    Problem(
                        "user-left-behind",
                        f"database {user}",
                        f"uses {folder.database.name}, which moves to {new.pin}: a database and "
                        "the databases it uses pin one release, so upgrade them together: "
                        f"`headwater upgrade {folder.path} {folder.path.parent / user}`",
                    )
                )
        if parent in seen:
            continue
        seen.add(parent)
        for entry in sorted(parent.iterdir()):
            if not (entry / "study.yaml").is_file() or entry.resolve() in moving_studies:
                continue
            try:
                study = read_study(entry)
            except InvalidDataError:
                continue  # validating it says what is wrong
            reached = sorted(set(study.reached) & moving)
            if reached and named_by_studies([study], old):
                problems.append(
                    Problem(
                        "study-left-behind",
                        f"study {study.name}",
                        f"reaches {', '.join(reached)}, which moves to {new.pin}, and names "
                        f"{old.pin} in its scenarios or products: upgrade them together: "
                        f"`headwater upgrade {folder.path} {entry}`",
                    )
                )
    return problems
