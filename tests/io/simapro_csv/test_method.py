"""Reading a SimaPro method export: factors keyed by name and compartment."""

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

import headwater as hw
from headwater.io.formats import inventory_from, method_from
from headwater.io.method import write_method
from headwater.io.simapro_csv import read, read_method
from headwater.link import FlowKey
from headwater.model import Indicator, InvalidDataError
from headwater.query._internals import internals

CO2 = "Air;(unspecified);Carbon dioxide, fossil;000124-38-9;1;kg"
CH4 = "Air;high. pop.;Methane, fossil;000074-82-8;29.8;kg"


def _codes(raised: pytest.ExceptionInfo[InvalidDataError]) -> list[str]:
    return [p.code for p in raised.value.problems]


def test_the_toy_method_reads_with_a_decimal_comma(sp: Any) -> None:
    source = read_method(sp.TOY_METHOD, replace(sp.METHOD, method="toy climate v1"))

    assert (source.name, source.id, source.version, source.variant) == (
        "toy climate v1",
        "toy-climate-1",
        "1",
        None,
    )
    assert source.indicators == (
        Indicator(id="climate-change", name="Climate change", unit="kg CO2-eq"),
    )
    assert [(f.indicator, f.flow, f.factor, f.unit) for f in source.factors] == [
        ("climate-change", FlowKey(name="Carbon dioxide, fossil", compartment="air"), 1.0, "kg"),
        ("climate-change", FlowKey(name="Methane, fossil", compartment="air"), 29.8, "kg"),
    ]
    assert source.flow_list == "simapro-9.5"
    assert source.factors[0].where == "toy-climate.csv:51"


def test_compartments_are_written_as_the_inventory_writes_them(sp: Any) -> None:
    rows = [CO2, CH4, "Raw;in ground;Coal, hard;;0.1;kg", "Water;;Nitrate;;0.2;kg"]
    path = sp.write(sp.method("m v1", ("Climate change;kg CO2-eq", rows)), header=sp.METHOD_HEADER)

    keys = [f.flow for f in read_method(path, sp.METHOD).factors]

    assert [k.compartment for k in keys] == ["air", "air/high. pop.", "raw/in ground", "water"]


def test_the_method_is_chosen_by_name_when_the_file_holds_several(sp: Any) -> None:
    path = sp.write(
        sp.method("first v1", ("Climate change;kg CO2-eq", [CO2])),
        sp.method("second v2", ("Climate change;kg CO2-eq", [CH4])),
        header=sp.METHOD_HEADER,
    )

    assert read_method(path, replace(sp.METHOD, method="second v2")).factors[0].factor == 29.8
    with pytest.raises(InvalidDataError) as raised:
        read_method(path, sp.METHOD)
    assert _codes(raised) == ["missing-method"]
    assert "first v1, second v2" in str(raised.value)
    with pytest.raises(InvalidDataError) as raised:
        read_method(path, replace(sp.METHOD, method="third"))
    assert _codes(raised) == ["unknown-method"]


def test_categories_can_be_chosen_and_the_others_are_named(sp: Any) -> None:
    path = sp.write(
        sp.method(
            "m v1",
            ("Climate change;kg CO2-eq", [CO2]),
            ("Acidification;mol H+ eq", ["Air;;Ammonia;;3.0;kg"]),
        ),
        header=sp.METHOD_HEADER,
    )

    source = read_method(path, replace(sp.METHOD, categories=("Acidification",)))

    assert [i.id for i in source.indicators] == ["acidification"]
    assert source.left_out == ("Climate change",)


@pytest.mark.parametrize(
    ("name", "id"),
    [("EF 3.1", "EF-3.1"), ("EF v3.1", "EF-3.1"), ("IPCC 2021 (incl. CO2)", "IPCC-2021-incl-CO2")],
)
def test_the_default_id_is_the_name_as_a_folder_name(sp: Any, name: str, id: str) -> None:
    path = sp.write(sp.method(name, ("Climate change;kg CO2-eq", [CO2])), header=sp.METHOD_HEADER)

    assert read_method(path, sp.METHOD).id == id


