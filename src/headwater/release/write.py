"""Freeze records into a release: check them, write them to a temporary folder, then rename it.

A release folder appears in one rename, after every file is written and synced, so a crash
leaves no release folder. A crash may leave a hidden `.tmp-...` folder, which nothing reads.
"""

import json
import os
import re
import shutil
from collections.abc import Iterable, Mapping, Sequence
from importlib.metadata import version
from pathlib import Path
from typing import Any, cast

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from numpy.typing import NDArray
from pydantic import JsonValue

from headwater.model import (
    DEFAULT_UNITS,
    Flow,
    FlowProperty,
    InvalidDataError,
    Issue,
    Method,
    Problem,
    Process,
    Units,
)
from headwater.release.cache import releases_dir, temporary_folder
from headwater.release.checks import (
    SourceTables,
    freeze_problems,
    key_problems,
    method_problems,
    row_problems,
)
from headwater.release.content import (
    canonical_json,
    conform,
    method_hash,
    release_hash,
    sort_rows,
    sorted_table_digest,
    table_digest,
)
from headwater.release.manifest import FOLDER_NAME, FORMAT, MethodEntry, ReleaseInfo, ReleaseKind
from headwater.release.read import (
    MANIFEST,
    Release,
    file_sha256,
    open_expecting,
    read_table,
)
from headwater.release.schema import (
    EXCHANGES,
    FACTORS,
    FLOW_NAME,
    FLOW_PROPERTIES,
    FLOWS,
    GEOGRAPHIES,
    ISSUES,
    PROCESSES,
    UNITS,
    TableSpec,
    exchanges_table,
    factors_path,
    factors_table,
    flow_properties_table,
    flows_table,
    indicators_path,
    indicators_spec,
    indicators_table,
    issues_table,
    processes_table,
    units_table,
)

PARQUET_SETTINGS: dict[str, Any] = {
    "version": "2.6",
    "compression": "zstd",
    "compression_level": 3,
    "row_group_size": 131_072,
    "data_page_version": "1.0",
    "use_dictionary": True,
    "write_statistics": True,
    "store_schema": True,
}
"""Fixed, so one version of pyarrow always writes the same bytes. The hash does not depend on
them."""


def build(
    info: ReleaseInfo,
    *,
    processes: Iterable[Process],
    flows: Iterable[Flow],
    flow_properties: Iterable[FlowProperty] = (),
    units: Units = DEFAULT_UNITS,
    issues: Iterable[Issue] = (),
    methods: Iterable[Method] = (),
    geographies: pa.Table | None = None,
    into: Path | str | None = None,
) -> Release:
    """Freeze records into a source release in `into`, by default the cache's releases folder.

    The records become the tables of `build_tables`, which checks them and refuses, naming every
    problem. A line's `flow_name` is checked against the flow, then not stored. A time an issue
    was resolved needs a time zone; it is stored in UTC.

    `units` is a lookup table: only the units the records use are stored. `geographies` is the
    source's table of places, `code` and `name`, as `headwater.io.geographies` reads it; without
    it the release's is empty. Building the same records twice gives the same release, and the
    existing folder is kept.
    """
    processes, issues = tuple(processes), tuple(issues)
    naive = [_naive_time(i) for i in issues if i.resolved_at and i.resolved_at.utcoffset() is None]
    if naive:
        raise InvalidDataError(naive)
    return build_tables(
        info,
        processes=processes_table(processes),
        exchanges=exchanges_table(processes, flow_names=True),
        flows=flows_table(flows),
        flow_properties=flow_properties_table(flow_properties),
        issues=issues_table(issues),
        geographies=geographies,
        units=units,
        methods=methods,
        into=into,
    )


