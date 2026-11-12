"""Where a folder's files stand in git: the commit of the repository that holds them, and whether
they differ from it. The provenance of a database's or a study's result names both."""

import subprocess
from collections.abc import Sequence
from pathlib import Path


def git_state(paths: Sequence[Path]) -> tuple[str | None, bool]:
    """The commit the repository that holds `paths[0]` is at, and whether any of `paths` has
    files that differ from it. (None, False) outside a git repository. A path that is not in
    that repository counts as changed: the commit alone does not give its files back."""
    first = paths[0].resolve()

    def git(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(first), *args], capture_output=True, text=True, check=False
        )

    head = git("rev-parse", "HEAD")
    if head.returncode != 0:
        return None, False
    root = Path(git("rev-parse", "--show-toplevel").stdout.strip()).resolve()
    resolved = [p.resolve() for p in paths]
    if any(not p.is_relative_to(root) for p in resolved):
        return head.stdout.strip(), True
    status = git("status", "--porcelain", "--", *(str(p) for p in resolved))
    return head.stdout.strip(), status.returncode != 0 or bool(status.stdout.strip())
