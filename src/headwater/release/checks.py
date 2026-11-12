"""What stops a source release from being frozen, checked on its Arrow tables.

`build` turns records into these tables and `build_tables` takes them as given, so both paths are
checked here, once. There are two kinds of check:

- `row_problems`: every row is a valid record. Records refuse a bad row when they are made, but
  tables can come from anywhere, so the same rules are applied to them here, with the same codes.
  A rule on a field's type or range is `invalid-field`, as when a study file is read.
- `freeze_problems`: the rows fit together. Ids are unique; lines point at flows, units and
  suppliers of the release; every line that buys from nothing is resolved by an issue; a role
  fits its flow's kind and has a sign in its process; a supplier makes the flow bought from it,
  and an own use is bought from the process itself; a unit of another quantity converts through a
  property of the flow.

Example: in the toy, fabric production buys 3 kWh of electricity from the incineration process.
That process makes textile waste, so freezing refuses `toy:fabric-production line e02`.

The checks run in DuckDB and Arrow over the tables, and make records only for the rows that
fail and for the small tables. Their cost on ecoinvent is in docs/appendix/benchmarks.md,
"Import of ecoinvent".
"""

import math
import re
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import closing
from dataclasses import dataclass, field
from typing import Any, cast

import duckdb
import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
from pydantic import BaseModel, ValidationError

from headwater.calc.signs import role_entries
from headwater.model import (
    ROLE_FLOW_KINDS,
    ROLES_ALLOWING_NEGATIVE,
    ROLES_WITH_SUPPLIER,
    Catalog,
    Exchange,
    Flow,
    FlowProperty,
    InvalidDataError,
    Issue,
    LinkStatus,
    Method,
    Problem,
    ProcessDetails,
    RecordError,
    Role,
    Unit,
    Units,
    did_you_mean,
)
from headwater.model.uncertainty import Distribution
from headwater.release.manifest import FOLDER_NAME, ReleaseInfo
from headwater.release.schema import issue_row, read_flow_properties, read_flows

compute: Any = pc

INVALID = "invalid-field"


@dataclass(frozen=True)
class SourceTables:
    """The tables of a source release, conformed to their specs, in any row order."""

    processes: pa.Table
    exchanges: pa.Table
    flows: pa.Table
    flow_properties: pa.Table
    issues: pa.Table
    geographies: pa.Table
    flow_names: "pa.ChunkedArray[Any] | None" = None
    """The flow name written next to each line, in the order of `exchanges`, if given."""


# --- keys ------------------------------------------------------------------------------------


def key_problems(tables: SourceTables, methods: Sequence[Method]) -> list[Problem]:
    """Every id defined twice: a flow, a process, a flow's property, an issue, a geography's
    code, a method, or a line id within its process. Lines of a process defined twice are not
    listed again."""
    t = tables
    problems: list[Problem] = []
    _unique(t.flows, ("id",), "duplicate-flow", lambda r: f"flow {r['id']}", problems)
    twins = _unique(
        t.processes, ("id",), "duplicate-process", lambda r: f"process {r['id']}", problems
    )
    _unique(
        t.flow_properties,
        ("flow_id", "property"),
        "duplicate-property",
        lambda r: f"flow {r['flow_id']} property {r['property']}",
        problems,
    )
    _unique(
        t.issues,
        ("process_id", "line_id"),
        "duplicate-issue",
        lambda r: f"issue {r['process_id']} line {r['line_id']}",
        problems,
    )
    _unique(
        t.geographies,
        ("code",),
        "duplicate-geography",
        lambda r: f"geography {r['code']}",
        problems,
    )
    seen: set[str] = set()
    for method in methods:
        if method.id in seen:
            problems.append(
                Problem("duplicate-method", f"method {method.id}", "is defined more than once")
            )
        seen.add(method.id)
    lines: list[Problem] = []
    _unique(
        t.exchanges,
        ("process_id", "line_id"),
        "duplicate-line",
        lambda r: f"{r['process_id']} line {r['line_id']}",
        lines,
        skip=lambda r: (r["process_id"],) in twins,
    )
    return problems + lines


def _unique(
    table: pa.Table,
    key: tuple[str, ...],
    code: str,
    label: Callable[[dict[str, Any]], str],
    problems: list[Problem],
    skip: Callable[[dict[str, Any]], bool] = lambda r: False,
) -> set[tuple[Any, ...]]:
    """One problem per key that appears more than once, in key order. Returns those keys."""
    counts: Any = table.select(list(key)).group_by(list(key)).aggregate([([], "count_all")])
    twice = counts.filter(compute.greater(counts.column("count_all"), 1)).sort_by(
        [(k, "ascending") for k in key]
    )
    found: set[tuple[Any, ...]] = set()
    for row in twice.select(list(key)).to_pylist():
        found.add(tuple(row[c] for c in key))
        if not skip(row):
            problems.append(Problem(code, label(row), "is defined more than once"))
    return found


# --- rows ------------------------------------------------------------------------------------


