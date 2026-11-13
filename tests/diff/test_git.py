"""Reading a study folder at a git ref, without touching the checkout."""

import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from headwater.diff import DiffError, FolderAt, StudyNotAtRef, read_at
from headwater.model import HeadwaterError, InvalidDataError

Git = Callable[..., str]
Commit = Callable[[str], str]


def edit(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    assert old in text, f"{old!r} not found in {path}"
    path.write_text(text.replace(old, new, 1))


def amount_of(read: FolderAt, line: str) -> float:
    """The amount of a line of the T-shirt, from a database read by `read_at`."""
    (process,) = [p for p in read.database.processes if p.id == "tshirt:tshirt"]
    (found,) = [e for e in process.exchanges if e.line_id == line]
    return found.amount


def snapshot(folder: Path) -> dict[str, tuple[int, bytes]]:
    """Every file under `folder`, but the .git folder, with its modification time and content."""
    return {
        str(p.relative_to(folder)): (p.stat().st_mtime_ns, p.read_bytes())
        for p in sorted(folder.rglob("*"))
        if p.is_file() and ".git" not in p.relative_to(folder).parts
    }


def test_the_working_tree_is_read_as_it_is(repo: Path) -> None:
    edit(repo / "tshirt/tshirt/exchanges.csv", "0.25", "0.22")  # not committed

    read = read_at(repo / "tshirt" / "tshirt")

    assert amount_of(read, "e02") == 0.22
    assert read.source.ref is None
    assert read.source.commit is None
    assert read.source.label == "working tree"


def test_a_ref_gives_the_study_as_committed_there(repo: Path, commit: Commit) -> None:
    first = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    edit(repo / "tshirt/tshirt/exchanges.csv", "0.25", "0.22")
    commit("lighter T-shirt")

    old = read_at(repo / "tshirt" / "tshirt", "HEAD~1")
    new = read_at(repo / "tshirt" / "tshirt", "HEAD")
    on_main = read_at(repo / "tshirt" / "tshirt", "main")

    assert amount_of(old, "e02") == 0.25
    assert amount_of(new, "e02") == 0.22
    assert amount_of(on_main, "e02") == 0.22
    assert old.source.ref == "HEAD~1"
    assert old.source.commit == first
    assert old.source.label == f"HEAD~1 ({first[:12]})"


def test_reading_a_ref_leaves_the_checkout_as_it_was(repo: Path, commit: Commit, git: Git) -> None:
    edit(repo / "tshirt/tshirt/exchanges.csv", "0.25", "0.22")
    commit("lighter T-shirt")
    edit(repo / "tshirt/tshirt/exchanges.csv", "0.22", "0.20")  # uncommitted
    (repo / "tshirt/tshirt/notes.md").write_text("scratch\n")
    before = snapshot(repo)
    status = git("status", "--porcelain")
    worktrees = git("worktree", "list")

    read_at(repo / "tshirt" / "tshirt", "HEAD~1")

    assert snapshot(repo) == before
    assert git("status", "--porcelain") == status
    assert git("worktree", "list") == worktrees
    assert (repo / "tshirt/tshirt/exchanges.csv").read_text().count("0.20") == 1


def test_the_study_may_be_given_relative_to_the_current_folder(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(repo / "tshirt")

    read = read_at(Path("tshirt"), "HEAD")

    assert amount_of(read, "e02") == 0.25


def test_the_study_may_sit_at_the_root_of_the_repository(
    tmp_path: Path, repo: Path, git: Git, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "study-repo"
    shutil.copytree(repo / "tshirt" / "tshirt", root)
    subprocess.run(["git", "init", "--quiet", "--initial-branch", "main", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "--all"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "--quiet", "-m", "study"], check=True)

    read = read_at(root, "HEAD")

    assert amount_of(read, "e02") == 0.25


def test_a_symlinked_folder_is_followed_to_its_real_place(tmp_path: Path, repo: Path) -> None:
    link = tmp_path / "link-to-repo"
    link.symlink_to(repo, target_is_directory=True)

    read = read_at(link / "tshirt" / "tshirt", "HEAD")

    assert amount_of(read, "e02") == 0.25


def test_an_unknown_ref_is_named(repo: Path) -> None:
    with pytest.raises(DiffError) as error:
        read_at(repo / "tshirt" / "tshirt", "no-such-branch")

    assert "no-such-branch" in str(error.value)
    assert "not a commit" in str(error.value)


def test_a_ref_that_looks_like_an_option_is_refused(repo: Path) -> None:
    with pytest.raises(DiffError) as error:
        read_at(repo / "tshirt" / "tshirt", "--output=/tmp/x")

    assert "--output=/tmp/x" in str(error.value)


def test_a_study_that_is_not_at_the_ref_is_named(repo: Path, commit: Commit) -> None:
    shutil.copytree(repo / "tshirt" / "tshirt", repo / "studies" / "polo")
    commit("a second study")

    with pytest.raises(DiffError) as error:
        read_at(repo / "studies" / "polo", "HEAD~1")

    text = str(error.value)
    assert "studies/polo" in text
    assert "HEAD~1" in text
    assert "does not exist" in text


def test_a_folder_of_the_format_before_d46_at_the_ref_is_not_a_database_there(
    repo: Path, commit: Commit
) -> None:
    """A folder that held study.yaml and processes/ at the ref, and database.yaml now, is a new
    database: the diff of the pull request that moves it compares nothing, as collect.py
    decides."""
    folder = repo / "tshirt" / "tshirt"
    tables = {name: (folder / name).read_text() for name in ("processes.csv", "exchanges.csv")}
    (folder / "processes").mkdir()
    (folder / "processes" / "tshirt.yaml").write_text(
        "name: T-shirt\nlocation: GLO\nkind: ordinary_transforming_activity\n"
    )
    (folder / "processes" / "tshirt.csv").write_text(
        "".join(row.split(",", 1)[1] + "\n" for row in tables["exchanges.csv"].splitlines())
    )
    for name in tables:
        (folder / name).unlink()
    (folder / "database.yaml").rename(folder / "study.yaml")
    commit("before D46")
    shutil.rmtree(folder / "processes")
    for name, text in tables.items():
        (folder / name).write_text(text)
    (folder / "study.yaml").rename(folder / "database.yaml")

    with pytest.raises(StudyNotAtRef, match="holds no database.yaml and no study.yaml"):
        read_at(folder, "HEAD")


def test_a_folder_outside_git_is_named(tmp_path: Path) -> None:
    outside = tmp_path / "plain"
    shutil.copytree(Path(__file__).resolve().parent.parent / "fixtures/toy/tshirt/tshirt", outside)

    with pytest.raises(DiffError) as error:
        read_at(outside, "HEAD")

    assert str(outside) in str(error.value)
    assert "git repository" in str(error.value)


def test_a_study_that_does_not_read_at_the_ref_says_which_ref(repo: Path, commit: Commit) -> None:
    edit(repo / "tshirt/tshirt/exchanges.csv", "0.25", "lots")
    commit("a broken study")

    with pytest.raises(InvalidDataError) as error:
        read_at(repo / "tshirt" / "tshirt", "HEAD")

    assert "lots" in str(error.value)
    assert isinstance(error.value, HeadwaterError)


def test_git_missing_from_the_path_is_said_plainly(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", str(repo))

    with pytest.raises(DiffError) as error:
        read_at(repo / "tshirt" / "tshirt", "HEAD")

    assert "git" in str(error.value)
    assert "PATH" in str(error.value)


def test_diff_error_is_a_headwater_error() -> None:
    assert issubclass(DiffError, HeadwaterError)


def test_a_study_that_is_not_in_the_commit_says_which_commit_it_looked_in(
    repo: Path, commit: Commit
) -> None:
    shutil.copytree(repo / "tshirt" / "tshirt", repo / "studies" / "polo")
    commit("a second study")
    first = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD~1"], capture_output=True, text=True
    ).stdout.strip()

    with pytest.raises(StudyNotAtRef) as error:
        read_at(repo / "studies" / "polo", "HEAD~1")

    assert isinstance(error.value, DiffError)
    assert error.value.source.ref == "HEAD~1"
    assert error.value.source.commit == first
    assert "does not exist at HEAD~1" in str(error.value)


def test_an_unknown_ref_is_not_a_study_that_is_missing(repo: Path) -> None:
    with pytest.raises(DiffError) as error:
        read_at(repo / "tshirt" / "tshirt", "no-such-branch")

    assert not isinstance(error.value, StudyNotAtRef)
