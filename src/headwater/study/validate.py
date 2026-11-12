"""Checks across records: units, suppliers, flows, factors.

`validate` never raises for bad data. It returns a `Report`: errors stop the work, warnings
are for people to read. `Report.raise_if_errors()` turns the errors into one exception.
"""

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any, cast

from pydantic import ValidationError

from headwater.model import (
    ROLE_FLOW_KINDS,
    ROLES_WITH_SUPPLIER,
    Catalog,
    Database,
    Exchange,
    Flow,
    FlowKind,
    InvalidDataError,
    LineChange,
    Method,
    Problem,
    Process,
    Report,
    Role,
    Scenario,
    Severity,
    did_you_mean,
    with_article,
)
from headwater.study._files import PLACEHOLDER, problems_from_validation
from headwater.study._no_factor import FILE as NO_FACTOR_FILE
from headwater.study.folders import DatabaseFolder, ProcessFolder, build_catalog, in_used, owner_of


def validate(
    folder: ProcessFolder,
    *,
    background: ProcessFolder | None = None,
    method: Method | None = None,
) -> Report:
    """Check a folder against what it links to.

    The folder and the `background`, when given, are both checked, each against the ids of
    both: a database's supplier may live in the background. With a `method`, elementary flows
    that no factor covers are named with the processes that use them. For a database's own
    processes each is an error until a row of its `no_factor.csv` acknowledges it, and a row
    that is not needed is a warning. For the background, and for a folder that is not a database,
    each is a warning.
    """
    folders = [folder] if background is None else [folder, background]
    prefixes = None if background is None else id_prefixes(p.id for p in background.processes)
    return _validate(folders, checked=folders, database=folder, method=method, prefixes=prefixes)


def validate_database(
    database: DatabaseFolder,
    background: ProcessFolder,
    *,
    method: Method | None = None,
    catalog: Catalog | None = None,
    lines_complete: bool = False,
    uncovered: Mapping[str, tuple[str, ...]] | None = None,
    prefixes: frozenset[str] | None = None,
) -> Report:
    """Check a database against a background that `validate` has already accepted.

    Only the database's own records are checked: its flows, its lines, and where they link into
    the background. The background's records are used to resolve ids, units and flows, and are
    not checked again. With a `method`, the flows with no factor are those of the database's
    processes only, as `validate` checks them: the background's are `flows_without_factor` of
    its own folder.

    A caller that already built a catalog of this database and this background can pass it as
    `catalog`, so it is not built again: `build_catalog(database, background)`, or one that holds
    only what the database names (`Background.study_catalog`). `lines_complete` says that the
    catalog holds every line of each background process that an override or a scenario
    changes, as `Background.study_catalog` does. A line it does not find then does not exist,
    even in a process whose only line is its reference.

    The checks of the database's flows with no factor need `flows_without_factor` of its own
    processes. A caller that worked it out already, as `calc` does to list those flows, passes it
    as `uncovered` so that it is not worked out again.

    The databases the database uses are checked as part of it: their flows, lines, parameters,
    overrides and flows with no factor, each problem at its place in their folder
    (`../dyes/processes/reactive-dye.csv`). Their scenarios are not, since they do not run. A
    line may buy only from its own database, a database its `uses` names, or the background,
    whose ids start with one of `prefixes` (`toy` for `toy:fabric-production`): by default, the
    prefixes of `background`'s processes.
    """
    if prefixes is None:
        prefixes = id_prefixes(p.id for p in background.processes)
    return _validate(
        [database, background],
        checked=[database],
        database=database,
        method=method,
        catalog=catalog,
        lines_complete=lines_complete,
        uncovered=uncovered,
        prefixes=prefixes,
    )


def id_prefixes(ids: Iterable[str]) -> frozenset[str]:
    """What the ids start with, up to the first colon: `{"toy"}` for the toy background."""
    return frozenset(i.partition(":")[0] for i in ids)


