"""A database.yaml that still says `TODO` where a person must write: validate names each field."""

from pathlib import Path

import pytest

from headwater.model import Problem, Severity
from headwater.study import read_database, read_method, read_process_folder, validate

PLACEHOLDERS = "description: 'TODO: say'\nallocation: {rule: TODO}\nend_of_life: TODO\n"


def todos(toy: Path) -> list[Problem]:
    background = read_process_folder(toy / "background", namespace="toy")
    method = read_method(toy / "methods" / "toy-climate")
    report = validate(
        read_database(toy / "tshirt" / "tshirt"), background=background, method=method
    )
    assert report.errors == ()
    return [p for p in report.problems if p.code == "database-todo"]


def add(toy: Path, text: str) -> None:
    """Add lines to the T-shirt's database.yaml."""
    path = toy / "tshirt/tshirt/database.yaml"
    path.write_text(path.read_text() + text)


def test_a_database_with_no_placeholder_has_no_warning(toy: Path) -> None:
    assert todos(toy) == []


def test_each_placeholder_is_a_warning_that_names_its_field(toy: Path) -> None:
    add(toy, PLACEHOLDERS)

    found = todos(toy)

    assert [p.item for p in found] == [
        "database tshirt: description",
        "database tshirt: allocation.rule",
        "database tshirt: end_of_life",
    ]
    assert all(p.severity is Severity.warning for p in found)
    assert all(p.location == "database.yaml" for p in found)
    assert "'TODO: say'" in found[0].message
    assert "still a placeholder" in found[0].message


@pytest.mark.parametrize(
    "description", ["TODOs of the mill", "TODO-based reporting", "Todo list of the mill"]
)
def test_a_description_that_only_starts_like_the_marker_is_not_a_placeholder(
    toy: Path, description: str
) -> None:
    add(toy, f"description: {description}\n")

    assert todos(toy) == []
