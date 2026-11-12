"""Write a conversion as a study folder: `database.yaml`, a YAML and a CSV for each process,
`flows.csv`, and `parameters.yaml` when the workbook had parameters.

The files are written into a hidden folder beside the target, then moved into place, so a failed
write leaves no half-written study. The same workbook always gives the same files: nothing in
them depends on the day.

**Importing again into the same folder.** The first import records the hash of each file it wrote
in the hidden `.headwater-import.json`, with how each line linked by name only was linked, so the
comment of a line is the workbook's alone. A second import replaces those files, and only those,
when none of them was edited since. Example: import, read the report, add rows to the mapping file,
import again: the lines the mapping decides are filled in. If a person edited one of the files, the
import refuses and names it, and writes nothing: their edit is never overwritten. Files a person
added (`cut_offs.csv`, `overrides/`, notes) are never touched. A folder that holds files and no
record of an import is refused.

`database.yaml` is the exception: a person is meant to edit it. It is merged, not replaced, and
not hashed: see `database_yaml`.
"""

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from headwater.io.excel.convert import Conversion, DatabaseDraft
from headwater.io.excel.database_yaml import DATABASE, Merged, merge
from headwater.model import InvalidDataError, Problem
from headwater.release.cache import temporary_folder
from headwater.release.read import file_sha256
from headwater.study._files import read_yaml
from headwater.study._write import flows_csv, parameters_yaml, process_csv, process_yaml

MANIFEST = ".headwater-import.json"
FORMAT = "headwater-excel-import/1"


@dataclass(frozen=True)
class Earlier:
    """What an earlier import left in a folder."""

    files: frozenset[str] = frozenset()
    """The files it wrote, as paths in the folder: the ones a new import replaces."""
    study_text: str | None = None
    """database.yaml as it is now, or None when there is none."""
    study_values: dict[str, Any] | None = None
    """database.yaml as read."""
    study_written: dict[str, Any] = field(default_factory=dict[str, Any])
    """What the import wrote for each field of database.yaml, from its record. Empty for a record
    from before it kept them."""

    @property
    def replaced(self) -> bool:
        """An earlier import left files here, which this one replaces."""
        return bool(self.files)

    def merged(self, draft: DatabaseDraft, workbook: str) -> Merged:
        """database.yaml for `draft`, merged with the one an earlier import left, if any."""
        return merge(
            draft,
            workbook,
            text=self.study_text,
            values=self.study_values,
            written=self.study_written,
        )


def check_target(folder: Path | str) -> Earlier:
    """What an import may write into `folder`: the files of an earlier import, as paths in the
    folder, and its database.yaml, or nothing for a folder that does not exist or is empty.

    Raises `InvalidDataError` for a folder that holds files and no record of an import, for one
    where a file of the earlier import was edited since, and for a database.yaml that is not YAML.
    """
    target = Path(folder)
    if not target.exists() or (target.is_dir() and not any(target.iterdir())):
        return Earlier()
    record = _record(target)
    if record is None:
        raise InvalidDataError(
            [
                Problem(
                    "folder-not-empty",
                    f"folder {target}",
                    "already holds files that no import wrote. An import never overwrites them: "
                    "choose another folder, or remove this one first.",
                )
            ]
        )
    written, study = record
    edited = sorted(
        name
        for name, digest in written.items()
        if name != DATABASE and (target / name).is_file() and file_sha256(target / name) != digest
    )
    if edited:
        raise InvalidDataError(
            Problem(
                "folder-edited",
                f"file {name}",
                "was edited after the earlier import wrote it, so a new import would overwrite "
                "the edit. Keep the edit in the workbook or the mapping file, or import into "
                "another folder.",
                location=name,
            )
            for name in edited
        )
    text, values = _study_yaml(target)
    return Earlier(frozenset(written), text, values, study)


def _study_yaml(folder: Path) -> tuple[str | None, dict[str, Any] | None]:
    """database.yaml as text and as read, or None for both when there is none. A person edited it,
    so it may not be YAML: that stops the import before it writes anything."""
    path = folder / DATABASE
    if not path.is_file():
        return None, None
    problems: list[Problem] = []
    values = read_yaml(path, folder, problems)
    if values is None:
        raise InvalidDataError(problems)
    return path.read_bytes().decode("utf-8"), values


