"""Read a database or a study folder at a git ref, with git's read commands only.

The files of the ref are written to a temporary folder and read from there. Nothing is checked
out, no worktree is made, and the user's files and index are not touched. A database of the
layout before D48, with `processes/`, is converted in that temporary folder as
`headwater database convert` converts it, with its checks, so a comparison across the commit that
converted it shows that no score changed.
"""

import os
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from headwater.model import HeadwaterError, InvalidDataError, Problem
from headwater.study import DatabaseFolder, StudyFolder, read_database, read_study
from headwater.study._convert import convert
from headwater.study._files import read_yaml
from headwater.study._tables import CONVERT, OLD_FOLDER
from headwater.study._uses import uses_on_disk
from headwater.study.reader import read_uses

DATABASE_YAML = "database.yaml"
STUDY_YAML = "study.yaml"

_FILE_MODES = frozenset({"100644", "100755"})


class DiffError(HeadwaterError):
    """A diff cannot be made: an unknown ref, a folder that is not in git, a pin that changed."""


@dataclass(frozen=True)
class Source:
    """Where a folder was read from: a commit, or the working tree when `ref` is None."""

    ref: str | None
    commit: str | None
    """The full hash `ref` named when it was read."""

    @property
    def label(self) -> str:
        """`main (1a2b3c4d5e6f)`, or `working tree`."""
        if self.ref is None or self.commit is None:
            return "working tree"
        return f"{self.ref} ({self.commit[:12]})"


class StudyNotAtRef(DiffError):
    """The database or study is not in the commit: it is new in the branch being compared, or
    the path is wrong. `source` says which commit was looked in."""

    def __init__(self, message: str, source: Source) -> None:
        super().__init__(message)
        self.source = source


@dataclass(frozen=True)
class FolderAt:
    """A database or a study folder read at a ref, and where it came from."""

    folder: DatabaseFolder | StudyFolder
    source: Source

    @property
    def kind(self) -> str:
        return "study" if isinstance(self.folder, StudyFolder) else "database"

    @property
    def database(self) -> DatabaseFolder:
        """The folder, a database. Raises `DiffError` for a study."""
        if not isinstance(self.folder, DatabaseFolder):
            raise DiffError(f"{self.folder.path} is a study, not a database")
        return self.folder


def read_at(path: Path | str, ref: str | None = None) -> FolderAt:
    """The database or study folder `path` as it is at `ref`, or as it is on disk when `ref` is
    None. A folder that holds `study.yaml` is a study, and one that holds `database.yaml` a
    database (D46).

    `ref` is anything git names a commit by: a branch, a tag, `HEAD~1`, a hash. The files are read
    from git's object store, so uncommitted changes and untracked files are not part of a ref.
    The databases a database uses, and those a study's products are in with the ones they use,
    are the folders next to it, read at the same ref. Raises `DiffError` for an unknown ref, a
    folder that is not in a git repository or not in that commit, and `InvalidDataError` when
    the folder at the ref does not read.
    """
    given = Path(path)
    if ref is None:
        if (given / STUDY_YAML).is_file():
            return FolderAt(read_study(given), Source(None, None))
        return FolderAt(read_database(given), Source(None, None))
    resolved = given.resolve()
    top, inside, commit = _located(resolved, path, ref)
    files = _files(top, commit, inside, ref, path)
    names = {name for name, _ in files}
    database = PurePosixPath(DATABASE_YAML) in names
    study = not database and PurePosixPath(STUDY_YAML) in names
    if study and any(name.parts[0] == "processes" for name in names):
        study = False  # the format before D46: study.yaml with the processes
    if not study and not database:
        raise StudyNotAtRef(
            f"{path} holds no database.yaml and no study.yaml without processes at {ref} "
            f"({commit[:12]}), so no database or study was there.",
            Source(ref, commit),
        )
    source = Source(ref, commit)
    with tempfile.TemporaryDirectory(prefix="headwater-diff-") as temp:
        root = Path(temp) / (resolved.name or "folder")
        _write(top, files, root)
        if not study:
            _convert_old(root, Path(path), source)

        def sibling(name: str) -> Path | None:
            """The database folder `name` next to the folder, at the same commit."""
            if str(inside) == ".":
                return None  # the folder is the repository: nothing is next to it in git
            target = Path(temp) / name
            if (target / DATABASE_YAML).is_file():
                return target
            try:
                found = _files(top, commit, inside.parent / name, ref, name)
            except StudyNotAtRef:
                return None
            if PurePosixPath(DATABASE_YAML) not in {path for path, _ in found}:
                return None
            _write(top, found, target)
            _convert_old(target, Path(path).parent / name, source)
            return target

        if not study:
            return FolderAt(read_database(root, sibling=sibling), Source(ref, commit))
        _write_reached(root, sibling)
        return FolderAt(read_study(root), Source(ref, commit))


