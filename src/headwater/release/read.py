"""Open a release folder, check it, and read its tables as Arrow, DuckDB or records."""

import hashlib
import json
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import JsonValue, ValidationError

from headwater.model import (
    Flow,
    FlowProperty,
    InvalidDataError,
    Issue,
    Method,
    Problem,
    Process,
    Units,
)
from headwater.release.content import conform, method_hash, release_hash, table_digest
from headwater.release.manifest import (
    FORMAT,
    Manifest,
    MethodEntry,
    ReleaseInfo,
    ReleaseKind,
    ReleaseRef,
)
from headwater.release.schema import (
    FACTORS,
    SOURCE_TABLES,
    TableSpec,
    factors_path,
    indicators_path,
    indicators_spec,
    read_flow_properties,
    read_flows,
    read_issues,
    read_method,
    read_processes,
    read_units,
)

if TYPE_CHECKING:
    import duckdb

MANIFEST = "release.json"


def expected_tables(manifest: Manifest) -> dict[str, TableSpec]:
    """Every table a release of this kind must hold, by file, with its spec."""
    tables: dict[str, TableSpec] = {}
    if manifest.kind is ReleaseKind.source:
        tables.update(SOURCE_TABLES)
    for method in manifest.methods:
        tables[factors_path(method.id)] = FACTORS
        if manifest.kind is ReleaseKind.indicators:
            tables[indicators_path(method.id)] = indicators_spec(i.id for i in method.indicators)
    return tables


def _read_manifest(path: Path) -> tuple[dict[str, Any], Manifest]:
    """release.json as written, and as a record. Raises naming what is wrong."""
    file = path / MANIFEST
    if not file.is_file():
        raise _error("missing-file", MANIFEST, f"{path} has no {MANIFEST}, so it is not a release")
    try:
        raw: Any = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise _error("bad-manifest", MANIFEST, f"cannot be read: {exc}") from None
    found = cast(dict[str, Any], raw).get("format") if isinstance(raw, dict) else None
    if isinstance(found, str) and found.startswith("headwater-release/") and found != FORMAT:
        older = _format_number(found) < _format_number(FORMAT)
        raise _error(
            "old-format" if older else "new-format",
            MANIFEST,
            f"{path.name} is in the format {found}, and this version of headwater reads "
            f"{FORMAT}. "
            + ("Import or build the release again." if older else "Upgrade headwater."),
        )
    try:
        manifest = Manifest.model_validate(raw)
    except ValidationError as exc:
        raise _error("bad-manifest", MANIFEST, f"cannot be read: {exc}") from None
    return cast(dict[str, Any], raw), manifest


def _format_number(found: str) -> int:
    number = found.rsplit("/", 1)[1]
    return int(number) if number.isdigit() else 0


def load_manifest(path: Path) -> Manifest:
    """release.json, checked against its own hash. Raises naming what is wrong."""
    raw, manifest = _read_manifest(path)
    problems: list[Problem] = []
    expected = expected_tables(manifest)
    if set(manifest.tables) != set(expected) or set(manifest.files) != set(expected):
        problems.append(
            Problem(
                "bad-manifest",
                MANIFEST,
                f"a {manifest.kind.value} release holds {', '.join(sorted(expected))}; "
                f"tables lists {', '.join(sorted(manifest.tables))} and files lists "
                f"{', '.join(sorted(manifest.files))}",
            )
        )
    if manifest.kind is ReleaseKind.indicators and (
        manifest.parent is None or not manifest.methods
    ):
        problems.append(
            Problem(
                "bad-manifest", MANIFEST, "an indicators release has one parent and its methods"
            )
        )
    computed = release_hash(raw)
    if computed != manifest.hash:
        problems.append(
            Problem(
                "changed-manifest",
                MANIFEST,
                f"its content hashes to {computed}, but it says {manifest.hash}",
            )
        )
    if problems:
        raise InvalidDataError(problems)
    return manifest


