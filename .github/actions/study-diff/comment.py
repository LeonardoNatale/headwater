"""Post the comment on a pull request, or update the one this action posted before.

Standard library and the `gh` command that GitHub's runners have. `gh` reads the token from
`GH_TOKEN`. The comment is found by a hidden marker on its first line and by its author, the
Actions bot, so a person who copies the marker into their own comment is not overwritten.
"""

import argparse
import json
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

MARKER = "<!-- headwater-study-diff -->"
BOT = "github-actions[bot]"

Gh = Callable[[list[str], str | None], str]
"""Runs `gh` with these arguments and this standard input, and returns what it printed."""


class GhError(Exception):
    pass


def run_gh(args: list[str], stdin: str | None = None) -> str:
    try:
        done = subprocess.run(
            ["gh", *args], input=stdin, capture_output=True, encoding="utf-8", check=False
        )
    except FileNotFoundError:
        raise GhError("The gh command was not found.") from None
    if done.returncode != 0:
        raise GhError(f"gh {' '.join(args[:3])} failed: {(done.stderr or done.stdout).strip()}")
    return done.stdout


def find_comment(
    comments: Sequence[dict[str, Any]], marker: str = MARKER, author: str = BOT
) -> dict[str, Any] | None:
    """The first comment by `author` that holds `marker`."""
    for found in comments:
        if found["login"] == author and marker in found["body"]:
            return found
    return None


def list_comments(repo: str, number: int, gh: Gh) -> list[dict[str, Any]]:
    """Every comment of a pull request: its `id`, the author's `login` and the `body`."""
    out = gh(
        [
            "api",
            "--paginate",
            f"repos/{repo}/issues/{number}/comments",
            "--jq",
            ".[] | {id, login: .user.login, body}",
        ],
        None,
    )
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def upsert(
    repo: str, number: int, body: str, *, gh: Gh = run_gh, only_existing: bool = False
) -> str:
    """Post `body`, or replace the comment this action posted before. Returns `created`,
    `updated` or `skipped`. With `only_existing`, a pull request that has no such comment gets
    none: there is nothing to say, and nothing out of date to correct."""
    existing = find_comment(list_comments(repo, number, gh))
    payload = json.dumps({"body": body})
    if existing is not None:
        path = f"repos/{repo}/issues/comments/{existing['id']}"
        gh(["api", "--method", "PATCH", path, "--input", "-"], payload)
        return "updated"
    if only_existing:
        return "skipped"
    gh(
        ["api", "--method", "POST", f"repos/{repo}/issues/{number}/comments", "--input", "-"],
        payload,
    )
    return "created"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Post or update the study diff comment.")
    parser.add_argument("--repo", required=True, help="owner/name")
    parser.add_argument("--number", required=True, type=int, help="The pull request number.")
    parser.add_argument("--body-file", required=True, help="The Markdown, from render.py.")
    parser.add_argument(
        "--only-existing", action="store_true", help="Update the comment, never start one."
    )
    args = parser.parse_args(argv)
    body = Path(args.body_file).read_text(encoding="utf-8")
    try:
        done = upsert(args.repo, args.number, body, only_existing=args.only_existing)
    except GhError as error:
        print("::error::" + str(error).replace("\n", "%0A"))
        return 1
    print(f"Comment {done}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