@dataclass
class _Rows:
    """Problems found per row: every reason a field is invalid, and the record rules broken."""

    invalid: dict[tuple[int, str], list[str]] = field(default_factory=lambda: defaultdict(list))
    rules: dict[tuple[int, str], list[Problem]] = field(default_factory=lambda: defaultdict(list))

    def field(self, order: int, item: str, reason: str) -> None:
        self.invalid[(order, item)].append(reason)

    def rule(self, order: int, item: str, problem: Problem) -> None:
        self.rules[(order, item)].append(problem)

    def problems(self) -> list[Problem]:
        """One problem per row, as a record would raise it: the invalid fields together, or
        else the first rule broken."""
        found: list[Problem] = []
        for key in sorted({*self.invalid, *self.rules}):
            reasons = self.invalid.get(key)
            if reasons:
                found.append(Problem(INVALID, key[1], "; ".join(dict.fromkeys(reasons))))
            else:
                found.append(self.rules[key][0])
        return found


_PROCESSES, _EXCHANGES, _FLOWS, _PROPERTIES, _ISSUES, _GEOGRAPHIES = range(6)
"""The order rows are reported in, by table."""


def row_problems(tables: SourceTables) -> list[Problem]:
    """Every row that is not a valid record, one problem per row, by table then by key."""
    rows = _Rows()
    labels = _Labels(tables)
    for order, table, label in labels.all():
        _untrimmed(table, label, order, rows)
        _nulls(table, label, order, rows)
    _records(tables.processes, labels.process, _PROCESSES, rows, _process_details)
    _records(tables.flows, labels.flow, _FLOWS, rows, lambda r: Flow.model_validate(r))
    _records(
        tables.flow_properties, labels.property, _PROPERTIES, rows, FlowProperty.model_validate
    )
    _records(
        tables.issues, labels.issue, _ISSUES, rows, lambda r: Issue.model_validate(issue_row(r))
    )
    _records(tables.geographies, labels.geography, _GEOGRAPHIES, rows, _geography)
    _exchange_rows(tables, labels.line, rows)
    return rows.problems()


class _Labels:
    """How a problem names a row of each table."""

    def __init__(self, tables: SourceTables) -> None:
        self.tables = tables

    def all(self) -> list[tuple[int, pa.Table, Callable[[int], str]]]:
        t = self.tables
        return [
            (_PROCESSES, t.processes, self.process),
            (_EXCHANGES, t.exchanges, self.line),
            (_FLOWS, t.flows, self.flow),
            (_PROPERTIES, t.flow_properties, self.property),
            (_ISSUES, t.issues, self.issue),
            (_GEOGRAPHIES, t.geographies, self.geography),
        ]

    def process(self, i: int) -> str:
        return f"process {_at(self.tables.processes, 'id', i)}"

    def line(self, i: int) -> str:
        e = self.tables.exchanges
        return f"{_at(e, 'process_id', i)} line {_at(e, 'line_id', i)}"

    def flow(self, i: int) -> str:
        return f"flow {_at(self.tables.flows, 'id', i)}"

    def property(self, i: int) -> str:
        p = self.tables.flow_properties
        return f"flow {_at(p, 'flow_id', i)} property {_at(p, 'property', i)}"

    def issue(self, i: int) -> str:
        t = self.tables.issues
        return f"issue {_at(t, 'process_id', i)} line {_at(t, 'line_id', i)}"

    def geography(self, i: int) -> str:
        return f"geography {_at(self.tables.geographies, 'code', i)}"


def _at(table: pa.Table, column: str, i: int) -> object:
    return table.column(column)[i].as_py()


def _untrimmed(table: pa.Table, label: Callable[[int], str], order: int, rows: _Rows) -> None:
    """Text with spaces around it. A record strips them, so the same line from a file and from
    a table would otherwise differ."""
    for name in table.column_names:
        for i, path, value in _untrimmed_values(table.column(name).combine_chunks(), name):
            rows.field(order, label(i), f"{path}: {value!r} has spaces around it")


def _untrimmed_values(array: Any, path: str) -> Iterable[tuple[int, str, str]]:
    """(row, where, value) of every text value with spaces around it, inside structs and maps."""
    type_ = array.type
    if pa.types.is_string(type_) or pa.types.is_large_string(type_):
        changed = compute.not_equal(array, compute.utf8_trim_whitespace(array))
        for i in np.flatnonzero(compute.fill_null(changed, False).to_numpy(zero_copy_only=False)):
            yield int(i), path, array[int(i)].as_py()
    elif pa.types.is_struct(type_):
        valid = array.is_valid().to_numpy(zero_copy_only=False)
        fields: list[Any] = list(type_)
        for child, f in zip(array.flatten(), fields, strict=True):
            for i, where, value in _untrimmed_values(child, f"{path}.{f.name}"):
                if valid[i]:
                    yield i, where, value
    elif pa.types.is_map(type_):
        parents, keys, items = _map_entries(array)
        for side, child in (("name", keys), ("value", items)):
            for i, where, value in _untrimmed_values(child, f"{path} {side}"):
                yield int(parents[i]), where, value