def build_tables(
    info: ReleaseInfo,
    *,
    processes: pa.Table,
    exchanges: pa.Table,
    flows: pa.Table,
    flow_properties: pa.Table | None = None,
    issues: pa.Table | None = None,
    geographies: pa.Table | None = None,
    units: Units = DEFAULT_UNITS,
    methods: Iterable[Method] = (),
    into: Path | str | None = None,
) -> Release:
    """Freeze Arrow tables into a source release, with no record made per line.

    Each table has the columns of its file, in any order and any row order (see
    docs/design.md, "A release"). `exchanges` may also have a `flow_name` column, which is
    checked against the flow, then not stored. `resolved_at` is UTC with no time zone.

    Refuses, naming every problem: an id defined twice; a row that is not a valid record (the
    same rules as the records, with the same codes); a line whose flow, unit or supplier is not in
    the release; a role that does not fit its flow's kind or has no sign in its process (an own
    use in a treatment); a supplier that does not make the flow bought from it, or an own use
    bought from another process; a unit of another quantity that no property of the flow
    converts; a line that buys from no supplier and is not resolved; an issue still open or that
    does not match its line; a method on another flow list.
    `geographies`, when given, has a row per place, `code` and `name`.
    """
    methods = tuple(methods)
    names = exchanges.column(FLOW_NAME) if FLOW_NAME in exchanges.column_names else None
    if names is not None:
        exchanges = exchanges.drop_columns([FLOW_NAME])
    tables = SourceTables(
        processes=conform(processes, PROCESSES, "processes.parquet"),
        exchanges=conform(exchanges, EXCHANGES, "exchanges.parquet"),
        flows=conform(flows, FLOWS, "flows.parquet"),
        flow_properties=conform(
            _or_empty(flow_properties, FLOW_PROPERTIES), FLOW_PROPERTIES, "flow_properties.parquet"
        ),
        issues=conform(_or_empty(issues, ISSUES), ISSUES, "issues.parquet"),
        geographies=conform(
            _or_empty(geographies, GEOGRAPHIES), GEOGRAPHIES, "geographies.parquet"
        ),
        flow_names=names,
    )
    for find in (lambda: key_problems(tables, methods), lambda: row_problems(tables)):
        problems = find()
        if problems:
            raise InvalidDataError(problems)
    problems, used_units = freeze_problems(info, tables, methods, units)
    if problems:
        raise InvalidDataError(problems)
    files: dict[str, tuple[TableSpec, pa.Table]] = {
        "processes.parquet": (PROCESSES, tables.processes),
        "flows.parquet": (FLOWS, tables.flows),
        "flow_properties.parquet": (FLOW_PROPERTIES, tables.flow_properties),
        "units.parquet": (UNITS, units_table(used_units)),
        "exchanges.parquet": (EXCHANGES, tables.exchanges),
        "issues.parquet": (ISSUES, tables.issues),
        "geographies.parquet": (GEOGRAPHIES, tables.geographies),
    }
    for method in methods:
        files[factors_path(method.id)] = (FACTORS, factors_table(method.factors))
    manifest: dict[str, Any] = {
        "format": FORMAT,
        "kind": ReleaseKind.source.value,
        **info.model_dump(mode="json"),
        "methods": [_method_entry(m) for m in sorted(methods, key=lambda m: m.id)],
        "parent": None,
        "recipe": None,
        "provenance": None,
    }
    return _publish(manifest, files, Path(into) if into is not None else releases_dir())


def _or_empty(table: pa.Table | None, spec: TableSpec) -> pa.Table:
    return table if table is not None else spec.schema.empty_table()


def _naive_time(issue: Issue) -> Problem:
    assert issue.resolved_at is not None
    return Problem(
        "naive-time",
        f"issue {issue.process_id} line {issue.line_id}",
        f"resolved_at {issue.resolved_at.isoformat()} has no time zone. "
        "Give one, for example 2026-10-11T09:30:00+00:00.",
    )


