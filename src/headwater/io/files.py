"""What the importers share when they write a file: the comment lines at the top of a YAML file,
and a value written on one line."""

from collections.abc import Iterable

import yaml


def one_line(text: str) -> str:
    """Text on one line, for a name or a comment that a CSV or YAML file shows. A cell can hold
    line breaks (Alt+Enter in Excel), and a study file's row is one line. Names used to link
    are never changed: only what is written."""
    return " ".join(text.split())


def yaml_comments(lines: Iterable[str]) -> str:
    """`lines` as YAML comments, one per line. A line break inside a line would turn the rest of
    it into YAML, so each is cut to one line: `["Read from a!A3"]` gives `# Read from a!A3\\n`."""
    return "".join(f"# {one_line(line)}\n" for line in lines)


def yaml_scalar(value: object) -> str:
    """One YAML value on one line, quoted when it has to be: `'NO'`, `'TODO: a goal'`."""
    text = yaml.safe_dump({"k": value}, allow_unicode=True, width=10**6, default_flow_style=True)
    return text.strip().removeprefix("{k:").removesuffix("}").strip()
