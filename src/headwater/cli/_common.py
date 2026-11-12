"""What the commands share: finding the background, picking indicators, the header of a
database's output, checking a database or a study, and printing sensitivities and errors."""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, NoReturn

import typer

import headwater as hw
from headwater.model import (
    Database,
    HeadwaterError,
    InvalidDataError,
    Problem,
    Report,
    did_you_mean,
)
from headwater.query._internals import internals

if TYPE_CHECKING:
    from headwater.model import Method
    from headwater.release import Release

ReleaseOption = Annotated[
    str | None,
    typer.Option(
        "--release",
        help="The background: a release (name@hash, with at least 12 characters of the hash, "
        "or its folder), or a folder of process files. Default: the release the database or "
        "study pins.",
    ),
]
MethodOption = Annotated[
    list[str] | None,
    typer.Option(
        "--method",
        help="The method: the id of one the release holds, or a method folder. Give it once per "
        "method to use several together. Default: the methods the database or study names. A "
        "folder of process files needs a method folder.",
    ),
]
NamespaceOption = Annotated[
    str | None,
    typer.Option(
        "--namespace",
        help="For a folder of process files: the prefix of its process ids, for example toy. "
        "Default: the folder's name.",
    ),
]
SetOption = Annotated[
    list[Path] | None,
    typer.Option(
        "--set",
        help="A normalisation and weighting set folder, pinned to one of the methods: it adds "
        "normalised and weighted scores and a single score as indicators, such as "
        "<set>:single-score. Give it once per set.",
    ),
]
IndicatorOption = Annotated[
    str | None,
    typer.Option("--indicator", help="One indicator of the method, by id. Default: all of them."),
]
"""For `upgrade`, which works on every indicator by default."""
IndicatorsOption = Annotated[
    list[str] | None,
    typer.Option(
        "--indicator",
        help="An indicator of the method, by id. Give it once per indicator; they are printed in "
        "the method's order. Default: all of them.",
    ),
]
"""For `calc` and `diff`, which work on every indicator by default."""
OneIndicatorOption = Annotated[
    str | None,
    typer.Option(
        "--indicator",
        help="One indicator of the method, by id. Needed when the method has several.",
    ),
]
"""For the commands that work on exactly one indicator: `contributions`, `where` and
`sensitivity`."""

MethodFolderOption = Annotated[
    list[Path] | None,
    typer.Option(
        "--method-folder",
        help="A method folder, for a method that the database or study names by its id and that "
        "its release does not hold, as the Excel import writes it when given a folder. Give it "
        "once per folder: a folder whose id it does not name is not used. The folders "
        "HEADWATER_METHOD_FOLDERS lists, separated as in PATH, come after these. --method "
        "replaces the methods it names instead.",
    ),
]

METHOD_FOLDERS_VARIABLE = "HEADWATER_METHOD_FOLDERS"
"""The environment variable that lists method folders, as `--method-folder` would."""

_PIN_VERSION = re.compile(r"[0-9a-f]{12,64}")

FOLDER_LAYOUT = (
    "a folder that holds method.yaml and factors.csv, as `headwater method import` writes"
)
"""What a method folder is, for the messages that ask for one."""


def fail(error: BaseException | str, code: int = 1) -> NoReturn:
    """Print an error and leave with `code`. Nothing goes to standard output."""
    typer.echo(str(error), err=True)
    raise typer.Exit(code=code)


def open_release(text: str, what: str = "release") -> Release:
    """The release `text` names: a release folder, which holds release.json, or `name@hash` of one
    in HEADWATER_HOME. `what` names it in the error: `release`, or `flow list` for a method's.

    Raises `InvalidDataError` for anything else.
    """
    from headwater.release import Release, find

    folder = Path(text)
    if (folder / "release.json").is_file():
        return Release.open(folder)
    if "@" in text and not folder.exists():
        return find(text)
    raise InvalidDataError(
        [
            Problem(
                "release-not-found",
                f"{what} {text}",
                "is neither a release folder, which holds release.json, nor name@hash of a "
                "release in HEADWATER_HOME",
            )
        ]
    )