def _map_entries(array: Any) -> tuple[np.ndarray[Any, Any], Any, Any]:
    """The row of each entry of a map array, and the entries' keys and values.

    A map's keys and values are not sliced with it: its offsets point into them."""
    offsets = array.offsets.to_numpy(zero_copy_only=False)
    start, end = int(offsets[0]), int(offsets[-1])
    parents = np.repeat(np.arange(len(array), dtype=np.int64), np.diff(offsets))
    return parents, array.keys.slice(start, end - start), array.items.slice(start, end - start)


def _nulls(table: pa.Table, label: Callable[[int], str], order: int, rows: _Rows) -> None:
    """A required column with no value. Arrow does not enforce a field's nullability."""
    schema: Any = table.schema
    for f in schema:
        column = table.column(f.name)
        if not f.nullable and column.null_count:
            missing = compute.is_null(column).to_numpy(zero_copy_only=False)
            for i in np.flatnonzero(missing):
                rows.field(order, label(int(i)), f"{f.name}: needs a value")


def _geography(row: dict[str, Any]) -> None:
    empty = [k for k in ("code", "name") if not row[k]]
    if empty:
        raise ValueError("; ".join(f"{k}: must not be empty" for k in empty))


def _process_details(row: dict[str, Any]) -> BaseModel:
    if not row["id"]:
        raise ValueError("id: must not be empty")
    return ProcessDetails.model_validate(
        {k: row[k] for k in ("name", "location", "time_start", "time_end", "kind")}
    )


def _records(
    table: pa.Table,
    label: Callable[[int], str],
    order: int,
    rows: _Rows,
    make: Callable[[dict[str, Any]], object],
) -> None:
    """Each row made into its record. These tables are small: one row per process or flow."""
    schema: Any = table.schema
    required = {f.name for f in schema if not f.nullable}
    for i, row in enumerate(table.to_pylist()):
        if any(row[k] is None for k in required):
            continue  # reported by `_nulls`
        try:
            make(row)
        except ValidationError as error:
            _from_validation(error, order, label(i), rows)
        except ValueError as error:
            rows.field(order, label(i), str(error))


def _from_validation(error: ValidationError, order: int, item: str, rows: _Rows) -> None:
    for err in error.errors():
        inner = (err.get("ctx") or {}).get("error")
        if isinstance(inner, RecordError):
            for p in inner.problems:
                rows.rule(order, item, Problem(p.code, item, p.message))
        else:
            where = ".".join(str(part) for part in err["loc"])
            message = err["msg"].removeprefix("Value error, ")
            rows.field(order, item, f"{where}: {message}" if where else message)


# Each rule on a line: a condition in SQL on the view `line`, and a message from its row. The
# field rules come first, as pydantic checks fields before a record's own validators run.

_ROLES = ", ".join(f"'{r.value}'" for r in Role)
_LINK_STATUSES = ", ".join(f"'{s.value}'" for s in LinkStatus)
_SUPPLIED = ", ".join(f"'{r.value}'" for r in sorted(ROLES_WITH_SUPPLIER))
_NEGATIVE_OK = ", ".join(f"'{r.value}'" for r in sorted(ROLES_ALLOWING_NEGATIVE))
_DISTRIBUTIONS = ", ".join(f"'{d.value}'" for d in Distribution)
_PARAMETERS = ("loc", "scale", "shape", "minimum", "maximum")
_PEDIGREE = (
    "reliability",
    "completeness",
    "temporal_correlation",
    "geographical_correlation",
    "further_technological_correlation",
)


def _u(name: str) -> str:
    return f"uncertainty.{name}"


_FIELD_RULES: list[tuple[str, Callable[[dict[str, Any]], str]]] = [
    (f"role NOT IN ({_ROLES})", lambda r: f"role: {r['role']!r} is not a role"),
    ("NOT isfinite(amount)", lambda r: f"amount: {r['amount']} is not a finite number"),
    ("process_id = ''", lambda r: "process_id: must not be empty"),
    ("line_id = ''", lambda r: "line_id: must not be empty"),
    ("flow_id = ''", lambda r: "flow_id: must not be empty"),
    ("unit = ''", lambda r: "unit: must not be empty"),
    (
        f"link_status NOT IN ({_LINK_STATUSES})",
        lambda r: f"link_status: {r['link_status']!r} is not a link status",
    ),
    (
        f"uncertainty IS NOT NULL AND (uncertainty.distribution IS NULL "
        f"OR uncertainty.distribution NOT IN ({_DISTRIBUTIONS}))",
        lambda r: f"uncertainty.distribution: {r['uncertainty']['distribution']!r} is not one",
    ),
    (
        "uncertainty IS NOT NULL AND ("
        + " OR ".join(f"NOT isfinite(coalesce({_u(p)}, 0))" for p in _PARAMETERS)
        + ")",
        lambda r: "uncertainty: its parameters must be finite numbers",
    ),
    (
        "pedigree IS NOT NULL AND ("
        + " OR ".join(f"coalesce(pedigree.{s}, 0) NOT BETWEEN 1 AND 5" for s in _PEDIGREE)
        + ")",
        lambda r: "pedigree: needs five scores from 1 to 5",
    ),
]

