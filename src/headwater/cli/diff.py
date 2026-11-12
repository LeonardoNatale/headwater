"""`headwater diff`: what changed in the results of a database or a study between two git refs,
and why."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, NoReturn

import typer

import headwater as hw
from headwater.cli._common import (
    IndicatorsOption,
    MethodFolderOption,
    MethodOption,
    NamespaceOption,
    ReleaseOption,
    SetOption,
    check_folder,
    fail,
    load_background,
    pick_indicators,
    pinned_record,
    print_validation,
    validation_data,
)
from headwater.diff import (
    FORMAT,
    DiffError,
    FolderAt,
    Source,
    StudyNotAtRef,
    diff_databases,
    diff_study,
    read_at,
    render_json,
    render_text,
    require_same_pin,
)
from headwater.diff._render import DEFAULT_TOP
from headwater.model import Database, HeadwaterError, InvalidDataError, Pin, Report
from headwater.study import DatabaseFolder, StudyFolder

_USAGE = "REF [REF2] FOLDER"

OldMethodOption = Annotated[
    list[str] | None,
    typer.Option(
        "--old-method",
        help="Across a release change, the method of the old side, as --method is for the new "
        "side: the id of one its release holds, or a method folder mapped to its flow list. "
        "Default: the methods the old side names.",
    ),
]


ProductOption = Annotated[
    list[str] | None,
    typer.Option(
        "--product",
        help="A product of the database to score, by the id of the process that makes it: the "
        "id of `products` in --json. Give it once per product. Default: every product of the "
        "database, and none of a database that is new.",
    ),
]


def diff_command(
    args: Annotated[
        list[str],
        typer.Argument(
            metavar=_USAGE,
            help="REF is a branch, tag or commit; REF2 defaults to the working tree; FOLDER is a "
            "database or a study folder in the repository.",
        ),
    ],
    release: ReleaseOption = None,
    method: MethodOption = None,
    old_method: OldMethodOption = None,
    namespace: NamespaceOption = None,
    indicator: IndicatorsOption = None,
    top: Annotated[
        int,
        typer.Option("--top", min=1, help="Lines to list for each indicator, the largest first."),
    ] = DEFAULT_TOP,
    as_json: Annotated[
        bool, typer.Option("--json", help="Print JSON, for CI, instead of text.")
    ] = False,
    nw_set: SetOption = None,
    method_folder: MethodFolderOption = None,
    product: ProductOption = None,
) -> None:
    """Compare a database or a study at two git refs: each score before and after, and why it
    changed.

    The folder is read from git at each ref, so your checkout is not touched.

    Both sides use one background: the release the folder pins, unless --release says another.
    When the two sides pin different releases, an upgrade, each is scored on its own, and each
    score says what the release changed, line by line, apart from what the folder changed.
    Every product of a database is scored, so a change to any of them shows; --product limits
    them. A study scores each of its products, its amount of it. The folder at REF2 is validated
    first, as `headwater validate` does, and not calculated when it has errors. A folder that is
    not at REF is new: a new study is calculated alone, and a new database has its products
    counted, not scored. A folder that names a method its release does not hold is scored with
    the --method-folder of that id.

    Exit code 0 whether or not anything differs, 1 when the folder has validation errors, and 2
    when the diff cannot be made.
    """
    if not 2 <= len(args) <= 3:
        raise typer.BadParameter(f"write it as {_USAGE}, got {len(args)} arguments")
    *refs, path = args
    old_ref, new_ref = refs[0], (refs[1] if len(refs) > 1 else None)
    try:
        new = read_at(Path(path), new_ref)
    except InvalidDataError as error:
        _invalid(path, path, None, Report(error.problems), as_json, None)
    except HeadwaterError as error:
        fail(error, code=2)
    if product and new.kind == "study":
        raise typer.BadParameter(
            "--product is for a database: a study scores each of its products, its amount of it.",
            param_hint="--product",
        )
    opened = as_api(new.folder)
    try:
        old, base = _read_base(path, old_ref)
        if old is not None and old.kind != new.kind:
            raise DiffError(
                f"{path} is a {old.kind} at {base.label} and a {new.kind} at "
                f"{new.source.label}: a diff compares two readings of one kind of folder."
            )
        labels = (base.label, new.source.label)
        old_background: hw.Background | None = None
        new_record = pinned_record(opened)
        old_record = None if old is None else pinned_record(as_api(old.folder))
        if old_record is not None and release is None and _moved(old_record.background, new_record):
            if nw_set:
                raise DiffError(
                    "--set is not supported across a release change: a set pins one method by "
                    "its hash, and each release has its own. Diff without --set, or pass "
                    "--release to put both sides on one background."
                )
            said: set[str] = set()  # both sides may take a method from the same folder
            old_background = load_background(
                old_record,
                release=None,
                method=old_method,
                namespace=namespace,
                method_folders=method_folder,
                kind=new.kind,
                said=said,
            )
            background = load_background(
                new_record,
                release=None,
                method=method,
                namespace=namespace,
                method_folders=method_folder,
                kind=new.kind,
                said=said,
            )
        else:
            background = load_background(
                old_record or new_record,
                release=release,
                method=method,
                namespace=namespace,
                sets=nw_set,
                method_folders=method_folder,
                kind=new.kind,
            )
            if old is not None and isinstance(old.folder, DatabaseFolder):
                assert isinstance(new.folder, DatabaseFolder)
                require_same_pin(old.folder, new.folder, background, labels)
        report = check_folder(opened, background)
        if report.errors:
            _invalid(path, new_record.name, background, report, as_json, new.kind)
        indicators = pick_indicators(background, indicator, one=False)
        before = None if old is None else old.folder
        if isinstance(new.folder, StudyFolder):
            assert before is None or isinstance(before, StudyFolder)
            result = diff_study(
                before,
                new.folder,
                background,
                old_background=old_background,
                indicators=indicators,
                labels=labels,
            )
        else:
            assert before is None or isinstance(before, DatabaseFolder)
            result = diff_databases(
                before,
                new.folder,
                background,
                old_background=old_background,
                indicators=indicators,
                products=product or (None if old is None else "all"),
                labels=labels,
            )
    except HeadwaterError as error:
        fail(error, code=2)
    if as_json:
        validation = validation_data(path, background, report, new.kind)
        data = render_json(result, path=path, old=base, new=new.source, validation=validation)
        typer.echo(json.dumps(data, indent=2, allow_nan=False, ensure_ascii=False))
    else:
        typer.echo(render_text(result, path=path, old=base, new=new.source, top=top))


def as_api(folder: DatabaseFolder | StudyFolder) -> hw.Database | hw.Study:
    """The folder as the Python API holds it."""
    if isinstance(folder, StudyFolder):
        return hw.Study._of(folder)  # pyright: ignore[reportPrivateUsage]
    return hw.Database(folder)


def read_folder_at(path: str, ref: str | None) -> FolderAt:
    """The folder at git `ref`, or on disk when `ref` is None. A folder that does not read says
    at which ref."""
    try:
        return read_at(Path(path), ref)
    except DiffError:
        raise
    except HeadwaterError as error:
        where = ref if ref is not None else "the working tree"
        raise DiffError(f"{path} cannot be read at {where}:\n{error}") from error


def _moved(pin: Pin, new: Database) -> bool:
    """True when the old pin names another release than the new one, however each is written:
    `toy-1@b24ebd7d98cc` and `toy-1@b24ebd7d98cc4e...` are one release."""
    other = new.background
    if pin.name != other.name:
        return True
    return not (pin.version.startswith(other.version) or other.version.startswith(pin.version))


def _read_base(path: str, ref: str) -> tuple[FolderAt | None, Source]:
    """The folder at `ref`, or None when it is not there, with the commit that was looked in."""
    try:
        old = read_folder_at(path, ref)
    except StudyNotAtRef as missing:
        return None, missing.source
    return old, old.source


def _invalid(
    path: str,
    name: str,
    background: hw.Background | None,
    report: Report,
    as_json: bool,
    kind: str | None,
) -> NoReturn:
    """Say what is wrong with the folder, as `validate` does, and exit with its code, 1."""
    if as_json:
        data = {
            "format": FORMAT,
            "path": path,
            "kind": kind,
            "validation": validation_data(path, background, report, kind),
        }
        typer.echo(json.dumps(data, indent=2, allow_nan=False, ensure_ascii=False))
    else:
        print_validation(name, background, report, (kind or "database").capitalize())
    raise typer.Exit(code=1)