def load_background(
    record: Database,
    *,
    release: str | None,
    method: list[str] | None,
    namespace: str | None,
    sets: list[Path] | None = None,
    method_folders: Sequence[Path] | None = None,
    kind: str = "database",
    said: set[str] | None = None,
) -> hw.Background:
    """The background for `record`: `release` when given, else the release its pin names, with
    the normalisation and weighting `sets` given. The method is `method` when given, else the
    methods the record names, each from its release or from the one of `method_folders` with its
    id (see `methods_from_folders`). `kind` is what the record is of, `database` or `study`, as
    the errors name it. The folder each method came from is said once on standard error: lines
    already in `said` are not said again, and are added to it.

    Raises `HeadwaterError` with a message that says what to pass on the command line: a method
    the record names that is neither in its release nor in `method_folders` asks for
    --method-folder.
    """
    if release is None:
        pin = record.background
        if not _PIN_VERSION.fullmatch(pin.version):
            raise InvalidDataError(
                [
                    Problem(
                        "background-pin",
                        f"{kind} {record.name} background {pin}",
                        "does not name a release. A release is pinned by its name and at least "
                        f"12 characters of its hash, for example {pin.name}@07ebaa9e28bc. Pass "
                        "--release with a release (name@hash, or its folder) or with a folder "
                        "of process files.",
                    )
                ]
            )
        release = str(pin)
    folder = Path(release)
    of_files = folder.is_dir() and not (folder / "release.json").is_file()
    if of_files and not method:
        raise InvalidDataError(
            [
                Problem(
                    "method-needed",
                    f"background {release}",
                    "is a folder of process files, so --method must be a method folder",
                )
            ]
        )
    if not method:
        given = list(method_folders or [])
        listed = [f for f in method_folders_from_environment() if f not in given]
        method = methods_from_folders(record, release, [*given, *listed])
        _require_held(record, release, method or list(record.methods), kind)
        _say_folders(record, method, listed, set() if said is None else said)
    chosen: list[Path | str] | None = (
        [Path(m) if (Path(m) / "method.yaml").is_file() else m for m in method]
        if method
        else list(record.methods) or None
    )
    try:
        return hw.load(release, method=chosen, namespace=namespace, sets=sets)
    except InvalidDataError as error:
        hinted = with_pull_hint(error)
        if hinted is error:
            raise
        raise hinted from None
    except ValueError as error:  # an argument the API refuses, such as an unknown method id
        raise InvalidDataError(
            [Problem("background", f"background {release}", str(error))]
        ) from None


def with_pull_hint(error: InvalidDataError) -> InvalidDataError:
    """`error`, with how to get the release when the problem is that it is not in the cache.
    Any other error is returned as it is."""
    if not any(p.code == "release-not-found" for p in error.problems):
        return error
    return InvalidDataError(
        [
            *error.problems,
            Problem(
                "release-not-found",
                "hint",
                "pull it with `headwater release pull <folder or URL>`, or pass --release",
            ),
        ]
    )


def method_folders_from_environment() -> list[Path]:
    """The method folders that HEADWATER_METHOD_FOLDERS lists, separated as in PATH: `:` on Linux
    and macOS, `;` on Windows. Empty when it is not set."""
    text = os.environ.get(METHOD_FOLDERS_VARIABLE, "")
    return [Path(part) for part in text.split(os.pathsep) if part.strip()]


def methods_from_folders(
    record: Database, release: str | None, folders: Sequence[Path]
) -> list[str] | None:
    """The methods `record` names, each by its id, or by the folder of `folders` with that id when
    the release does not hold it. Of several folders with one id, those mapped to the release's
    flow list; of those, the first, when they all hold the same method. None when no folder is
    used, so the record's methods load as they are.

    Raises InvalidDataError naming the folders and their hashes when two that fit hold different
    methods.
    """
    if not folders:
        return None
    from headwater.release import method_hash
    from headwater.study import read_method

    held, flow_list = _release_methods(release or str(record.background)) or (frozenset(), None)
    if all(m in held for m in record.methods):
        return None
    by_id: dict[str, list[tuple[Path, Method]]] = {}
    for folder in folders:
        found = read_method(folder)
        by_id.setdefault(found.id, []).append((folder, found))
    chosen: list[str] = []
    for method_id in record.methods:
        fits = [] if method_id in held else by_id.get(method_id, [])
        if len(fits) > 1:
            fits = [f for f in fits if f[1].flow_list == flow_list]
        digests = [method_hash(m) for _, m in fits] if len(fits) > 1 else []
        if len(set(digests)) > 1:
            named = ", ".join(
                f"{folder} (hash {digest[:12]})"
                for (folder, _), digest in zip(fits, digests, strict=True)
            )
            raise InvalidDataError(
                [
                    Problem(
                        "method-folder",
                        f"method {method_id}",
                        f"is in {len(fits)} method folders that hold different methods: {named}. "
                        "Give one of them.",
                    )
                ]
            )
        chosen.append(str(fits[0][0]) if fits else method_id)
    return chosen if chosen != list(record.methods) else None


