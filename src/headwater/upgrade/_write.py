"""Writing moves into the files of databases and studies, cell by cell.

Only the cells that name the old release change: a CSV record that needs no change keeps its
exact text, and a YAML value is replaced where it stands, so comments and layout stay. Example:
`e02,input,toy:cotton-fabric,0.25,kg,toy:1:fabric-a,cotton fabric,` becomes
`e02,input,toy:cotton-fabric,0.25,kg,toy:2:fabric-b,cotton fabric,`, and the rest of the file is
untouched.
"""

import shutil
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import cast

import yaml

from headwater.study import DatabaseFolder, StudyFolder
from headwater.study._write import edit_csv
from headwater.upgrade._moves import Moves

type Cells = dict[str, str]
type Edit = Callable[[str, Moves], str]

_BOM = "﻿"


def write(folders: Sequence[DatabaseFolder], moves: Moves, into: Path | None) -> tuple[Path, ...]:
    """Write `moves` into the files of each folder, in place, or into a copy of each folder in
    `into`. Returns the files that changed."""
    written: list[Path] = []
    for folder in folders:
        root = folder.path
        if into is not None:
            root = into / folder.path.name
            shutil.copytree(folder.path, root)
        written += _edit(list(_files(root)), moves)
    return tuple(written)


def _edit(files: Sequence[tuple[Path, Edit]], moves: Moves) -> list[Path]:
    """Make each edit, keeping a byte order mark where there is one, and return the files that
    changed."""
    written: list[Path] = []
    for path, edit in files:
        raw = path.read_text(encoding="utf-8")
        bom = _BOM if raw.startswith(_BOM) else ""
        text = raw.removeprefix(_BOM)
        new = edit(text, moves)
        if new != text:
            path.write_text(bom + new, encoding="utf-8")
            written.append(path)
    return written


def write_studies(
    studies: Sequence[StudyFolder], moves: Moves, into: Path | None
) -> tuple[Path, ...]:
    """Write `moves` into study.yaml and the scenarios of each study, in place, or into a copy of
    each folder in `into`, next to the databases written there. Returns the files that
    changed."""
    written: list[Path] = []
    for study in studies:
        assert study.path is not None  # an upgrade reads studies from their folders
        root = study.path
        if into is not None:
            root = into / study.path.name
            shutil.copytree(study.path, root)
        files = [(root / "study.yaml", _study_yaml)]
        files += [(path, _scenario_yaml) for path in sorted((root / "scenarios").glob("*.yaml"))]
        written += _edit(files, moves)
    return tuple(written)


def _files(root: Path) -> Iterator[tuple[Path, Edit]]:
    """The files that can name the old release, each with how to edit it."""
    yield root / "database.yaml", _database_yaml
    for path in sorted((root / "processes").glob("*.csv")):
        yield path, _process_csv
    for path in sorted((root / "overrides").glob("*.csv")):
        yield path, _override_csv
    for path in sorted((root / "scenarios").glob("*.yaml")):
        yield path, _scenario_yaml
    for name, edit in (("parameters.yaml", _parameters_yaml), ("no_factor.csv", _no_factor_csv)):
        if (root / name).is_file():
            yield root / name, edit


# --- CSV ---------------------------------------------------------------------------------------


def _process_csv(text: str, moves: Moves) -> str:
    return edit_csv(text, lambda cells: _line(cells, moves))


def _override_csv(text: str, moves: Moves) -> str:
    def edit(cells: Cells) -> Cells:
        process = cells.get("process", "").strip()
        moved = _line(cells, moves)
        if "process" in moved:
            moved["process"] = moves.process(process)
        if "line" in moved:
            moved["line"] = moves.line_id(process, cells["line"].strip())
        return moved

    return edit_csv(text, edit)


def _no_factor_csv(text: str, moves: Moves) -> str:
    def edit(cells: Cells) -> Cells:
        name = cells.get("flow_name", "").strip()
        if not name:
            return cells
        return {**cells, "flow_name": moves.names.get(cells.get("flow", "").strip(), name)}

    return edit_csv(text, edit)


