"""The content hash of a release: computed from rows, never from the bytes of a Parquet file.

Parquet files record the version of the library that wrote them (`created_by`), and their bytes
change with compression, row groups and page settings. The content hash does not. It is defined
in docs/design.md, "A release", and implemented here:

- table digest: sha256 of a fixed binary encoding of the table's rows, sorted by key
- release hash: sha256 of release.json's content. A source release's covers each table by its
  digest. An indicators release's covers its recipe instead of its scores: see `release_hash`.
- method hash: sha256 of the method's metadata and indicators, with its factors' digest
- set hash: sha256 of a normalisation and weighting set, every field and row
"""

import hashlib
import json
import struct
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc

from headwater.model import InvalidDataError, Method, NwSet, Problem
from headwater.release.schema import FACTORS, TableSpec, factors_table

compute: Any = pc

TABLE_FORMAT = b"headwater-table/1\n"
METHOD_FORMAT = "headwater-method/1"
SET_FORMAT = "headwater-set/1"

NOT_HASHED = frozenset({"hash", "engine_version", "files"})
"""Keys of release.json that are not content: the hash itself, the writer, and file checksums."""

NOT_IN_RECIPE = NOT_HASHED | {"tables", "provenance"}
"""Keys of an indicators release's release.json that are not part of its recipe: what the solve
gave (the rows' digests, the residual, the steps taken), which differs between machines in the
last bits."""


def canonical_json(value: object) -> bytes:
    """JSON with sorted keys, no spaces, UTF-8, and no NaN or infinity."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def release_hash(manifest: Mapping[str, Any]) -> str:
    """The hash of release.json's content.

    A source release: every key except `hash`, `engine_version` and `files`, so every row counts.
    An indicators release: its recipe, that is every key except those and `tables` and
    `provenance`. The parent's hash, the method's hash and `recipe` (engine version and solver
    settings) say how the scores were made, and two machines making them get the same release.
    """
    left_out = NOT_IN_RECIPE if manifest.get("kind") == "indicators" else NOT_HASHED
    return sha256(canonical_json({k: v for k, v in manifest.items() if k not in left_out}))


def method_hash(method: Method) -> str:
    """The hash of a method's content. The order of its indicators and factors does not count.

    Example: toy-climate version 1, with CO2 1 and CH4 29.8, has one hash wherever it is stored.
    """
    document = {
        "format": METHOD_FORMAT,
        "id": method.id,
        "version": method.version,
        "variant": method.variant,
        "flow_list": method.flow_list,
        "indicators": [
            i.model_dump(mode="json") for i in sorted(method.indicators, key=lambda i: i.id)
        ],
        "factors": table_digest(factors_table(method.factors), FACTORS, "factors"),
    }
    return sha256(canonical_json(document))


def set_hash(nw_set: NwSet) -> str:
    """The hash of a normalisation and weighting set: every field, the method it pins with its
    hash, and every row. The order of the rows does not count.

    Example: toy-nw version 1 pins toy-impacts by its hash, so a set with the same rows for
    another method, or for the same method imported again with other factors, has another hash.
    """
    document = {
        "format": SET_FORMAT,
        **nw_set.model_dump(mode="json", exclude={"rows"}),
        "rows": [
            row.model_dump(mode="json") for row in sorted(nw_set.rows, key=lambda r: r.indicator)
        ],
    }
    return sha256(canonical_json(document))


def methods_hash(hashes: Sequence[str]) -> str:
    """The hash of methods used together, from their own hashes, in order. One method's is its
    own hash.

    Example: EF-3.1 and IPCC-2021 used together have one hash, made from the hash of each, so it
    can be checked against a release that holds both without reading their factors.
    """
    if len(hashes) == 1:
        return hashes[0]
    return sha256(canonical_json({"format": METHOD_FORMAT, "methods": list(hashes)}))


# --- tables ---------------------------------------------------------------------------------
#
# Arrow values are typed Any here: pyarrow-stubs lags behind pyarrow and leaves most of the
# compute and nested-type API unknown.


def type_name(type_: Any) -> str:
    """A fixed name for each type a release uses. Field nullability does not count."""
    if pa.types.is_string(type_) or pa.types.is_large_string(type_):
        return "string"
    if pa.types.is_float64(type_):
        return "float64"
    if pa.types.is_int8(type_):
        return "int8"
    if pa.types.is_date32(type_):
        return "date32"
    if pa.types.is_timestamp(type_):
        return (
            f"timestamp[{type_.unit}]"
            if type_.tz is None
            else f"timestamp[{type_.unit},{type_.tz}]"
        )
    if pa.types.is_map(type_):
        return f"map<{type_name(type_.key_type)},{type_name(type_.item_type)}>"
    if pa.types.is_struct(type_):
        struct_type: Any = type_
        fields: list[Any] = [struct_type.field(i) for i in range(struct_type.num_fields)]
        return "struct<" + ",".join(f"{f.name}:{type_name(f.type)}" for f in fields) + ">"
    raise TypeError(f"a release has no column of type {type_}")


def conform(table: pa.Table, spec: TableSpec, name: str) -> pa.Table:
    """The table with the spec's columns, in the spec's order and types, as one chunk.

    Raises when a column is missing, extra or of another type, naming the table. Field names
    inside a map and nullability may differ, since other Parquet writers set them differently.
    """
    expected: Any = spec.schema
    found: Any = table.schema
    problems: list[str] = []
    missing = [n for n in expected.names if n not in found.names]
    extra = [n for n in found.names if n not in expected.names]
    if missing:
        problems.append(f"missing column(s) {', '.join(missing)}")
    if extra:
        problems.append(f"unexpected column(s) {', '.join(extra)}")
    for field in expected:
        if field.name in found.names:
            was, wanted = type_name(found.field(field.name).type), type_name(field.type)
            if was != wanted:
                problems.append(f"column {field.name} is {was}, not {wanted}")
    if problems:
        raise InvalidDataError([Problem("bad-table", name, "; ".join(problems))])
    return table.select(expected.names).cast(spec.schema).combine_chunks()


def sort_rows(table: pa.Table, key: tuple[str, ...]) -> pa.Table:
    """Rows in key order: ascending, UTF-8 byte order, nulls first."""
    sortable: Any = table
    return sortable.sort_by([(k, "ascending", "at_start") for k in key])


def table_digest(table: pa.Table, spec: TableSpec, name: str) -> str:
    """sha256 of the table's rows, sorted by key, in the binary encoding of `_encode`."""
    return sorted_table_digest(sort_rows(conform(table, spec, name), spec.key), spec)


