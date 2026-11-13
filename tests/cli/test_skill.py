"""`headwater skill install` and `skill show`, and the skill itself: valid front matter, commands
that exist, links to files of the repository at the version's tag (D49)."""

import json
import re
import shlex
from importlib.metadata import version
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from headwater.cli import COMMANDS, app, load
from headwater.cli._json import GROUPS
from headwater.cli.skill import END, START, agents_section, packaged
from tests.cli.conftest import ANSI

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "src" / "headwater" / "skill" / "SKILL.md"
REPOSITORY = "https://github.com/LeonardoNatale/headwater"
INSTALLED = Path(".claude") / "skills" / "headwater"


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
    copies = [p for p in ROOT.rglob("SKILL.md") if ".venv" not in p.parts and "dist" not in p.parts]
    assert [p.relative_to(ROOT) for p in copies] == [SKILL.relative_to(ROOT)]


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


def test_every_headwater_command_the_skill_names_exists_with_its_options() -> None:
    text = SKILL.read_text(encoding="utf-8")
    spans = re.findall(r"`(headwater [^`]+)`", text)
    for block in re.findall(r"^```bash\n(.*?)^```", text, flags=re.M | re.S):
        spans += [line for line in block.splitlines() if line.startswith("headwater ")]
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


def test_the_agents_section_takes_the_rules_from_the_skill() -> None:
    section = agents_section()
    rules = SKILL.read_text(encoding="utf-8").split("## Rules\n", 1)[1].split("\n## ", 1)[0]

    assert section.startswith(START + "\n") and section.endswith(END + "\n")
    assert rules.strip() in section
    assert "headwater skill show" in section


def test_show_prints_the_skill_byte_for_byte() -> None:
    result = CliRunner().invoke(app, ["skill", "show"], catch_exceptions=False)

    assert result.exit_code == 0
    assert result.stdout_bytes == SKILL.read_bytes()


# --- install ------------------------------------------------------------------------------------


def test_install_writes_the_packaged_skill_byte_for_byte(project: Path) -> None:
    code, output = run("skill", "install")

    assert code == 0, output
    assert (project / INSTALLED / "SKILL.md").read_bytes() == SKILL.read_bytes()
    record = json.loads((project / INSTALLED / ".headwater.json").read_text())
    assert record["version"] == version("headwater")
    assert "Wrote the headwater skill" in output


def test_a_second_install_changes_nothing(project: Path) -> None:
    run("skill", "install")
    files = [p for p in (project / ".claude").rglob("*") if p.is_file()]
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in files}

    code, output = run("skill", "install", "--json")

    assert code == 0
    assert json.loads(output)["skill"]["action"] == "up to date"
    after = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in files}
    assert after == before
    assert [p for p in (project / ".claude").rglob("*") if p.is_file()] == files


def test_a_skill_a_person_changed_is_refused_naming_it_unless_force(project: Path) -> None:
    run("skill", "install")
    installed = project / INSTALLED / "SKILL.md"
    installed.write_text(installed.read_text() + "\nMy own rule.\n")
    changed = installed.read_bytes()

    code, output = run("skill", "install", "--json")

    assert code == 1
    assert installed.read_bytes() == changed
    document = json.loads(output[output.index("{") :])
    [problem] = document["errors"]
    assert problem["code"] == "skill-changed" and problem["item"].endswith("SKILL.md")
    assert "--force" in problem["message"]

    code, output = run("skill", "install", "--force")

    assert code == 0, output
    assert installed.read_bytes() == SKILL.read_bytes()


def test_a_skill_an_older_headwater_wrote_is_updated(project: Path) -> None:
    import hashlib

    folder = project / INSTALLED
    folder.mkdir(parents=True)
    old = b"---\nname: headwater\ndescription: an older skill\n---\n"
    (folder / "SKILL.md").write_bytes(old)
    record = {"version": "0.2.9", "sha256": hashlib.sha256(old).hexdigest()}
    (folder / ".headwater.json").write_text(json.dumps(record))

    code, output = run("skill", "install", "--json")

    assert code == 0, output
    skill = json.loads(output)["skill"]
    assert (skill["action"], skill["from_version"]) == ("updated", "0.2.9")
    assert (folder / "SKILL.md").read_bytes() == SKILL.read_bytes()


def test_user_installs_under_home(project: Path, tmp_path: Path) -> None:
    code, output = run("skill", "install", "--user")

    assert code == 0, output
    assert (tmp_path / "home" / INSTALLED / "SKILL.md").read_bytes() == SKILL.read_bytes()
    assert not (project / ".claude").exists()


def test_user_with_agents_md_is_refused(project: Path) -> None:
    code, output = run("skill", "install", "--user", "--agents-md")

    assert code == 2
    assert "AGENTS.md" in output
    assert list(project.iterdir()) == []


def test_agents_md_adds_one_section_then_replaces_it_and_keeps_the_rest(project: Path) -> None:
    agents = project / "AGENTS.md"
    agents.write_text("# My project\n\nOur own rules.\n")

    code, output = run("skill", "install", "--agents-md")

    assert code == 0, output
    first = agents.read_text()
    assert first.startswith("# My project\n\nOur own rules.\n\n" + START)
    assert first.count(START) == 1 and first.count(END) == 1
    assert (project / INSTALLED / "SKILL.md").is_file()

    stale = first.replace("### Rules", "### Old rules") + "\n## After\n\nKept too.\n"
    agents.write_text(stale)

    code, output = run("skill", "install", "--agents-md", "--json")

    assert code == 0, output
    assert json.loads(output)["agents_md"]["action"] == "replaced"
    assert agents.read_text() == first + "\n## After\n\nKept too.\n"

    code, output = run("skill", "install", "--agents-md", "--json")

    assert json.loads(output)["agents_md"]["action"] == "up to date"


def test_agents_md_is_created_when_there_is_none(project: Path) -> None:
    code, _ = run("skill", "install", "--agents-md")

    assert code == 0
    assert (project / "AGENTS.md").read_text() == agents_section()


@pytest.mark.parametrize(
    "text",
    [
        f"# Mine\n{START}\nhalf a section\n",
        f"{START}\n{END}\n{START}\n{END}\n",
        f"{END}\n{START}\n",
    ],
)
def test_agents_md_with_broken_markers_is_refused_naming_it(project: Path, text: str) -> None:
    (project / "AGENTS.md").write_text(text)

    code, output = run("skill", "install", "--agents-md")

    assert code == 1
    assert "AGENTS.md" in output and "markers" in output
    assert (project / "AGENTS.md").read_text() == text
