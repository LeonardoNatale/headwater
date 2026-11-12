"""Find the databases and studies a pull request changed, check each one, and compare it with
the base.

Standard library only: the action runs it before anything else is installed. It reads git, runs the
`headwater` command, and writes one JSON report for `render.py`:

    {"format": "headwater-ci/2", "base": {...}, "head": {...}, "failed": false, "folders": [...]}

A folder is a database when it holds `database.yaml` and a study when it holds `study.yaml`. It is
`changed` when it exists on both sides, `new` when only the head has it and `removed` when only the
base has it. A database that did not change but uses one of those, directly or not, is
`uses-changed`, and a study whose products reach one is `reaches-changed`: their results move with
it. Each folder lists, in `uses`, the changed databases it reaches and the way it goes, from one run
of `headwater database uses` on the head. One run of `headwater diff` does the rest for each folder
that is not removed, so the background is loaded once: it validates the folder at the head, then
compares it with the base, or, for a folder the base does not have, counts the products of a new
database and calculates a new study. A removed folder is only listed. Databases come before
studies. Method folders given to the job are passed to each run: a folder that names a method its
release does not hold, by its id, is scored with the folder of that id.
"""

import argparse
import json
import os
import shlex
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

FORMAT = "headwater-ci/2"


class CollectError(Exception):
    """Something the job cannot go on without: git does not answer, a release is not there."""


@dataclass(frozen=True)
class Change:
    path: str
    """The folder, relative to the repository, with `/` as separator."""
    status: str
    """`changed`, `new`, `removed`, `uses-changed` for a database that uses one of those, or
    `reaches-changed` for a study whose products reach one."""
    kind: str
    """`database` or `study`."""


_FILES = {"database.yaml": "database", "study.yaml": "study"}
"""The file that makes a folder a database or a study (D46)."""


# --- which folders -----------------------------------------------------------------------------


def changed_folders(repo: Path, base: str, head: str, under: str = ".") -> list[Change]:
    """The databases and studies that differ between `base` and `head`, found by the files that
    changed, databases first.

    A database is a folder that holds `database.yaml` at either commit, and a study one that holds
    `study.yaml`. A changed file belongs to the deepest such folder above it. Files that belong to
    none are left out. `under` limits the result to folders inside that folder.
    """
    before, after = _folders(repo, base), _folders(repo, head)
    folders = {**before, **after}
    limit = PurePosixPath(_clean(under))
    found: set[str] = set()
    for name in _changed_files(repo, base, head):
        file = PurePosixPath(name)
        owners = [f for f in folders if _inside(file, PurePosixPath(f))]
        if owners:
            found.add(max(owners, key=lambda f: len(PurePosixPath(f).parts)))
    changes = [
        Change(
            f,
            "changed" if f in before and f in after else "new" if f in after else "removed",
            folders[f],
        )
        for f in sorted(found)
        if _inside(PurePosixPath(f), limit)
    ]
    return in_order(changes)


def in_order(changes: list[Change]) -> list[Change]:
    """Databases first, then studies, each by path."""
    return sorted(changes, key=lambda c: (c.kind != "database", c.path))


def _clean(path: str) -> str:
    """`./studies/` is `studies`; nothing is `.`."""
    return PurePosixPath(path.strip() or ".").as_posix()


def _inside(path: PurePosixPath, folder: PurePosixPath) -> bool:
    return folder == PurePosixPath(".") or path.is_relative_to(folder)


def _folders(repo: Path, rev: str) -> dict[str, str]:
    """Each database and study folder at `rev`, with its kind."""
    names = _git(repo, "ls-tree", "-r", "--name-only", "-z", rev).split("\0")
    return {
        PurePosixPath(n).parent.as_posix(): _FILES[PurePosixPath(n).name]
        for n in names
        if n and PurePosixPath(n).name in _FILES
    }


def _changed_files(repo: Path, base: str, head: str) -> list[str]:
    """Files that differ, with a rename counted as a deletion and an addition, so a folder that
    moves is one `removed` and one `new`."""
    out = _git(repo, "diff", "--name-only", "--no-renames", "-z", base, head)
    return [n for n in out.split("\0") if n]


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        encoding="utf-8",
        errors="surrogateescape",
        check=False,
    )
    if done.returncode != 0:
        raise CollectError(f"git {' '.join(args[:2])} failed: {done.stderr.strip()}")
    return done.stdout


def _commit(repo: Path, ref: str) -> str:
    return _git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}").strip()