def flows_without_factor(
    processes: Iterable[Process], catalog: Catalog, method: Method
) -> dict[str, tuple[str, ...]]:
    """Elementary flows that no factor of the method covers, with the ids of the processes that
    use them, sorted.

    A flow with a factor for at least one indicator is covered: it counts 0 for the others.
    """
    characterised = method.characterised_flow_ids
    users: dict[str, set[str]] = defaultdict(set)
    for process in processes:
        for exchange in process.exchanges:
            flow = catalog.flow(exchange.flow_id)
            if flow is not None and flow.kind is FlowKind.elementary:
                if flow.id not in characterised:
                    users[flow.id].add(process.id)
    return {flow_id: tuple(sorted(ids)) for flow_id, ids in sorted(users.items())}


def _validate(
    folders: list[ProcessFolder],
    *,
    checked: list[ProcessFolder],
    database: ProcessFolder,
    method: Method | None,
    catalog: Catalog | None = None,
    lines_complete: bool = False,
    uncovered: Mapping[str, tuple[str, ...]] | None = None,
    prefixes: frozenset[str] | None = None,
) -> Report:
    """Resolve ids over all `folders` and the databases they use, and check the records of the
    `checked` ones."""
    if catalog is None:
        try:
            catalog = build_catalog(
                *(g for f in folders for g in (f.chain if isinstance(f, DatabaseFolder) else (f,)))
            )
        except InvalidDataError as error:
            return Report(error.problems)  # the ids do not agree, so nothing else can be checked

    scopes: dict[str, _Scope] = {}
    if isinstance(database, DatabaseFolder):
        owners = owner_of(database.chain)
        scopes = {f.database.name: _Scope.of(f, owners, prefixes) for f in database.chain}
    problems: list[Problem] = []
    for folder in checked:
        scope = scopes.get(database.database.name) if isinstance(database, DatabaseFolder) else None
        problems.extend(_check_records(folder, catalog, scope if folder is database else None))
    if method is not None:
        own = isinstance(database, DatabaseFolder)
        others = [p for f in checked if not (own and f is database) for p in f.processes]
        if others:
            problems.extend(_warn_flows_without_factor(others, catalog, method))
        if isinstance(database, DatabaseFolder):
            if uncovered is None:
                uncovered = flows_without_factor(database.processes, catalog, method)
            problems.extend(_check_no_factor(database, catalog, method, uncovered))
    if isinstance(database, DatabaseFolder):
        scope = scopes[database.database.name]
        problems.extend(_check_database(database, method))
        problems.extend(_check_parameters(database, catalog))
        problems.extend(_check_overrides(database, catalog, lines_complete, scope))
        for used in database.used:
            problems.extend(_check_used(used, catalog, method, lines_complete, scopes))
    return Report(tuple(dict.fromkeys(problems)))


@dataclass(frozen=True)
class _Scope:
    """What the lines of one database may buy from: its own processes, those of the databases its
    `uses` names, and the background's, known by the prefixes of their ids. Example: the T-shirt
    with `uses: [dyes]` on the toy may buy from `tshirt:*`, `dyes:*` and `toy:*`."""

    database: str
    uses: frozenset[str]
    owners: Mapping[str, str]
    """The database each process of the calculation belongs to, by process id."""
    background: frozenset[str] | None
    """The prefixes of the background's ids, or None when there is no background to say."""

    @classmethod
    def of(
        cls, folder: DatabaseFolder, owners: Mapping[str, str], background: frozenset[str] | None
    ) -> "_Scope":
        return cls(folder.database.name, frozenset(folder.database.uses), owners, background)

    def refused(self, exchange: Exchange) -> Problem | None:
        """A problem when the line's supplier is a process of a database this one does not use."""
        supplier = exchange.supplier_id
        if supplier is None:
            return None
        owner = self.owners.get(supplier)
        if owner is not None:
            if owner == self.database or owner in self.uses:
                return None
            return Problem(
                "supplier-not-in-uses",
                exchange.label,
                f"buys from {supplier}, a process of the database {owner}, which "
                f"{self.database} does not list in uses. Add {owner} to uses in database.yaml.",
            )
        prefix = supplier.partition(":")[0]
        if self.background is None or prefix in self.background:
            return None
        if prefix == self.database or prefix in self.uses:
            return None
        return Problem(
            "supplier-not-in-uses",
            exchange.label,
            f"buys from {supplier}, but {prefix} is not this database, a database in its uses, "
            "or the "
            f"background. To buy from the database {prefix} next to this one, add {prefix} to uses "
            "in database.yaml.",
        )


