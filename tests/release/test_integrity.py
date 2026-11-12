import hashlib
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from headwater.model import InvalidDataError
from headwater.release import Release, content_hash


def _problems(error: pytest.ExceptionInfo[InvalidDataError]) -> list[tuple[str, str]]:
    return [(p.code, p.item) for p in error.value.problems]


def _double_first_amount(path: Path) -> None:
    table = pq.read_table(path)
    amounts = table.column("amount").to_numpy().copy()
    amounts[0] *= 2
    index = table.schema.get_field_index("amount")
    pq.write_table(table.set_column(index, "amount", pa.array(amounts, pa.float64())), path)


def test_a_tampered_file_fails_the_integrity_check_naming_the_file(toy_release: Release) -> None:
    _double_first_amount(toy_release.path / "exchanges.parquet")

    with pytest.raises(InvalidDataError) as raised:
        Release.open(toy_release.path)

    assert _problems(raised) == [("changed-file", "exchanges.parquet")]
    assert "sha256" in str(raised.value)


def test_one_extra_byte_is_caught_in_a_nested_file(toy_release: Release) -> None:
    path = toy_release.path / "methods" / "toy-climate" / "factors.parquet"
    path.write_bytes(path.read_bytes() + b"\0")

    with pytest.raises(InvalidDataError) as raised:
        Release.open(toy_release.path)

    assert _problems(raised) == [("changed-file", "methods/toy-climate/factors.parquet")]


def test_a_file_that_is_not_parquet_is_named_when_it_is_read(toy_release: Release) -> None:
    # The release is already open, so the integrity check is past: this is the reader's error.
    (toy_release.path / "exchanges.parquet").write_bytes(b"not parquet")

    with pytest.raises(InvalidDataError) as raised:
        toy_release.table("exchanges")

    assert _problems(raised) == [("unreadable-file", "exchanges.parquet")]


def test_a_missing_file_and_an_unexpected_file_are_named(toy_release: Release) -> None:
    (toy_release.path / "issues.parquet").unlink()
    (toy_release.path / "notes.txt").write_text("hello")

    with pytest.raises(InvalidDataError) as raised:
        Release.open(toy_release.path)

    assert _problems(raised) == [
        ("missing-file", "issues.parquet"),
        ("unexpected-file", "notes.txt"),
    ]


def test_a_folder_name_that_does_not_match_the_hash_fails(toy_release: Release) -> None:
    moved = toy_release.path.with_name("toy-background@000000000000")
    toy_release.path.rename(moved)

    with pytest.raises(InvalidDataError) as raised:
        Release.open(moved)

    assert _problems(raised) == [("release-name", "toy-background@000000000000")]


def test_a_folder_without_release_json_is_not_a_release(tmp_path: Path) -> None:
    with pytest.raises(InvalidDataError) as raised:
        Release.open(tmp_path)

    assert _problems(raised) == [("missing-file", "release.json")]


def test_verify_catches_a_rewritten_file_whose_sha256_was_updated(toy_release: Release) -> None:
    path = toy_release.path / "exchanges.parquet"
    _double_first_amount(path)
    manifest_path = toy_release.path / "release.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files"]["exchanges.parquet"] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest))
    opened = Release.open(toy_release.path)

    with pytest.raises(InvalidDataError) as raised:
        opened.verify()

    assert _problems(raised) == [("changed-content", "exchanges.parquet")]


def test_verify_passes_on_an_untouched_release(toy_release: Release) -> None:
    Release.open(toy_release.path).verify()


def test_content_hash_of_a_folder_without_release_json_names_it(tmp_path: Path) -> None:
    with pytest.raises(InvalidDataError) as raised:
        content_hash(tmp_path)

    assert _problems(raised) == [("missing-file", "release.json")]


def test_a_release_in_an_older_format_says_to_build_it_again(toy_release: Release) -> None:
    path = toy_release.path / "release.json"
    manifest = json.loads(path.read_text())
    manifest["format"] = "headwater-release/1"
    path.write_text(json.dumps(manifest))

    with pytest.raises(InvalidDataError) as raised:
        Release.open(toy_release.path)

    assert _problems(raised) == [("old-format", "release.json")]
    assert "headwater-release/1" in str(raised.value)
    assert "Import or build the release again" in str(raised.value)


def test_a_release_in_a_newer_format_says_to_upgrade(toy_release: Release) -> None:
    path = toy_release.path / "release.json"
    manifest = json.loads(path.read_text())
    manifest["format"] = "headwater-release/9"
    path.write_text(json.dumps(manifest))

    with pytest.raises(InvalidDataError) as raised:
        Release.open(toy_release.path)

    assert _problems(raised) == [("new-format", "release.json")]
    assert "Upgrade headwater" in str(raised.value)