def merge_base(repo: Path) -> str:
    """The first parent of HEAD, when HEAD is the merge of a pull request into its base."""
    parents = _git(repo, "rev-list", "--parents", "-n", "1", "HEAD").split()[1:]
    if len(parents) != 2:
        raise CollectError(
            "HEAD is not a merge commit, so the base of the pull request is not known. Run this "
            "on the merge ref of a pull request (the default checkout of the pull_request "
            "event), or give the base."
        )
    return parents[0]


# --- running the command line ------------------------------------------------------------------


def collect_report(
    repo: Path,
    headwater: Sequence[str],
    base: str,
    head: str,
    *,
    under: str = ".",
    base_label: str | None = None,
    head_label: str | None = None,
    methods: Sequence[str] = (),
) -> dict[str, Any]:
    """Validate, calculate and diff every database and study that changed, and those that reach
    a changed database, and say how each went.

    `headwater` is the command to run, for example `["headwater"]`. It runs in `repo`, which is
    the root of the checkout. `base` and `head` are anything git names a commit by. `methods` are
    method folders, for the methods that folders name and their releases do not hold.
    """
    base_commit, head_commit = _commit(repo, base), _commit(repo, head)
    changes = changed_folders(repo, base_commit, head_commit, under)
    uses, kinds, uses_error, unread = users(repo, headwater, changes, head_commit)
    known = {c.path for c in changes}
    limit = PurePosixPath(_clean(under))
    changes += [
        Change(
            path,
            "reaches-changed" if kinds[path] == "study" else "uses-changed",
            kinds[path],
        )
        for path in uses
        if path not in known and _inside(PurePosixPath(path), limit)
    ]
    folders = [
        {
            **_check(repo, headwater, change, base_commit, head_commit, methods),
            "uses": uses.get(change.path, []),
        }
        for change in in_order(changes)
    ]
    return {
        "format": FORMAT,
        "base": {"label": base_label or base, "commit": base_commit},
        "head": {"label": head_label or head, "commit": head_commit},
        "failed": uses_error is not None or any(not f["ok"] for f in folders),
        "uses_error": uses_error,
        "uses_unread": unread,
        "folders": folders,
    }


def users(
    repo: Path, headwater: Sequence[str], changes: Sequence[Change], head: str
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, str], str | None, list[dict[str, str]]]:
    """For each database that uses one of the databases of `changes` at `head`, and each study
    whose products reach one, directly or not, what it reaches that changed:
    `{"studies/tshirt": [{"chain": ["dyes", "chemicals"], "status": "changed"}]}` when the T-shirt
    uses `dyes`, which uses `chemicals`, which changed. Then the kind of each. One run of
    `headwater database uses`, which reads only database.yaml and study.yaml files.

    Also the folders whose file did not read, so whether they reach a changed database is not
    known: the comment lists them. When the command fails, nothing is found, and what it said is
    returned, so the job reports it and fails."""
    databases = [c for c in changes if c.kind == "database"]
    if not databases:
        return {}, {}, None, []
    args = ["database", "uses", "--ref", head, "--json", *(c.path for c in databases)]
    done = _run(headwater, args, repo)
    try:
        data = json.loads(done.stdout) if done.returncode == 0 else None
    except json.JSONDecodeError:
        data = None
    if not isinstance(data, dict) or "databases" not in data:
        return {}, {}, _said(done), []
    status = {c.path: c.status for c in databases}
    found: dict[str, list[dict[str, Any]]] = {}
    kinds: dict[str, str] = {}
    unread: dict[str, tuple[str, str]] = {}
    for database in data["databases"]:
        for user in database["users"]:
            kinds[user["path"]] = user["kind"]
            found.setdefault(user["path"], []).append(
                {"chain": user["through"], "status": status[database["path"]]}
            )
        for error in database["errors"]:
            unread.setdefault(error["path"], (error["file"], error["message"]))
    unread_list = [
        {"path": k, "file": file, "message": message}
        for k, (file, message) in sorted(unread.items())
    ]
    return found, kinds, None, unread_list


def _check(
    repo: Path,
    headwater: Sequence[str],
    change: Change,
    base: str,
    head: str,
    methods: Sequence[str] = (),
) -> dict[str, Any]:
    folder: dict[str, Any] = {
        "path": change.path,
        "kind": change.kind,
        "status": change.status,
        "ok": True,
        "validation": None,
        "diff": None,
        "diff_error": None,
    }
    if change.status == "removed":
        return folder
    folders = [arg for folder in methods for arg in ("--method-folder", folder)]
    done = _run(headwater, ["diff", base, head, change.path, "--json", *folders], repo)
    data = _read_json(done)
    if data is None:
        folder["diff_error"] = _said(done)
        folder["ok"] = False
        return folder
    validation = data["validation"]
    folder["validation"] = {
        "exit_code": 0 if validation["valid"] else 1,
        "errors": [problem["text"] for problem in validation["errors"]],
        "warnings": [problem["text"] for problem in validation["warnings"]],
    }
    if validation["valid"]:
        folder["diff"] = data
    else:
        folder["ok"] = False
    return folder


