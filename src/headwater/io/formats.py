"""Every format headwater reads, one line each, and the one way into each kind of import.

`inventory_from` and `method_from` choose the format (`--format`, or the one format of that kind
that recognises the path), check the options it takes, read, and run the shared stages. The
command line, the conformance suite and the hash tests all go through them. Adding a format is
adding its line here.
"""

from dataclasses import fields
from pathlib import Path

from headwater.io import ecospold1, ecospold2, lcia, olca_jsonld, simapro_csv
from headwater.io.inventory import Inventory, link_inventory
from headwater.io.method import MappedMethod, map_method
from headwater.io.source import Format, InventoryOptions, MethodOptions, SourceKind
from headwater.link import FlowMapping, SupplierMapping
from headwater.model import InvalidDataError, Problem
from headwater.model.errors import with_article
from headwater.release import Release

FORMATS: tuple[Format, ...] = (
    ecospold2.FORMAT,
    ecospold1.FORMAT,
    lcia.FORMAT,
    olca_jsonld.FORMAT,
    simapro_csv.FORMAT,
)

_FLAGS = {"categories": "--category"}
"""Option fields whose command-line flag is not `--<field>`."""


def inventory_from(
    path: Path,
    options: InventoryOptions | None = None,
    *,
    format: str | None = None,
    suppliers: SupplierMapping | None = None,
) -> Inventory:
    """Read the inventory at `path` and run the shared stages on it."""
    chosen = choose(path, SourceKind.inventory, format)
    options = options or InventoryOptions()
    check_options(chosen, SourceKind.inventory, options)
    assert chosen.read_inventory is not None
    return link_inventory(chosen.read_inventory(path, options), suppliers=suppliers)


def method_from(
    path: Path,
    release: Release,
    options: MethodOptions | None = None,
    *,
    format: str | None = None,
    flows: FlowMapping | None = None,
) -> MappedMethod:
    """Read the method at `path` and map it onto `release`'s flow list."""
    chosen = choose(path, SourceKind.method, format)
    options = options or MethodOptions()
    check_options(chosen, SourceKind.method, options)
    assert chosen.read_method is not None
    return map_method(chosen.read_method(path, options), release, flows=flows)


def formats(kind: SourceKind) -> tuple[Format, ...]:
    """The formats that read this kind of source, in the order of `FORMATS`."""
    return tuple(f for f in FORMATS if f.reads(kind))


def find(name: str) -> Format | None:
    return next((f for f in FORMATS if f.name == name), None)


def describe(kind: SourceKind) -> str:
    """The formats of this kind, for `--help`."""
    return "; ".join(f"{f.name}: {f.title}" for f in formats(kind))


def choose(path: Path, kind: SourceKind, name: str | None = None) -> Format:
    """The format called `name`, or else the one format of this kind that recognises `path`.

    Raises `InvalidDataError` for an unknown name, a format that does not read this kind, and a
    path that no format, or several, recognise.
    """
    listed = ", ".join(f.name for f in formats(kind))
    if name is not None:
        chosen = find(name)
        if chosen is None:
            message = f"is not a format. Formats: {listed}"
            raise InvalidDataError([Problem("unknown-format", f"format {name}", message)])
        if not chosen.reads(kind):
            message = f"does not read {with_article(kind.value)}. Formats that do: {listed}"
            raise InvalidDataError([Problem("wrong-format-kind", f"format {name}", message)])
        return chosen
    found = [f for f in formats(kind) if f.detect(path)]
    if len(found) == 1:
        return found[0]
    if not found:
        message = f"is not recognised as {with_article(kind.value)} format. Give --format: {listed}"
        raise InvalidDataError([Problem("unknown-format", str(path), message)])
    names = ", ".join(f.name for f in found)
    raise InvalidDataError(
        [Problem("ambiguous-format", str(path), f"looks like {names}. Give --format.")]
    )


def check_options(
    chosen: Format, kind: SourceKind, options: InventoryOptions | MethodOptions
) -> None:
    """Raises `InvalidDataError` naming, by its flag, every option given that the format's reader
    of this kind does not take."""
    taken = chosen.options(kind)
    problems = [
        Problem(
            "option-not-taken",
            _FLAGS.get(f.name, f"--{f.name}"),
            f"does not apply to format {chosen.name}",
        )
        for f in fields(options)
        if getattr(options, f.name) != f.default and f.name not in taken
    ]
    if problems:
        raise InvalidDataError(problems)
