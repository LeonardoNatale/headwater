"""What a pip user gets: one version, the licence, and metadata that names no email.

The metadata is read from the installed distribution, which the build backend writes from
pyproject.toml, the same way it writes the wheel's METADATA.
"""

import re
import shutil
import subprocess
import sys
import tomllib
from importlib.metadata import metadata, version
from pathlib import Path

import headwater

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
REPOSITORY = "https://github.com/LeonardoNatale/headwater"


def test_the_version_is_written_once_in_pyproject() -> None:
    assert PYPROJECT["version"] == "0.2.0"
    assert version("headwater") == "0.2.0"
    assert headwater.__version__ == "0.2.0"
    source = (ROOT / "src" / "headwater" / "__init__.py").read_text()
    assert "0.2.0" not in source


def test_headwater_version_prints_the_version() -> None:
    command = shutil.which("headwater", path=str(Path(sys.executable).parent))
    assert command, "the headwater command is installed next to this Python"

    done = subprocess.run([command, "--version"], capture_output=True, text=True)

    assert done.returncode == 0, done.stderr
    assert done.stdout == "headwater 0.2.0\n"


def test_the_metadata_names_the_licence_the_author_and_no_email() -> None:
    meta = metadata("headwater")

    assert meta["License-Expression"] == "Apache-2.0"
    assert meta.get_all("License-File") == ["LICENSE"]
    assert meta["Author"] == "Leonardo Natale"
    assert "Author-email" not in meta and "Maintainer-email" not in meta
    values = [
        v for value in meta.json.values() for v in (value if isinstance(value, list) else [value])
    ]
    assert not [v for v in values if "@" in v and "://" not in v]


def test_the_metadata_links_the_repository_its_docs_and_its_issues() -> None:
    urls = dict(line.split(", ", 1) for line in metadata("headwater").get_all("Project-URL") or [])

    assert urls == {
        "Repository": REPOSITORY,
        "Documentation": f"{REPOSITORY}/tree/main/docs",
        "Issues": f"{REPOSITORY}/issues",
    }


def test_the_classifiers_say_alpha_the_python_versions_and_typed() -> None:
    classifiers = metadata("headwater").get_all("Classifier") or []

    assert {
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Science/Research",
        "Programming Language :: Python :: 3.13",
        "Programming Language :: Python :: 3.14",
        "Typing :: Typed",
    } <= set(classifiers)
    # PEP 639: the licence expression replaces the licence classifiers.
    assert not [c for c in classifiers if c.startswith("License ::")]
    assert metadata("headwater")["Keywords"]


def test_the_licence_file_is_apache_2_with_the_copyright_line() -> None:
    text = (ROOT / "LICENSE").read_text()

    assert text.lstrip().startswith("Apache License\n")
    assert "Version 2.0, January 2004" in text
    assert "Copyright 2026 Leonardo Natale" in text


def test_every_dependency_has_a_lower_bound() -> None:
    requirements = [
        *PYPROJECT["dependencies"],
        *(r for extra in PYPROJECT["optional-dependencies"].values() for r in extra),
    ]

    unbounded = [r for r in requirements if not re.search(r">=\s*\d", r)]
    assert unbounded == []