def _check_records(folder: ProcessFolder, catalog: Catalog, scope: _Scope | None) -> list[Problem]:
    """The flows of `folder` and every line of its processes."""
    problems = _check_flows(folder, catalog)
    for process in folder.processes:
        for exchange in process.exchanges:
            problems.extend(_check_exchange(exchange, catalog, scope))
    return problems


def _check_used(
    used: DatabaseFolder,
    catalog: Catalog,
    method: Method | None,
    lines_complete: bool,
    scopes: Mapping[str, _Scope],
) -> list[Problem]:
    """A database that the database checked uses, as part of it: everything but its scenarios,
    which do not run, and its `database.yaml`, whose methods are not used. Each problem is at its
    place in the used database's folder."""
    name = used.database.name
    scope = scopes[name]
    found = [
        *_check_records(used, catalog, scope),
        *_check_parameters(used, catalog),
        *_check_overrides(used, catalog, lines_complete, scope),
    ]
    if method is not None:
        uncovered = flows_without_factor(used.processes, catalog, method)
        found.extend(_check_no_factor(used, catalog, method, uncovered, used=True))
    return [replace(p, location=in_used(name, p.location or _file_of(p.item, name))) for p in found]


def _file_of(item: str, database: str) -> str | None:
    """The process file a problem's item names: `processes/reactive-dye.csv` for
    `dyes:reactive-dye line e02`."""
    if not item.startswith(f"{database}:"):
        return None
    stem = item.removeprefix(f"{database}:").split(" ")[0]
    return None if ":" in stem else f"processes/{stem}.csv"


def _check_flows(folder: ProcessFolder, catalog: Catalog) -> list[Problem]:
    problems: list[Problem] = []
    for flow in folder.flows:
        if flow.unit not in catalog.units:
            problems.append(catalog.units.unknown(flow.unit, f"flow {flow.id}"))
    for prop in folder.flow_properties:
        item = f"flow {prop.flow_id} property {prop.property.value}"
        if catalog.flow(prop.flow_id) is None:
            problems.append(
                Problem(
                    "unknown-flow",
                    item,
                    f"flow {prop.flow_id!r} is not defined."
                    f"{did_you_mean(prop.flow_id, catalog.flows)}",
                )
            )
        if prop.unit not in catalog.units:
            problems.append(catalog.units.unknown(prop.unit, item))
    return problems


def _check_exchange(
    exchange: Exchange, catalog: Catalog, scope: _Scope | None = None
) -> list[Problem]:
    problems: list[Problem] = []
    try:
        catalog.convert(exchange)
    except InvalidDataError as error:
        problems.extend(error.problems)
    flow = catalog.flow(exchange.flow_id)
    if flow is not None:
        if flow.kind not in ROLE_FLOW_KINDS[exchange.role]:
            allowed = " or ".join(sorted(k.value for k in ROLE_FLOW_KINDS[exchange.role]))
            problems.append(
                Problem(
                    "role-flow-kind",
                    exchange.label,
                    f"role {exchange.role.value!r} needs {with_article(f'{allowed} flow')}, but "
                    f"{flow.id!r} is {with_article(f'{flow.kind.value} flow')}",
                )
            )
        problems.extend(_check_flow_name(exchange.label, flow, exchange.flow_name))
    problems.extend(_check_supplier(exchange, catalog, scope))
    return problems


def _check_flow_name(item: str, flow: Flow, written: str | None) -> list[Problem]:
    """A flow's name written next to its id, in a process file or in no_factor.csv."""
    if written is None or written == flow.name:
        return []
    return [
        Problem(
            "flow-name-mismatch",
            item,
            f"names flow {flow.id!r} {written!r}, but its name is {flow.name!r}",
        )
    ]


