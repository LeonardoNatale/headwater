"""Every headwater name the notebooks show exists, with the arguments they give it.

The toy notebooks run in CI, which checks the code they run. This test also checks what CI does
not run: the text of every notebook, and the code of the notebooks on licensed data. It reads:

- in code, `hw.<name>`, `from headwater... import <name>`, and `<variable>.<name>(<keyword>=...)`
  for the variables below, such as `bg.where(..., indicator=...)`;
- in the text, the same written in backticks, such as `bg.sensitivity(product_id, top=None)`;
- `headwater` commands: in the text, `headwater release import ... --version`, and in code, the
  arguments of the notebooks' `headwater(...)` helper.

A variable whose name is not below is not checked.
"""

import ast
import dataclasses
import importlib
import inspect
import re
from pathlib import Path

import nbformat
import pytest

import headwater as hw
from headwater.cli import COMMANDS, load
from headwater.model import Problem, Report
from headwater.query.result import MethodRef
from headwater.release import Release

NOTEBOOKS = sorted(Path(__file__).parent.glob("[0-9][0-9]-*.ipynb"))

RECEIVERS: dict[str, object] = {
    "hw": hw,
    "bg": hw.Background,
    "toy_bg": hw.Background,
    "db": hw.Database,
    "parameter_db": hw.Database,
    "study": hw.Study,
    "batch": hw.Study,
    "saved": hw.Study,
    "result": hw.Result,
    "biogenic": hw.Result,
    "with_waste": hw.Result,
    "more_fabric": hw.Result,
    "heavier_result": hw.Result,
    "excel_result": hw.Result,
    "tote_result": hw.Result,
    "dye_result": hw.Result,
    "provenance": hw.Provenance,
    "p": hw.Provenance,
    "tree": hw.Contribution,
    "root": hw.Contribution,
    "short": hw.Contribution,
    "child": hw.Contribution,
    "where": hw.Where,
    "emitter": hw.Emitter,
    "match": hw.Match,
    "line": hw.Sensitivity,
    "fabric_line": hw.Sensitivity,
    "parameter": hw.ParameterSensitivity,
    "flow": hw.NotCharacterised,
    "skus": hw.ManyScores,
    "scores": hw.ManyScores,
    "one_kg": hw.ManyScores,
    "every": hw.ManyScores,
    "from_release": hw.ManyScores,
    "release": Release,
    "indicators": Release,
    "error": hw.InvalidDataError,
    "problem": Problem,
    "report": Report,
    "indicator_method": MethodRef,
}


def attributes(kind: object) -> set[str]:
    """Every name an object of this kind has: members, dataclass fields, and what `__init__`
    sets on `self`."""
    names = set(dir(kind))
    if dataclasses.is_dataclass(kind):
        names |= {field.name for field in dataclasses.fields(kind)}
    init = getattr(kind, "__init__", None)
    if inspect.isclass(kind) and init is not None and init is not object.__init__:
        try:
            source = inspect.getsource(init)
        except (OSError, TypeError):
            source = ""
        names |= set(re.findall(r"self\.(\w+)\s*(?::[^=\n]*)?=", source))
    return names


def keywords(kind: object, name: str) -> set[str] | None:
    """The keyword arguments `kind.name` takes, or None when it takes any."""
    member = getattr(kind, name, None)
    if member is None or not callable(member):
        return set()
    try:
        parameters = inspect.signature(member).parameters.values()
    except (TypeError, ValueError):  # a class with no signature, such as an exception
        return None
    if any(p.kind is p.VAR_KEYWORD for p in parameters):
        return None
    return {p.name for p in parameters}


def check(receiver: str, name: str, given: list[str], where: str) -> list[str]:
    kind = RECEIVERS[receiver]
    if name not in attributes(kind):
        return [f"{where}: {receiver}.{name} does not exist"]
    allowed = keywords(kind, name)
    if allowed is None:
        return []
    return [f"{where}: {receiver}.{name} takes no {k}=" for k in given if k not in allowed]