# The rules an uncertainty's own validator makes, in its order, per distribution's parameters.
_NEEDS = {
    "lognormal": ("loc", "scale"),
    "normal": ("loc", "scale"),
    "uniform": ("minimum", "maximum"),
    "triangular": ("loc", "minimum", "maximum"),
}
_INCOMPLETE = " OR ".join(
    f"(uncertainty.distribution = '{d}' AND ({' OR '.join(f'{_u(p)} IS NULL' for p in needs)}))"
    for d, needs in _NEEDS.items()
)

type _Rule = tuple[str, str, Callable[[dict[str, Any]], str]]
"""A record's own rule on a line: its code, the condition in SQL, the message from the row."""

# In the order a record checks them: its uncertainty's rules, the amount, then (after duplicate
# properties) the link status.
_VALUE_RULES: list[_Rule] = [
    (
        "incomplete-uncertainty",
        f"uncertainty IS NOT NULL AND ({_INCOMPLETE})",
        lambda r: (
            f"{r['uncertainty']['distribution']} uncertainty needs "
            + ", ".join(
                p for p in _NEEDS[r["uncertainty"]["distribution"]] if r["uncertainty"][p] is None
            )
        ),
    ),
    (
        "bad-uncertainty",
        f"uncertainty IS NOT NULL AND ({_u('scale')} <= 0 OR {_u('minimum')} > {_u('maximum')} "
        f"OR (uncertainty.distribution = 'triangular' AND "
        f"NOT {_u('loc')} BETWEEN {_u('minimum')} AND {_u('maximum')}))",
        lambda r: (
            f"{r['uncertainty']['distribution']} uncertainty has parameters that do not "
            f"fit: {_parameters(r['uncertainty'])}"
        ),
    ),
    (
        "negative-amount",
        f"amount < 0 AND role NOT IN ({_NEGATIVE_OK})",
        lambda r: (
            f"amount {r['amount']:g} is negative, but role {r['role']!r} needs a positive "
            "amount. Only emission and resource may be negative."
        ),
    ),
    (
        "reference-amount",
        "role = 'reference' AND amount = 0",
        lambda r: "the amount of the reference line must be above zero",
    ),
]

_LINK_RULES: list[_Rule] = [
    (
        "link-status",
        f"link_status IS NOT NULL AND role NOT IN ({_SUPPLIED})",
        lambda r: (
            f"role {r['role']!r} buys from no supplier, so it has no link status, but "
            f"{r['link_status']!r} is given"
        ),
    ),
    (
        "link-status",
        "link_status = 'linked' AND supplier_id IS NULL",
        lambda r: "link status 'linked' needs a supplier",
    ),
    (
        "link-status",
        "link_status != 'linked' AND supplier_id IS NOT NULL",
        lambda r: (
            f"link status {r['link_status']!r} takes no supplier, but {r['supplier_id']!r} is given"
        ),
    ),
]


def _parameters(u: Mapping[str, Any]) -> str:
    return ", ".join(f"{p} {u[p]:g}" for p in _PARAMETERS if u[p] is not None)


def _exchange_rows(tables: SourceTables, label: Callable[[int], str], rows: _Rows) -> None:
    e = tables.exchanges
    view = e.select(
        [
            "process_id",
            "line_id",
            "role",
            "flow_id",
            "amount",
            "unit",
            "supplier_id",
            "link_status",
            "uncertainty",
            "pedigree",
        ]
    ).append_column("row", pa.array(np.arange(e.num_rows, dtype=np.int64)))
    properties = _Properties(e.column("properties").combine_chunks())
    with closing(duckdb.connect(":memory:")) as con:
        con.register("line", view)
        con.register("process", tables.processes.select(["id", "reference_flow_id"]))
        for r, message in _failing(con, _FIELD_RULES):
            rows.field(_EXCHANGES, label(r["row"]), message(r))
        properties.fields(label, rows)
        for rules in (_VALUE_RULES, None, _LINK_RULES):
            if rules is None:
                properties.duplicates(label, rows)  # a record checks these between the two
                continue
            for r, (code, message) in _failing(con, [(c, (code, m)) for code, c, m in rules]):
                item = label(r["row"])
                rows.rule(_EXCHANGES, item, Problem(code, item, message(r)))
        _line_set_rules(con, rows)


