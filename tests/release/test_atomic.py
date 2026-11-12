import subprocess
import sys
from pathlib import Path

import pyarrow as pa
import pytest

import headwater.release.write as write_module
from headwater.release import Release

# Builds the plain toy background, and kills the process without cleanup on the third file.
HARD_CRASH = """
import os
import sys

import headwater.release.write as write_module
from headwater.release import ReleaseInfo, build
from headwater.study import read_process_folder

real = write_module._write_table
written = []


def crash(table, path):
    written.append(path)
    if len(written) == 3:
        os._exit(3)
    real(table, path)


write_module._write_table = crash
folder = read_process_folder(sys.argv[1], namespace="toy")
info = ReleaseInfo(
    name="toy-background", source="toy", version="1", system_model=None, licence="CC0-1.0",
    flow_list="toy",
)
build(info, processes=folder.processes, flows=folder.flows, into=sys.argv[2])
"""

TOY_BACKGROUND = Path(__file__).resolve().parent.parent / "fixtures" / "toy" / "background"


def _releases(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.iterdir() if "@" in p.name)


def test_an_error_mid_write_leaves_no_release_folder_and_no_temporary_files(
    records, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = write_module._write_table
    written: list[Path] = []

    def crash(table: pa.Table, path: Path) -> None:
        written.append(path)
        if len(written) == 3:
            raise OSError("disk full")
        real(table, path)

    monkeypatch.setattr(write_module, "_write_table", crash)
    into = tmp_path / "releases"

    with pytest.raises(OSError, match="disk full"):
        records.build(into)

    assert len(written) == 3
    assert list(into.iterdir()) == []


def test_a_killed_process_mid_write_leaves_no_release_folder(records, tmp_path: Path) -> None:
    into = tmp_path / "releases"

    run = subprocess.run(
        [sys.executable, "-c", HARD_CRASH, str(TOY_BACKGROUND), str(into)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert run.returncode == 3, run.stderr
    assert _releases(into) == []
    leftovers = [p for p in into.iterdir()]
    assert leftovers and all(p.name.startswith(".") for p in leftovers)
    built = records.build(into)
    assert _releases(into) == [built.path.name]
    assert Release.open(built.path).hash == built.hash


def test_building_an_existing_release_again_keeps_the_first_one(records, tmp_path: Path) -> None:
    first = records.build(tmp_path)
    before = (first.path / "release.json").stat().st_mtime_ns

    again = records.build(tmp_path)

    assert again.path == first.path
    assert (first.path / "release.json").stat().st_mtime_ns == before
    assert _releases(tmp_path) == [first.path.name]
    assert [p.name for p in tmp_path.iterdir()] == [first.path.name]
