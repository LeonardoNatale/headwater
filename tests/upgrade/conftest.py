"""Fixtures for the upgrade tests: two versions of a toy background, and a database on the first.

`tests/fixtures/upgrade` holds toy-1 and toy-2, each a folder of process files and its method,
frozen here into releases with ids `toy:1:<file>` and `toy:2:<file>`. From toy-1 to toy-2:

- `elec`, electricity production, GLO: 0.5 kg CO2 per kWh, then 0.4. Same id. toy-1's also
  emits N2O, which toy-2 does not have.
- `market-elec`: buys 1 kWh of `elec`, then 0.5 of `elec` and 0.5 of `wind`. Same id.
- `fabric-a`, cotton fabric production, RoW, 3.5, is `fabric-b` in toy-2, 2.75: the same name,
  product and place. toy-2 also has `fabric-in`, for IN, so its RoW leaves out IN.
- `dye`, dye production, GLO, 4, is `dye-r` in toy-2, reactive dye production, 3, and the flow
  `toy:dye` is renamed `reactive dye`. A mapping row decides it.
- `tape`, tape production, GLO, 1, has no successor: toy-2 has `tape-rer`, the same in RER, 0.8.
- `glue`, glue production, GLO, 2, is gone from toy-2, and so is its product `toy:glue`.

The method counts CO2 1 and CH4 29.8 in toy-1, and CH4 27 in toy-2. Every line id of toy-2 is
new (`a1`, `a2`, ...), as ecoinvent's are.

The T-shirt study buys 0.25 kg of fabric, 0.5 kWh from the market, 0.01 kg of dye and 0.01 kg of
tape, and emits 0.001 kg of CH4: 0.875 + 0.25 + 0.04 + 0.01 + 0.0298 = 1.2048 on toy-1.
"""

import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

from headwater.release import Release, ReleaseInfo, build
from headwater.study import read_method, read_process_folder

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "upgrade"
REL = 1e-9  # AGENTS.md: relative 1e-9 for toy fixtures

Pin = Callable[[Release], str]


def build_toy(version: str, into: Path | None = None) -> Release:
    """Freeze `toy-<version>` and its method into a release, in the cache unless `into` says."""
    root = FIXTURES / f"toy-{version}"
    folder = read_process_folder(root / "background", namespace=f"toy:{version}")
    info = ReleaseInfo(
        name=f"toy-{version}",
        source="toy",
        version=version,
        system_model="cutoff",
        licence="CC0-1.0",
        flow_list=f"toy-{version}",
    )
    return build(
        info,
        processes=folder.processes,
        flows=folder.flows,
        flow_properties=folder.flow_properties,
        methods=(read_method(root / "toy-climate"),),
        into=into,
    )


def short(release: Release) -> str:
    """`toy-1@b24ebd7d98cc`: how a database pins it."""
    return f"{release.name}@{release.hash[:12]}"


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A HEADWATER_HOME of its own, so the releases built here are the only ones."""
    path = tmp_path / "home"
    monkeypatch.setenv("HEADWATER_HOME", str(path))
    return path


@pytest.fixture
def toy1(home: Path) -> Release:
    return build_toy("1")


@pytest.fixture
def toy2(home: Path) -> Release:
    return build_toy("2")


@pytest.fixture
def studies(tmp_path: Path, toy1: Release, toy2: Release) -> Path:
    """A writable `studies/` folder holding the T-shirt, pinned to toy-1. toy-2 is in the cache."""
    target = tmp_path / "studies"
    shutil.copytree(FIXTURES / "studies", target)
    path = target / "tshirt" / "database.yaml"
    path.write_text(path.read_text().replace("toy-1@PIN", short(toy1)))
    return target


@pytest.fixture
def mapping() -> Path:
    """The reviewed rows for dye (renamed) and tape (gone from GLO)."""
    return FIXTURES / "upgrade-mapping.csv"


@pytest.fixture
def to(toy2: Release) -> str:
    """The pin of toy-2, the release the database moves to."""
    return short(toy2)


Edit = Callable[[Path, str, str], None]


@pytest.fixture
def edit() -> Edit:
    """Replace the first `old` in a file by `new`, and fail when it is not there."""

    def run(path: Path, old: str, new: str) -> None:
        text = path.read_text()
        assert old in text, f"{old!r} not found in {path}"
        path.write_text(text.replace(old, new, 1))

    return run