def _record(folder: Path) -> tuple[dict[str, str], dict[str, Any]] | None:
    """The hash of each file an import wrote, and what it wrote for each field of database.yaml, or
    None when the folder has no record that can be read."""
    path = folder / MANIFEST
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data["format"] != FORMAT:
            return None
        study = data.get("study", {})
        if not isinstance(study, dict):
            return None
        return {str(k): str(v) for k, v in data["files"].items()}, dict(study)  # pyright: ignore[reportUnknownArgumentType, reportUnknownVariableType]
    except (OSError, ValueError, KeyError, AttributeError):
        return None


def write_database(conversion: Conversion, folder: Path | str, *, workbook: str = "") -> Path:
    """Write `conversion` into `folder`, which must not exist, or be empty, or hold the unedited
    files of an earlier import (see the top of this module). Returns it.

    database.yaml is merged with the file as it is now, so an edit made while the import ran is
    kept.
    """
    previous = check_target(folder)
    merged = previous.merged(conversion.database, workbook)
    target = Path(folder)
    temp = temporary_folder(target.parent, target.name)
    (temp / "processes").mkdir()
    try:
        (temp / DATABASE).write_bytes(merged.text.encode("utf-8"))
        stems = {p.id: p.id.split(":", 1)[1] for p in conversion.processes}
        for process in conversion.processes:
            stem = stems[process.id]
            comment = conversion.comments.get(process.id, ())
            (temp / "processes" / f"{stem}.yaml").write_text(
                process_yaml(process, comment), encoding="utf-8"
            )
            (temp / "processes" / f"{stem}.csv").write_text(
                process_csv(process.exchanges), encoding="utf-8"
            )
        (temp / "flows.csv").write_text(flows_csv(conversion.flows), encoding="utf-8")
        if conversion.parameters:
            (temp / "parameters.yaml").write_text(
                parameters_yaml(conversion.parameters), encoding="utf-8"
            )
        files = sorted(p.relative_to(temp).as_posix() for p in temp.rglob("*") if p.is_file())
        record = {
            "format": FORMAT,
            "files": {name: file_sha256(temp / name) for name in files if name != DATABASE},
            "study": merged.written,
            "notes": _notes(conversion),
        }
        (temp / MANIFEST).write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8")
        if previous.replaced:
            _replace(target, temp, previous.files | {DATABASE}, [*files, MANIFEST])
        else:
            if target.exists():
                target.rmdir()
            temp.rename(target)
    except BaseException:
        shutil.rmtree(temp, ignore_errors=True)
        raise
    return target


def _notes(conversion: Conversion) -> list[dict[str, str | None]]:
    """How each line linked by name only was linked: its process, line, supplier, and what the
    workbook left out. The record keeps them so a line's comment stays the workbook's."""
    suppliers = {
        (p.id, e.line_id): e.supplier_id for p in conversion.processes for e in p.exchanges
    }
    notes: list[dict[str, str | None]] = []
    for outcome in conversion.report.linked_by_name:
        assert outcome.by_name is not None and outcome.process_id is not None
        assert outcome.line_id is not None  # a line linked by name was written
        supplier = suppliers.get((outcome.process_id, outcome.line_id))
        note = outcome.by_name.removeprefix(f"{supplier}: ")
        notes.append(
            {
                "process": outcome.process_id,
                "line": outcome.line_id,
                "supplier": supplier,
                "note": note,
            }
        )
    return notes


def _replace(target: Path, temp: Path, previous: frozenset[str], files: list[str]) -> None:
    """Swap the files of an earlier import for the new ones. Nothing else in `target` moves."""
    for name in sorted(previous):
        (target / name).unlink(missing_ok=True)
    for name in files:
        (target / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.move(temp / name, target / name)
    shutil.rmtree(temp)
    processes = target / "processes"
    if processes.is_dir() and not any(processes.iterdir()):
        processes.rmdir()