def _failing[T](
    con: duckdb.DuckDBPyConnection, rules: Sequence[tuple[str, T]]
) -> list[tuple[dict[str, Any], T]]:
    """Each line that breaks a rule, with the rule's payload, in the order of the rules. One scan
    of the lines for all the rules."""
    flags = ", ".join(
        f"coalesce({condition}, false) AS rule_{i}" for i, (condition, _) in enumerate(rules)
    )
    any_flag = " OR ".join(f"rule_{i}" for i in range(len(rules)))
    found = _select(con, f"SELECT * FROM (SELECT *, {flags} FROM line) WHERE {any_flag}")
    return [(r, payload) for i, (_, payload) in enumerate(rules) for r in found if r[f"rule_{i}"]]


class _Properties:
    """The properties of every line, as one list of entries: each entry's line, name, value
    and unit."""

    def __init__(self, properties: Any) -> None:
        self.parents, self.names, items = _map_entries(properties)
        self.values = items.field("value")
        self.units = items.field("unit")

    def fields(self, label: Callable[[int], str], rows: _Rows) -> None:
        def blank(a: Any) -> Any:
            return compute.or_kleene(compute.is_null(a), compute.equal(a, ""))

        finite = compute.fill_null(compute.is_finite(self.values), False)
        for bad, reason in (
            (blank(self.names), "a property needs a name"),
            (compute.invert(finite), "a property's value must be a finite number"),
            (blank(self.units), "a property needs a unit"),
            (self._against_previous(compute.less), "must be sorted by name"),
        ):
            for row in self._rows(bad):
                rows.field(_EXCHANGES, label(row), f"properties: {reason}")

    def duplicates(self, label: Callable[[int], str], rows: _Rows) -> None:
        """A name given twice in a line. Sorted names put the two next to each other; unsorted
        ones are already refused."""
        same = self._against_previous(compute.equal)
        for i in np.flatnonzero(same):
            item = label(int(self.parents[i]))
            message = f"property {self.names[int(i)].as_py()!r} is given more than once"
            rows.rule(_EXCHANGES, item, Problem("duplicate-property", item, message))

    def _against_previous(self, compare: Callable[[Any, Any], Any]) -> np.ndarray[Any, Any]:
        """For each entry, whether `compare(name, previous name in the same line)` holds."""
        found = np.zeros(len(self.parents), dtype=bool)
        if len(self.parents) > 1:
            names = compute.fill_null(self.names, "")
            result = compare(names.slice(1), names.slice(0, len(names) - 1))
            same_line = self.parents[1:] == self.parents[:-1]
            found[1:] = same_line & result.to_numpy(zero_copy_only=False)
        return found

    def _rows(self, bad: Any) -> list[int]:
        if not isinstance(bad, np.ndarray):
            bad = compute.fill_null(bad, False).to_numpy(zero_copy_only=False)
        mask: np.ndarray[Any, np.dtype[np.bool_]] = np.asarray(cast(Any, bad), dtype=bool)
        return sorted({int(r) for r in self.parents[mask]})


def _line_set_rules(con: duckdb.DuckDBPyConnection, rows: _Rows) -> None:
    """What a process record checks of its lines (one reference), and what only a table can get
    wrong: a line of a process that is not there, a reference flow that is not the reference
    line's."""
    orphans = "SELECT * FROM line WHERE process_id NOT IN (SELECT id FROM process)"
    for r in _select(con, orphans):
        item = f"{r['process_id']} line {r['line_id']}"
        message = "is a line of a process that has no row in processes"
        rows.rule(_EXCHANGES, item, Problem("unknown-process", item, message))
    references = """
        SELECT p.id, p.reference_flow_id,
               list(l.line_id ORDER BY l.line_id) AS lines,
               list(l.flow_id ORDER BY l.line_id) AS flows
        FROM process p LEFT JOIN line l ON l.process_id = p.id AND l.role = 'reference'
        GROUP BY p.id, p.reference_flow_id
        HAVING count(l.line_id) != 1 OR any_value(l.flow_id) != p.reference_flow_id
        ORDER BY p.id
    """
    for r in _select(con, references):
        item = f"process {r['id']}"
        found_lines: list[Any] = r["lines"] or []
        lines: list[str] = [x for x in found_lines if x is not None]
        if len(lines) != 1:
            found = ", ".join(lines) if lines else "none"
            message = f"needs exactly one reference line, found {found}"
            rows.rule(_PROCESSES, item, Problem("reference-count", item, message))
        elif r["flows"][0] != r["reference_flow_id"]:
            message = (
                f"has reference_flow_id {r['reference_flow_id']!r}, but its reference line "
                f"{lines[0]} carries {r['flows'][0]!r}"
            )
            rows.rule(_PROCESSES, item, Problem("reference-flow-mismatch", item, message))


def _select(con: duckdb.DuckDBPyConnection, sql: str) -> list[dict[str, Any]]:
    return con.sql(sql).to_arrow_table().to_pylist()


# --- how the rows fit together ---------------------------------------------------------------


