import hashlib
import json
import struct
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from headwater.release import Release, ReleaseInfo, content_hash

TOY_RELEASE_HASH = "56e3c924facc322c44a44e48341d06a4be059d4c86f882a559b8fbf142b87584"
TOY_EXCHANGES_DIGEST = "2bd8404040166779c34843cba4f5a41651448cd03e12286509d9b1d2ee2450dd"
"""The hash of the toy release and the digest of its exchanges. If this test fails after a
library upgrade, the hash is not as stable as the format says. Change these only with a
deliberate change of the format."""


def test_two_builds_give_the_same_hash_and_the_same_files(fresh_records, tmp_path: Path) -> None:
    first = fresh_records().build(tmp_path / "a")
    second = fresh_records().build(tmp_path / "b")

    assert first.hash == second.hash
    assert first.path.name == second.path.name
    assert (first.path / "release.json").read_bytes() == (second.path / "release.json").read_bytes()
    for name in json.loads((first.path / "release.json").read_text())["files"]:
        assert (first.path / name).read_bytes() == (second.path / name).read_bytes(), name


def test_the_order_of_records_does_not_change_the_hash(records, tmp_path: Path) -> None:
    assert records.build(tmp_path / "a").hash == records.reversed().build(tmp_path / "b").hash


def test_the_toy_release_hash_is_pinned(toy_release: Release) -> None:
    assert toy_release.manifest.tables["exchanges.parquet"].content_sha256 == TOY_EXCHANGES_DIGEST
    assert toy_release.hash == TOY_RELEASE_HASH


def test_the_hash_recomputed_from_the_files_matches(toy_release: Release) -> None:
    assert content_hash(toy_release.path) == toy_release.hash


@pytest.mark.parametrize(
    "settings",
    [
        {"row_group_size": 1, "compression": "snappy"},
        {"row_group_size": 2, "compression": "none", "use_dictionary": False},
        {"compression": "gzip", "store_schema": False, "write_statistics": False},
        {"compression": "zstd", "compression_level": 19, "data_page_version": "1.0"},
    ],
)
def test_rewriting_the_files_with_other_writer_settings_keeps_the_hash(
    toy_release: Release, settings: dict[str, object]
) -> None:
    before = {p: p.read_bytes() for p in toy_release.path.rglob("*.parquet")}
    for path in before:
        pq.write_table(pq.read_table(path), path, **settings)  # pyright: ignore[reportArgumentType]

    assert any(path.read_bytes() != data for path, data in before.items())
    assert content_hash(toy_release.path) == toy_release.hash


def test_rewriting_the_files_with_duckdb_keeps_the_hash(toy_release: Release) -> None:
    con = duckdb.connect()
    for path in toy_release.path.rglob("*.parquet"):
        copy = path.with_suffix(".duckdb.parquet")
        source = str(path).replace("'", "''")
        target = str(copy).replace("'", "''")
        con.execute(
            f"COPY (SELECT * FROM read_parquet('{source}')) TO '{target}' "
            "(FORMAT parquet, ROW_GROUP_SIZE 3)"
        )
        copy.replace(path)

    assert content_hash(toy_release.path) == toy_release.hash


def test_one_changed_amount_changes_the_hash(records, tmp_path: Path) -> None:
    changed = records.with_line("toy:fabric-production", "e02", amount=2.5)

    assert changed.build(tmp_path / "b").hash != records.build(tmp_path / "a").hash


def test_metadata_is_part_of_the_hash(records, info, tmp_path: Path) -> None:
    other = ReleaseInfo.model_validate({**info.model_dump(), "system_model": "apos"})

    assert records.build(tmp_path / "b", other).hash != records.build(tmp_path / "a").hash


def test_the_engine_version_is_not_part_of_the_hash(toy_release: Release) -> None:
    path = toy_release.path / "release.json"
    manifest = json.loads(path.read_text())
    manifest["engine_version"] = "99.0.0"
    path.write_text(json.dumps(manifest))

    assert content_hash(toy_release.path) == toy_release.hash


# A second implementation of the table digest, written from docs/design.md in plain Python. It
# agrees with headwater's on every toy table, so the code does what the docs define.


def _type(t: Any) -> str:
    if pa.types.is_string(t):
        return "string"
    if pa.types.is_float64(t):
        return "float64"
    if pa.types.is_int8(t):
        return "int8"
    if pa.types.is_date32(t):
        return "date32"
    if pa.types.is_timestamp(t):
        return "timestamp[us]"
    if pa.types.is_map(t):
        return f"map<{_type(t.key_type)},{_type(t.item_type)}>"
    fields = [t.field(i) for i in range(t.num_fields)]
    return "struct<" + ",".join(f"{f.name}:{_type(f.type)}" for f in fields) + ">"


def _column(values: list[Any], t: Any) -> bytes:
    out = bytes(0 if v is None else 1 for v in values)
    if pa.types.is_string(t):
        data = [b"" if v is None else v.encode("utf-8") for v in values]
        return out + b"".join(struct.pack("<q", len(d)) for d in data) + b"".join(data)
    if pa.types.is_float64(t):
        return out + b"".join(struct.pack("<d", 0.0 if v is None else v) for v in values)
    if pa.types.is_int8(t):
        return out + b"".join(struct.pack("<q", v or 0) for v in values)
    if pa.types.is_date32(t):
        days = [0 if v is None else (v - date(1970, 1, 1)).days for v in values]
        return out + b"".join(struct.pack("<q", d) for d in days)
    if pa.types.is_timestamp(t):
        epoch = datetime(1970, 1, 1)  # stored in UTC, with no time zone
        micros = [0 if v is None else (v - epoch) // _MICROSECOND for v in values]
        return out + b"".join(struct.pack("<q", m) for m in micros)
    if pa.types.is_map(t):
        entries = [e for v in values for e in (v or [])]
        counts = b"".join(struct.pack("<q", len(v or [])) for v in values)
        keys = _column([k for k, _ in entries], t.key_type)
        return out + counts + keys + _column([v for _, v in entries], t.item_type)
    for i in range(t.num_fields):
        f = t.field(i)
        out += _column([None if v is None else v[f.name] for v in values], f.type)
    return out


_MICROSECOND = datetime(1970, 1, 1, 0, 0, 0, 1, tzinfo=UTC) - datetime(1970, 1, 1, tzinfo=UTC)


def _reference_digest(path: Path, spec: Any) -> str:
    rows = pq.read_table(path).to_pylist()
    rows.sort(key=lambda r: [(r[k] is not None, (r[k] or "").encode("utf-8")) for k in spec.key])
    schema = [[f.name, _type(f.type)] for f in spec.schema]
    out = b"headwater-table/1\n"
    out += json.dumps(schema, separators=(",", ":")).encode("utf-8") + b"\n"
    out += struct.pack("<q", len(rows))
    for f in spec.schema:
        out += _column([r[f.name] for r in rows], f.type)
    return hashlib.sha256(out).hexdigest()


def test_the_table_digest_matches_the_definition_in_the_docs(toy_release: Release) -> None:
    from headwater.release.read import expected_tables

    tables = expected_tables(toy_release.manifest)

    for name, spec in tables.items():
        expected = toy_release.manifest.tables[name].content_sha256
        assert _reference_digest(toy_release.path / name, spec) == expected, name
