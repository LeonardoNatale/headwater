"""The JSON schemas of the `--json` formats, as the tests read them (AGENTS.md rule 11).

Example: `headwater calc ... --json` prints `{"format": "headwater-calc/3", ...}`, and
`check(document)` validates it against src/headwater/cli/schemas/headwater-calc-3.schema.json.
"""

import hashlib
import json
from functools import cache
from importlib.resources import files
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

SCHEMAS = Path(str(files("headwater.cli") / "schemas"))
"""The folder of the installed package that holds one schema per format."""

IN_THE_REPOSITORY = "src/headwater/cli/schemas"
"""The same folder, as error messages name it."""

ANNOTATIONS = frozenset({"description", "$comment", "examples"})
"""What a released schema may still change: words for people, not what it accepts."""

MAPS = frozenset({"properties", "patternProperties", "$defs", "dependentSchemas"})
"""Keywords whose keys are names, not keywords: a property may be called `description`."""


def file_name(format: str) -> str:
    """The file of a format's schema: `headwater-calc/3` gives `headwater-calc-3.schema.json`."""
    return format.replace("/", "-") + ".schema.json"


@cache
def schemas() -> dict[str, dict[str, Any]]:
    """Every schema of the package, by file name."""
    return {p.name: json.loads(p.read_text(encoding="utf-8")) for p in SCHEMAS.glob("*.json")}


@cache
def registry() -> Registry[Any]:
    """The schemas by file name, so that `{"$ref": "headwater-validate-2.schema.json"}` reads
    the validate schema."""
    return Registry().with_resources(
        (name, Resource.from_contents(schema)) for name, schema in schemas().items()
    )


def problems(document: Any) -> list[str]:
    """Where `document` does not match the schema of its `format`, one line each."""
    if not isinstance(document, dict) or not isinstance(document.get("format"), str):
        return ["the document has no `format`"]
    name = file_name(document["format"])
    if name not in schemas():
        return [f"no schema for {document['format']}: add {IN_THE_REPOSITORY}/{name}"]
    validator = Draft202012Validator(schemas()[name], registry=registry())
    return [
        f"{document['format']} at /{'/'.join(map(str, e.absolute_path))}: {e.message}"
        for e in validator.iter_errors(document)
    ]


def check(document: Any) -> None:
    """Fail naming each place `document` does not match its schema."""
    found = problems(document)
    assert not found, "the output does not match its schema:\n" + "\n".join(found[:20])


def released_hash(schema: Any) -> str:
    """The sha256 of what a schema accepts: its canonical JSON without descriptions."""

    def strip(node: Any) -> Any:
        if isinstance(node, list):
            return [strip(item) for item in node]
        if not isinstance(node, dict):
            return node
        return {
            key: {k: strip(v) for k, v in value.items()} if key in MAPS else strip(value)
            for key, value in node.items()
            if key not in ANNOTATIONS
        }

    text = json.dumps(strip(schema), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