def freeze_problems(
    info: ReleaseInfo,
    tables: SourceTables,
    methods: Sequence[Method],
    units: Units,
) -> tuple[list[Problem], list[Unit]]:
    """Every problem that stops freezing valid rows, and the units the rows use.

    The flows first, then their properties, the lines, the issues and the methods, each by key.
    The problems of one line come in a fixed order.
    """
    t = tables
    problems: list[Problem] = []
    with closing(_connect(t, units)) as con:
        known = _Known(con)
        problems += _flow_problems(con, units)
        problems += _property_problems(con, known, units)
        problems += line_problems(con, known, t, units)
        problems += _issue_problems(con)
        used = _used_units(con, units)
    for method in sorted(methods, key=lambda m: m.id):
        problems += method_problems(method, info.flow_list, info.name)
    return problems, used


def method_problems(method: Method, flow_list: str, release: str) -> list[Problem]:
    """A method mapped to another flow list, or with an id that cannot name a folder."""
    problems: list[Problem] = []
    if method.flow_list != flow_list:
        problems.append(
            Problem(
                "flow-list-mismatch",
                f"method {method.id}",
                f"is mapped to flow list {method.flow_list!r}, but release {release} "
                f"uses {flow_list!r}",
            )
        )
    if not re.fullmatch(FOLDER_NAME, method.id):
        problems.append(
            Problem(
                "bad-method-id",
                f"method {method.id}",
                "a method id names a folder: use letters, digits, '.', '_' and '-' only",
            )
        )
    return problems


def _connect(t: SourceTables, units: Units) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    try:
        names = (
            t.flow_names
            if t.flow_names is not None
            else pa.nulls(t.exchanges.num_rows, pa.string())
        )
        con.register(
            "line",
            t.exchanges.select(
                [
                    "process_id",
                    "line_id",
                    "role",
                    "flow_id",
                    "amount",
                    "unit",
                    "supplier_id",
                    "link_status",
                ]
            ).append_column("flow_name", names),
        )
        con.register("process", t.processes.select(["id", "reference_flow_id"]))
        con.register("flow", t.flows.select(["id", "name", "kind", "unit"]))
        con.register("flow_property", t.flow_properties)
        con.register("issue", t.issues)
        con.register(
            "unit_table",
            pa.Table.from_pydict(
                {"unit": [u.name for u in units], "quantity": [u.quantity for u in units]}
            ),
        )
        kinds = [(r.value, k.value) for r, allowed in ROLE_FLOW_KINDS.items() for k in allowed]
        con.register(
            "role_kind",
            pa.Table.from_pydict({"role": [r for r, _ in kinds], "kind": [k for _, k in kinds]}),
        )
        signed = {(e.role, e.treatment) for e in role_entries()}
        unsigned = [
            (r, treats) for r in Role for treats in (False, True) if (r, treats) not in signed
        ]
        con.register(
            "no_sign",
            pa.Table.from_pydict(
                {
                    "role": pa.array([r.value for r, _ in unsigned], pa.string()),
                    "treatment": pa.array([treats for _, treats in unsigned], pa.bool_()),
                }
            ),
        )
    except BaseException:
        con.close()
        raise
    return con


class _Known:
    """The ids of the release, for "did you mean" in a message. Read only when one is needed."""

    def __init__(self, con: duckdb.DuckDBPyConnection) -> None:
        self.con = con
        self._cache: dict[str, list[str]] = {}

    def ids(self, table: str) -> list[str]:
        if table not in self._cache:
            self._cache[table] = [r["id"] for r in _select(self.con, f"SELECT id FROM {table}")]
        return self._cache[table]


def _unknown(code: str, item: str, what: str, value: str, known: Iterable[str]) -> Problem:
    return Problem(
        code, item, f"{what} {value!r} is not in the release.{did_you_mean(value, known)}"
    )


def _flow_problems(con: duckdb.DuckDBPyConnection, units: Units) -> list[Problem]:
    sql = "SELECT id, unit FROM flow WHERE unit NOT IN (SELECT unit FROM unit_table) ORDER BY id"
    return [units.unknown(r["unit"], f"flow {r['id']}") for r in _select(con, sql)]


def _property_problems(
    con: duckdb.DuckDBPyConnection, known: _Known, units: Units
) -> list[Problem]:
    sql = """
        SELECT flow_id, property, unit,
               flow_id NOT IN (SELECT id FROM flow) AS unknown_flow,
               unit NOT IN (SELECT unit FROM unit_table) AS unknown_unit
        FROM flow_property
        WHERE flow_id NOT IN (SELECT id FROM flow) OR unit NOT IN (SELECT unit FROM unit_table)
        ORDER BY flow_id, property
    """
    problems: list[Problem] = []
    for r in _select(con, sql):
        item = f"flow {r['flow_id']} property {r['property']}"
        if r["unknown_flow"]:
            problems.append(_unknown("unknown-flow", item, "flow", r["flow_id"], known.ids("flow")))
        if r["unknown_unit"]:
            problems.append(units.unknown(r["unit"], item))
    return problems


@dataclass(frozen=True)
class _LineCheck:
    code: str
    where: str
    message: Callable[[dict[str, Any], _Known], str]


