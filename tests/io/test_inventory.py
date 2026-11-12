"""The stages in order, on a source with every kind of line: by id, by name, and a gin to split."""

from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

import headwater as hw
from headwater.io.inventory import link_inventory
from headwater.io.source import flow_id, product_flow_id
from headwater.model import InvalidDataError
from headwater.query._internals import internals


def test_the_gin_links_freezes_and_scores_by_hand(gin, tmp_path: Path) -> None:
    inventory = link_inventory(gin.source())
    release = inventory.freeze(into=tmp_path / "releases")
    method = tmp_path / "climate"
    method.mkdir()
    (method / "method.yaml").write_text(
        'id: climate\nversion: "1"\nflow_list: gin\n'
        "indicators: [{id: climate, name: Climate, unit: kg CO2-eq}]\n"
    )
    (method / "factors.csv").write_text("indicator,flow,location,factor\nclimate,co2,,1\n")

    background = hw.load(release.path, method=method)
    x = internals(background).solved.x["climate"].values
    score = {p: x[i] for p, i in internals(background).index.items()}

    # gin:p1 makes 0.35 kg fibre from 0.9 kg seed cotton (0.9), 0.18 kWh (0.09) and 0.09 kg CO2.
    assert score["gin:p1"] == pytest.approx(1.08 / 0.35, rel=1e-12)
    assert score["gin:p2"] == pytest.approx(0.12 / 0.6, rel=1e-12)
    assert score["shirt"] == pytest.approx(0.2 * 1.08 / 0.35, rel=1e-12)
    assert np.isclose(0.35 * score["gin:p1"] + 0.6 * score["gin:p2"], 1.2, rtol=1e-12)


def test_the_line_named_by_name_links_to_the_one_process_making_it(gin) -> None:
    inventory = link_inventory(gin.source())

    rows = {
        (r["process_id"], r["line_id"]): (r["supplier_id"], r["link_status"])
        for r in inventory.exchange_table.to_pylist()
    }
    assert rows["gin:p1", "e04"] == ("grid", "linked")
    assert rows["shirt", "e02"] == ("gin:p1", "linked")
    assert rows["gin:p1", "e05"] == (None, None)
    assert inventory.problems == ()


def test_a_line_that_names_its_supplier_two_ways_is_refused(gin) -> None:
    lines = gin.lines()
    lines[6]["supplier_product"] = "seed cotton"

    with pytest.raises(InvalidDataError) as raised:
        link_inventory(gin.source(lines))

    assert [(p.code, p.item) for p in raised.value.problems] == [
        ("two-supplier-keys", "gin line e03")
    ]


def test_a_supplier_on_a_line_that_buys_nothing_is_refused(gin) -> None:
    lines = gin.lines()
    lines[1]["supplier_product"] = "electricity"

    with pytest.raises(InvalidDataError) as raised:
        link_inventory(gin.source(lines))

    assert [p.code for p in raised.value.problems] == ["supplier-on-role"]


def test_a_unit_that_does_not_convert_is_refused_before_linking(gin) -> None:
    lines = gin.lines()
    lines[7]["unit"] = "kg"  # electricity in kg

    with pytest.raises(InvalidDataError) as raised:
        link_inventory(gin.source(lines))

    assert "gin line e04" in str(raised.value)


def test_an_unsplit_gin_is_a_problem_and_freezing_is_refused(gin, tmp_path: Path) -> None:
    inventory = link_inventory(gin.source(factors=()))

    assert [p.code for p in inventory.problems] == ["not-allocated"]
    with pytest.raises(InvalidDataError):
        inventory.freeze(into=tmp_path)


def test_a_name_that_is_not_a_folder_name_is_refused(gin, tmp_path: Path) -> None:
    with pytest.raises(InvalidDataError) as raised:
        link_inventory(gin.source()).freeze(name="gin release", into=tmp_path)

    assert [p.code for p in raised.value.problems] == ["bad-name"]


def test_a_process_with_no_reference_line_is_refused_naming_it(gin) -> None:
    lines = [r for r in gin.lines() if r["process_id"] != "grid" or r["role"] != "reference"]

    with pytest.raises(InvalidDataError) as raised:
        link_inventory(gin.source(lines))

    assert [(p.code, p.item) for p in raised.value.problems] == [
        ("no-reference-line", "process grid")
    ]


def test_a_bad_amount_on_a_line_in_another_unit_is_left_to_the_freeze(gin, tmp_path: Path) -> None:
    lines = gin.lines()
    lines[6]["unit"], lines[6]["amount"] = "g", float("nan")

    inventory = link_inventory(gin.source(lines))
    with pytest.raises(InvalidDataError) as raised:
        inventory.freeze(into=tmp_path)

    assert "gin:p1 line e03" in str(raised.value)


def test_a_flow_defined_twice_or_not_defined_is_refused(gin) -> None:
    source = gin.source()
    lines = gin.lines()
    lines[1]["flow_id"] = "co3"
    twice = replace(source, flows=(*source.flows, source.flows[0]))

    with pytest.raises(InvalidDataError) as raised:
        link_inventory(replace(twice, lines=gin.source(lines).lines))

    assert [(p.code, p.item) for p in raised.value.problems] == [
        ("duplicate-flow", "flow seed-cotton"),
        ("unknown-flow", "farm line e02"),
    ]


def test_flow_ids_from_name_and_compartment_are_stable_and_distinct() -> None:
    first = flow_id("agribalyse-3.2", "Carbon dioxide, fossil", "air/high population density")

    assert first == flow_id(
        "agribalyse-3.2", "Carbon dioxide, fossil", "air/high population density"
    )
    assert first.startswith("agribalyse-3.2:flow:")
    assert first != flow_id(
        "agribalyse-3.2", "Carbon dioxide, fossil", "air/low population density"
    )
    assert flow_id("x", "a|b", "c") != flow_id("x", "a", "b|c")


def test_product_ids_from_name_and_quantity_never_meet_elementary_ids() -> None:
    mass = product_flow_id("bafu-2026", "natural gas", "mass")

    assert mass == product_flow_id("bafu-2026", "natural gas", "mass")
    assert mass != product_flow_id("bafu-2026", "natural gas", "volume")
    assert mass != flow_id("bafu-2026", "natural gas", "mass")