def _check_supplier(exchange: Exchange, catalog: Catalog, scope: _Scope | None) -> list[Problem]:
    supplier_id = exchange.supplier_id
    role = exchange.role
    if role not in ROLES_WITH_SUPPLIER:
        if supplier_id is None:
            return []
        return [
            Problem(
                "supplier-not-allowed",
                exchange.label,
                f"role {role.value!r} takes no supplier, but {supplier_id!r} is given",
            )
        ]
    if supplier_id is None:
        if exchange.link_status is not None:
            return []  # cut off or a data gap: resolved on purpose, and an issue says why
        return [
            Problem(
                "supplier-missing",
                exchange.label,
                f"{role.value} of flow {exchange.flow_id!r} has no supplier. "
                "Name the process that makes it. To leave it out on purpose, record a cut-off "
                "in the database's cut_offs.csv.",
            )
        ]
    if role is Role.own_use and supplier_id != exchange.process_id:
        return [
            Problem(
                "own-use-supplier",
                exchange.label,
                f"own_use is the process's own product, so its supplier is "
                f"{exchange.process_id!r}, not {supplier_id!r}",
            )
        ]
    refused = scope.refused(exchange) if scope is not None else None
    if refused is not None:
        return [refused]
    supplier = catalog.process(supplier_id)
    if supplier is None:
        return [
            Problem(
                "supplier-not-found",
                exchange.label,
                f"supplier {supplier_id!r} does not exist (flow {exchange.flow_id!r})."
                f"{did_you_mean(supplier_id, catalog.processes)}",
            )
        ]
    made = supplier.reference_exchange.flow_id
    if made != exchange.flow_id:
        return [
            Problem(
                "supplier-wrong-flow",
                exchange.label,
                f"supplier {supplier_id!r} makes {made!r}, not {exchange.flow_id!r}",
            )
        ]
    return []


def _warn_flows_without_factor(
    processes: Iterable[Process], catalog: Catalog, method: Method
) -> list[Problem]:
    return [
        Problem(
            "no-factor",
            f"flow {flow_id}",
            f"has no factor in {_methods(method.ids)}; used by {', '.join(process_ids)}",
            severity=Severity.warning,
        )
        for flow_id, process_ids in flows_without_factor(processes, catalog, method).items()
    ]


def _check_no_factor(
    database: DatabaseFolder,
    catalog: Catalog,
    method: Method,
    missing: Mapping[str, tuple[str, ...]],
    *,
    used: bool = False,
) -> list[Problem]:
    """The database's own flows with no factor (`missing`: `flows_without_factor` of its processes),
    each an error until a row of `no_factor.csv` for this method acknowledges it. A row that is
    not needed is a warning, so that the rows of an old method never block moving to a new one:
    only the rows in use have their `flow_name` checked.

    For a database that another one `used`, the error says which file to add the row to, and a
    row that is not needed says nothing: the used database is checked against its own method when
    it is validated itself."""
    acknowledged = {row.flow_id for row in database.no_factor if row.method in method.ids}
    problems = [
        Problem(
            "no-factor",
            f"flow {flow_id}",
            f"has no factor in {_methods(method.ids)}; used by {', '.join(process_ids)}. To count "
            f"it as 0, add a row to the database's {NO_FACTOR_FILE}: flow, method, reason, who, "
            "when.",
            location=NO_FACTOR_FILE if used else None,
        )
        for flow_id, process_ids in missing.items()
        if flow_id not in acknowledged
    ]
    characterised = method.characterised_flow_ids
    for row in database.no_factor:
        hint = ""
        if used and (row.method not in method.ids or row.flow_id not in missing):
            continue
        if row.method not in method.ids:
            why = (
                f"it is for method {row.method}, and the database is checked with "
                f"{_methods(method.ids)}"
            )
        elif row.flow_id in characterised:
            why = f"the flow has a factor in {_methods(method.ids)}"
        elif row.flow_id not in missing:
            why = "no process of the database emits or takes it"
            hint = did_you_mean(row.flow_id, missing)
        else:
            flow = catalog.flow(row.flow_id)
            if flow is not None:
                found = _check_flow_name(f"flow {row.flow_id}", flow, row.flow_name)
                problems.extend(replace(p, location=NO_FACTOR_FILE) for p in found)
            continue
        problems.append(
            Problem(
                "no-factor-not-needed",
                f"flow {row.flow_id}",
                f"row not needed: {why}. Remove the row.{hint}",
                severity=Severity.warning,
                location=NO_FACTOR_FILE,
            )
        )
    return problems


