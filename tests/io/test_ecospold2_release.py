"""The fixture source frozen into a release with T1's writer, and read back."""

from pathlib import Path

import pytest

from headwater.io.ecospold2 import import_ecospold2
from headwater.io.exclude import read_exclude
from headwater.io.inventory import Inventory
from headwater.model import DEFAULT_UNITS, InvalidDataError, Process, Role
from headwater.release import Release, ReleaseInfo


def _without_flow_names(processes: tuple[Process, ...]) -> list[dict[str, object]]:
    """A release does not store the flow name written next to a line: it is a check."""
    return [
        {**p.model_dump(), "exchanges": [e.model_dump(exclude={"flow_name"}) for e in p.exchanges]}
        for p in processes
    ]


def test_the_release_holds_what_was_read(source: Inventory, tmp_path: Path) -> None:
    release = source.freeze(into=tmp_path)

    assert release.info == ReleaseInfo(
        name="ecoinvent-3.10-cutoff",
        source="ecoinvent",
        version="3.10",
        system_model="cutoff",
        licence="ecoinvent EULA",
        flow_list="ecoinvent-3.10",
    )
    assert _without_flow_names(release.processes()) == _without_flow_names(source.processes)
    assert set(release.flows()) == set(source.flows)
    assert set(release.flow_properties()) == set(source.flow_properties)
    assert release.issues() == source.issues
    assert release.methods() == ()
    release.verify()


def test_the_release_stores_the_units_it_uses_and_they_merge_with_the_built_in_ones(
    source: Inventory, tmp_path: Path
) -> None:
    release = source.freeze(into=tmp_path)

    units = release.units()

    assert set(units.names) == {"EUR2005", "MJ", "dimensionless", "kWh", "kg", "m3", "unit"}
    DEFAULT_UNITS.merge(units)


def test_import_writes_into_the_cache_by_default(fx, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HEADWATER_HOME", str(tmp_path / "home"))

    release = import_ecospold2(
        fx.source, version="3.10", exclude=read_exclude(fx.exclude), workers=1
    )

    assert release.path.parent == tmp_path / "home" / "releases"
    assert release.path.name.startswith("ecoinvent-3.10-cutoff@")
    assert Release.open(release.path).hash == release.hash


def test_a_name_can_be_given(fx, tmp_path: Path) -> None:
    release = import_ecospold2(
        fx.source,
        version="3.10",
        name="ei-toy",
        exclude=read_exclude(fx.exclude),
        into=tmp_path,
        workers=1,
    )

    assert release.name == "ei-toy"


def test_counts_by_role(source: Inventory) -> None:
    roles = [e.role for p in source.processes for e in p.exchanges]

    assert {r: roles.count(r) for r in Role if r in roles} == {
        Role.reference: 8,
        Role.input: 7,
        Role.own_use: 1,
        Role.waste_to_treatment: 1,
        Role.recyclable: 1,
        Role.emission: 5,
        Role.resource: 1,
    }


def test_a_name_that_is_not_a_folder_name_is_a_problem(source: Inventory, tmp_path: Path) -> None:
    with pytest.raises(InvalidDataError) as raised:
        source.freeze(name="ei 3.10", into=tmp_path)

    assert [(p.code, p.item) for p in raised.value.problems] == [("bad-name", "release ei 3.10")]


FIXTURE_RELEASE_HASH = "3f68c59b62fc418ef15efa79e18625066e89650a8671efb5a801023cd9bd0a41"
"""The fixture source with its exclude file, frozen. Recorded before the importer moved onto the
shared stages of `headwater.io.inventory`, so a change to a reader or a stage that moves a row
changes it. Change it only with a deliberate change of what the importer reads."""

TOY_RELEASE_HASH = "2605f149739bbba56e1beff4d1c29d5d3224150e05d4cb4849c59b273a7be509"
"""The toy background written as ecoSpold2, `tests/fixtures/formats/ecospold2/toy`, frozen."""


def test_the_fixture_release_hash_is_pinned(fx, tmp_path: Path) -> None:
    release = import_ecospold2(
        fx.source, version="3.10", exclude=read_exclude(fx.exclude), workers=1, into=tmp_path
    )

    assert release.hash == FIXTURE_RELEASE_HASH


def test_the_toy_release_hash_is_pinned(tmp_path: Path) -> None:
    toy = Path(__file__).resolve().parents[1] / "fixtures" / "formats" / "ecospold2" / "toy"

    release = import_ecospold2(toy, version="1.0", workers=1, into=tmp_path)

    assert release.hash == TOY_RELEASE_HASH
