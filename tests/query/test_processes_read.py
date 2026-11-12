"""`bg.processes` on a release: the records it gives, and the tables it reads to give them."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pyarrow.dataset as ds
import pytest

import headwater as hw
from headwater.model import Process
from headwater.release import Release

Load = Callable[[], hw.Background]


@pytest.fixture
def load(release: Release) -> Load:
    """A background of the toy release that nothing has been read from yet. Not the shared one:
    its cache may already hold every process."""
    return lambda: hw.load(release.path, method="toy-climate")


@pytest.fixture
def opened(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The file name of every parquet dataset opened from now on."""
    names: list[str] = []
    real: Any = ds.dataset  # pyright: ignore[reportUnknownMemberType]

    def counting(source: Any, *args: Any, **kwargs: Any) -> Any:
        names.append(Path(source).name)
        return real(source, *args, **kwargs)

    monkeypatch.setattr(ds, "dataset", counting)
    return names


def expected(release: Release) -> dict[str, Process]:
    """Every process with all its lines from the tables, and each line's flow named from the flows
    table: what `processes` must give."""
    names = {flow.id: flow.name for flow in release.flows()}
    return {
        p.id: p.model_copy(
            update={
                "exchanges": tuple(
                    e.model_copy(update={"flow_name": names.get(e.flow_id)}) for e in p.exchanges
                )
            }
        )
        for p in release.processes()
    }


def test_every_toy_process_comes_back_with_all_its_lines_and_flow_names(
    release: Release, load: Load
) -> None:
    want = expected(release)
    assert len(want) == 3

    got = load().processes(want)

    assert got == want
    assert all(e.flow_name for p in got.values() for e in p.exchanges)


def test_one_id_at_a_time_gives_the_same_records(release: Release, load: Load) -> None:
    want = expected(release)
    background = load()

    for process_id, process in want.items():
        assert background.processes([process_id]) == {process_id: process}

    assert background.processes(want) == want


def test_a_process_read_once_is_not_read_again(load: Load, opened: list[str]) -> None:
    background = load()
    opened.clear()  # loading opens datasets too
    first = background.processes(["toy:fabric-production"])
    reads = list(opened)

    again = background.processes(["toy:fabric-production"])

    assert again == first
    assert opened == reads
    assert "exchanges.parquet" in reads


def test_calls_with_new_ids_do_not_read_the_flows_table_each_time(
    load: Load, opened: list[str]
) -> None:
    background = load()
    opened.clear()  # loading opens datasets too

    for process_id in ("toy:fabric-production", "toy:electricity-production"):
        background.processes([process_id])
    background.processes(["toy:incineration-textile-waste"])

    assert opened.count("flows.parquet") <= 1
    assert opened.count("exchanges.parquet") == 3  # the lines are still read, once per call
