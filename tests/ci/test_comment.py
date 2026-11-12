"""Posting the comment: one per pull request, found again by its hidden marker."""

import json
import os
import stat
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

MARKER = "<!-- headwater-study-diff -->"
BOT = "github-actions[bot]"


def entry(id: int, login: str, body: str) -> dict[str, Any]:
    return {"id": id, "login": login, "body": body}


class FakeGh:
    """Stands in for `gh api`: answers the listing, and records every other call."""

    def __init__(self, existing: list[dict[str, Any]]) -> None:
        self.existing = existing
        self.calls: list[tuple[list[str], dict[str, Any] | None]] = []

    def __call__(self, args: list[str], stdin: str | None = None) -> str:
        sent = json.loads(stdin) if stdin is not None else None
        self.calls.append((args, sent))
        if "--paginate" in args:
            return "\n".join(json.dumps(e) for e in self.existing)
        return ""

    def writes(self) -> list[tuple[list[str], dict[str, Any] | None]]:
        return [c for c in self.calls if "--paginate" not in c[0]]


def test_the_marker_is_the_one_the_renderer_writes(comment: ModuleType, render: ModuleType) -> None:
    assert comment.MARKER == render.MARKER == MARKER


def test_the_comment_is_the_bots_one_that_holds_the_marker(comment: ModuleType) -> None:
    comments = [
        entry(1, "alice", f"{MARKER}\nI copied this"),
        entry(2, BOT, "some other bot message"),
        entry(3, BOT, f"{MARKER}\n## Results diff"),
        entry(4, BOT, f"{MARKER}\nlater duplicate"),
    ]

    assert comment.find_comment(comments)["id"] == 3
    assert comment.find_comment(comments[:2]) is None
    assert comment.find_comment([]) is None


def test_a_pull_request_with_no_comment_gets_one(comment: ModuleType) -> None:
    gh = FakeGh([entry(1, "alice", "looks good")])

    done = comment.upsert("owner/repo", 7, f"{MARKER}\nbody → text", gh=gh)

    assert done == "created"
    [(args, sent)] = gh.writes()
    assert args == ["api", "--method", "POST", "repos/owner/repo/issues/7/comments", "--input", "-"]
    assert sent == {"body": f"{MARKER}\nbody → text"}


def test_an_existing_comment_is_updated_in_place(comment: ModuleType) -> None:
    gh = FakeGh([entry(41, BOT, f"{MARKER}\nold"), entry(42, "alice", "thanks")])

    done = comment.upsert("owner/repo", 7, f"{MARKER}\nnew", gh=gh)

    assert done == "updated"
    [(args, sent)] = gh.writes()
    assert args == [
        "api",
        "--method",
        "PATCH",
        "repos/owner/repo/issues/comments/41",
        "--input",
        "-",
    ]
    assert sent == {"body": f"{MARKER}\nnew"}


def test_with_nothing_to_report_a_comment_is_only_updated_never_started(
    comment: ModuleType,
) -> None:
    quiet = FakeGh([entry(1, "alice", "looks good")])
    stale = FakeGh([entry(41, BOT, f"{MARKER}\nold results")])

    assert comment.upsert("o/r", 7, "none", gh=quiet, only_existing=True) == "skipped"
    assert quiet.writes() == []
    assert comment.upsert("o/r", 7, "none", gh=stale, only_existing=True) == "updated"
    assert len(stale.writes()) == 1


def test_main_runs_gh_with_the_token_and_a_body_file(
    comment: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`gh` is a script here that logs what it was given, so the real command line is checked."""
    log = tmp_path / "log"
    gh = tmp_path / "bin" / "gh"
    gh.parent.mkdir()
    gh.write_text(
        "#!/bin/sh\n"
        f'echo "$GH_TOKEN $@" >> {log}\n'
        'case "$*" in *--paginate*) echo \'{"id": 5, "login": "github-actions[bot]", '
        f'"body": "{MARKER}"}}\' ;; *) cat >> {log}.stdin ;; esac\n'
    )
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{gh.parent}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("GH_TOKEN", "secret-token")
    body = tmp_path / "comment.md"
    body.write_text(f"{MARKER}\nnew → body\n", encoding="utf-8")

    code = comment.main(["--repo", "o/r", "--number", "7", "--body-file", str(body)])

    assert code == 0
    lines = log.read_text().splitlines()
    assert lines[0].startswith("secret-token api --paginate repos/o/r/issues/7/comments")
    assert lines[1] == "secret-token api --method PATCH repos/o/r/issues/comments/5 --input -"
    assert json.loads(Path(f"{log}.stdin").read_text()) == {"body": f"{MARKER}\nnew → body\n"}


def test_a_failing_gh_is_an_error_with_its_message(
    comment: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    gh = tmp_path / "bin" / "gh"
    gh.parent.mkdir()
    gh.write_text(
        '#!/bin/sh\necho "HTTP 403: Resource not accessible by integration" >&2\nexit 1\n'
    )
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{gh.parent}{os.pathsep}{os.environ['PATH']}")
    body = tmp_path / "comment.md"
    body.write_text(MARKER)

    code = comment.main(["--repo", "o/r", "--number", "7", "--body-file", str(body)])

    assert code == 1
    assert "Resource not accessible by integration" in capsys.readouterr().out
