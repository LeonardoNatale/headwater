"""`headwater skill install` and `headwater skill show`: the skill that teaches an AI agent to use
headwater, kept once in the package as `headwater/skill/SKILL.md` (D49).

Example: in a project, `headwater skill install` writes `.claude/skills/headwater/SKILL.md`, which
Claude Code reads in its next session, and beside it `.headwater.json`, the version and sha256 of
what it wrote. A second run of the same version changes nothing. A run of a newer headwater
replaces a skill that is as an older one wrote it, and refuses one a person changed, naming it,
unless `--force`.
"""

from __future__ import annotations

import hashlib
import json
import sys
from importlib.resources import files
from pathlib import Path
from typing import Annotated, Any

import typer

from headwater.cli._common import JsonOption, fail
from headwater.cli._json import echo
from headwater.model import InvalidDataError, Problem

FORMAT = "headwater-skill-install/1"
"""The `format` of `skill install --json`. Within version 1 keys may be added, none is renamed or
removed."""

RECORD = ".headwater.json"
"""Beside SKILL.md: the version of headwater that wrote it, and the sha256 of what it wrote."""

START = "<!-- headwater:start -->"
END = "<!-- headwater:end -->"
"""The lines around the section `--agents-md` writes into AGENTS.md. A second run replaces what is
between them, markers included, and leaves the rest of the file as it was."""

skill_app = typer.Typer(
    help="Install the skill that teaches an AI agent, such as Claude Code, to use headwater.",
    no_args_is_help=True,
)


def packaged() -> bytes:
    """SKILL.md as the package holds it."""
    return (files("headwater") / "skill" / "SKILL.md").read_bytes()


def version() -> str:
    from importlib.metadata import version

    return version("headwater")


def agents_section() -> str:
    """The section of AGENTS.md, for agents other than Claude Code: what headwater is, where the
    full guide is, and the rules of the skill, taken from SKILL.md so they are written once."""
    text = packaged().decode("utf-8")
    rules = text.split("\n## Rules\n", 1)[1].split("\n## ", 1)[0].strip("\n")
    return "\n".join(
        [
            START,
            f"<!-- Written by `headwater skill install --agents-md`, headwater {version()}. A "
            "second run replaces the lines between these markers. -->",
            "## headwater",
            "",
            "This project uses headwater for life cycle assessment (LCA). Run "
            "`headwater skill show` for the guide: the commands to score, explain a score, "
            "edit and compare, with examples. Every command but `skill show` takes `--json` "
            "and then prints one JSON document.",
            "",
            "### Rules",
            "",
            rules,
            END,
            "",
        ]
    )


@skill_app.command("install")
def install_command(
    user: Annotated[
        bool,
        typer.Option(
            "--user", help="Install for every project of this user, in ~/.claude/skills/headwater."
        ),
    ] = False,
    agents_md: Annotated[
        bool,
        typer.Option(
            "--agents-md",
            help="Also add or replace the headwater section of AGENTS.md in this folder, for "
            "agents that read that file, such as Codex, Cursor or Copilot.",
        ),
    ] = False,
    force: Annotated[
        bool,
        typer.Option("--force", help="Replace a SKILL.md that a person changed."),
    ] = False,
    as_json: JsonOption = False,
) -> None:
    """Write the skill into .claude/skills/headwater/ of this folder, where Claude Code finds it.

    A second run says the skill is up to date, or replaces one an older headwater wrote. A
    SKILL.md that a person changed is refused, naming it, unless --force. --agents-md also writes
    one marked section of AGENTS.md, and a second run replaces it, leaving the rest of the file
    as it was. Exit code 0 when done, 1 when a file is refused, 2 for --user with --agents-md.
    """
    if user and agents_md:
        raise typer.BadParameter(
            "AGENTS.md belongs to a project: run --agents-md in the project's folder, without "
            "--user.",
            param_hint="--agents-md",
        )
    folder = (Path.home() if user else Path()) / ".claude" / "skills" / "headwater"
    try:
        planned = _agents_md(Path("AGENTS.md")) if agents_md else None  # refused before writing
        skill = _install_skill(folder, force=force)
        agents = None
        if planned is not None:
            text, agents = planned
            if text is not None:
                Path(agents["path"]).write_text(text, encoding="utf-8")
    except (InvalidDataError, OSError) as error:
        fail(error)
    if as_json:
        echo({"format": FORMAT, "version": version(), "skill": skill, "agents_md": agents})
        return
    typer.echo(_said(skill))
    if agents is not None:
        typer.echo(_agents_said(agents))