def check_files(path: Path, manifest: Manifest) -> None:
    """Every listed file is there with its sha256, and no other file is. Hidden files, such as
    .DS_Store, are ignored: nothing reads them."""
    present = {
        p.relative_to(path).as_posix()
        for p in path.rglob("*")
        if p.is_file() and not any(part.startswith(".") for part in p.relative_to(path).parts)
    }
    present.discard(MANIFEST)
    problems: list[Problem] = []
    for name in sorted(present | set(manifest.files)):
        if name not in manifest.files:
            problems.append(
                Problem(
                    "unexpected-file",
                    name,
                    f"is not listed in {MANIFEST}, so it is not part of the release",
                )
            )
        elif name not in present:
            problems.append(Problem("missing-file", name, f"is listed in {MANIFEST} but missing"))
        else:
            found = file_sha256(path / name)
            if found != manifest.files[name]:
                problems.append(
                    Problem(
                        "changed-file",
                        name,
                        f"has sha256 {found}, but {MANIFEST} says {manifest.files[name]}. "
                        "The file was changed after the release was frozen.",
                    )
                )
    if problems:
        raise InvalidDataError(problems)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def read_table(path: Path, name: str, spec: TableSpec) -> pa.Table:
    try:
        # A ParquetFile loads no pyarrow.dataset, and with it no pandas when that is installed;
        # pq.read_table opens a dataset even for one file.
        with pq.ParquetFile(path / name) as parquet:
            table = parquet.read()  # pyright: ignore[reportUnknownMemberType]
    except (OSError, pa.ArrowException) as exc:
        raise _error("unreadable-file", name, f"cannot be read as Parquet: {exc}") from None
    return conform(table, spec, name)


def content_hash(path: Path | str) -> str:
    """The release hash recomputed from the rows of every table, not from the file bytes.

    It ignores the files' sha256 in release.json, so it stays the same when the files are
    rewritten with other Parquet settings or another Parquet writer. An indicators release's
    hash is its recipe, which the rows do not change: `verify()` checks them.
    """
    folder = Path(path)
    raw, manifest = _read_manifest(folder)
    tables = {
        name: _table_entry(read_table(folder, name, spec), spec, name)
        for name, spec in sorted(expected_tables(manifest).items())
    }
    return release_hash({**raw, "tables": tables})


def _table_entry(table: pa.Table, spec: TableSpec, name: str) -> dict[str, JsonValue]:
    return {"rows": table.num_rows, "content_sha256": table_digest(table, spec, name)}