def _placeholders(value: Any, field: str) -> Iterable[tuple[str, str]]:
    """The fields of `value`, written as `allocation.rule` or `assumptions[1]`, whose text starts
    with the word TODO, each with that text."""
    if isinstance(value, str):
        if PLACEHOLDER.match(value.strip()):
            yield field, value
    elif isinstance(value, dict):
        for key, item in cast(dict[str, Any], value).items():
            yield from _placeholders(item, f"{field}.{key}" if field else key)
    elif isinstance(value, list | tuple):
        for n, item in enumerate(cast(list[Any], value), 1):
            yield from _placeholders(item, f"{field}[{n}]")


def _check_placeholders(database: Database) -> list[Problem]:
    """A warning for each field of database.yaml that still says TODO, such as a description
    written as `TODO: say what this database holds`."""
    return [
        Problem(
            "database-todo",
            f"database {database.name}: {field}",
            f"is still a placeholder ({text!r}). Write the database's own, in database.yaml.",
            severity=Severity.warning,
            location="database.yaml",
        )
        for field, text in _placeholders(database.model_dump(mode="json"), "")
    ]


def _check_database(folder: DatabaseFolder, method: Method | None) -> list[Problem]:
    database = folder.database
    problems: list[Problem] = _check_placeholders(database)
    if database.methods and method is not None and database.methods != method.ids:
        problems.append(
            Problem(
                "method-mismatch",
                f"database {database.name}",
                f"asks for {_methods(database.methods, quoted=True)}, but the background is "
                f"loaded with {_methods(method.ids, quoted=True)}",
            )
        )
    if database.cut_off is not None and not folder.cut_offs:
        problems.append(
            Problem(
                "cut-off-not-needed",
                f"database {database.name}: cut_off",
                "is set, but cut_offs.csv has no rows: take it out of database.yaml, or cut off "
                "a line",
                severity=Severity.warning,
                location="database.yaml",
            )
        )
    return problems


def _line_of(process: Process, line_id: str) -> Exchange | None:
    return next((e for e in process.exchanges if e.line_id == line_id), None)


def _not_checked(label: str, process: Process) -> Problem:
    return Problem(
        "line-not-checked",
        label,
        f"cannot be checked: the background holds only the reference line of {process.id}, so "
        "whether this line exists, and what it is, is not known here",
        severity=Severity.warning,
    )


def _lines_of(process: Process) -> str:
    return ", ".join(e.line_id for e in process.exchanges)


def _check_parameters(folder: DatabaseFolder, catalog: Catalog) -> list[Problem]:
    return [
        catalog.units.unknown(p.unit, f"parameter {p.id}")
        for p in folder.parameters
        if p.unit is not None and p.unit not in catalog.units
    ]


def _check_overrides(
    folder: DatabaseFolder, catalog: Catalog, complete: bool, scope: _Scope
) -> list[Problem]:
    """Each override line against the background process it changes."""
    problems: list[Problem] = []
    for override in folder.overrides:
        process = catalog.process(override.process_id)
        for line in override.lines:
            where = folder.override_source(line.label)
            found = _override_line(line, process, catalog, complete, scope)
            problems.extend(replace(p, location=p.location or where) for p in found)
    return problems