def test_id_version_and_variant_can_be_given(sp: Any) -> None:
    path = sp.write(
        sp.method("EF 3.1", ("Climate change;kg CO2-eq", [CO2])), header=sp.METHOD_HEADER
    )

    source = read_method(path, replace(sp.METHOD, id="EF-3.1", version="3.1", variant="SimaPro"))

    assert (source.id, source.version, source.variant) == ("EF-3.1", "3.1", "SimaPro")


def test_a_compartment_simapro_does_not_have_is_refused(sp: Any) -> None:
    path = sp.write(
        sp.method("m v1", ("Climate change;kg CO2-eq", ["Space;;Carbon dioxide;;1;kg"])),
        header=sp.METHOD_HEADER,
    )

    with pytest.raises(InvalidDataError) as raised:
        read_method(path, sp.METHOD)

    assert _codes(raised) == ["unknown-compartment"]


def test_a_process_export_is_not_a_method(sp: Any) -> None:
    with pytest.raises(InvalidDataError) as raised:
        read_method(sp.TOY, sp.METHOD)

    assert _codes(raised) == ["wrong-export-kind"]
    assert "headwater release import" in str(raised.value)


def test_the_method_maps_onto_the_flows_of_the_inventory(sp: Any) -> None:
    flows = {(f.name, f.compartment) for f in read(sp.TOY, sp.OPTIONS).flows}
    method = read_method(sp.TOY_METHOD, sp.METHOD)

    assert {(f.flow.name, f.flow.compartment) for f in method.factors} <= flows


def test_a_category_without_substances_is_refused(sp: Any) -> None:
    block = sp.method("m v1", ("Climate change;kg CO2-eq", [CO2]))
    broken = block.replace("Substances\n", "Damage category\n")
    path = sp.write(broken, header=sp.METHOD_HEADER)

    with pytest.raises(InvalidDataError) as raised:
        read_method(path, sp.METHOD)

    assert _codes(raised) == ["bad-category"]


def test_a_factor_for_an_unspecified_subcompartment_covers_the_others(sp: Any) -> None:
    rows = [CO2, CH4, "Water;;Nitrate;;0.2;kg"]
    path = sp.write(sp.method("m v1", ("Climate change;kg CO2-eq", rows)), header=sp.METHOD_HEADER)

    found = [
        (f.flow.compartment, f.covers_subcompartments) for f in read_method(path, sp.METHOD).factors
    ]

    assert found == [("air", True), ("air/high. pop.", False), ("water", True)]


def test_an_emission_to_a_subcompartment_scores_with_the_unspecified_factor(
    sp: Any, tmp_path: Path
) -> None:
    path = sp.write(
        sp.process(
            "P1",
            ["shirt {GLO}| sewing;p;1;100;not defined;Shirts;"],
            lines={"Emissions to air": [
                "Carbon dioxide, fossil;urban air close to ground;kg;2;Undefined;0;0;0;",
                "Carbon dioxide, fossil;;kg;0.5;Undefined;0;0;0;",
            ]},
        )
    )  # fmt: skip
    release = inventory_from(path, sp.OPTIONS, format="simapro-csv").freeze(into=tmp_path / "r")

    mapped = method_from(sp.TOY_METHOD, release, sp.METHOD, format="simapro-csv")
    background = hw.load(release.path, method=write_method(mapped, tmp_path / "toy-climate-1"))

    # SimaPro gives the CO2 to urban air the factor of air (unspecified): 2 + 0.5 kg CO2-eq.
    score = (
        internals(background)
        .solved.x["climate-change"]
        .values[internals(background).index["test:1.0:P1"]]
    )
    assert score == pytest.approx(2.5, rel=1e-12)
    assert [r.copied for r in mapped.indicators] == [1]