def _line(cells: Cells, moves: Moves) -> Cells:
    """The cells of a line once its supplier, flow and flow name are moved."""
    supplier = cells.get("supplier", "").strip() or None
    name = cells.get("flow_name", "").strip() or None
    new_supplier, flow, new_name = moves.line(supplier, cells.get("flow", "").strip(), name)
    moved = dict(cells)
    for column, old, new in (
        ("supplier", supplier, new_supplier),
        ("flow", cells.get("flow", "").strip(), flow),
        ("flow_name", name, new_name),
    ):
        if column in moved and new != old:
            moved[column] = new or ""
    return moved


# --- YAML --------------------------------------------------------------------------------------


def _database_yaml(text: str, moves: Moves) -> str:
    root = _compose(text)
    node = _value(root, "background")
    if not isinstance(node, yaml.ScalarNode):
        return text
    return _splice(text, [(node, str(moves.pin))])


def _study_yaml(text: str, moves: Moves) -> str:
    """The products of the release a study names, and its pin when it has one."""
    root = _compose(text)
    edits: list[tuple[yaml.Node, str]] = []
    products = _value(root, "products")
    if isinstance(products, yaml.SequenceNode):
        for product in products.value:
            process = _value(product, "process")
            if isinstance(process, yaml.ScalarNode) and process.value in moves.suppliers:
                edits.append((process, moves.process(process.value)))
    pin = _value(root, "background")
    if isinstance(pin, yaml.ScalarNode) and pin.value:
        edits.append((pin, str(moves.pin)))
    return _splice(text, edits)


def _scenario_yaml(text: str, moves: Moves) -> str:
    changes = _value(_compose(text), "changes")
    if not isinstance(changes, yaml.SequenceNode):
        return text
    edits: list[tuple[yaml.Node, str]] = []
    for change in changes.value:
        process, line = _value(change, "process"), _value(change, "line")
        supplier = _value(change, "supplier")
        if isinstance(process, yaml.ScalarNode) and process.value in moves.suppliers:
            edits.append((process, moves.process(process.value)))
            if isinstance(line, yaml.ScalarNode):
                edits.append((line, moves.line_id(process.value, line.value)))
        if isinstance(supplier, yaml.ScalarNode) and supplier.value in moves.suppliers:
            edits.append((supplier, moves.process(supplier.value)))
    return _splice(text, edits)


def _parameters_yaml(text: str, moves: Moves) -> str:
    processes = _value(_compose(text), "processes")
    if not isinstance(processes, yaml.MappingNode):
        return text
    edits: list[tuple[yaml.Node, str]] = [
        (key, moves.process(key.value))
        for key, _ in processes.value
        if isinstance(key, yaml.ScalarNode) and key.value in moves.suppliers
    ]
    return _splice(text, edits)


def _compose(text: str) -> yaml.Node | None:
    """The node tree of a YAML file, which keeps where each value stands in the text."""
    return cast(yaml.Node | None, yaml.compose(text, Loader=yaml.SafeLoader))  # pyright: ignore[reportUnknownMemberType]


def _value(node: yaml.Node | None, key: str) -> yaml.Node | None:
    """The value of `key` in a mapping node, or None."""
    if not isinstance(node, yaml.MappingNode):
        return None
    for k, v in node.value:
        if isinstance(k, yaml.ScalarNode) and k.value == key:
            return v
    return None


def _splice(text: str, edits: list[tuple[yaml.Node, str]]) -> str:
    """`text` with each scalar replaced where it stands, in the quotes it had."""
    for node, value in sorted(edits, key=lambda e: e[0].start_mark.index, reverse=True):
        if isinstance(node, yaml.ScalarNode) and node.value == value:
            continue
        quote = node.style if isinstance(node, yaml.ScalarNode) and node.style in ("'", '"') else ""
        start, end = node.start_mark.index, node.end_mark.index
        text = f"{text[:start]}{quote}{value}{quote}{text[end:]}"
    return text
