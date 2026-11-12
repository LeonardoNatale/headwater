import shutil
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

from headwater.model import InvalidDataError, Problem, Process
from headwater.study import ProcessFolder, read_database, read_process_folder

TOY_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "toy"


@pytest.fixture
def toy_dir() -> Path:
    """The toy fixtures as committed. Read only."""
    return TOY_DIR


@pytest.fixture
def toy(tmp_path: Path) -> Path:
    """A writable copy of the toy fixtures, for tests that break one thing."""
    target = tmp_path / "toy"
    shutil.copytree(TOY_DIR, target)
    return target


@pytest.fixture
def edit() -> Callable[[Path, str, str], None]:
    """Replace text in a file. Fails if the text is not there, so a typo cannot pass silently."""

    def _edit(path: Path, old: str, new: str) -> None:
        text = path.read_text()
        assert old in text, f"{old!r} not found in {path}"
        path.write_text(text.replace(old, new, 1))

    return _edit


@pytest.fixture
def write() -> Callable[[Path, str], None]:
    """Write a file, making its folders."""

    def _write(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    return _write


@pytest.fixture
def problems_of() -> Callable[..., tuple[Problem, ...]]:
    """The problems a reader raises for a folder. Fails if it reads without one."""

    def _problems(
        path: Path, reader: Callable[[Path], object] = read_database
    ) -> tuple[Problem, ...]:
        with pytest.raises(InvalidDataError) as raised:
            reader(path)
        return raised.value.problems

    return _problems


@pytest.fixture
def reference_only(toy: Path) -> ProcessFolder:
    """The toy background as a release gives it to validation: each process with its reference
    line only."""
    folder = read_process_folder(toy / "background", namespace="toy")
    return replace(
        folder,
        processes=tuple(
            Process.model_validate(
                {
                    **p.model_dump(exclude={"exchanges"}),
                    "exchanges": [p.reference_exchange],
                }
            )
            for p in folder.processes
        ),
    )