def build_indicators(
    parent: Release,
    method: Method | Sequence[Method],
    *,
    product_ids: Sequence[str],
    scores: Mapping[str, NDArray[np.float64]],
    solver: Mapping[str, JsonValue],
    provenance: Mapping[str, JsonValue] | None = None,
    name: str | None = None,
    into: Path | str | None = None,
) -> Release:
    """Freeze solved scores into an indicators release that pins `parent` and `method`.

    `scores` maps each indicator of the method to one float64 score per id in `product_ids`,
    which must be exactly the parent's processes. The folder holds
    `indicators/<method>.parquet` and the method's factors. It goes beside the parent by
    default, named `<parent>.<method>`, and the parent is not touched.

    `method` can be several methods used together, whose indicators have different ids. Each is
    held as it is, with its own factors, indicators file and hash, and the default name joins
    their ids with dots: `<parent>.EF-3.1.IPCC-2021`.

    The release is named by its recipe: the parent's hash, the method's hash, this engine's
    version and `solver`, the settings the scores were asked for with. Scores from another
    machine differ in the last bits and give the same release. `provenance` records what the
    solve gave (steps, residual), and is not part of the hash. Building a recipe that exists
    keeps the existing release, after checking the scores agree within `RECIPE_TOLERANCE`.
    """
    methods = (method,) if isinstance(method, Method) else tuple(method)
    problems = _check_scores(parent, methods, product_ids, scores)
    release_name = name or ".".join([parent.name, *(m.id for m in methods)])
    if not re.fullmatch(FOLDER_NAME, release_name):
        problems.append(
            Problem(
                "bad-name",
                f"release {release_name}",
                "a release name is a folder name: letters, digits, '.', '_' and '-' only",
            )
        )
    recipe: dict[str, JsonValue] = {"engine_version": version("headwater"), "solver": dict(solver)}
    for key, value in (("recipe", recipe), ("provenance", provenance)):
        try:
            canonical_json(value)
        except (TypeError, ValueError) as exc:
            problems.append(
                Problem(f"bad-{key}", key, f"must be plain JSON with finite numbers: {exc}")
            )
    if problems:
        raise InvalidDataError(problems)
    info = ReleaseInfo.model_validate({**parent.info.model_dump(), "name": release_name})
    tables: dict[str, tuple[TableSpec, pa.Table]] = {}
    for m in methods:
        own = {i.id: scores[i.id] for i in m.indicators}
        tables[factors_path(m.id)] = (FACTORS, factors_table(m.factors))
        tables[indicators_path(m.id)] = (
            indicators_spec(own),
            indicators_table(product_ids, {k: v.tolist() for k, v in own.items()}),
        )
    manifest: dict[str, Any] = {
        "format": FORMAT,
        "kind": ReleaseKind.indicators.value,
        **info.model_dump(mode="json"),
        "methods": [_method_entry(m) for m in methods],
        "parent": parent.ref.model_dump(),
        "recipe": recipe,
        "provenance": dict(provenance) if provenance is not None else None,
    }
    return _publish(manifest, tables, Path(into) if into is not None else parent.path.parent)


def _method_entry(method: Method) -> dict[str, Any]:
    return MethodEntry(
        id=method.id,
        version=method.version,
        variant=method.variant,
        flow_list=method.flow_list,
        indicators=tuple(sorted(method.indicators, key=lambda i: i.id)),
        hash=method_hash(method),
    ).model_dump(mode="json")


# --- publishing -----------------------------------------------------------------------------