def sorted_table_digest(table: pa.Table, spec: TableSpec) -> str:
    """`table_digest` of a table already conformed to `spec` and sorted by its key."""
    schema: Any = spec.schema
    digest = hashlib.sha256(TABLE_FORMAT)
    digest.update(canonical_json([[f.name, type_name(f.type)] for f in schema]) + b"\n")
    digest.update(struct.pack("<q", table.num_rows))
    for column in table.columns:
        _encode(column.combine_chunks(), digest)
    return digest.hexdigest()


def _encode(array: Any, digest: Any) -> None:
    """One column: a validity byte per row (1 or 0), then the values.

    - string: an int64 byte length per row (0 when null), then the UTF-8 bytes of all rows
    - float64: 8 bytes per row, IEEE 754 (0.0 when null)
    - int8, date32 (days) and timestamp (microseconds since 1970 in UTC): an int64 per row (0
      when null)
    - struct: each field in order, as a column, with the struct's nulls applied to it
    - map: an int64 entry count per row, then the keys, then the values, as columns

    Every integer and float is little-endian.
    """
    digest.update(_numpy(array.is_valid()).astype(np.uint8).tobytes())
    type_: Any = array.type
    if pa.types.is_string(type_) or pa.types.is_large_string(type_):
        filled: Any = compute.fill_null(array, "")
        digest.update(_int64(compute.binary_length(filled)))
        digest.update(_utf8(filled))
    elif pa.types.is_float64(type_):
        digest.update(_numpy(compute.fill_null(array, 0.0)).astype("<f8").tobytes())
    elif pa.types.is_date32(type_):
        digest.update(_int64(array.cast(pa.int32())))
    elif pa.types.is_int8(type_) or pa.types.is_timestamp(type_):
        digest.update(_int64(array))
    elif pa.types.is_struct(type_):
        for child in array.flatten():
            _encode(child, digest)
    elif pa.types.is_map(type_):
        entry = pa.struct(
            [pa.field("key", type_.key_type, nullable=False), pa.field("value", type_.item_type)]
        )
        entries: Any = array.cast(pa.list_(entry))
        digest.update(_int64(compute.list_value_length(entries)))
        for child in entries.flatten().flatten():
            _encode(child, digest)
    else:
        raise TypeError(f"a release has no column of type {type_}")


def _utf8(array: Any) -> memoryview | bytes:
    """The UTF-8 bytes of every value of a string array with no null, one after the other: the
    array's data buffer between its first and last offset."""
    offset_type = np.int64 if pa.types.is_large_string(array.type) else np.int32
    _, offsets_buffer, data = array.buffers()
    offsets = np.frombuffer(offsets_buffer, dtype=offset_type)
    start, end = offsets[array.offset], offsets[array.offset + len(array)]
    return memoryview(data)[start:end] if data is not None else b""


def _numpy(array: Any) -> np.ndarray[Any, Any]:
    return array.to_numpy(zero_copy_only=False)


def _int64(array: Any) -> bytes:
    return _numpy(compute.fill_null(array.cast(pa.int64()), 0)).astype("<i8").tobytes()