@skill_app.command("show")
def show_command(
    agents_md: Annotated[
        bool,
        typer.Option("--agents-md", help="Print the section --agents-md writes instead."),
    ] = False,
) -> None:
    """Print the skill, SKILL.md, for an agent or a tool that does not read .claude/skills."""
    if agents_md:
        typer.echo(agents_section(), nl=False)
        return
    sys.stdout.buffer.write(packaged())
    sys.stdout.flush()


def _install_skill(folder: Path, *, force: bool) -> dict[str, Any]:
    """Write SKILL.md into `folder` unless it is already there as this version wrote it. Returns
    what was done: `written`, `up to date`, `updated` (from an older headwater) or `replaced`
    (with --force).

    Raises InvalidDataError naming the file when a person changed it and `force` is False.
    """
    path = folder / "SKILL.md"
    new = packaged()
    done: dict[str, Any] = {"path": str(path), "action": "written", "from_version": None}
    if path.exists():
        old = path.read_bytes()
        record = _read_record(folder)
        if old == new:
            done["action"] = "up to date"
        elif record is not None and record[1] == _sha256(old):
            done["action"], done["from_version"] = "updated", record[0]
        elif force:
            done["action"] = "replaced"
        else:
            raise InvalidDataError(
                [
                    Problem(
                        "skill-changed",
                        str(path),
                        "differs from the skill headwater wrote: a person changed it, or it was "
                        "not written by `headwater skill install`. Keep your copy, or pass "
                        "--force to replace it with the skill of headwater " + version() + ".",
                    )
                ]
            )
    if done["action"] != "up to date":
        folder.mkdir(parents=True, exist_ok=True)
        path.write_bytes(new)
    record_text = json.dumps({"version": version(), "sha256": _sha256(new)}, indent=2) + "\n"
    record_path = folder / RECORD
    if not record_path.exists() or record_path.read_text(encoding="utf-8") != record_text:
        record_path.write_text(record_text, encoding="utf-8")
    return done


def _read_record(folder: Path) -> tuple[str, str] | None:
    """The version and sha256 of the record beside SKILL.md, or None when there is none or it
    does not read."""
    try:
        data: Any = json.loads((folder / RECORD).read_text(encoding="utf-8"))
        return str(data["version"]), str(data["sha256"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _agents_md(path: Path) -> tuple[str | None, dict[str, Any]]:
    """AGENTS.md with the section added, or the one between the markers replaced, every other
    line as it was, and what that does: `added`, `replaced`, or `up to date` with no text to
    write.

    Raises InvalidDataError naming the file when its markers are not one start then one end.
    """
    section = agents_section()
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    lines = text.splitlines(keepends=True)
    starts = [i for i, line in enumerate(lines) if line.rstrip("\r\n") == START]
    ends = [i for i, line in enumerate(lines) if line.rstrip("\r\n") == END]
    if not starts and not ends:
        gap = "" if not text else "\n" if text.endswith("\n") else "\n\n"
        return text + gap + section, {"path": str(path), "action": "added"}
    if len(starts) != 1 or len(ends) != 1 or starts[0] > ends[0]:
        raise InvalidDataError(
            [
                Problem(
                    "agents-md-markers",
                    str(path),
                    f"has {len(starts)} {START} and {len(ends)} {END} lines: the headwater "
                    "section is one start line, then one end line. Fix the markers by hand, or "
                    "remove them to add the section again.",
                )
            ]
        )
    old = "".join(lines[starts[0] : ends[0] + 1])
    if old.rstrip("\r\n") == section.rstrip("\n"):
        return None, {"path": str(path), "action": "up to date"}
    kept = "".join(lines[: starts[0]]) + section + "".join(lines[ends[0] + 1 :])
    return kept, {"path": str(path), "action": "replaced"}


def _said(skill: dict[str, Any]) -> str:
    """One line for a person: what `install` did with SKILL.md."""
    path, action = skill["path"], skill["action"]
    if action == "up to date":
        return f"The headwater skill in {path} is up to date ({version()})."
    if action == "updated":
        return f"Updated the headwater skill in {path} from {skill['from_version']} to {version()}."
    if action == "replaced":
        return f"Replaced {path} with the headwater skill {version()}."
    return (
        f"Wrote the headwater skill {version()} to {path}. Claude Code reads it in its next "
        "session."
    )


def _agents_said(agents: dict[str, Any]) -> str:
    """One line for a person: what `install --agents-md` did with AGENTS.md."""
    path, action = agents["path"], agents["action"]
    if action == "up to date":
        return f"The headwater section of {path} is up to date."
    if action == "added":
        return f"Added the headwater section to {path}."
    return f"Replaced the headwater section of {path}."