class Release:
    """A frozen release folder, `<name>@<hash12>`, checked when opened.

    Example: `Release.open(".../toy-background@3f9a...")`, then `release.table("exchanges")`,
    `release.connect().sql("SELECT count(*) FROM exchanges")` or `release.processes()`.
    """

    def __init__(self, path: Path, manifest: Manifest) -> None:
        self.path = path
        self.manifest = manifest

    @classmethod
    def open(cls, path: Path | str) -> "Release":
        """Read release.json and check the folder name and every file's sha256.

        Raises InvalidDataError naming each file that is missing, changed or not listed.
        """
        folder = Path(path)
        manifest = load_manifest(folder)
        expected = f"{manifest.name}@{manifest.hash[:12]}"
        if folder.name != expected:
            raise _error(
                "release-name",
                folder.name,
                f"the folder of release {manifest.name} with hash {manifest.hash} must be "
                f"named {expected}",
            )
        check_files(folder, manifest)
        return cls(folder, manifest)

    def __repr__(self) -> str:
        return f"Release({self.name}@{self.hash[:12]}, {self.kind.value})"

    @property
    def hash(self) -> str:
        return self.manifest.hash

    @property
    def name(self) -> str:
        return self.manifest.name

    @property
    def kind(self) -> ReleaseKind:
        return self.manifest.kind

    @property
    def info(self) -> ReleaseInfo:
        return self.manifest.info

    @property
    def parent(self) -> ReleaseRef | None:
        return self.manifest.parent

    @property
    def method_entries(self) -> tuple[MethodEntry, ...]:
        return self.manifest.methods

    @property
    def recipe(self) -> dict[str, JsonValue] | None:
        """How an indicators release's scores are made: engine version and solver settings."""
        return self.manifest.recipe

    @property
    def ref(self) -> ReleaseRef:
        return ReleaseRef(name=self.name, hash=self.hash)

    def verify(self) -> None:
        """Recompute every table's content digest and every method hash from the files.

        Slower than the sha256 check of `open`, and catches a file that was rewritten with its
        sha256 updated in release.json.
        """
        problems: list[Problem] = []
        for name, spec in sorted(self._specs.items()):
            entry = _table_entry(self.table(name), spec, name)
            if entry != self.manifest.tables[name].model_dump():
                problems.append(
                    Problem(
                        "changed-content",
                        name,
                        f"its rows hash to {entry['content_sha256']} ({entry['rows']} rows), "
                        f"but release.json says {self.manifest.tables[name].content_sha256}",
                    )
                )
        if not problems:
            for method in self.methods():
                entry = next(m for m in self.method_entries if m.id == method.id)
                if method_hash(method) != entry.hash:
                    problems.append(
                        Problem("changed-content", f"method {method.id}", "its hash has changed")
                    )
        if problems:
            raise InvalidDataError(problems)

    # --- tables -----------------------------------------------------------------------------

    @cached_property
    def _specs(self) -> dict[str, TableSpec]:
        return expected_tables(self.manifest)

    def table(self, name: str) -> pa.Table:
        """One table as Arrow, by file name with or without `.parquet`: `exchanges`,
        `methods/EF-3.1/factors`, `indicators/EF-3.1`."""
        file = name if name.endswith(".parquet") else f"{name}.parquet"
        spec = self._specs.get(file)
        if spec is None:
            raise _error(
                "no-table",
                name,
                f"{self.kind.value} release {self.name} has no table {name}. "
                f"It has: {', '.join(sorted(self._specs))}",
            )
        return read_table(self.path, file, spec)

    def connect(self) -> "duckdb.DuckDBPyConnection":
        """A new in-memory DuckDB connection with one view per table, named like the file
        without `.parquet`: `exchanges`, `"methods/EF-3.1/factors"`, `"indicators/EF-3.1"`."""
        import duckdb
        import pyarrow.dataset as ds

        con = duckdb.connect(":memory:")
        for file in sorted(self._specs):
            # A dataset, not read_parquet: DuckDB would read `[`, `*` and `?` in the folder's
            # path as a glob, and could match another folder.
            table = ds.dataset(self.path / file, format="parquet")  # pyright: ignore[reportUnknownMemberType]
            con.register(file.removesuffix(".parquet"), table)
        return con

    # --- records ----------------------------------------------------------------------------

    def processes(self) -> tuple[Process, ...]:
        """Every process with its lines, sorted by id and line id."""
        return read_processes(self.table("processes"), self.table("exchanges"))

    def flows(self) -> tuple[Flow, ...]:
        return read_flows(self.table("flows"))

    def flow_properties(self) -> tuple[FlowProperty, ...]:
        return read_flow_properties(self.table("flow_properties"))

    def units(self) -> Units:
        """The units the release's records use, and only those."""
        return Units(read_units(self.table("units")))

    def issues(self) -> tuple[Issue, ...]:
        return read_issues(self.table("issues"))

    def methods(self) -> tuple[Method, ...]:
        """Each method with its indicators and factors, sorted by id."""
        return tuple(
            read_method(
                id=m.id,
                version=m.version,
                variant=m.variant,
                flow_list=m.flow_list,
                indicators=m.indicators,
                factors=self.table(factors_path(m.id)),
            )
            for m in self.method_entries
        )


def open_expecting(path: Path, expected: str) -> Release:
    """Open the release in `path` and check it is the one expected, not another release whose
    hash starts with the same 12 characters."""
    release = Release.open(path)
    if release.hash != expected:
        raise _error("hash-collision", path.name, f"holds release {release.hash}, not {expected}")
    return release


def _error(code: str, item: str, message: str) -> InvalidDataError:
    return InvalidDataError([Problem(code, item, message)])
