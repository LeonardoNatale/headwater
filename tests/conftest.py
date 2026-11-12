"""Helpers that the tests of several folders share."""

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

import pytest

import headwater as hw

DATABASE_YAML = """name: {name}
background: {background}
methods: [toy-climate]
uses: [{uses}]
"""

EXCHANGE_HEADER = "line,role,flow,amount,unit,supplier\n"


def toy_database(root: Path, case: str) -> Path:
    """The folder of a toy database: `tshirt-waste` is `<root>/tshirt-waste/tshirt`, the T-shirt
    of that case, and `dyes` is `<root>/tshirt-dyed/dyes`. Each case of the toy is a folder of
    its own, so every database in it has the name of its folder."""
    if case == "dyes":
        return root / "tshirt-dyed" / "dyes"
    return root / case / "tshirt"


def open_database(path: Path | str) -> "hw.Database":
    """`hw.open` of a folder that must hold a database."""
    opened = hw.open(path)
    assert isinstance(opened, hw.Database), f"{path} holds a study"
    return opened


def open_study_folder(path: Path | str) -> "hw.Study":
    """`hw.open` of a folder that must hold a study."""
    opened = hw.open(path)
    assert isinstance(opened, hw.Study), f"{path} holds a database"
    return opened


def toy_cases(root: Path) -> list[str]:
    """The names `toy_database` takes, one per toy database: each case, and `dyes`."""
    cases = [p.parent.parent.name for p in root.glob("*/tshirt/database.yaml")]
    return sorted([*cases, "dyes"])


def approx_numbers(expected: object, rel: float | None = None) -> object:
    """`expected` with every number in it, at any depth, wrapped in `pytest.approx`, and the rest
    as it is, to compare with `==`. Example: `{"distribution": "normal", "loc": 0.1}` compares its
    distribution exactly and its loc with the tolerance. `pytest.approx` alone refuses nested data
    and values that are not numbers."""
    if isinstance(expected, bool) or not isinstance(expected, int | float | Mapping | list | tuple):
        return expected
    if isinstance(expected, int | float):
        return pytest.approx(expected, rel=rel)
    if isinstance(expected, Mapping):
        return {k: approx_numbers(v, rel) for k, v in expected.items()}
    found = [approx_numbers(v, rel) for v in expected]
    return tuple(found) if isinstance(expected, tuple) else found


def only_process(database: hw.Database) -> str:
    """The id of the one process of a toy database: `tshirt:tshirt`."""
    [process] = database.folder.processes
    return process.id


FLOW_HEADER = "id,name,kind,unit,compartment,cas\n"

WriteStudy = Callable[..., Path]


def _write_study(
    folder: Path,
    processes: Mapping[str, str],
    *,
    name: str | None = None,
    uses: Sequence[str] = (),
    flows: Sequence[str] = (),
    background: str = "toy-background@fixture",
) -> Path:
    """A database folder on the toy background. `processes` maps a file name to the lines of its
    CSV, without the header: `{"salt": "e01,reference,chemicals:salt,1,kg,\\n..."}`. Each process
    is in GLO. `flows` are rows of `flows.csv`, without the header."""
    name = name or folder.name
    (folder / "processes").mkdir(parents=True, exist_ok=True)
    (folder / "database.yaml").write_text(
        DATABASE_YAML.format(name=name, background=background, uses=", ".join(uses))
    )
    if flows:
        (folder / "flows.csv").write_text(FLOW_HEADER + "".join(f"{row}\n" for row in flows))
    for stem, lines in processes.items():
        (folder / "processes" / f"{stem}.csv").write_text(EXCHANGE_HEADER + lines.strip() + "\n")
        (folder / "processes" / f"{stem}.yaml").write_text(
            f"name: {stem}\nlocation: GLO\nkind: ordinary_transforming_activity\n"
        )
    return folder


@pytest.fixture
def write_study() -> WriteStudy:
    """Write a toy database folder: see `_write_study`."""
    return _write_study
