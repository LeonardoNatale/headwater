import importlib
import subprocess
import sys
from importlib.metadata import version

import pytest

import headwater
from headwater.model.roles import Role

SUBPACKAGES = ["model", "io", "link", "release", "study", "calc", "query", "cli"]

ROLES = [
    "reference",
    "input",
    "own_use",
    "waste_to_treatment",
    "recyclable",
    "avoided_product",
    "emission",
    "resource",
]


def test_version_is_a_string_and_matches_the_project_version() -> None:
    assert isinstance(headwater.__version__, str)
    assert headwater.__version__ == version("headwater")


@pytest.mark.parametrize("name", SUBPACKAGES)
def test_subpackage_imports(name: str) -> None:
    importlib.import_module(f"headwater.{name}")


def test_role_has_exactly_the_eight_roles_of_the_design() -> None:
    # __members__ also lists aliases, so a duplicate value cannot hide as a ninth name.
    assert list(Role.__members__) == ROLES


def test_role_values_equal_names() -> None:
    assert all(role.value == role.name for role in Role)


def test_monte_carlo_is_the_function_after_its_module_is_imported() -> None:
    """Importing `headwater.query.monte_carlo` first makes the package attribute of that name the
    module; `headwater.monte_carlo` must still be the function."""
    code = (
        "import headwater as hw\n"
        "from headwater.query.monte_carlo import Item\n"
        "assert callable(hw.monte_carlo), type(hw.monte_carlo)\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
