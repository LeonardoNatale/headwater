"""llms.txt, the index of the docs for language models: a title, one line on what headwater is,
and absolute links, each to a file of the repository (D49)."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEXT = (ROOT / "llms.txt").read_text(encoding="utf-8")
REPOSITORY = "https://github.com/LeonardoNatale/headwater"


def test_it_starts_with_the_title_and_one_line_on_headwater() -> None:
    lines = TEXT.splitlines()

    assert lines[0] == "# headwater"
    assert lines[2].startswith("> headwater is ")


def test_every_link_is_absolute_and_points_at_a_file_of_the_repository() -> None:
    links = re.findall(r"\]\(([^)\s]+)\)", TEXT)

    assert len(links) >= 6
    for link in links:
        assert link.startswith(f"{REPOSITORY}/blob/main/"), link
        assert (ROOT / link.removeprefix(f"{REPOSITORY}/blob/main/")).is_file(), link


def test_it_links_the_readme_the_docs_the_notebooks_and_the_decisions() -> None:
    for path in ("README.md", "docs/workflows.md", "notebooks/README.md", "docs/decisions.md"):
        assert f"{REPOSITORY}/blob/main/{path}" in TEXT, path