def _say_folders(
    record: Database, chosen: Sequence[str] | None, listed: Sequence[Path], said: set[str]
) -> None:
    """On standard error, the folder each method of `record` came from, when one did, and whether
    --method-folder or HEADWATER_METHOD_FOLDERS (`listed`) gave it. A line in `said` is not said
    again: across a release change both sides may take the same folder."""
    if chosen is None:
        return
    for method_id, source in zip(record.methods, chosen, strict=True):
        if source == method_id:
            continue
        given = METHOD_FOLDERS_VARIABLE if Path(source) in listed else "--method-folder"
        line = f"Method {method_id}: from the folder {source}, given by {given}."
        if line not in said:
            said.add(line)
            typer.echo(line, err=True)


def _require_held(record: Database, release: str, chosen: Sequence[str], kind: str) -> None:
    """Raises InvalidDataError, asking for --method-folder, when the record names a method by an id
    that its release does not hold and no folder fills. `chosen` is, for each method the record
    names, its id or the folder that fills it. Nothing is checked when the release cannot be read
    here: loading it says what is wrong."""
    found = _release_methods(release)
    if found is None:
        return
    held = found[0]
    named = record.methods
    missing = [m for m, c in zip(named, chosen, strict=True) if c == m and m not in held]
    if not missing:
        return
    raise InvalidDataError(
        [
            Problem(
                "method-not-held",
                f"{kind} {record.name}",
                f"names method {', '.join(missing)}, which release {release} does not hold (it "
                f"holds {', '.join(sorted(held)) or 'none'}). Pass --method-folder with the "
                f"method's folder, or list it in {METHOD_FOLDERS_VARIABLE}: {FOLDER_LAYOUT} it.",
            )
        ]
    )


def _release_methods(release: str) -> tuple[frozenset[str], str | None] | None:
    """The ids of the methods `release` holds, and its flow list, from its release.json: for a
    release folder, or `name@hash` in the cache. None for a folder of process files or a release
    that is not there: loading it says what is wrong."""
    from headwater.release.cache import releases_dir
    from headwater.release.read import load_manifest

    folder = Path(release)
    if not (folder / "release.json").is_file():
        name, _, release_hash = release.partition("@")
        folder = releases_dir() / f"{name}@{release_hash[:12]}"
    if not (folder / "release.json").is_file():
        return None
    try:
        manifest = load_manifest(folder)
    except HeadwaterError:
        return None
    return frozenset(m.id for m in manifest.methods), manifest.info.flow_list


def pick_indicators(
    background: hw.Background, indicator: str | Sequence[str] | None, *, one: bool
) -> list[str]:
    """The indicator ids to work on: those asked for, in the order of `background.indicators`,
    else all, or the only one when `one`. All are the method's, then those of its sets. An
    indicator of a set is never the only one: as in the Python API, it is chosen by name.

    Raises a usage error (exit code 2) for an unknown indicator, and when `one` is wanted from
    a method with several and none is asked for.
    """
    ids = [i.id for i in background.indicators]
    asked = [indicator] if isinstance(indicator, str) else list(indicator or [])
    for wanted in asked:
        if wanted not in ids:
            raise typer.BadParameter(
                f"method {background.method.id} has no indicator {wanted!r}."
                f"{did_you_mean(wanted, ids)} Its indicators: {', '.join(ids)}",
                param_hint="--indicator",
            )
    if asked:
        return [i for i in ids if i in asked]
    own = [i.id for i in background.method.indicators]
    if one and len(own) > 1:
        raise typer.BadParameter(
            f"method {background.method.id} has {len(own)} indicators ({', '.join(own)}). "
            "Name one with --indicator.",
            param_hint="--indicator",
        )
    return own[:1] if one else ids


def background_text(background: hw.Background) -> str:
    """`toy-background@07ebaa9e28bc`, or `folder tests/fixtures/toy/background`."""
    inner = internals(background)
    if inner.releases:
        ref = inner.releases[0]
        return f"{ref.name}@{ref.hash[:12]}"
    return f"folder {inner.folder.path}"


def database_header(
    database: hw.Database, path: Path | str, background: hw.Background
) -> list[str]:
    """The first lines of `calc`, `contributions`, `where` and `sensitivity`: the database, and
    the background with its method."""
    return [
        f"Database {database.name} ({path})",
        f"Background: {background_text(background)}, method {background.method.id} "
        f"{background.method.version}",
    ]


