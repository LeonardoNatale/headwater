"""An ecoSpold1 source freezes into a release: every role the reader writes passes the checks."""

import pytest

from headwater.model import FlowKind, Role


def _source(es1):
    grid = es1.dataset(
        "grid",
        es1.exchange(1, "electricity", "kWh", 1, "out0", location="CH"),
        es1.exchange(2, "electricity", "kWh", 0.02, "in2", location="CH"),
        es1.exchange(
            3,
            "Carbon dioxide",
            "kg",
            0.4,
            "out4",
            category="air",
            uncertaintyType=1,
            standardDeviation95=1.5,
            generalComment="(1,2,3,4,5,na)",
        ),
        number=1,
        unit="kWh",
    )
    incineration = es1.dataset(
        "incineration",
        es1.exchange(1, "textile waste", "kg", -1, "out0", location="CH"),
        es1.exchange(2, "Carbon dioxide", "kg", 1.2, "out4", category="air"),
        number=2,
    )
    widget = es1.dataset(
        "widget",
        es1.exchange(1, "widget", "kg", 1, "out0", location="CH"),
        es1.exchange(2, "electricity", "kWh", 3, "in5", location="CH"),
        es1.exchange(3, "textile waste", "kg", -0.05, "in5", location="CH"),
        es1.exchange(4, "scrap steel", "kg", -0.2, "in5", location="CH"),
        es1.exchange(5, "Water", "m3", 2, "in4", category="resource"),
        number=3,
    )
    return es1.make(grid, incineration, widget, es1.maker("scrap steel", number=4))


def test_every_role_freezes_and_reads_back(es1) -> None:
    inventory = es1.load(_source(es1))

    release = inventory.freeze(into=es1.tmp_path / "releases")

    by_name = {p.name: p for p in release.processes()}
    roles = {e.line_id: e.role for e in by_name["widget"].exchanges}
    assert roles == {
        "1": Role.reference,
        "2": Role.input,
        "3": Role.waste_to_treatment,
        "4": Role.avoided_product,
        "5": Role.resource,
    }
    grid = by_name["grid"]
    assert [e.role for e in grid.exchanges][:2] == [Role.reference, Role.own_use]
    assert grid.exchanges[1].supplier_id == grid.id
    assert grid.exchanges[2].pedigree is not None and grid.exchanges[2].uncertainty is not None
    kinds = {f.name: f.kind for f in release.flows()}
    assert kinds["textile waste"] is FlowKind.waste
    assert kinds["Carbon dioxide"] is FlowKind.elementary
    treatment = by_name["widget"].exchanges[2]
    assert treatment.supplier_id == by_name["incineration"].id


@pytest.mark.parametrize("workers", [1, 2])
def test_the_hash_does_not_depend_on_the_workers(es1, workers: int) -> None:
    from dataclasses import replace

    folder = _source(es1)

    inventory = es1.load(folder, replace(es1.options, workers=workers))

    assert inventory.freeze(into=es1.tmp_path / f"w{workers}").hash == (
        es1.load(folder).freeze(into=es1.tmp_path / "base").hash
    )
