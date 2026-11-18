"""ecoinvent 3.13 imports to the same release, and EF v3.1 maps to the same method, as when the
hashes below were recorded. Runs only when `ECOINVENT_DIR` and `ECOINVENT_LCIA` are set.

`ECOINVENT_DIR` is the folder that holds `datasets/` and `MasterData/`, for example
`/path/to/ecoinvent/3.13-cutoff`. `ECOINVENT_LCIA` is the workbook, for example
`.../LCIA Implementation 3.13.xlsx`. A change to an importer or to the method mapping that moves
a hash is caught here. The method's hash covers each category's category indicator (D41). They
go through `inventory_from` and `method_from`, the path the command line takes. Run with `-s` to
see the timing.
"""

import hashlib
import os
import time
from pathlib import Path

import pytest

from headwater.io.formats import inventory_from, method_from
from headwater.io.method import write_method
from headwater.io.source import InventoryOptions, MethodOptions
from headwater.release import Release
from headwater.release.content import method_hash

ECOINVENT_DIR = os.environ.get("ECOINVENT_DIR")
ECOINVENT_LCIA = os.environ.get("ECOINVENT_LCIA")

pytestmark = pytest.mark.skipif(
    not (ECOINVENT_DIR and ECOINVENT_LCIA), reason="ECOINVENT_DIR and ECOINVENT_LCIA are not set"
)

VERSION = "3.13"

RELEASE = "9bbbb9d97a81ab2025e53cb784b7bd1a8668ab35b71aa6980fce083bf6e1cc01"

EF_3_1 = (
    "12b71a956691576dd4287467b106d5e1bc31a9ef7d1274bc2e57eb12bd5dda76",
    "b179632c638b4dae539fef5767475387895a8bd1a0363c5cf990887b5fef68bb",
    "4af5531458168b43a88f72ffe25a72997d3114c4b81ead031f7fa3178283b5b0",
)
"""The method hash, and the sha256 of `method.yaml` and of `factors.csv`."""


@pytest.fixture(scope="module")
def release(tmp_path_factory: pytest.TempPathFactory) -> Release:
    assert ECOINVENT_DIR
    start = time.perf_counter()
    options = InventoryOptions(version=VERSION)
    found = inventory_from(Path(ECOINVENT_DIR), options).freeze(
        into=tmp_path_factory.mktemp("releases")
    )
    load = ", ".join(f"{x:.1f}" for x in os.getloadavg())
    print(f"\nimported {VERSION} in {time.perf_counter() - start:.1f} s, load average {load}")
    return found


def test_the_release_rebuilds_to_the_same_hash(release: Release) -> None:
    assert release.info.version == VERSION
    assert release.hash == RELEASE


def test_ef_3_1_maps_to_the_same_method_and_folder(release: Release, tmp_path: Path) -> None:
    assert ECOINVENT_LCIA

    imported = method_from(Path(ECOINVENT_LCIA), release, MethodOptions(method="EF v3.1"))
    folder = write_method(imported, tmp_path / "EF-3.1")

    expected_hash, method_yaml, factors_csv = EF_3_1
    assert method_hash(imported.method) == expected_hash
    assert _sha256(folder / "method.yaml") == method_yaml
    assert _sha256(folder / "factors.csv") == factors_csv


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