def indicator_line(background: hw.Background, result: hw.Result) -> str:
    """`Indicator: Climate change (kg CO2-eq)`, for the indicator of `result`. A normalised score
    has no unit: `Indicator: Climate change, normalised (toy-nw)`."""
    names = {i.id: i.name for i in background.indicators}
    unit = f" ({result.unit})" if result.unit else ""
    return f"Indicator: {names[result.indicator]}{unit}"


def sensitivity_line(line: hw.Sensitivity) -> str:
    """One line of a sensitivity analysis: the line, what it is, and its effect. Example, on one
    line: `tshirt:tshirt line e02: input 0.25 kg of toy:cotton-fabric from toy:fabric-production,
    elasticity 0.7778, sensitivity 3.7086`."""
    bought = f" from {line.supplier_id}" if line.supplier_id else ""
    return (
        f"  {line.process_id} line {line.line_id}: {line.role.value} "
        f"{line.amount:g} {line.unit} of {line.flow_id}{bought}, "
        f"elasticity {line.elasticity:.4f}, sensitivity {line.sensitivity:.5g}"
    )


def pinned_record(opened: hw.Database | hw.Study) -> Database:
    """What `load_background` reads from a folder, for a database or a study: its name, the
    release it pins and its methods. A study pins the release of its databases, or its own when
    every product is of a release."""
    if isinstance(opened, hw.Database):
        return opened.folder.database
    folder = opened.folder
    pin = folder.background
    if pin is None and folder.databases:
        pin = folder.databases[0].database.background
    return Database.model_validate(
        {
            "name": folder.name or "study",
            "background": str(pin) if pin is not None else "none@none",
            "methods": list(folder.methods),
        }
    )


def check_folder(opened: hw.Database | hw.Study, background: hw.Background) -> Report:
    """Everything `validate` reports for a database or a study on a background: the checks
    `calc` makes, and for a database the pin."""
    if isinstance(opened, hw.Study):
        return Report(tuple(opened.validate(background).problems))
    problems = list(opened.validate(background).problems)
    try:
        internals(background).require_pin(opened.folder.database.background, opened.name)
    except InvalidDataError as error:
        problems.extend(error.problems)
    return Report(tuple(problems))


VALIDATE_FORMAT = "headwater-validate/2"
"""The `format` of `validate --json`. Within version 2 keys may be added, none is renamed or
removed."""


def validation_data(
    path: str, background: hw.Background | None, report: Report, kind: str | None = None
) -> dict[str, Any]:
    """A validation as plain data: `json.dumps` of it is the output of `validate --json`.

    `path` is the folder as given, and `kind` `database` or `study`, or None for a folder that
    did not read. `background` is None for a folder that did not read, and then there is no
    background and no method to name.
    """
    named = None
    if background is not None:
        inner = internals(background)
        release = str(inner.releases[0]) if inner.releases else None
        named = {"release": release, "folder": None if release else str(inner.folder.path)}
    return {
        "format": VALIDATE_FORMAT,
        "path": path,
        "kind": kind,
        "valid": not report.errors,
        "background": named,
        "method": _method_data(background),
        "methods": None if background is None else list(background.method.ids),
        "errors": [_problem_data(p) for p in report.errors],
        "warnings": [_problem_data(p) for p in report.warnings],
    }


def _method_data(background: hw.Background | None) -> dict[str, str] | None:
    """The method's `id` and `version`; None for no background, and for several methods used
    together, whose joined id is not written to a file: `methods` lists their ids."""
    if background is None or len(background.method.ids) > 1:
        return None
    return {"id": background.method.id, "version": background.method.version}


def _problem_data(problem: Problem) -> dict[str, Any]:
    return {
        "code": problem.code,
        "item": problem.item,
        "message": problem.message,
        "location": problem.location,
        "text": str(problem),
    }


def print_validation(
    name: str, background: hw.Background | None, report: Report, kind: str = "Database"
) -> None:
    """What `validate` prints: the background, every error and warning, then one line."""
    errors, warnings = report.errors, report.warnings
    if background is not None:
        typer.echo(
            f"Background: {background_text(background)}, method {background.method.id} "
            f"{background.method.version}"
        )
    for p in errors:
        typer.echo(f"error: {p}")
    for p in warnings:
        typer.echo(f"warning: {p}")
    counts = [count(len(errors), "error")] if errors else ["valid"]
    if warnings:
        counts.append(count(len(warnings), "warning"))
    typer.echo(f"{kind} {name}: {', '.join(counts)}.")


def count(n: int, noun: str) -> str:
    """`1 error`, `2 errors`."""
    return f"{n} {noun}{'' if n == 1 else 's'}"