def _override_line(
    line: Exchange, process: Process | None, catalog: Catalog, complete: bool, scope: _Scope
) -> list[Problem]:
    owner = scope.owners.get(line.process_id)
    if owner is not None:
        return [
            Problem(
                "override-used-database",
                line.label,
                f"changes a process of the database {owner}, which this database uses. An override "
                f"changes a process of the background: change {owner}'s own files instead.",
            )
        ]
    if process is None:
        return [
            Problem(
                "override-unknown-process",
                line.label,
                f"process {line.process_id!r} is not in the background."
                f"{did_you_mean(line.process_id, catalog.processes)}",
            )
        ]
    existing = _line_of(process, line.line_id)
    if existing is None:
        if complete or len(process.exchanges) > 1:
            return [
                Problem(
                    "override-unknown-line",
                    line.label,
                    f"{process.id} has no line {line.line_id!r}."
                    f"{did_you_mean(line.line_id, [e.line_id for e in process.exchanges])} "
                    f"Its lines: {_lines_of(process)}",
                )
            ]
        # The background process is known by its reference line only, so there is no line to
        # compare with. The line still has to fit its own flow, unit and supplier.
        return [_not_checked(line.label, process), *_check_exchange(line, catalog, scope)]
    else:
        changed: list[str] = []
        if existing.role is not line.role:
            changed.append(f"the role ({existing.role.value} to {line.role.value})")
        if existing.flow_id != line.flow_id:
            changed.append(f"the flow ({existing.flow_id} to {line.flow_id})")
        if changed:
            return [
                Problem(
                    "override-changes-line",
                    line.label,
                    f"changes {' and '.join(changed)} of the line. An override changes the "
                    "amount, the unit or the supplier of a line, not what the line is.",
                )
            ]
    return _check_exchange(line, catalog, scope)


def check_scenarios(
    scenarios: Sequence[Scenario],
    catalog: Catalog,
    complete: bool,
    owners: Mapping[str, str],
    background: frozenset[str] | None,
) -> list[Problem]:
    """Each line change of each scenario of a study against the process it changes, in a
    database the study reaches or in the background. `owners` names the database of each process
    the study reaches, by process id."""
    problems: list[Problem] = []
    for scenario in scenarios:
        for change in scenario.line_changes:
            owner = owners.get(change.process_id, "")
            scope = _Scope(owner, frozenset(owners.values()), owners, background)
            problems.extend(
                replace(
                    p,
                    item=f"scenario {scenario.name}: {p.item}",
                    location=f"scenarios/{scenario.name}.yaml",
                )
                for p in _scenario_line(change, catalog, complete, scope)
            )
    return problems


def _scenario_line(
    change: LineChange, catalog: Catalog, complete: bool, scope: _Scope
) -> list[Problem]:
    process = catalog.process(change.process_id)
    if process is None:
        return [
            Problem(
                "scenario-unknown-process",
                change.label,
                f"process {change.process_id!r} does not exist."
                f"{did_you_mean(change.process_id, catalog.processes)}",
            )
        ]
    existing = _line_of(process, change.line_id)
    if existing is None:
        if complete or len(process.exchanges) > 1:
            return [
                Problem(
                    "scenario-unknown-line",
                    change.label,
                    f"{process.id} has no line {change.line_id!r}."
                    f"{did_you_mean(change.line_id, [e.line_id for e in process.exchanges])} "
                    f"Its lines: {_lines_of(process)}",
                )
            ]
        return [_not_checked(change.label, process)]
    if existing.role is Role.reference:
        return [
            Problem(
                "scenario-reference-line",
                change.label,
                "is the reference line, which is what the process makes. A scenario changes "
                "the other lines.",
            )
        ]
    try:
        changed = change.apply_to(existing)
    except ValidationError as error:
        return problems_from_validation(error, item=change.label)
    return _check_exchange(changed, catalog, scope)


def _methods(ids: tuple[str, ...], *, quoted: bool = False) -> str:
    """`method EF-3.1`, or `methods EF-3.1, IPCC-2021`, with each id in quotes if `quoted`."""
    names = [repr(i) if quoted else i for i in ids]
    return f"method {names[0]}" if len(names) == 1 else f"methods {', '.join(names)}"
