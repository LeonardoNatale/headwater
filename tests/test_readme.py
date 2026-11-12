"""README.md is the page on pypi.org: links that work there, and a quick start that runs as written.

The quick start runs with the `headwater` and `python` next to this test's Python. CI also runs it
from the built wheel in a clean venv: `HEADWATER_QUICK_START_BIN` names that venv's `bin` folder.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text()
SCORE = 1.242646
"""The demo study `tshirt-footprint`: the toy T-shirt with 0.01 kg of dye."""


def section(title: str) -> str:
    match = re.search(rf"^## {re.escape(title)}\n(.*?)(?=^## |\Z)", README, flags=re.M | re.S)
    assert match, f"README.md has no section {title!r}"
    return match.group(1)


def blocks(text: str, language: str) -> list[str]:
    return re.findall(rf"^```{language}\n(.*?)^```", text, flags=re.M | re.S)


def test_every_link_of_the_readme_is_absolute() -> None:
    targets = [
        *re.findall(r"\]\(([^)\s]+)", README),
        *re.findall(r"""(?:href|src)=["']([^"']+)""", README),
    ]

    assert targets, "the README links its docs"
    assert [t for t in targets if not t.startswith("https://")] == []


def test_the_readme_says_alpha_and_names_no_milestone() -> None:
    assert "alpha" in README.lower()
    assert re.findall(r"\bM\d+\b", README) == []


def test_the_readme_has_an_install_line() -> None:
    assert "pip install headwater" in section("Quick start")


def test_the_quick_start_runs_as_written_and_scores_the_demo_study(tmp_path: Path) -> None:
    quick_start = section("Quick start")
    [shell] = blocks(quick_start, "bash")
    [python] = blocks(quick_start, "python")
    lines = [line for line in shell.splitlines() if not line.startswith("pip install")]
    script = "\n".join(["set -euo pipefail", *lines, "python - <<'PYTHON'", python, "PYTHON\n"])
    bin_folder = os.environ.get("HEADWATER_QUICK_START_BIN") or str(Path(sys.executable).parent)
    env = {
        **os.environ,
        "PATH": f"{bin_folder}{os.pathsep}{os.environ['PATH']}",
        "HEADWATER_HOME": str(tmp_path / "home"),
    }

    done = subprocess.run(
        ["bash", "-c", script], cwd=tmp_path, env=env, capture_output=True, text=True
    )

    assert done.returncode == 0, done.stdout + done.stderr
    assert "Warning" not in done.stderr, done.stderr
    printed = float(done.stdout.strip().splitlines()[-1])
    assert printed == pytest.approx(SCORE, rel=1e-9)
    assert f"# {SCORE}" in python, "the quick start shows the number it prints"
