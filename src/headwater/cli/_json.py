"""What `--json` prints (D49): one JSON document on standard output, and a failure as a document
too, `headwater-error/1`, whose problems have the shape of `validate --json`.

Example: `headwater contributions project/none --json` exits 1 and prints
`{"format": "headwater-error/1", "command": "contributions", "exit_code": 1, "errors": [...]}`,
each error with `code`, `item`, `message`, `location` and `text`. The message also goes to
standard error, as without `--json`.

This module imports only the standard library and Typer: the command group uses it before any
command's module is loaded.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from contextvars import ContextVar
from typing import Any, NamedTuple

import typer

ERROR_FORMAT = "headwater-error/1"
"""The `format` of a failure under `--json`. Within version 1 keys may be added, none is renamed
or removed."""

GROUPS = frozenset({"release", "method", "database", "skill"})
"""The commands that hold commands: `database import-excel` is one command of two words."""


class Asked(NamedTuple):
    """Whether the command line asked for `--json`, and which command it runs."""

    json: bool
    command: str
    """`contributions`, or `database import-excel` for a command of a group."""

    @classmethod
    def of(cls, args: Sequence[str]) -> Asked:
        words = list(args)
        named = words[:2] if words[:1] and words[0] in GROUPS else words[:1]
        return cls("--json" in words, " ".join(w for w in named if not w.startswith("-")))


NOTHING_ASKED = Asked(json=False, command="")
"""Outside a command line, as when a command's function is called from Python."""

asked: ContextVar[Asked] = ContextVar("asked", default=NOTHING_ASKED)
"""Set by the command group from the arguments, before a command runs."""


def dumps(data: Any) -> str:
    """JSON as every command prints it: indented, no NaN, text as it is."""
    return json.dumps(data, indent=2, allow_nan=False, ensure_ascii=False)


def echo(data: Any) -> None:
    """Print `data` as the one JSON document of standard output."""
    typer.echo(dumps(data))


def problem(code: str, item: str, message: str, location: str | None = None) -> dict[str, Any]:
    """One problem as `validate --json` writes it. `text` is the line `validate` prints."""
    where = f" [{location}]" if location else ""
    return {
        "code": code,
        "item": item,
        "message": message,
        "location": location,
        "text": f"{item}: {message}{where}",
    }


def error_document(exit_code: int, errors: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """A failure under `--json`: the command, its exit code, and what is wrong."""
    return {
        "format": ERROR_FORMAT,
        "command": asked.get().command,
        "exit_code": exit_code,
        "errors": list(errors),
    }


def kebab(name: str) -> str:
    """The code of an error from its class: `SingularMatrixError` gives `singular-matrix`."""
    words = re.sub(r"(?<!^)(?=[A-Z][a-z])", "-", name.removesuffix("Error"))
    return words.lower() or "error"
