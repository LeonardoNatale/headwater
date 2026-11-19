"""`headwater skill install` and `skill show`, and the skill itself: where the Library Skills
convention puts it, valid front matter, commands that exist, links to files of the repository at
the version's tag (D49)."""

import json
import os
import re
import shlex
from importlib.metadata import version
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from headwater.cli import COMMANDS, app, load
from headwater.cli._json import GROUPS
from headwater.cli.skill import packaged
from tests.cli.conftest import ANSI

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "src" / "headwater" / ".agents" / "skills" / "headwater" / "SKILL.md"
REPOSITORY = "https://github.com/LeonardoNatale/headwater"
TARGETS = (Path(".claude") / "skills" / "headwater", Path(".agents") / "skills" / "headwater")


def run(*args: str) -> tuple[int, str]:
    result = CliRunner().invoke(app, list(args), catch_exceptions=False)
    return result.exit_code, ANSI.sub("", result.output)


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty project folder as the current folder, and a HOME of its own."""
    folder = tmp_path / "project"
    folder.mkdir()
    monkeypatch.chdir(folder)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    return folder


def front_matter() -> dict[str, object]:
    text = SKILL.read_text(encoding="utf-8")
    match = re.match(r"---\n(.*?)\n---\n", text, flags=re.S)
    assert match, "SKILL.md starts with its front matter"
    data = yaml.safe_load(match.group(1))
    assert isinstance(data, dict)
    return {str(k): v for k, v in data.items()}  # pyright: ignore[reportUnknownVariableType]


# --- the skill ----------------------------------------------------------------------------------


def test_the_package_holds_one_skill_and_it_is_the_file_of_the_repository() -> None:
    assert packaged() == SKILL.read_bytes()
    skipped = {".venv", "dist", ".claude"}  # .claude holds the worktrees of agent sessions
    copies = [
        p for p in ROOT.rglob("SKILL.md") if not skipped.intersection(p.relative_to(ROOT).parts)
    ]
    assert [p.relative_to(ROOT) for p in copies] == [SKILL.relative_to(ROOT)]


def test_the_skill_is_where_library_skills_looks_for_it() -> None:
    """`<package>/.agents/skills/<name>/SKILL.md`, the name of the folder the skill's own, as in
    FastAPI's wheel: `fastapi/.agents/skills/fastapi/SKILL.md`."""
    assert SKILL.parent.name == front_matter()["name"]
    assert SKILL.parent.parent.parent.name == ".agents"
    assert SKILL.parent.parent.parent.parent == ROOT / "src" / "headwater"


def test_the_front_matter_is_that_of_an_agent_skill() -> None:
    data = front_matter()

    assert data["name"] == "headwater"
    assert re.fullmatch(r"[a-z0-9-]{1,64}", str(data["name"]))
    description = str(data["description"])
    assert 100 < len(description) <= 1024
    assert "Use it when" in description
    assert data["metadata"] == {"headwater-version": version("headwater")}


def test_the_skill_is_short() -> None:
    assert len(SKILL.read_text(encoding="utf-8").splitlines()) <= 200


def test_every_link_points_at_a_file_of_the_repository_at_this_version() -> None:
    text = SKILL.read_text(encoding="utf-8")
    links = re.findall(r"\]\((https?://[^)\s]+)\)", text)

    assert links
    for link in links:
        prefix = f"{REPOSITORY}/blob/v{version('headwater')}/"
        assert link.startswith(prefix), link
        path = link.removeprefix(prefix).split("#")[0]
        assert (ROOT / path).is_file(), link


def command_spans() -> list[str]:
    """Every `headwater ...` the skill writes, in backticks or in a bash block."""
    text = SKILL.read_text(encoding="utf-8")
    spans = re.findall(r"`(headwater [^`]+)`", text)
    for block in re.findall(r"^```bash\n(.*?)^```", text, flags=re.M | re.S):
        spans += [line for line in block.splitlines() if line.startswith("headwater ")]
    return spans


def test_every_headwater_command_the_skill_names_exists_with_its_options() -> None:
    spans = command_spans()
    assert len(spans) > 20

    for span in spans:
        words = [w for w in shlex.split(span)[1:] if not w.startswith("<")]
        if words[0].startswith("-"):  # `headwater --version`
            assert words[0] == "--version"
            continue
        assert words[0] in COMMANDS, span
        name = words[0]
        if name in GROUPS:
            group = load(name)
            assert words[1] in getattr(group, "commands", {}), span
            name = f"{name} {words[1]}"
        _, help_text = run(*name.split(), "--help")
        for option in (w.split("=")[0] for w in words if w.startswith("--")):
            assert option in help_text, f"{span}: {name} has no {option}"