def code_problems(source: str, where: str) -> list[str]:
    problems: list[str] = []
    tree = ast.parse(source)
    keywords_of = {
        id(call.func): [k.arg for k in call.keywords if k.arg is not None]
        for call in ast.walk(tree)
        if isinstance(call, ast.Call)
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("headwater"):
            module = importlib.import_module(node.module or "")
            problems += [
                f"{where}: {node.module}.{alias.name} does not exist"
                for alias in node.names
                if not hasattr(module, alias.name)
            ]
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "headwater":
                problems += command_problems(
                    [a.value for a in node.args if isinstance(a, ast.Constant)], where
                )
        if not isinstance(node, ast.Attribute) or not isinstance(node.value, ast.Name):
            continue
        receiver = node.value.id
        if receiver not in RECEIVERS:
            continue
        problems += check(receiver, node.attr, keywords_of.get(id(node), []), where)
    return problems


TEXT_CALL = re.compile(r"\b([a-z_]+)\.([A-Za-z_]+)(?:\(([^()`]*)\))?")
FILE_ENDINGS = {"yaml", "yml", "json", "csv", "parquet", "py", "md", "txt", "xlsx", "ipynb"}
"""`database.yaml` in the text is a file, not an attribute."""


def text_problems(text: str, where: str) -> list[str]:
    problems: list[str] = []
    for span in re.findall(r"`([^`\n]+)`", text):
        if span.startswith("headwater "):
            problems += command_problems(span.split()[1:], where)
            continue
        for receiver, name, arguments in TEXT_CALL.findall(span):
            if receiver not in RECEIVERS or name in FILE_ENDINGS:
                continue
            given = re.findall(r"\b([a-z_]+)=", arguments or "")
            problems += check(receiver, name, given, where)
        for first, second in re.findall(r"\b([a-z_]+)\.provenance\.([a-z_]+)", span):
            if second not in attributes(hw.Provenance):
                problems.append(f"{where}: {first}.provenance.{second} does not exist")
    return problems


def command_problems(words: list[object], where: str) -> list[str]:
    """`["release", "import", <path>, "--version", "1.0"]`: the command and its options.

    The `headwater` group loads a command's module only when it is run, so the first word is
    loaded the way the group loads it."""
    words = [str(w) for w in words]
    if not words or words[0] not in COMMANDS:
        return [f"{where}: headwater {' '.join(words)}: no such command"]
    command = load(words[0])
    path = [words[0]]
    for word in words[1:]:
        sub = getattr(command, "commands", {}).get(word)
        if sub is None:
            break
        command = sub
        path.append(word)
    if hasattr(command, "commands"):
        return [f"{where}: headwater {' '.join(words)}: no such command"]
    options = {opt for p in command.params for opt in p.opts}
    return [
        f"{where}: headwater {' '.join(path)} has no option {word}"
        for word in words
        if word.startswith("--") and word not in options
    ]


@pytest.mark.parametrize("path", NOTEBOOKS, ids=[p.name for p in NOTEBOOKS])
def test_names_exist(path: Path) -> None:
    notebook = nbformat.read(path, as_version=4)
    problems: list[str] = []
    for number, cell in enumerate(notebook.cells):
        where = f"{path.name} cell {number}"
        if cell.cell_type == "code":
            problems += code_problems(cell.source, where)
        problems += text_problems(cell.source, where)
    assert not problems, "\n".join(problems)


def test_the_checks_catch_a_wrong_name() -> None:
    assert check("bg", "find", ["location"], "x") == []
    assert check("bg", "search", [], "x") == ["x: bg.search does not exist"]
    assert check("bg", "where", ["limit"], "x") == ["x: bg.where takes no limit="]
    assert check("result", "score", [], "x") == []
    assert text_problems("`result.provenance.cancellation`", "x") == []
    assert text_problems("`result.provenance.cancel`", "x") != []
    assert text_problems("`hw.InvalidDataError`", "x") == []
    assert text_problems("`hw.InvalidDataErorr`", "x") != []
    assert code_problems("bg.where('p', limit=3)", "x") == ["x: bg.where takes no limit="]
    assert command_problems(["release", "import", "p", "--version", "1"], "x") == []
    assert command_problems(["release", "import", "p", "--versoin", "1"], "x") != []
    assert command_problems(["relase"], "x") != []
