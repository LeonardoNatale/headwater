"""`headwater database import-brightway`."""

from pathlib import Path

import pytest

from headwater.release import Release

from .conftest import Invoke, Project


def test_it_writes_the_database_and_exits_0(
    project: Project, studies: Path, toy_release: Release, invoke: Invoke
) -> None:
    code, output = invoke(
        "database",
        "import-brightway",
        "toy",
        "tshirt",
        "--brightway-dir",
        project.root,
        "--release",
        toy_release.path,
        "--out",
        studies / "tshirt",
    )
    assert code == 0, output
    assert "Wrote database tshirt" in output
    assert "TODO" not in output and "study" not in output.lower()
    assert (studies / "tshirt" / "database.yaml").is_file()


def test_it_reads_brightway2_dir(
    project: Project,
    studies: Path,
    toy_release: Release,
    invoke: Invoke,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BRIGHTWAY2_DIR", str(project.root))
    code, output = invoke(
        "database",
        "import-brightway",
        "toy",
        "tshirt",
        "--release",
        toy_release.path,
        "--out",
        studies / "tshirt",
    )
    assert code == 0, output


def test_a_line_that_waits_exits_1(
    project: Project, studies: Path, toy_release: Release, invoke: Invoke
) -> None:
    code, output = invoke(
        "database",
        "import-brightway",
        "toy",
        "tshirt-dyed",
        "--brightway-dir",
        project.root,
        "--release",
        toy_release.path,
        "--out",
        studies / "tshirt-dyed",
    )
    assert code == 1, output
    assert "dyes" in output


def test_a_dangling_key_exits_2_and_writes_nothing(
    project: Project, studies: Path, toy_release: Release, invoke: Invoke
) -> None:
    project.sql("UPDATE exchangedataset SET input_database = 'nowhere' WHERE id = 15")
    code, output = invoke(
        "database",
        "import-brightway",
        "toy",
        "tshirt",
        "--brightway-dir",
        project.root,
        "--release",
        toy_release.path,
        "--out",
        studies / "tshirt",
    )
    assert code == 2, output
    assert "nowhere" in output
    assert not (studies / "tshirt").exists()