def _publish(
    manifest: dict[str, Any], tables: Mapping[str, tuple[TableSpec, pa.Table]], into: Path
) -> Release:
    """Hash the tables, write them to a temporary folder, check what was written, rename."""
    sorted_tables = {
        name: sort_rows(conform(table, spec, name), spec.key)
        for name, (spec, table) in sorted(tables.items())
    }
    specs = {name: spec for name, (spec, _) in tables.items()}
    manifest["tables"] = {
        name: {"rows": table.num_rows, "content_sha256": sorted_table_digest(table, specs[name])}
        for name, table in sorted_tables.items()
    }
    release = release_hash(manifest)
    folder = f"{manifest['name']}@{release[:12]}"
    target = into / folder
    if target.exists():
        return _existing(target, release, sorted_tables)
    temp = temporary_folder(into, manifest["name"])
    try:
        files: dict[str, str] = {}
        for name, table in sorted_tables.items():
            path = temp / name
            path.parent.mkdir(parents=True, exist_ok=True)
            _write_table(table, path)
            files[name] = file_sha256(path)
        for name, spec in specs.items():
            written = table_digest(read_table(temp, name, spec), spec, name)
            if written != manifest["tables"][name]["content_sha256"]:
                raise InvalidDataError(
                    [Problem("write-mismatch", name, "reads back different rows than were written")]
                )
        manifest["hash"] = release
        manifest["engine_version"] = version("headwater")
        manifest["files"] = files
        text = json.dumps(manifest, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
        (temp / MANIFEST).write_text(text, encoding="utf-8")
        _sync(temp)
        try:
            temp.rename(target)
        except OSError:
            if not target.exists():
                raise
            shutil.rmtree(temp)  # another process published the same release first
            return open_expecting(target, release)
        _sync_folder(into)
    except BaseException:
        shutil.rmtree(temp, ignore_errors=True)
        raise
    return Release.open(target)


RECIPE_TOLERANCE = 1e-9
"""Largest relative difference between two scores of one recipe, each against the larger.

The same recipe solved on two machines differs by rounding only. Every solve is checked to a
componentwise residual of 1e-12 or less, so scores 1e-9 apart come from another computation:
the engine changed without a new version, and the release on disk would silently win."""


def _existing(target: Path, release: str, tables: Mapping[str, pa.Table]) -> Release:
    """The release already in `target`. An indicators release is named by its recipe, so its
    scores are compared with the new ones."""
    found = open_expecting(target, release)
    if found.kind is not ReleaseKind.indicators:
        return found
    problems: list[Problem] = []
    for name, new in tables.items():
        if not name.startswith("indicators/"):
            continue
        old = found.table(name)
        ids = cast(list[str], new.column("product_id").to_pylist())
        for indicator in new.column_names[1:]:
            a = old.column(indicator).to_numpy()
            b = new.column(indicator).to_numpy()
            far = np.abs(a - b) > RECIPE_TOLERANCE * np.maximum(np.abs(a), np.abs(b))
            if far.any():
                shown = ", ".join(ids[int(i)] for i in np.flatnonzero(far)[:10])
                problems.append(
                    Problem(
                        "recipe-conflict",
                        f"indicator {indicator}",
                        f"release {target.name} has the same recipe, but scores that differ "
                        f"by more than {RECIPE_TOLERANCE:g} for {int(far.sum())} products: "
                        f"{shown}. Did the engine change without a new version?",
                    )
                )
    if problems:
        raise InvalidDataError(problems)
    return found


def _write_table(table: pa.Table, path: Path) -> None:
    pq.write_table(table, path, **PARQUET_SETTINGS)  # pyright: ignore[reportUnknownMemberType]


def _sync(folder: Path) -> None:
    """Flush every file and folder to disk, so the rename never shows half-written files."""
    for path in sorted(folder.rglob("*")):
        if path.is_file():
            fd = os.open(path, os.O_RDWR)  # Windows flushes only a handle opened for writing
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        else:
            _sync_folder(path)
    _sync_folder(folder)


def _sync_folder(folder: Path) -> None:
    if os.name == "nt":  # Windows cannot open a folder to flush it
        return
    fd = os.open(folder, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


# --- checks ---------------------------------------------------------------------------------


def _check_scores(
    parent: Release,
    methods: Sequence[Method],
    product_ids: Sequence[str],
    scores: Mapping[str, NDArray[np.float64]],
) -> list[Problem]:
    problems: list[Problem] = []
    if parent.kind is not ReleaseKind.source:
        return [
            Problem(
                "not-a-source-release",
                parent.name,
                f"is a {parent.kind.value} release; indicators are solved from a source release",
            )
        ]
    for method in methods:
        problems += method_problems(method, parent.info.flow_list, parent.name)
    ids = [m.id for m in methods]
    for twice in sorted({i for i in ids if ids.count(i) > 1}):
        problems.append(Problem("duplicate-method", f"method {twice}", "is given twice"))
    owner: dict[str, str] = {}
    for method in methods:
        for i in method.indicators:
            if owner.setdefault(i.id, method.id) != method.id:
                problems.append(
                    Problem(
                        "duplicate-indicator",
                        f"indicator {i.id}",
                        f"is in method {owner[i.id]} and in method {method.id}",
                    )
                )
    label = "+".join(ids)
    known = set(cast(list[str], parent.table("processes").column("id").to_pylist()))
    given: set[str] = set()
    for i in product_ids:
        if i in given:
            problems.append(Problem("duplicate-score", f"process {i}", "is defined more than once"))
        given.add(i)
    for missing in sorted(known - given):
        problems.append(Problem("missing-score", f"process {missing}", "has no score"))
    for unknown in sorted(given - known):
        problems.append(
            Problem("unknown-process", f"process {unknown}", f"is not in release {parent.name}")
        )
    indicators = set(owner)
    if "product_id" in indicators:
        problems.append(
            Problem(
                "bad-indicator-id",
                f"method {owner['product_id']}",
                "an indicator is called product_id, the name of the key column",
            )
        )
    if set(scores) != indicators:
        problems.append(
            Problem(
                "indicator-mismatch",
                f"method {label}",
                f"has indicators {', '.join(sorted(indicators))}, but scores are given for "
                f"{', '.join(sorted(scores))}",
            )
        )
        return problems
    for indicator in sorted(scores):
        values = scores[indicator]
        item = f"indicator {indicator}"
        if values.dtype != np.float64:
            problems.append(Problem("not-float64", item, f"scores are {values.dtype}, not float64"))
        elif values.shape != (len(product_ids),):
            problems.append(
                Problem(
                    "wrong-length",
                    item,
                    f"has {values.shape} scores for {len(product_ids)} products",
                )
            )
        elif not np.isfinite(values).all():
            bad = [product_ids[int(i)] for i in np.flatnonzero(~np.isfinite(values))[:10]]
            problems.append(
                Problem(
                    "not-finite",
                    item,
                    f"scores are NaN or infinite for {', '.join(bad)}",
                )
            )
    return problems
