"""The schemas of the `--json` formats are valid, named by their format, and never change once
released (AGENTS.md rule 11).

tests/cli/schemas.sha256 lists the hash of each released schema. A released schema may change its
descriptions, not what it accepts: a new shape is a new format version, `headwater-calc/4`, in a
new file next to the old one. That the commands print what their schemas say is checked in
tests/cli/test_examples_and_json.py, and for every `--json` run of the tests in tests/conftest.py.
"""

from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from tests.cli.schemas import IN_THE_REPOSITORY, file_name, released_hash, schemas

HASHES = Path(__file__).with_name("schemas.sha256")


def released() -> dict[str, str]:
    """The hash of each released schema, by file name."""
    found: dict[str, str] = {}
    for line in HASHES.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#"):
            digest, name = line.split()
            found[name] = digest
    return found


def test_there_is_a_schema_for_each_format() -> None:
    assert len(schemas()) >= 21


@pytest.mark.parametrize("name", sorted(schemas()))
def test_each_schema_is_a_json_schema_named_by_its_format(name: str) -> None:
    schema = schemas()[name]

    Draft202012Validator.check_schema(schema)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["properties"]["format"] == {"const": schema["title"]}
    assert file_name(schema["title"]) == name
    assert "format" in schema["required"]
    assert schema.get("additionalProperties", True) is True, "keys may be added within a version"


@pytest.mark.parametrize("name", sorted(schemas()))
def test_a_released_schema_never_changes(name: str) -> None:
    digest = released_hash(schemas()[name])
    hashes = released()
    base, _, version = name.removesuffix(".schema.json").rpartition("-")

    assert name in hashes, (
        f"{name} is new: add the line `{digest}  {name}` to {HASHES.name} once its format is final"
    )
    assert hashes[name] == digest, (
        f"{IN_THE_REPOSITORY}/{name} changed what it accepts, and a released schema never changes. "
        f"Keep it as it was, and give the new shape a new format version in a new file, "
        f"{base}-{int(version) + 1}.schema.json, with the format {base}/{int(version) + 1}."
    )


def test_every_released_schema_is_still_in_the_package() -> None:
    missing = sorted(set(released()) - set(schemas()))

    assert not missing, f"released schemas removed: {missing}"
