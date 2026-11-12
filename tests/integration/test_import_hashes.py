"""ecoinvent 3.12 rebuilds to the release of before the importers moved onto the shared stages, and
EF v3.1 maps to the same method. Runs only when `ECOINVENT_DIR` and `ECOINVENT_LCIA` are set.

`ECOINVENT_DIR` is the folder that holds `datasets/` and `MasterData/`, for example
`/path/to/ecoinvent/3.12-cutoff`. `ECOINVENT_LCIA` is the workbook, for example
`.../LCIA Implementation 3.12.xlsx`. The release hash was recorded before that move, the
method's with each category's category indicator (D41). They go through `inventory_from` and
`method_from`, the path the command line takes. Run with `-s` to see the timing.
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

VERSION = "3.12"

RELEASE = "b9428cdd8045742a211775b2c990ca21d3a39dfa8ed5dd9d99d1a2ec1db53d16"

EF_3_1 = (
    "dbb0de237f259caa43f9249d30e4dfacf134c97c387da2a6172ba7391863752e",
    "3654ed4afc1b0797eb06d405759986d7f8cbc1a923a7cbe5e72851b1cae57fc1",
    "7935c9ce4cbbd51ff93f7d0cadb631b6601dbc4b126645a041b3c07b0a99732d",
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