def _convert_old(folder: Path, path: Path, source: Source) -> None:
    """Convert a database written from a commit of the layout before D48, in its temporary
    folder. Raises `DiffError` naming the command when the converter refuses it."""
    if not (folder / OLD_FOLDER).is_dir():
        return
    try:
        convert(folder)
    except InvalidDataError as error:
        raise DiffError(
            f"{path} at {source.label} has the layout before headwater 0.2.0 (processes/), and "
            f"it could not be converted to compare: {error}\nConvert it on a checkout of that "
            f"commit with `{CONVERT} {path}`, or compare from the commit that converted it on."
        ) from None


def _write_reached(root: Path, sibling: Callable[[str], Path | None]) -> None:
    """Write next to the study at `root` each database folder its products are in, and those
    they use, directly or not, so that the study reads them from its parent folder."""
    try:
        queue = list(dict.fromkeys(p.split(":", 1)[0] for p in study_products(root)))
    except InvalidDataError:
        return  # reading the study says what is wrong
    seen: set[str] = set()
    while queue:
        name = queue.pop(0)
        if name in seen or name == root.name:
            continue
        seen.add(name)
        folder = sibling(name)
        if folder is None:
            continue  # a product of the background
        try:
            queue += read_uses(folder)
        except InvalidDataError:
            continue  # reading the database says what is wrong


def study_products(folder: Path) -> tuple[str, ...]:
    """The process ids of the products that `study.yaml` in `folder` names, reading nothing
    else: `("tshirt:tshirt",)`. Raises `InvalidDataError` when it does not read."""
    problems: list[Problem] = []
    data = read_yaml(folder / STUDY_YAML, folder, problems)
    if data is None:
        raise InvalidDataError(problems)
    products = data.get("products")
    if not isinstance(products, list) or not all(
        isinstance(p, dict) and isinstance(p.get("process"), str)  # pyright: ignore[reportUnknownMemberType]
        for p in products  # pyright: ignore[reportUnknownVariableType]
    ):
        raise InvalidDataError(
            [
                Problem(
                    "wrong-type",
                    f"{STUDY_YAML}: products",
                    "is not a list of products, each `{process: <id>, amount: <number>}`",
                    location=STUDY_YAML,
                )
            ]
        )
    return tuple(str(p["process"]) for p in products)  # pyright: ignore[reportUnknownArgumentType, reportUnknownVariableType]


@dataclass(frozen=True)
class Neighbours:
    """What the folders next to a database reach, by folder name: what each database uses, and
    the databases each study's products are in. A file that does not read gives its error."""

    databases: dict[str, tuple[str, ...] | InvalidDataError]
    studies: dict[str, tuple[str, ...] | InvalidDataError]