def _kinds(role: str) -> str:
    allowed = sorted(k.value for k in ROLE_FLOW_KINDS[Role(role)])
    text = " or ".join(allowed)
    return f"{'an' if text[0] in 'aeiou' else 'a'} {text}"


_SUPPLIED_ROLE = f"l.role IN ({_SUPPLIED})"

# The checks of one line, in the order its problems are listed. `l` is the line, `f` its flow,
# `s` its supplier and `u` its unit; each is NULL when it is not in the release.
_LINE_CHECKS = [
    _LineCheck(
        "unknown-flow",
        "f.id IS NULL",
        lambda r, k: (
            f"flow {r['flow_id']!r} is not in the release."
            f"{did_you_mean(r['flow_id'], k.ids('flow'))}"
        ),
    ),
    _LineCheck(
        "flow-name-mismatch",
        "f.id IS NOT NULL AND l.flow_name IS NOT NULL AND l.flow_name != f.name",
        lambda r, k: (
            f"names flow {r['flow_id']!r} {r['flow_name']!r}, but its name is {r['flow_display']!r}"
        ),
    ),
    _LineCheck(
        "role-flow-kind",
        "f.id IS NOT NULL AND NOT EXISTS "
        "(SELECT 1 FROM role_kind k WHERE k.role = l.role AND k.kind = f.kind)",
        lambda r, k: (
            f"role {r['role']!r} needs {_kinds(r['role'])} flow, but "
            f"{r['flow_id']!r} is a {r['flow_kind']} flow"
        ),
    ),
    _LineCheck(
        "role-no-sign",
        "EXISTS (SELECT 1 FROM no_sign n, process p, flow pf "
        "WHERE p.id = l.process_id AND pf.id = p.reference_flow_id "
        "AND n.role = l.role AND n.treatment = (pf.kind = 'waste'))",
        lambda r, k: (
            f"role {r['role']!r} has no sign in a process that treats waste. "
            "Use another role, or remove the line."
        ),
    ),
    _LineCheck(
        "unknown-supplier",
        "l.supplier_id IS NOT NULL AND s.id IS NULL",
        lambda r, k: (
            f"supplier {r['supplier_id']!r} is not in the release."
            f"{did_you_mean(r['supplier_id'], k.ids('process'))}"
        ),
    ),
    _LineCheck(
        "supplier-not-allowed",
        f"l.supplier_id IS NOT NULL AND NOT {_SUPPLIED_ROLE}",
        lambda r, k: f"role {r['role']!r} takes no supplier, but {r['supplier_id']!r} is given",
    ),
    _LineCheck(
        "own-use-supplier",
        "l.role = 'own_use' AND l.supplier_id IS NOT NULL AND l.supplier_id != l.process_id",
        lambda r, k: (
            f"own_use is the process's own product, so its supplier is "
            f"{r['process_id']!r}, not {r['supplier_id']!r}"
        ),
    ),
    _LineCheck(
        "supplier-wrong-flow",
        f"{_SUPPLIED_ROLE} AND f.id IS NOT NULL AND s.id IS NOT NULL "
        "AND s.reference_flow_id != l.flow_id "
        "AND NOT (l.role = 'own_use' AND l.supplier_id != l.process_id)",
        lambda r, k: (
            f"supplier {r['supplier_id']!r} makes {r['supplier_flow_id']!r}, not {r['flow_id']!r}"
        ),
    ),
    _LineCheck(
        "unlinked-line",
        f"{_SUPPLIED_ROLE} AND l.supplier_id IS NULL AND l.link_status IS NULL",
        lambda r, k: (
            f"{r['role']} of flow {r['flow_id']} has no supplier and no link status. "
            "Link it, or resolve it as cut_off or data_gap with an issue."
        ),
    ),
    _LineCheck(
        "missing-issue",
        f"{_SUPPLIED_ROLE} AND l.supplier_id IS NULL AND l.link_status IS NOT NULL "
        "AND NOT EXISTS (SELECT 1 FROM issue i "
        "WHERE i.process_id = l.process_id AND i.line_id = l.line_id)",
        lambda r, k: (
            f"is resolved as {r['link_status']}, but no issue records why. "
            "Add a resolved issue for this line."
        ),
    ),
]


