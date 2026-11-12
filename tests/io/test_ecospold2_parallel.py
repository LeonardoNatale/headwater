"""Parallel parsing: an error in a worker comes back with its own message, and the result does not
depend on the number of workers."""

from collections.abc import Callable, Iterator
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path
from typing import Any

import pytest

from headwater.io.ecospold2 import dataset, import_ecospold2, read_ecospold2
from headwater.io.ecospold2 import reader as reader_module
from headwater.io.exclude import read_exclude
from headwater.model import InvalidDataError


def _errors(folder: Path, workers: int) -> InvalidDataError:
    with pytest.raises(InvalidDataError) as raised:
        read_ecospold2(folder, version="3.10", workers=workers)
    return raised.value


@pytest.mark.parametrize("workers", [1, 2])
def test_errors_in_workers_surface_with_their_own_message(
    source_copy: Callable[..., Path], fx, workers: int
) -> None:
    """An empty file, a file that is not XML, one with two reference products, and one whose
    process fails validation (its period runs backwards). Brightway's importer turned the first
    into an unrelated pickling error when parsing in parallel."""
    folder = source_copy(add=(fx.malformed_file, fx.two_references_file, fx.backwards_period_file))

    error = _errors(folder, workers)

    backwards = f"process ecoinvent:3.10:cutoff:{fx.activity(13)}_b0000000-0000-4000-8000-{5:012d}"
    found = {p.item: p for p in error.problems}
    assert set(found) == {
        f"datasets/{fx.empty_file}",
        f"datasets/{fx.malformed_file}",
        f"datasets/{fx.two_references_file}",
        backwards,
    }
    assert found[f"datasets/{fx.empty_file}"].code == "empty-file"
    malformed = found[f"datasets/{fx.malformed_file}"]
    assert malformed.code == "unreadable-file"
    assert "line" in malformed.message  # lxml's own message says where the XML breaks
    assert found[f"datasets/{fx.two_references_file}"].code == "reference-count"
    assert found[backwards].code == "invalid-dataset"
    assert "time_end 2020-01-01 is before time_start 2025-12-31" in found[backwards].message
    assert found[backwards].location == f"datasets/{fx.backwards_period_file}"
    assert "pickl" not in str(error).lower()


def test_one_worker_and_two_workers_give_the_same_errors(
    source_copy: Callable[..., Path], fx
) -> None:
    folder = source_copy(add=(fx.malformed_file, fx.two_references_file, fx.backwards_period_file))

    assert _errors(folder, 1).problems == _errors(folder, 2).problems


def test_x3_importing_twice_gives_the_same_release(fx, tmp_path: Path) -> None:
    exclude = read_exclude(fx.exclude)

    first = import_ecospold2(
        fx.source, version="3.10", exclude=exclude, into=tmp_path / "a", workers=1
    )
    second = import_ecospold2(
        fx.source, version="3.10", exclude=exclude, into=tmp_path / "b", workers=2
    )

    assert first.hash == second.hash
    assert first.path.name == second.path.name


def test_parsing_in_place_does_not_keep_shared_records(fx) -> None:
    read_ecospold2(fx.source, version="3.10", exclude=read_exclude(fx.exclude), workers=1)

    assert dataset._SHARED_PROPERTIES == {}
    assert dataset._SHARED_PEDIGREES == {}


def test_a_worker_that_stops_without_a_message_is_a_named_problem(
    fx, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Stopping(ProcessPoolExecutor):
        def map(self, *args: Any, **kwargs: Any) -> Iterator[Any]:
            raise BrokenProcessPool("a child process terminated abruptly")

    monkeypatch.setattr(reader_module, "ProcessPoolExecutor", Stopping)

    with pytest.raises(InvalidDataError) as raised:
        read_ecospold2(fx.source, version="3.10", exclude=read_exclude(fx.exclude), workers=2)

    assert [p.code for p in raised.value.problems] == ["worker-stopped"]
    assert "workers" in str(raised.value)