def uses_next_to(path: Path | str, ref: str | None = None) -> Neighbours:
    """What each folder next to `path`, `path` included if it is there, reaches: each database's
    `uses` (`{"dyes": (), "tshirt": ("dyes",)}`) and the databases of each study's products
    (`{"tshirt-footprint": ("tshirt",)}`). Only `database.yaml` and `study.yaml` are read, at
    `ref`, or on disk when `ref` is None. A folder that is the whole repository has nothing next
    to it in git, as `read_at` reads it.

    Raises `DiffError` for an unknown ref or a folder that is not in a git repository.
    """
    given = Path(path)
    if ref is None:
        parent = given.resolve().parent
        studies: dict[str, tuple[str, ...] | InvalidDataError] = {}
        if parent.is_dir():
            for entry in sorted(parent.iterdir()):
                if (entry / STUDY_YAML).is_file():
                    studies[entry.name] = _prefixes_or_error(entry)
        return Neighbours(uses_on_disk(parent, read_uses), studies)
    top, inside, commit = _located(given.resolve(), path, ref)
    if str(inside) == ".":
        return Neighbours({}, {})
    parent = inside.parent
    folders = [name for mode, name in _entries(top, commit, parent) if mode == "040000"]
    found: dict[str, dict[str, tuple[str, ...] | InvalidDataError]] = {}
    with tempfile.TemporaryDirectory(prefix="headwater-uses-") as temp:
        for file, read in ((DATABASE_YAML, read_uses), (STUDY_YAML, _prefixes)):
            wanted = [(parent / name / file).as_posix() for name in folders]
            listed = [
                (PurePosixPath(name).parent.name, mode, sha)
                for mode, name, sha in _listed(top, commit, wanted)
            ]
            read_here: dict[str, tuple[str, ...] | InvalidDataError] = {}
            plain = [(name, sha) for name, mode, sha in listed if mode in _FILE_MODES]
            blobs = _blobs(top, [sha for _, sha in plain])
            for (name, _), content in zip(plain, blobs, strict=True):
                folder = Path(temp) / file / name
                folder.mkdir(parents=True)
                (folder / file).write_bytes(content)
                try:
                    read_here[name] = read(folder)
                except InvalidDataError as error:
                    read_here[name] = error
            for name, mode, _ in listed:
                if mode not in _FILE_MODES:
                    read_here[name] = InvalidDataError(
                        [
                            Problem(
                                "wrong-kind",
                                file,
                                "is a symbolic link or a submodule in git, not a plain file",
                                location=file,
                            )
                        ]
                    )
            found[file] = dict(sorted(read_here.items()))
    return Neighbours(found[DATABASE_YAML], found[STUDY_YAML])


def _prefixes(folder: Path) -> tuple[str, ...]:
    """The databases a study's products are in, by the prefix of their ids, each once."""
    return tuple(dict.fromkeys(p.split(":", 1)[0] for p in study_products(folder)))


def _prefixes_or_error(folder: Path) -> tuple[str, ...] | InvalidDataError:
    try:
        return _prefixes(folder)
    except InvalidDataError as error:
        return error


def _entries(top: Path, commit: str, folder: PurePosixPath) -> list[tuple[str, str]]:
    """The mode and name of each entry of `folder` at `commit`, not of what they hold."""
    args = ["ls-tree", "-z", "--full-tree", commit]
    if str(folder) != ".":
        args += ["--", f"{folder}/"]
    found: list[tuple[str, str]] = []
    for entry in _git(top, *args).split(b"\0"):
        if entry:
            meta, _, raw = entry.partition(b"\t")
            found.append((meta.split(b" ")[0].decode(), PurePosixPath(os.fsdecode(raw)).name))
    return found


def _listed(top: Path, commit: str, paths: list[str]) -> list[tuple[str, str, str]]:
    """The mode, path and blob hash of each of `paths` that `commit` has."""
    if not paths:
        return []
    found: list[tuple[str, str, str]] = []
    for entry in _git(top, "ls-tree", "-z", "--full-tree", commit, "--", *paths).split(b"\0"):
        if entry:
            meta, _, raw = entry.partition(b"\t")
            mode, _, sha = meta.decode().split(" ")
            found.append((mode, os.fsdecode(raw), sha))
    return found