NOT_IN_THE_SKILL = {"skill show"}
"""The one command the skill need not name: its output is the skill."""


def test_the_skill_names_every_command() -> None:
    """AGENTS.md rule 11: a new command gets a line in the skill."""
    words = [shlex.split(span)[1:3] for span in command_spans()]
    named = {w[0] for w in words} | {" ".join(w) for w in words}
    commands = [
        f"{name} {word}" if name in GROUPS else name
        for name in COMMANDS
        for word in (getattr(load(name), "commands", {}) if name in GROUPS else [""])
    ]

    missing = [c for c in commands if c not in named | NOT_IN_THE_SKILL]
    assert not missing, f"add a line to {SKILL.relative_to(ROOT)} for: {', '.join(missing)}"


def test_show_prints_the_skill_byte_for_byte() -> None:
    result = CliRunner().invoke(app, ["skill", "show"], catch_exceptions=False)

    assert result.exit_code == 0
    assert result.stdout_bytes == SKILL.read_bytes()


# --- install ------------------------------------------------------------------------------------


def test_install_links_the_skill_of_the_installed_package_for_claude_and_other_agents(
    project: Path,
) -> None:
    code, output = run("skill", "install")

    assert code == 0, output
    for target in TARGETS:
        link = project / target
        assert link.is_symlink(), target
        assert not Path(os.readlink(link)).is_absolute(), "a relative link, as library-skills makes"
        assert link.resolve() == SKILL.parent.resolve()
        assert (link / "SKILL.md").read_bytes() == SKILL.read_bytes()
    assert "a link to the skill of headwater" in output


def test_a_second_install_changes_nothing(project: Path) -> None:
    run("skill", "install")
    before = {t: os.readlink(project / t) for t in TARGETS}

    code, output = run("skill", "install", "--json")

    assert code == 0
    found = json.loads(output)
    assert found["format"] == "headwater-skill-install/1"
    assert [t["action"] for t in found["targets"]] == ["up to date", "up to date"]
    assert {t: os.readlink(project / t) for t in TARGETS} == before


def test_a_link_to_another_place_is_updated(project: Path, tmp_path: Path) -> None:
    old = tmp_path / "old-venv" / "headwater" / ".agents" / "skills" / "headwater"
    link = project / TARGETS[0]
    link.parent.mkdir(parents=True)
    link.symlink_to(old, target_is_directory=True)  # an older venv, gone since

    code, output = run("skill", "install", "--json")

    assert code == 0, output
    assert json.loads(output)["targets"][0]["action"] == "updated"
    assert link.resolve() == SKILL.parent.resolve()


def test_a_folder_a_person_wrote_is_refused_naming_it_unless_force(project: Path) -> None:
    mine = project / TARGETS[1]
    mine.mkdir(parents=True)
    (mine / "SKILL.md").write_text("---\nname: headwater\ndescription: my own notes\n---\n")

    code, output = run("skill", "install", "--json")

    assert code == 1
    assert (mine / "SKILL.md").read_text().endswith("my own notes\n---\n")
    assert not (project / TARGETS[0]).exists(), "nothing is changed when one place is refused"
    document = json.loads(output[output.index("{") :])
    [problem] = document["errors"]
    assert problem["code"] == "skill-folder" and problem["item"] == str(TARGETS[1])
    assert "--force" in problem["message"]

    code, output = run("skill", "install", "--force")

    assert code == 0, output
    assert mine.is_symlink() and mine.resolve() == SKILL.parent.resolve()


def test_a_folder_that_holds_the_skill_as_it_is_becomes_a_link(project: Path) -> None:
    folder = project / TARGETS[0]
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_bytes(SKILL.read_bytes())

    code, output = run("skill", "install")

    assert code == 0, output
    assert folder.is_symlink()


def test_copy_copies_the_skill_byte_for_byte(project: Path) -> None:
    code, output = run("skill", "install", "--copy", "--json")

    assert code == 0, output
    assert [t["kind"] for t in json.loads(output)["targets"]] == ["copy", "copy"]
    for target in TARGETS:
        assert not (project / target).is_symlink()
        assert (project / target / "SKILL.md").read_bytes() == SKILL.read_bytes()

    code, output = run("skill", "install", "--copy", "--json")

    assert [t["action"] for t in json.loads(output)["targets"]] == ["up to date", "up to date"]


def test_user_links_under_home(project: Path, tmp_path: Path) -> None:
    code, output = run("skill", "install", "--user")

    assert code == 0, output
    for target in TARGETS:
        assert (tmp_path / "home" / target).resolve() == SKILL.parent.resolve()
    assert not (project / ".claude").exists() and not (project / ".agents").exists()
