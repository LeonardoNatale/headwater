"""JSON schemas of the files a person writes: database.yaml, study.yaml and a study's scenario
files. Editors that read JSON schemas complete keys and values with them, show each field's
description, and mark a wrong key as it is typed.

They are written from the Pydantic models that read the files, so they cannot disagree with
headwater. `validate` stays the check: a schema only suggests. Example: in the toy folder
`tshirt-dyed`, a product of `study.yaml` completes from `tshirt:tshirt` and `dyes:reactive-dye`,
the processes of the databases next to the study, and a process of a release still validates.
"""

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field

from headwater.model import Database, Study
from headwater.model.study import PROCESS_ID

FOLDER = ".schemas"
PIN = r"^[^@\s]+@[^@\s]+$"


class _LineChange(BaseModel):
    """A change to one line of a process: of a database the study reaches, or of the
    background, for everything."""

    model_config = ConfigDict(extra="forbid")

    process: str = Field(description="The id of the process whose line changes.")
    line: str = Field(description="The line id in the process file, such as e03.")
    amount: float | str | None = Field(
        default=None, description="The new amount: a number, or a formula of parameters."
    )
    unit: str | None = Field(default=None, description="The new unit of the line.")
    supplier: str | None = Field(default=None, description="The id of the new supplier.")


class _ParameterChange(BaseModel):
    """A new value for a named parameter of a database the study reaches."""

    model_config = ConfigDict(extra="forbid")

    parameter: str = Field(description="The parameter's name, such as fabric_kg.")
    process: str | None = Field(
        default=None, description="The process, for a parameter of one process."
    )
    database: str | None = Field(
        default=None,
        description="The database that holds the parameter, when the study reaches several "
        "that have one of this name.",
    )
    value: float | str = Field(description="The new value: a number, or a formula.")


class ScenarioFile(BaseModel):
    """`scenarios/<name>.yaml` of a study: named changes to lines and parameters."""

    model_config = ConfigDict(extra="forbid")

    description: str | None = Field(default=None, description="What the scenario asks.")
    changes: list[_LineChange | _ParameterChange] = Field(min_length=1)


def database_schema() -> dict[str, Any]:
    """The JSON schema of database.yaml."""
    schema = Database.model_json_schema()
    _pin(schema)
    return _titled(schema, "database.yaml", "A database: a folder of processes in git (D46).")


def study_schema(process_ids: Sequence[str]) -> dict[str, Any]:
    """The JSON schema of study.yaml, with `process_ids` as the suggested products."""
    schema = Study.model_json_schema()
    _pin(schema)
    schema["$defs"]["Product"]["properties"]["process"] = _suggested(
        schema["$defs"]["Product"]["properties"]["process"], process_ids
    )
    return _titled(schema, "study.yaml", "A study: products with amounts and a question (D46).")


def scenario_schema(process_ids: Sequence[str]) -> dict[str, Any]:
    """The JSON schema of a study's scenario file, with `process_ids` as suggested processes."""
    schema = ScenarioFile.model_json_schema()
    for name in ("_LineChange", "_ParameterChange"):
        properties = schema["$defs"][name]["properties"]
        properties["process"] = _suggested(properties["process"], process_ids)
    return _titled(schema, "scenario", "A scenario of a study: changes to lines and parameters.")


def process_ids_in(folder: Path) -> list[str]:
    """The ids of the processes of every database folder in `folder`, by the name its
    database.yaml gives and the names of its process files: `tshirt:tshirt`. A database.yaml that
    does not read gives none."""
    found: list[str] = []
    for entry in sorted(folder.iterdir()):
        path = entry / "database.yaml"
        if not path.is_file():
            continue
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError, UnicodeDecodeError):
            continue
        name = cast(dict[str, Any], data).get("name") if isinstance(data, dict) else None
        if not isinstance(name, str):
            continue
        processes = entry / "processes"
        if processes.is_dir():
            found += [f"{name}:{p.stem}" for p in sorted(processes.glob("*.csv"))]
    return found


def write_schemas(folder: Path | str) -> tuple[Path, ...]:
    """Write the three schemas into `<folder>/.schemas`, with the process ids of the databases
    in `folder` as suggestions. Returns the files written."""
    root = Path(folder)
    ids = process_ids_in(root)
    out = root / FOLDER
    out.mkdir(exist_ok=True)
    written: list[Path] = []
    for kind, schema in (
        ("database", database_schema()),
        ("study", study_schema(ids)),
        ("scenario", scenario_schema(ids)),
    ):
        path = out / f"{kind}.schema.json"
        path.write_text(json.dumps(schema, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        written.append(path)
    return tuple(written)


def _pin(schema: dict[str, Any]) -> None:
    """A pin is written `name@version`: the record reads it into a `Pin`, the file holds text."""
    pin = {
        "type": "string",
        "pattern": PIN,
        "description": "A release, by its name and at least 12 characters of its hash: "
        "toy-background@6864b7dbb65d.",
    }
    background = schema["properties"]["background"]
    schema["properties"]["background"] = (
        {"anyOf": [pin, {"type": "null"}], "default": None} if "anyOf" in background else pin
    )
    schema["$defs"].pop("Pin", None)


def _suggested(field: dict[str, Any], process_ids: Sequence[str]) -> dict[str, Any]:
    """`field` with `process_ids` as suggestions: an editor completes them, and any process id
    still validates."""
    plain = {"type": "string", "pattern": PROCESS_ID}
    options: list[dict[str, Any]] = [plain]
    if process_ids:
        options.insert(0, {"enum": list(dict.fromkeys(process_ids))})
    suggested: dict[str, Any] = {"anyOf": options}
    if "description" in field:
        suggested["description"] = field["description"]
    if "default" in field:
        suggested["anyOf"].append({"type": "null"})
        suggested["default"] = None
    return suggested


def _titled(schema: dict[str, Any], title: str, description: str) -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        **schema,
        "title": title,
        "description": description,
    }