def _located(resolved: Path, study: Path | str, ref: str) -> tuple[Path, PurePosixPath, str]:
    """The repository that holds `resolved`, its path inside it, and the commit `ref` names."""
    if ref.startswith("-"):
        raise DiffError(f"{ref!r} is not a ref: a ref does not start with a dash")
    top = _toplevel(resolved, study)
    try:
        inside = PurePosixPath(resolved.relative_to(top).as_posix())
    except ValueError:
        raise DiffError(f"{study} is not inside the git repository at {top}") from None
    return top, inside, _commit(top, ref)


def _write(top: Path, files: list[tuple[PurePosixPath, str]], root: Path) -> None:
    """Write the blobs of `files` under `root`, at their paths."""
    blobs = _blobs(top, [sha for _, sha in files])
    for (name, _), content in zip(files, blobs, strict=True):
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)


def _toplevel(resolved: Path, study: Path | str) -> Path:
    where = resolved
    while not where.exists():
        where = where.parent
    cwd = where if where.is_dir() else where.parent
    try:
        out = _git(cwd, "rev-parse", "--show-toplevel")
    except _GitFailed:
        raise DiffError(f"{study} is not inside a git repository") from None
    return Path(os.fsdecode(out).strip()).resolve()


def _commit(top: Path, ref: str) -> str:
    try:
        return os.fsdecode(
            _git(top, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
        ).strip()
    except _GitFailed:
        raise DiffError(f"{ref!r} is not a commit in the git repository at {top}") from None


def _files(
    top: Path, commit: str, inside: PurePosixPath, ref: str, study: Path | str
) -> list[tuple[PurePosixPath, str]]:
    """The files of a commit under `inside`, as (path relative to `inside`, blob hash)."""
    args = ["ls-tree", "-r", "-z", "--full-tree", commit]
    if str(inside) != ".":
        args += ["--", str(inside)]
    found: list[tuple[PurePosixPath, str]] = []
    for entry in _git(top, *args).split(b"\0"):
        if not entry:
            continue
        meta, _, raw = entry.partition(b"\t")
        mode, _, sha = meta.decode().split(" ")
        name = PurePosixPath(os.fsdecode(raw))
        if str(inside) != "." and not name.is_relative_to(inside):
            continue
        relative = name.relative_to(inside)
        if mode not in _FILE_MODES:
            kind = "a symbolic link" if mode == "120000" else "a submodule"
            raise DiffError(
                f"{name} is {kind}, which a study read at {ref} cannot contain. "
                "Replace it with a plain file."
            )
        found.append((relative, sha))
    if not found:
        raise StudyNotAtRef(
            f"{study} does not exist at {ref} ({commit[:12]}): the commit has no files in "
            f"{inside}.",
            Source(ref, commit),
        )
    return found


def _blobs(top: Path, shas: list[str]) -> list[bytes]:
    """The content of each blob, in order, from one `git cat-file --batch`."""
    out = _git(top, "cat-file", "--batch", stdin="".join(f"{sha}\n" for sha in shas).encode())
    blobs: list[bytes] = []
    at = 0
    for sha in shas:
        end = out.index(b"\n", at)
        header = out[at:end].split(b" ")
        if len(header) != 3 or header[1] != b"blob":
            raise DiffError(f"git could not read {sha}: {out[at:end].decode(errors='replace')}")
        size = int(header[2])
        blobs.append(out[end + 1 : end + 1 + size])
        at = end + 1 + size + 1
    return blobs


class _GitFailed(Exception):
    pass


def _git(cwd: Path, *args: str, stdin: bytes | None = None) -> bytes:
    try:
        done = subprocess.run(
            ["git", *args], cwd=cwd, input=stdin, capture_output=True, check=False
        )
    except FileNotFoundError:
        raise DiffError(
            "git was not found. diff reads studies from git, so it needs the git command on PATH"
        ) from None
    if done.returncode != 0:
        raise _GitFailed(done.stderr.decode(errors="replace").strip())
    return done.stdout
