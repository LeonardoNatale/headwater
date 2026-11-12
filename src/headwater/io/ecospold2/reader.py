"""Read an ecoSpold2 folder (`datasets/` and `MasterData/`) into an inventory source.

`read` parses every dataset, in parallel by default, and checks the lines against MasterData.
Problems that make the records wrong raise `InvalidDataError` naming each one. Each line keeps the
supplier its file names by UUID, unlinked: linking is a shared stage.

The lines travel as Arrow tables, from the workers to `build_tables`. A record is made for every
line in the worker that reads it, which checks it.
"""

import multiprocessing
import os
import re
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from functools import partial
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc

from headwater.io.ecospold2.dataset import FileInfo, ParsedBatch, forget_shared, parse_files
from headwater.io.ecospold2.geographies import FILE as GEOGRAPHIES_FILE
from headwater.io.ecospold2.geographies import read_geographies
from headwater.io.ecospold2.masterdata import MasterData, read_masterdata
from headwater.io.exclude import Exclusion
from headwater.io.source import InventoryOptions, InventorySource, as_lines, lookup
from headwater.io.units import unit_table
from headwater.link import DataGap
from headwater.model import Flow, FlowKind, InvalidDataError, Problem, Role, Severity
from headwater.release import ReleaseInfo

compute: Any = pc

SOURCE = "ecoinvent"
LICENCE = "ecoinvent EULA"

_FILE_NAME = re.compile(r"^[0-9a-f-]{36}_[0-9a-f-]{36}\.spold$")

_VERSION = re.compile(r"^(\d+)\.(\d+)(\.\d+)?$")
_UNSTAMPED = (3, 0)
"""ecoinvent 3.12 writes release 3.0 in every file. A source whose files name no later release
does not say which version it is."""

_WINDOWS_MAX_WORKERS = 61
"""ProcessPoolExecutor refuses more workers than this on Windows."""

_CHUNK = 128
"""Most files per task sent to a worker. Large enough that a batch's tables are worth sending,
small enough to keep every worker busy to the end."""


def read(folder: Path, options: InventoryOptions) -> InventorySource:
    """Read every dataset of `folder`, ecoinvent `options.version` (for example "3.12"), except
    the excluded files. Links nothing: that is `headwater.io.inventory.link_inventory`.

    The files do not record the database's version: each one gives the release it last changed
    in. So the version is given, and must be the newest of those releases, with an optional third
    part ("3.9.1"). It goes into every process id. ecoinvent 3.12 writes release 3.0 in every
    file: when no file names a release after 3.0, the version is taken as given, with the warning
    `version-unchecked`.

    `options.workers` is the number of processes that parse files, by default one per CPU; 1
    parses in this process. The result does not depend on it. With more than one, a script that
    calls this needs `if __name__ == "__main__":`, as any Python multiprocessing does.
    """
    root = folder
    version = options.version
    wanted = _VERSION.match(version or "")
    if version is None or wanted is None:
        raise InvalidDataError(
            [
                Problem(
                    "bad-version",
                    f"version {version}",
                    "ecoSpold2 files do not record the database version: give it as numbers, "
                    "for example 3.12",
                )
            ]
        )
    _require_folders(root)
    master = read_masterdata(root / "MasterData")
    geographies = read_geographies(root / "MasterData" / GEOGRAPHIES_FILE)
    files = sorted(
        p
        for p in (root / "datasets").iterdir()
        if p.suffix == ".spold" and not p.name.startswith(".")
    )
    _check_exclusions(options.exclude, {p.name for p in files})
    skipped = {e.file for e in options.exclude}
    batches = _parse([p for p in files if p.name not in skipped], version, options.workers)
    _raise_if_any([p for b in batches for p in b.problems])
    files_read = [f for b in batches for f in b.files]
    system_model, unchecked = _check_release(files_read, version, (int(wanted[1]), int(wanted[2])))
    processes = _concat([b.processes for b in batches])
    exchanges = _concat([b.exchanges for b in batches])
    _check_lines(processes, exchanges, _concat([b.classes for b in batches]), master)
    materials = [m for b in batches for m in b.materials]
    flows = _materials_as_waste(processes, exchanges, materials, master)
    names = {f.unit for f in master.flows}
    names |= {p.unit for p in master.flow_properties}
    names |= set(compute.unique(exchanges.column("unit")).to_pylist())
    prefix = f"{SOURCE}:{version}:{system_model}"
    left_out = {
        f"{prefix}:{e.file.removesuffix('.spold')}": DataGap(
            reason=f"{e.reason} (excluded source file {e.file})",
            resolved_by=e.resolved_by,
            resolved_at=e.resolved_at,
            source_ref=f"datasets/{e.file}",
        )
        for e in options.exclude
    }
    return InventorySource(
        info=ReleaseInfo(
            name=f"{SOURCE}-{version}-{system_model}",
            source=SOURCE,
            version=version,
            system_model=system_model,
            licence=LICENCE,
            flow_list=f"{SOURCE}-{version}",
        ),
        processes=processes.drop_columns(["reference_flow_id"]),
        lines=as_lines(exchanges),
        flows=flows,
        flow_properties=master.flow_properties,
        units=unit_table(master.conversions, names, source="UnitConversions.xml"),
        geographies=geographies,
        left_out=left_out,
        supplier_hint=_missing_file,
        warnings=(*unchecked, *(w for b in batches for w in b.warnings)),
    )


