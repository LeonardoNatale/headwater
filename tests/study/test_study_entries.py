"""A misspelt file or folder in a database is an error: it would be ignored without a word."""

import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

from headwater.model import Problem
from headwater.study import read_database, read_process_folder, validate

Write = Callable[[Path, str], None]
Problems = Callable[..., tuple[Problem, ...]]

DATABASE = "tshirt/tshirt"


def only(problems: tuple[Problem, ...]) -> Problem:
    assert len(problems) == 1, [str(p) for p in problems]
    return problems[0]


@pytest.mark.parametrize(
    ("name", "kind", "meant"),
    [
        ("override", "dir", "overrides"),
        ("process", "dir", "processes"),
        ("parameter.yaml", "file", "parameters.yaml"),
        ("parameters.yml", "file", "parameters.yaml"),
        ("database.yml", "file", "database.yaml"),
        ("cutoffs.csv", "file", "cut_offs.csv"),
        ("cut-offs.csv", "file", "cut_offs.csv"),
        ("flow.csv", "file", "flows.csv"),
        ("unit.csv", "file", "units.csv"),
    ],
)
def test_a_name_close_to_a_known_one_is_refused(
    toy: Path, write: Write, problems_of: Problems, name: str, kind: str, meant: str
) -> None:
    path = toy / DATABASE / name
    if kind == "dir":
        (path / "x").mkdir(parents=True)
    else:
        write(path, "")

    problem = only(problems_of(toy / DATABASE))

    assert problem.code == "unknown-file"
    assert problem.item == name
    assert f"Did you mean {meant!r}?" in problem.message


@pytest.mark.parametrize(
    "name", ["README.md", "notes.txt", "scenarios.md", "overrides.txt", "data", ".git", ".DS_Store"]
)
def test_a_name_that_is_far_from_every_known_one_is_left_alone(
    toy: Path, write: Write, name: str
) -> None:
    write(toy / DATABASE / name / "x" if name == "data" else toy / DATABASE / name, "")

    assert [p.id for p in read_database(toy / DATABASE).processes] == ["tshirt:tshirt"]


@pytest.mark.parametrize("kind", ["dir", "file"])
def test_a_database_with_a_scenarios_entry_is_refused(
    toy: Path, write: Write, problems_of: Problems, kind: str
) -> None:
    path = toy / DATABASE / "scenarios"
    if kind == "dir":
        path.mkdir()
    else:
        write(path, "")

    problem = only(problems_of(toy / DATABASE))

    assert problem.code == "database-scenarios"
    assert "scenarios belong in a study" in problem.message


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("overrides", "file"),
        ("processes", "file"),
        ("parameters.yaml", "dir"),
        ("cut_offs.csv", "dir"),
        ("flows.csv", "dir"),
    ],
)
def test_a_known_name_of_the_wrong_kind(
    toy: Path, write: Write, problems_of: Problems, name: str, kind: str
) -> None:
    path = toy / DATABASE / name
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()
    if kind == "dir":
        path.mkdir()
    else:
        write(path, "")

    problems = problems_of(toy / DATABASE)

    assert any(p.code == "wrong-kind" and p.item == name for p in problems), [
        str(p) for p in problems
    ]


def test_a_parameter_with_a_unit_that_does_not_exist(toy: Path, write: Write) -> None:
    write(toy / DATABASE / "parameters.yaml", "database:\n  fabric_kg: {value: 0.25, unit: kgg}\n")
    folder = read_process_folder(toy / "background", namespace="toy")

    report = validate(read_database(toy / DATABASE), background=folder)

    problem = only(report.errors)
    assert problem.code == "unknown-unit"
    assert problem.item == "parameter fabric_kg"
    assert "Did you mean 'kg'?" in problem.message
