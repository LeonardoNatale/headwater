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
TABLE_HEADER = "process," + EXCHANGE_HEADER
PROCESS_HEADER = "id,name,location,kind\n"


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
    """A database folder on the toy background. `processes` maps a process's id in the tables
    to its lines, without the process column: `{"salt": "e01,reference,chemicals:salt,1,kg,"}`.
    Each process is in GLO. `flows` are rows of `flows.csv`, without the header."""
    name = name or folder.name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "database.yaml").write_text(
        DATABASE_YAML.format(name=name, background=background, uses=", ".join(uses))
    )
    if flows:
        (folder / "flows.csv").write_text(FLOW_HEADER + "".join(f"{row}\n" for row in flows))
    write_tables(folder, processes)
    return folder


def write_tables(
    folder: Path, processes: Mapping[str, str], details: Mapping[str, str] | None = None
) -> None:
    """processes.csv and exchanges.csv of a folder. `processes` maps a process's id in the tables
    to its lines, without the header and the process column: `{"salt": "e01,reference,..."}`.
    `details` maps an id to the rest of its row of processes.csv, `Salt,GLO,ordinary_...`; by
    default the id is the name, in GLO."""
    rows = [
        f"{code},{(details or {}).get(code, f'{code},GLO,ordinary_transforming_activity')}\n"
        for code in sorted(processes)
    ]
    (folder / "processes.csv").write_text(PROCESS_HEADER + "".join(rows))
    lines = [
        f"{code},{line}\n"
        for code in sorted(processes)
        for line in processes[code].strip().splitlines()
    ]
    (folder / "exchanges.csv").write_text(TABLE_HEADER + "".join(lines))


def add_lines(folder: Path, process: str, *lines: str) -> None:
    """Add lines of a process at the end of exchanges.csv, each without the process column:
    `add_lines(db, "tshirt", "e04,emission,toy:co2,0.01,kg,")`."""
    table = folder / "exchanges.csv"
    text = table.read_text()
    if text and not text.endswith("\n"):
        text += "\n"
    table.write_text(text + "".join(f"{process},{line}\n" for line in lines))


def add_process(folder: Path, code: str, lines: str, details: str | None = None) -> None:
    """Add a process at the end of both tables: its row of processes.csv and its lines, given
    without the process column, one per line of `lines`. `details` is the rest of its row,
    `"Sewing,RER,market_activity"`; by default the id is the name, in GLO."""
    table = folder / "processes.csv"
    text = table.read_text()
    if text and not text.endswith("\n"):
        text += "\n"
    row = details if details is not None else f"{code},GLO,ordinary_transforming_activity"
    table.write_text(text + f"{code},{row}\n")
    add_lines(folder, code, *lines.strip().splitlines())


@pytest.fixture
def write_study() -> WriteStudy:
    """Write a toy database folder: see `_write_study`."""
    return _write_study