def _read_json(done: subprocess.CompletedProcess[str]) -> dict[str, Any] | None:
    """What `headwater diff --json` printed, or None when it failed or printed something else.
    It exits 0 with the diff, and 1 with the validation errors, both as JSON with a `validation`
    key. Anything else is a failure whose message the caller reports with `_said`, so a traceback
    reaches the comment instead of crashing the job."""
    if done.returncode not in (0, 1):
        return None
    try:
        data = json.loads(done.stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or "validation" not in data:
        return None
    return data


def _run(
    headwater: Sequence[str], args: Sequence[str], repo: Path
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            [*headwater, *args],
            cwd=repo,
            capture_output=True,
            encoding="utf-8",
            check=False,
        )
    except FileNotFoundError:
        raise CollectError(
            f"The command {shlex.join(headwater)} was not found. Install headwater first."
        ) from None


def _said(done: subprocess.CompletedProcess[str]) -> str:
    return (done.stderr or done.stdout).strip() or f"exit code {done.returncode}, no message"


# --- releases ----------------------------------------------------------------------------------


def pull_releases(headwater: Sequence[str], paths: Sequence[str]) -> list[str]:
    """Pull releases into the cache. Each path is a release folder (`name@hash`, with
    `release.json`) or a folder that holds release folders. Empty paths are skipped."""
    pulled: list[str] = []
    for raw in paths:
        if not raw.strip():
            continue
        found = _releases_in(Path(raw.strip()))
        if not found:
            raise CollectError(
                f"There is no release in {raw.strip()}. Give a release folder (named name@hash, "
                "with a release.json) or a folder that holds release folders."
            )
        for release in found:
            done = _run(headwater, ["release", "pull", str(release)], Path.cwd())
            if done.returncode != 0:
                raise CollectError(f"Pulling {release} failed:\n{_said(done)}")
            pulled.append(release.name)
    return pulled


def _releases_in(path: Path) -> list[Path]:
    if (path / "release.json").is_file():
        return [path]
    if not path.is_dir():
        return []
    return [p for p in sorted(path.iterdir()) if "@" in p.name and (p / "release.json").is_file()]


# --- the command -------------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0] if __doc__ else None)
    parser.add_argument("--repo", default=".", help="The checkout. Default: the current folder.")
    parser.add_argument("--out", required=True, help="Where to write the JSON report.")
    parser.add_argument("--base", default="", help="Default: the first parent of HEAD.")
    parser.add_argument("--base-label", default="")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--head-label", default="")
    parser.add_argument("--under", default=".", help="Only folders inside this folder.")
    parser.add_argument("--headwater", default="headwater", help="The command, as a shell line.")
    parser.add_argument("--releases", default="", help="Release paths to pull, one per line.")
    parser.add_argument("--methods", default="", help="Method folders, one per line.")
    args = parser.parse_args(argv)
    headwater = shlex.split(args.headwater)
    repo = Path(args.repo)
    try:
        for name in pull_releases(headwater, args.releases.splitlines()):
            print(f"Pulled release {name}")
        base = args.base or merge_base(repo)
        report = collect_report(
            repo,
            headwater,
            base,
            args.head,
            under=args.under,
            base_label=args.base_label or None,
            head_label=args.head_label or None,
            methods=[m.strip() for m in args.methods.splitlines() if m.strip()],
        )
    except CollectError as error:
        print("::error::" + str(error).replace("\n", "%0A"))
        return 1
    Path(args.out).write_text(json.dumps(report, allow_nan=False), encoding="utf-8")
    for folder in report["folders"]:
        state = "ok" if folder["ok"] else "failed"
        print(f"{folder['kind']} {folder['path']}: {folder['status']}, {state}")
    if not report["folders"]:
        print("No database or study changed.")
    if github_output := os.environ.get("GITHUB_OUTPUT"):
        with open(github_output, "a", encoding="utf-8") as handle:
            handle.write(f"folders={len(report['folders'])}\n")
            handle.write(f"failed={'true' if report['failed'] else 'false'}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