def _missing_file(supplier: str) -> str:
    return f"There is no file datasets/{supplier.rsplit(':', 1)[1]}.spold."


def _concat(tables: Sequence[pa.Table]) -> pa.Table:
    return pa.concat_tables(tables).combine_chunks()


def _require_folders(root: Path) -> None:
    missing = [
        Problem("missing-folder", f"{name}/", f"{root} has no {name} folder")
        for name in ("datasets", "MasterData")
        if not (root / name).is_dir()
    ]
    _raise_if_any(missing)


def _check_exclusions(exclusions: Sequence[Exclusion], files: set[str]) -> None:
    problems: list[Problem] = []
    seen: set[str] = set()
    for e in exclusions:
        item = f"datasets/{e.file}"
        if e.file in seen:
            problems.append(Problem("duplicate-exclusion", item, "is excluded twice"))
        seen.add(e.file)
        if not _FILE_NAME.match(e.file):
            problems.append(
                Problem(
                    "bad-file-name",
                    item,
                    "an excluded file is named <activity uuid>_<product uuid>.spold, so the lines "
                    "that buy from it can be found",
                )
            )
        elif e.file not in files:
            problems.append(Problem("unknown-file", item, "is excluded but is not in the folder"))
    _raise_if_any(problems)


def _parse(files: Sequence[Path], version: str, workers: int | None) -> list[ParsedBatch]:
    """Parse the files in batches of at most `_CHUNK`, in order, at least one per worker."""
    parse = partial(parse_files, version=version)
    count = min(workers or os.cpu_count() or 1, len(files))
    if os.name == "nt":
        count = min(count, _WINDOWS_MAX_WORKERS)
    size = max(1, min(_CHUNK, -(-len(files) // max(count, 1))))
    batches = [files[i : i + size] for i in range(0, len(files), size)]
    if count <= 1:
        try:
            return [parse(batch) for batch in batches]
        finally:
            forget_shared()
    context = multiprocessing.get_context("spawn")
    try:
        with ProcessPoolExecutor(max_workers=count, mp_context=context) as pool:
            return list(pool.map(parse, batches))
    except BrokenProcessPool as error:
        raise InvalidDataError(
            [
                Problem(
                    "worker-stopped",
                    "datasets/",
                    f"a parsing process stopped without a message ({error}), for example when "
                    "out of memory. Run again with fewer workers, or with 1 to parse in place.",
                )
            ]
        ) from None


def _check_release(
    parsed: Sequence[FileInfo], version: str, wanted: tuple[int, int]
) -> tuple[str, list[Problem]]:
    """The system model of the files, after checking `version` is the newest release among
    them, and a warning when they name no release after 3.0, so `version` cannot be checked.
    Every file must have the same system model."""
    releases = [f for f in parsed if f.release is not None]
    if not releases:
        raise InvalidDataError([Problem("missing-file", "datasets/", "has no dataset to import")])
    newest = max(releases, key=lambda f: (f.release, f.file))
    assert newest.release is not None
    found = ".".join(map(str, newest.release))
    warnings: list[Problem] = []
    if newest.release <= _UNSTAMPED < wanted:
        warnings.append(
            Problem(
                "version-unchecked",
                f"version {version}",
                f"no dataset names a release after {found} (datasets/{newest.file}), so the "
                "version cannot be checked against the files. It is taken as given.",
                severity=Severity.warning,
            )
        )
    elif newest.release != wanted:
        raise InvalidDataError(
            [
                Problem(
                    "version-mismatch",
                    f"version {version}",
                    f"the newest dataset is from release {found} (datasets/{newest.file}), so "
                    f"the source is ecoinvent {found}",
                )
            ]
        )
    systems: dict[str, list[str]] = {}
    for f in parsed:
        assert f.system_model is not None
        systems.setdefault(f.system_model, []).append(f.file)
    if len(systems) > 1:
        each = "; ".join(
            f"{model}: {len(files)} file{'s' if len(files) > 1 else ''} "
            f"({', '.join(sorted(files)[:3])}{', ...' if len(files) > 3 else ''})"
            for model, files in sorted(systems.items())
        )
        raise InvalidDataError(
            [
                Problem(
                    "mixed-system-models",
                    "datasets/",
                    f"holds datasets of {len(systems)} system models, and a release holds "
                    f"one: {each}",
                )
            ]
        )
    [system_model] = systems
    return system_model, warnings


def _check_lines(
    processes: pa.Table, exchanges: pa.Table, classes: pa.Table, master: MasterData
) -> None:
    """Every line's flow is in MasterData, and every product line classifies its flow as
    MasterData does. A process is a treatment when its reference flow is a waste, so a line
    that disagrees would get the wrong sign. Lines are listed in the order of the files."""
    files = dict(
        zip(
            processes.column("id").to_pylist(),
            processes.column("source_ref").to_pylist(),
            strict=True,
        )
    )
    flows = pa.array(sorted(f.id for f in master.flows), pa.string())
    problems: list[Problem] = []
    unknown = exchanges.filter(
        compute.invert(compute.is_in(exchanges.column("flow_id"), value_set=flows))
    )
    for r in unknown.select(["process_id", "line_id", "flow_id", "flow_name"]).to_pylist():
        problems.append(
            Problem(
                "unknown-flow",
                f"{r['process_id']} line {r['line_id']}",
                f"flow {r['flow_id']} ({r['flow_name']}) is not in MasterData",
                location=f"datasets/{files[r['process_id']]}",
            )
        )
    known = classes.filter(compute.is_in(classes.column("flow_id"), value_set=flows))
    expected = lookup(known.column("flow_id"), master.classes)
    wrong = known.filter(compute.not_equal(known.column("class"), expected))
    if wrong.num_rows:
        names = {
            (r["process_id"], r["line_id"]): r["flow_name"]
            for r in exchanges.filter(
                compute.is_in(exchanges.column("process_id"), value_set=wrong.column("process_id"))
            )
            .select(["process_id", "line_id", "flow_name"])
            .to_pylist()
        }
        for r in wrong.to_pylist():
            key = (r["process_id"], r["line_id"])
            problems.append(
                Problem(
                    "class-mismatch",
                    f"{key[0]} line {key[1]}",
                    f"classifies flow {r['flow_id']} ({names.get(key)}) as {r['class']!r}, but "
                    f"MasterData classifies it as {master.classes[r['flow_id']]!r}",
                    location=f"datasets/{files[key[0]]}",
                )
            )
    order = {name: i for i, name in enumerate(processes.column("source_ref").to_pylist())}
    problems.sort(key=lambda p: order[(p.location or "").removeprefix("datasets/")])
    _raise_if_any(problems)


def _materials_as_waste(
    processes: pa.Table,
    exchanges: pa.Table,
    materials: Sequence[tuple[str, str, bool]],
    master: MasterData,
) -> tuple[Flow, ...]:
    """MasterData's flows, with each flow a dataset treats or sends to treatment made a waste,
    whatever its class.

    APOS treats some Recyclable and allocatable flows: their reference is negative, and the lines
    that send them there are negative inputs. Such a flow is a waste in the release, so a line
    that makes or buys it as a product is an error naming the line and a dataset that treats or
    sends it. Cut-off treats and sends only Waste, so nothing changes there.
    """
    if not materials:
        return master.flows
    files = dict(
        zip(
            processes.column("id").to_pylist(),
            processes.column("source_ref").to_pylist(),
            strict=True,
        )
    )
    # One dataset named per flow: the first that treats it, else the first that sends it.
    named: dict[str, tuple[bool, str]] = {}
    for process_id, flow_id, treated in materials:
        found = (not treated, process_id)
        named[flow_id] = min(named.get(flow_id, found), found)
    treats = {(process_id, flow_id) for process_id, flow_id, treated in materials if treated}
    used = exchanges.filter(
        compute.is_in(exchanges.column("flow_id"), value_set=pa.array(sorted(named)))
    )
    problems: list[Problem] = []
    for r in used.select(["process_id", "line_id", "role", "flow_id", "flow_name"]).to_pylist():
        role = Role(r["role"])
        if role is Role.reference and (r["process_id"], r["flow_id"]) not in treats:
            how = "makes"
        elif role in (Role.input, Role.own_use):
            how = "buys"
        else:
            continue
        sends, other = named[r["flow_id"]]
        what = (
            "sends it to treatment (a negative input)"
            if sends
            else ("treats it (its reference is negative)")
        )
        problems.append(
            Problem(
                "treated-flow-as-product",
                f"{r['process_id']} line {r['line_id']}",
                f"{how} flow {r['flow_id']} ({r['flow_name']}) as a product, but "
                f"datasets/{files[other]} {what}, so it is a waste in this release",
                location=f"datasets/{files[r['process_id']]}",
            )
        )
    _raise_if_any(problems)
    return tuple(
        f.model_copy(update={"kind": FlowKind.waste}) if f.id in named else f for f in master.flows
    )


def _raise_if_any(problems: Sequence[Problem]) -> None:
    if problems:
        raise InvalidDataError(problems)
