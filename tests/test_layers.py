"""The import contracts of pyproject.toml keep calc/ to numpy and scipy (AGENTS.md rule 13).

`uv run lint-imports` checks the contracts, in CI and before each commit. A contract can only
refuse the libraries it names, so this test checks that the calc/ contracts name every dependency
of headwater but numpy and scipy. Example: a new dependency `polars` fails here until the first
contract names it.
"""

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

IMPORT_NAMES = {"pyyaml": "yaml"}
"""The module a distribution is imported as, where the two names differ."""

ALLOWED = {"numpy", "scipy"}


def dependencies() -> set[str]:
    """The modules of every dependency of headwater, optional ones included."""
    project = PYPROJECT["project"]
    lines = list(project["dependencies"])
    for extra in project["optional-dependencies"].values():
        lines += extra
    names = {re.split(r"[\[<>=!~ ;]", line, maxsplit=1)[0].lower() for line in lines}
    return {IMPORT_NAMES.get(name, name) for name in names}


def refused_in_calc() -> set[str]:
    contracts = PYPROJECT["tool"]["importlinter"]["contracts"]
    return {
        module
        for contract in contracts
        if contract["type"] == "forbidden" and contract["source_modules"] == ["headwater.calc"]
        for module in contract["forbidden_modules"]
    }


def test_the_calc_contracts_refuse_every_dependency_but_numpy_and_scipy() -> None:
    missing = dependencies() - ALLOWED - refused_in_calc()

    assert not missing, (
        f"calc/ may import {sorted(missing)}: add them to a calc/ contract in pyproject.toml"
    )


def test_the_calc_contracts_allow_numpy_and_scipy() -> None:
    assert not ALLOWED & refused_in_calc()
