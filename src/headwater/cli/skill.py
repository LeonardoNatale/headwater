"""`headwater skill install` and `headwater skill show`: the skill that teaches an AI agent to use
headwater (D49).

The skill is shipped as the Library Skills convention asks: `headwater/.agents/skills/headwater/`
inside the package, with its `SKILL.md`, so `uvx library-skills` finds it as it finds FastAPI's.
`headwater skill install` does the same for one library, with no other tool: in a project it
links `.claude/skills/headwater`, where Claude Code reads skills, and `.agents/skills/headwater`,
where Codex, Copilot and Cursor read them, to that folder of the installed package. A link
follows upgrades: after `pip install -U headwater` the agent reads the new skill. Example: in a
venv at `.venv`, `.claude/skills/headwater` links to
`../../.venv/lib/python3.13/site-packages/headwater/.agents/skills/headwater`.
"""

from __future__ import annotations

import os
import shutil
import sys
from importlib.resources import as_file, files
from pathlib import Path
from typing import Annotated, Any

import typer

from headwater.cli._common import JsonOption, fail
from headwater.cli._json import echo
from headwater.model import InvalidDataError, Problem

FORMAT = "headwater-skill-install/1"
"""The `format` of `skill install --json`. Within version 1 keys may be added, none is renamed or
removed."""

NAME = "headwater"
"""The skill's name, and the name of its folder wherever it is."""

TARGETS = (Path(".claude") / "skills", Path(".agents") / "skills")
"""Where a project's skills are, for Claude Code and for the agents that read `.agents/skills`."""

skill_app = typer.Typer(
    help="Install the skill that teaches an AI agent, such as Claude Code, to use headwater.",
    no_args_is_help=True,
)


def packaged() -> bytes:
    """SKILL.md as the package holds it."""
    return (files("headwater") / ".agents" / "skills" / NAME / "SKILL.md").read_bytes()


def version() -> str:
    from importlib.metadata import version

    return version("headwater")


@skill_app.command("install")
def install_command(
    user: Annotated[
        bool,
        typer.Option(
            "--user",
            help="Install for every project of this user, under ~/.claude/skills and "
            "~/.agents/skills.",
        ),
    ] = False,
    copy: Annotated[
        bool,
        typer.Option(
            "--copy",
            help="Copy the skill instead of linking it, where links cannot be made, as on some "
            "Windows machines. A copy does not follow upgrades: run the command again with "
            "--force after one.",
        ),
    ] = False,
    force: Annotated[
        bool,
        typer.Option("--force", help="Replace a skill folder that is not a link to this one."),
    ] = False,
    as_json: JsonOption = False,
) -> None:
    """Link the skill into .claude/skills/headwater and .agents/skills/headwater of this folder.

    Claude Code reads .claude/skills; Codex, Copilot and Cursor read .agents/skills. Each is a
    link to the skill inside the installed package, so it follows upgrades of headwater, as
    `uvx library-skills` makes them. A second run says it is up to date, and replaces a link to
    an older place, such as another venv. A folder that is not a link and differs from the skill,
    which a person may have written, is refused, naming it, unless --force. Exit code 0 when
    done, 1 when a folder is refused.
    """
    root = Path.home() if user else Path()
    with as_file(files("headwater") / ".agents" / "skills" / NAME) as source:
        try:
            for target in TARGETS:  # check every place before changing any
                _check(root / target / NAME, source, force=force)
            found = [_place(root / target / NAME, source, copy=copy) for target in TARGETS]
        except (InvalidDataError, OSError) as error:
            fail(error)
    if as_json:
        echo({"format": FORMAT, "version": version(), "skill": str(source), "targets": found})
        return
    for done in found:
        typer.echo(_said(done))


@skill_app.command("show")
def show_command() -> None:
    """Print the skill, SKILL.md, for an agent or a tool that reads no skills folder."""
    sys.stdout.buffer.write(packaged())
    sys.stdout.flush()


def _check(path: Path, source: Path, *, force: bool) -> None:
    """Raises InvalidDataError naming `path` when it is a folder, not a link, whose files differ
    from the skill, and `force` is False: a person may have written it."""
    if path.is_symlink() or not path.exists() or force or _same(path, source):
        return
    raise InvalidDataError(
        [
            Problem(
                "skill-folder",
                str(path),
                f"is a folder, not a link to the skill of the installed headwater, and its files "
                f"differ from the skill of headwater {version()}: a person may have written it, "
                "or it is a copy of an older one. Keep it, or pass --force to replace it.",
            )
        ]
    )


def _place(path: Path, source: Path, *, copy: bool) -> dict[str, Any]:
    """Link, or copy, the skill folder `source` to `path`, which `_check` let through. Returns the
    path, `link` or `copy`, and what was done: `linked`, `copied`, `up to date`, `updated` (a
    link that pointed elsewhere) or `replaced` (a folder)."""
    done: dict[str, Any] = {"path": str(path), "kind": "copy" if copy else "link"}
    if path.is_symlink():
        if not copy and path.resolve() == source.resolve():
            return {**done, "action": "up to date"}
        path.unlink()
        done["action"] = "updated"
    elif path.exists():
        if _same(path, source) and copy:
            return {**done, "action": "up to date"}
        shutil.rmtree(path)
        done["action"] = "replaced"
    else:
        done["action"] = "copied" if copy else "linked"
    path.parent.mkdir(parents=True, exist_ok=True)
    if copy:
        shutil.copytree(source, path)
    else:
        path.symlink_to(os.path.relpath(source.resolve(), path.parent.resolve()), True)
    return done


def _same(folder: Path, source: Path) -> bool:
    """True when `folder` holds the files of `source`, byte for byte, and no others."""
    mine = {p.relative_to(folder): p for p in folder.rglob("*") if p.is_file()}
    theirs = {p.relative_to(source): p for p in source.rglob("*") if p.is_file()}
    return mine.keys() == theirs.keys() and all(
        mine[k].read_bytes() == theirs[k].read_bytes() for k in mine
    )


def _said(done: dict[str, Any]) -> str:
    """One line for a person: what `install` did at one place."""
    path, action = done["path"], done["action"]
    what = "a copy of" if done["kind"] == "copy" else "a link to"
    if action == "up to date":
        return f"{path} is up to date: {what} the skill of headwater {version()}."
    if action == "updated":
        return f"{path} is now {what} the skill of headwater {version()}."
    if action == "replaced":
        return f"Replaced {path} with {what} the skill of headwater {version()}."
    return f"{path}: {what} the skill of headwater {version()}."