def line_problems(
    con: duckdb.DuckDBPyConnection,
    known: _Known,
    t: SourceTables,
    units: Units,
) -> list[Problem]:
    """Every line's problems, by process and line id, each line's in the order of the checks."""
    found: list[tuple[str, str, int, Problem]] = []
    joined = """
        FROM line l
        LEFT JOIN flow f ON f.id = l.flow_id
        LEFT JOIN process s ON s.id = l.supplier_id
    """
    columns = (
        "l.process_id, l.line_id, l.role, l.flow_id, l.flow_name, l.supplier_id, "
        "l.link_status, f.name AS flow_display, f.kind AS flow_kind, "
        "s.reference_flow_id AS supplier_flow_id"
    )
    for order, check in enumerate(_LINE_CHECKS):
        for r in _select(con, f"SELECT {columns} {joined} WHERE {check.where}"):
            item = f"{r['process_id']} line {r['line_id']}"
            found.append(
                (
                    r["process_id"],
                    r["line_id"],
                    order,
                    Problem(check.code, item, check.message(r, known)),
                )
            )
    unit_order = len(_LINE_CHECKS)
    unknown_units = (
        "SELECT process_id, line_id, flow_id, unit FROM line "
        "WHERE unit NOT IN (SELECT unit FROM unit_table)"
    )
    for r in _select(con, unknown_units):
        item = f"{r['process_id']} line {r['line_id']}"
        problem = units.unknown(r["unit"], item, f" (flow {r['flow_id']})")
        found.append((r["process_id"], r["line_id"], unit_order, problem))
    for process_id, line_id, problem in _conversion_problems(con, t, units):
        found.append((process_id, line_id, unit_order + 1, problem))
    found.sort(key=lambda x: x[:3])
    return [p for *_, p in found]


def _conversion_problems(
    con: duckdb.DuckDBPyConnection, t: SourceTables, units: Units
) -> list[tuple[str, str, Problem]]:
    """Lines in a unit of another quantity than their flow's, which a property of the flow must
    convert. `Catalog.convert` holds that rule; it is asked only for these lines, which are few."""
    sql = """
        SELECT l.process_id, l.line_id, l.role, l.flow_id, l.amount, l.unit
        FROM line l
        JOIN flow f ON f.id = l.flow_id
        JOIN unit_table lu ON lu.unit = l.unit
        JOIN unit_table fu ON fu.unit = f.unit
        WHERE lu.quantity != fu.quantity
    """
    rows = _select(con, sql)
    if not rows:
        return []
    wanted = pa.array(sorted({r["flow_id"] for r in rows}), pa.string())
    flows = t.flows.filter(compute.is_in(t.flows.column("id"), value_set=wanted))
    properties = t.flow_properties.filter(
        compute.is_in(t.flow_properties.column("flow_id"), value_set=wanted)
    )
    catalog = Catalog(read_flows(flows), (), read_flow_properties(properties), units)
    problems: list[tuple[str, str, Problem]] = []
    for r in rows:
        line = Exchange(
            process_id=r["process_id"],
            line_id=r["line_id"],
            role=Role(r["role"]),
            flow_id=r["flow_id"],
            amount=r["amount"] if math.isfinite(r["amount"]) else 0.0,
            unit=r["unit"],
        )
        try:
            catalog.convert(line)
        except InvalidDataError as error:
            problems += [(r["process_id"], r["line_id"], p) for p in error.problems]
    return problems


def _issue_problems(con: duckdb.DuckDBPyConnection) -> list[Problem]:
    sql = """
        SELECT i.process_id, i.line_id, i.flow_id, i.status, i.supplier_id,
               l.line_id IS NULL AS no_line, l.flow_id AS line_flow,
               l.supplier_id AS line_supplier, l.link_status AS line_status
        FROM issue i LEFT JOIN line l ON l.process_id = i.process_id AND l.line_id = i.line_id
        ORDER BY i.process_id, i.line_id
    """
    problems: list[Problem] = []
    for r in _select(con, sql):
        item = f"issue {r['process_id']} line {r['line_id']}"
        if r["no_line"]:
            problems.append(
                Problem("unknown-line", item, "points at a line that is not in the release")
            )
            continue
        if r["flow_id"] != r["line_flow"]:
            problems.append(
                Problem(
                    "issue-mismatch",
                    item,
                    f"is about flow {r['flow_id']}, but the line carries {r['line_flow']}",
                )
            )
        status = r["status"]
        if status == "open":
            problems.append(
                Problem(
                    "open-issue",
                    item,
                    f"flow {r['flow_id']} is still open. Resolve it as linked, cut_off or "
                    "data_gap before freezing.",
                )
            )
        elif status == "linked":
            if r["line_supplier"] != r["supplier_id"]:
                problems.append(
                    Problem(
                        "issue-mismatch",
                        item,
                        f"is resolved as linked to {r['supplier_id']}, but the line's supplier "
                        f"is {r['line_supplier']}",
                    )
                )
        elif r["line_status"] != status:
            problems.append(
                Problem(
                    "issue-mismatch",
                    item,
                    f"is resolved as {status}, but the line's link status is {r['line_status']}",
                )
            )
    return problems


def _used_units(con: duckdb.DuckDBPyConnection, units: Units) -> list[Unit]:
    sql = """
        SELECT DISTINCT unit FROM (
            SELECT unit FROM flow UNION ALL SELECT unit FROM flow_property
            UNION ALL SELECT unit FROM line
        ) WHERE unit IN (SELECT unit FROM unit_table)
    """
    found = [units.get(r["unit"]) for r in _select(con, sql)]
    return sorted((u for u in found if u is not None), key=lambda u: u.name)
