"""What a release offers to link to."""

from pathlib import Path

import pytest

import headwater as hw
from headwater.io.excel import ReleaseIndex
from headwater.link import SupplierKey, Suppliers
from headwater.model import FlowKind, InvalidDataError
from headwater.release import Release


def test_processes_are_found_by_name_reference_product_and_location(mini_release: Release) -> None:
    index = ReleaseIndex(mini_release)
    assert index.process_count == 6
    (steel,) = index.suppliers("market for steel", "steel", "RER")
    assert (steel.id, steel.flow_id, steel.kind, steel.unit) == (
        "mini:steel-rer",
        "mini:steel",
        FlowKind.product,
        "kg",
    )
    assert index.suppliers("market for steel", "steel", "GLO") == ()
    assert index.suppliers("Market for steel", "steel", "RER") == ()  # no case folding
    (treatment,) = index.suppliers("treatment of wastewater", "wastewater", "RoW")
    assert treatment.kind is FlowKind.waste and treatment.unit == "m3"


def test_a_line_that_gives_less_matches_on_what_it_gives(mini_release: Release) -> None:
    index = ReleaseIndex(mini_release)
    assert len(index.suppliers("market for steel", None, None)) == 3
    assert [s.id for s in index.suppliers("market for steel", None, "GLO")] == ["mini:alloy-glo"]
    assert len(index.suppliers("market for steel", "steel", None)) == 2
    assert index.suppliers("market for gold", None, None) == ()


def test_elementary_flows_are_found_by_name_and_compartment(mini_release: Release) -> None:
    index = ReleaseIndex(mini_release)
    (water,) = index.flows("Water", "water/unspecified")
    assert water.id == "mini:water-ground" and water.unit == "m3"
    assert index.flows("Water", "water") == ()
    assert index.flow("mini:co2") is not None and index.flow("mini:steel") is None  # a product


def test_suggestions_start_with_the_same_name_then_the_same_product(mini_release: Release) -> None:
    index = ReleaseIndex(mini_release)
    found = index.suggestions("market for steel", "steel", "GLO")
    assert found[0].startswith("mini:alloy-glo")  # same name and place, the other product
    assert any("mini:steel-rer" in s for s in found)
    assert index.suggestions("zinc smelting", None, None) == ()
    close = index.suggestions("market for steal", None, None)
    assert any("market for steel" in s for s in close)


def test_a_name_that_was_extended_is_suggested(mini_release: Release) -> None:
    index = ReleaseIndex(mini_release)
    assert any(
        "market for sand" in s for s in index.suggestions("market for sand, washed", None, None)
    )


def test_flow_suggestions_name_the_other_compartments(mini_release: Release) -> None:
    index = ReleaseIndex(mini_release)
    assert len(index.flow_suggestions("Water")) == 2
    assert index.flow_suggestions("Radon") == ()


@pytest.mark.parametrize(
    "key",
    [
        ("market for steel", "steel", "RER"),
        ("market for steel", None, None),
        ("market for steel", None, "GLO"),
        ("market for steel", "steel", None),
        ("market for steel", "steel", "GLO"),
        ("market for gold", None, None),
    ],
)
def test_the_suppliers_are_the_ones_that_every_format_links_by_name_with(
    mini_release: Release, key: tuple[str, str | None, str | None]
) -> None:
    shared = Suppliers.of_release(mini_release)
    name, product, location = key
    found = shared.find(SupplierKey(name=name, product=product, location=location))
    assert [s.id for s in ReleaseIndex(mini_release).suppliers(*key)] == [s.id for s in found]


def test_an_indicators_release_has_no_processes_to_link_to(
    mini_release: Release, tmp_path: Path
) -> None:
    scores = hw.load(mini_release.path, method="mini-climate").write_indicators(into=tmp_path)
    with pytest.raises(InvalidDataError) as error:
        ReleaseIndex(scores)
    assert [p.code for p in error.value.problems] == ["indicators-release"]
