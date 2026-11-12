"""Reading a SimaPro CSV process export into an inventory source: what each part becomes."""

import math
from dataclasses import replace
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc
import pytest

from headwater.io.inventory import link_inventory
from headwater.io.simapro_csv import FORMAT, read
from headwater.io.source import flow_id, product_flow_id
from headwater.model import FlowKind, InvalidDataError
from tests.conftest import approx_numbers

compute: Any = pc

FLOWS = "simapro-9.5"
"""The flow list and the namespace of flow ids of an export from SimaPro 9.5."""

ELECTRICITY = "electricity {GLO}| electricity production"


def _codes(raised: pytest.ExceptionInfo[InvalidDataError]) -> list[str]:
    return [p.code for p in raised.value.problems]


def _rows(table: pa.Table, *columns: str) -> list[dict[str, Any]]:
    return table.select(list(columns)).to_pylist()


def _of(table: pa.Table, column: str, value: str) -> pa.Table:
    return table.filter(compute.equal(table[column], value))


def test_the_toy_reads_into_processes_lines_flows_and_units(sp: Any) -> None:
    source = read(sp.TOY, sp.OPTIONS)

    assert source.info.model_dump() == {
        "name": "test-1.0",
        "source": "test",
        "version": "1.0",
        "system_model": None,
        "licence": "CC0-1.0",
        "flow_list": FLOWS,
    }
    assert _rows(source.processes, "id", "name", "location", "kind") == [
        {
            "id": f"test:1.0:TOY000000000{n}",
            "name": name,
            "location": "GLO",
            "kind": "ordinary_transforming_activity",
        }
        for n, name in (
            (1, "cotton fabric production"),
            (2, "electricity production"),
            (3, "incineration of textile waste"),
        )
    ]
    fabric = _of(source.lines, "process_id", "test:1.0:TOY0000000001")
    assert _rows(
        fabric, "line_id", "role", "flow_name", "amount", "unit", "supplier_name",
        "supplier_product", "supplier_location", "supplier_unit",
    ) == [
        {"line_id": "p1", "role": "reference", "flow_name": "cotton fabric", "amount": 1.0,
         "unit": "kg", "supplier_name": None, "supplier_product": None,
         "supplier_location": None, "supplier_unit": None},
        {"line_id": "e1", "role": "input", "flow_name": "electricity", "amount": 3.0,
         "unit": "kWh", "supplier_name": "electricity production",
         "supplier_product": "electricity", "supplier_location": "GLO", "supplier_unit": None},
        {"line_id": "air1", "role": "emission", "flow_name": "Carbon dioxide, fossil",
         "amount": 2.0, "unit": "kg", "supplier_name": None, "supplier_product": None,
         "supplier_location": None, "supplier_unit": None},
        {"line_id": "air2", "role": "emission", "flow_name": "Methane, fossil", "amount": 0.004,
         "unit": "kg", "supplier_name": None, "supplier_product": None,
         "supplier_location": None, "supplier_unit": None},
    ]  # fmt: skip
    flows = {f.name: f for f in source.flows}
    assert flows["Carbon dioxide, fossil"].id == flow_id(FLOWS, "Carbon dioxide, fossil", "air")
    assert [(f.name, f.kind, f.unit, f.compartment, f.cas) for f in source.flows] == [
        ("Carbon dioxide, fossil", FlowKind.elementary, "kg", "air", "124-38-9"),
        ("Methane, fossil", FlowKind.elementary, "kg", "air", "74-82-8"),
        ("cotton fabric", FlowKind.product, "kg", None, None),
        ("electricity", FlowKind.product, "kWh", None, None),
        ("textile waste", FlowKind.waste, "kg", None, None),
    ]
    assert sorted(source.units.names) == ["MJ", "kWh", "kg"]
    assert source.warnings == ()


def test_the_toy_links_by_name_and_freezes(sp: Any, tmp_path: Any) -> None:
    inventory = link_inventory(read(sp.TOY, sp.OPTIONS))

    assert inventory.problems == ()
    [electricity] = [e for p in inventory.processes for e in p.exchanges if e.role.value == "input"]
    assert electricity.supplier_id == "test:1.0:TOY0000000002"
    inventory.freeze(into=tmp_path)


def test_every_line_section_has_its_role_and_compartment(sp: Any) -> None:
    lines = {
        "Avoided products": [f"{ELECTRICITY};kWh;0.2;Undefined;0;0;0;"],
        "Resources": ["Water, river;in water;m3;0.01;Undefined;0;0;0;"],
        "Materials/fuels": ["cotton fibre {GLO}| ginning;kg;1.1;Undefined;0;0;0;"],
        "Electricity/heat": [f"{ELECTRICITY};kWh;3;Undefined;0;0;0;"],
        "Emissions to air": ["Carbon dioxide, fossil;high. pop.;kg;2;Undefined;0;0;0;"],
        "Emissions to water": ["Nitrate;river;kg;0.1;Undefined;0;0;0;"],
        "Emissions to soil": ["Cadmium;agricultural;kg;1E-6;Undefined;0;0;0;"],
        "Final waste flows": ["Waste, unspecified;;kg;0.01;Undefined;0;0;0;"],
        "Non material emissions": ["Noise;;p;1;Undefined;0;0;0;"],
        "Social issues": ["Child labour;;p;1;Undefined;0;0;0;"],
        "Economic issues": ["Added value;;p;1;Undefined;0;0;0;"],
        "Waste to treatment": ["textile waste {GLO}| incineration;kg;0.05;Undefined;0;0;0;"],
    }
    path = sp.write(
        sp.process("P1", ["shirt {GLO}| sewing;p;1;100;not defined;Shirts;"], lines=lines)
    )

    source = read(path, sp.OPTIONS)

    found = {(r["line_id"], r["role"]) for r in _rows(source.lines, "line_id", "role")}
    assert found == {
        ("p1", "reference"), ("a1", "avoided_product"), ("r1", "resource"), ("m1", "input"),
        ("e1", "input"), ("air1", "emission"), ("water1", "emission"), ("soil1", "emission"),
        ("waste1", "emission"), ("nm1", "emission"), ("social1", "emission"),
        ("economic1", "emission"), ("wt1", "waste_to_treatment"),
    }  # fmt: skip
    compartments = {f.name: f.compartment for f in source.flows if f.kind is FlowKind.elementary}
    assert compartments == {
        "Water, river": "raw/in water",
        "Carbon dioxide, fossil": "air/high. pop.",
        "Nitrate": "water/river",
        "Cadmium": "soil/agricultural",
        "Waste, unspecified": "waste",
        "Noise": "non material",
        "Child labour": "social",
        "Added value": "economic",
    }
    kinds = {f.name: f.kind for f in source.flows if f.kind is not FlowKind.elementary}
    assert kinds == {
        "shirt": FlowKind.product,
        "electricity": FlowKind.product,
        "cotton fibre": FlowKind.product,
        "textile waste": FlowKind.waste,
    }


def test_a_decimal_comma_and_a_comma_in_a_quoted_cell(sp: Any) -> None:
    header = sp.HEADER.replace("{Decimal separator: .}", "{Decimal separator: ,}")
    path = sp.write(
        sp.process(
            "P1",
            ['"fabric, dyed {GLO}| dyeing";kg;1;100;not defined;Textiles;'],
            lines={"Emissions to air": ["Carbon dioxide, fossil;;kg;1,5E-2;Undefined;0;0;0;"]},
        ),
        header=header,
    )

    source = read(path, sp.OPTIONS)

    assert source.lines["amount"].to_pylist() == [1.0, 0.015]
    assert "fabric, dyed" in {f.name for f in source.flows}


def test_the_separators_are_read_from_the_header(sp: Any) -> None:
    header = sp.HEADER.replace("{CSV separator: Semicolon}", "{CSV separator: Tab}")
    block = sp.process("P1", ["fabric {GLO}| weaving;kg;1;100;not defined;Textiles;"])
    path = sp.write(block.replace(";", "\t"), header=header)

    assert read(path, sp.OPTIONS).processes.num_rows == 1

    clash = sp.HEADER.replace("{Decimal separator: .}", "{Decimal separator: ;}")
    with pytest.raises(InvalidDataError) as raised:
        read(sp.write(block, header=clash), sp.OPTIONS)
    assert _codes(raised) == ["bad-header"]


def test_a_file_written_in_windows_1252_is_read(sp: Any) -> None:
    path = sp.write(
        sp.process("P1", ["café {FR}| roasting;kg;1;100;not defined;Food;"]), encoding="cp1252"
    )

    assert "café" in {f.name for f in read(path, sp.OPTIONS).flows}


def test_line_breaks_in_a_comment_are_kept(sp: Any) -> None:
    path = sp.write(
        sp.process(
            "P1",
            ["fabric {GLO}| weaving;kg;1;100;not defined;Textiles;"],
            lines={"Emissions to air": ["Carbon dioxide, fossil;;kg;1;Undefined;0;0;0;one\x7ftwo"]},
        )
    )

    comments = read(path, sp.OPTIONS).lines["comment"].to_pylist()

    assert "one\ntwo" in comments


def test_several_products_give_their_allocation_factors(sp: Any) -> None:
    path = sp.write(
        sp.process(
            "GIN",
            [
                "cotton fibre {IN}| cotton ginning;kg;0.35;90;not defined;Textiles;",
                "cotton seed {IN}| cotton ginning;kg;0.6;10;not defined;Textiles;",
            ],
            lines={"Emissions to air": ["Carbon dioxide, fossil;;kg;0.1;Undefined;0;0;0;"]},
        ),
        sp.process(
            "SHIRT",
            ["shirt {IN}| sewing;p;1;100;not defined;Shirts;"],
            lines={
                "Materials/fuels": ["cotton fibre {IN}| cotton ginning;kg;0.2;Undefined;0;0;0;"]
            },
        ),
    )

    source = read(path, sp.OPTIONS)

    assert _rows(source.allocation, "process_id", "product", "line_id", "factor") == [
        {"process_id": "test:1.0:GIN", "product": "p1", "line_id": None, "factor": 0.9},
        {"process_id": "test:1.0:GIN", "product": "p2", "line_id": None, "factor": 0.1},
        {"process_id": "test:1.0:SHIRT", "product": "p1", "line_id": None, "factor": 1.0},
    ]
    inventory = link_inventory(source)
    assert inventory.problems == ()
    [bought] = [e for p in inventory.processes for e in p.exchanges if e.role.value == "input"]
    assert bought.supplier_id == "test:1.0:GIN:p1"


def test_products_of_one_process_must_agree_on_name_and_location(sp: Any) -> None:
    path = sp.write(
        sp.process(
            "GIN",
            [
                "cotton fibre {IN}| cotton ginning;kg;0.35;90;not defined;Textiles;",
                "cotton seed {CN}| cotton ginning;kg;0.6;10;not defined;Textiles;",
            ],
        )
    )

    with pytest.raises(InvalidDataError) as raised:
        read(path, sp.OPTIONS)

    assert _codes(raised) == ["products-disagree"]
    assert "cotton seed {CN}" in str(raised.value)


def test_a_process_named_by_a_text_without_a_process_part_takes_its_process_name(sp: Any) -> None:
    path = sp.write(
        sp.process(
            "P1", ["Wheat grain, at farm gate {FR} U;kg;1;100;not defined;Cereals;"],
            process_name="Wheat production",
        ),
        sp.process("P2", ["Barley grain, at farm gate {FR} U;kg;1;100;not defined;Cereals;"]),
        sp.process("P3", ["Hay;kg;1;100;not defined;Fodder;"]),
    )  # fmt: skip

    source = read(path, sp.OPTIONS)

    assert _rows(source.processes, "name", "location") == [
        {"name": "Wheat production", "location": "FR"},
        {"name": "Barley grain, at farm gate {FR} U", "location": "FR"},
        {"name": "Hay", "location": "unspecified"},
    ]


def test_units_come_from_the_units_block_and_simapro_names_map_to_headwater(sp: Any) -> None:
    path = sp.write(
        sp.process(
            "P1",
            ["fabric {GLO}| weaving;kg;1;100;not defined;Textiles;"],
            lines={
                "Materials/fuels": [
                    "water {GLO}| market for;l;2;Undefined;0;0;0;",
                    "button {GLO}| market for;p;4;Undefined;0;0;0;",
                    "steel {GLO}| market for;ton;0.001;Undefined;0;0;0;",
                ],
                "Emissions to air": ["Radon-222;;kBq;3;Undefined;0;0;0;"],
            },
        ),
        sp.table("Units", "kg;Mass;1;kg", "ton;Mass;1000;kg", "kBq;Radioactivity;1;kBq",
                 "Bq;Radioactivity;0.001;kBq"),
    )  # fmt: skip

    source = read(path, sp.OPTIONS)

    units = {u.name: (u.quantity, u.factor_to_reference) for u in source.units}
    assert units["t"] == ("mass", 1000.0)
    assert units["L"] == ("volume", 0.001)
    assert units["unit"] == ("item", 1.0)
    assert units["Bq"] == ("Radioactivity", 0.001)
    assert set(source.lines["unit"].to_pylist()) == {"L", "kBq", "kg", "t", "unit"}


def test_a_unit_neither_simapro_nor_headwater_defines_is_refused(sp: Any) -> None:
    path = sp.write(
        sp.process(
            "P1",
            ["fabric {GLO}| weaving;kg;1;100;not defined;Textiles;"],
            lines={"Materials/fuels": ["dye {GLO}| market for;bucket;2;Undefined;0;0;0;"]},
        )
    )

    with pytest.raises(InvalidDataError) as raised:
        read(path, sp.OPTIONS)

    assert _codes(raised) == ["unknown-unit"]
    assert "test:1.0:P1 line m1" in str(raised.value)


def test_a_units_block_that_disagrees_with_headwater_is_refused(sp: Any) -> None:
    path = sp.write(
        sp.process("P1", ["fabric {GLO}| weaving;kg;1;100;not defined;Textiles;"]),
        sp.table("Units", "kg;Mass;1;kg", "g;Mass;0.01;kg"),
    )

    with pytest.raises(InvalidDataError) as raised:
        read(path, sp.OPTIONS)

    assert _codes(raised) == ["unit-conflict"]


@pytest.mark.parametrize(
    ("cells", "expected"),
    [
        ("Undefined;0;0;0", None),
        ("Lognormal;1.21;0;0",
         {"distribution": "lognormal", "loc": math.log(3), "scale": math.log(1.1)}),
        ("Normal;0.4;0;0", {"distribution": "normal", "loc": 3.0, "scale": 0.2}),
        ("Uniform;0;2;4", {"distribution": "uniform", "minimum": 2.0, "maximum": 4.0}),
        ("Triangle;0;2;4",
         {"distribution": "triangular", "loc": 3.0, "minimum": 2.0, "maximum": 4.0}),
    ],
)  # fmt: skip
def test_simapro_uncertainty_becomes_the_stats_arrays_parameters(
    sp: Any, cells: str, expected: dict[str, Any] | None
) -> None:
    path = sp.write(
        sp.process(
            "P1",
            ["fabric {GLO}| weaving;kg;1;100;not defined;Textiles;"],
            lines={"Electricity/heat": [f"{ELECTRICITY};kWh;3;{cells};"]},
        )
    )

    [found] = _of(read(path, sp.OPTIONS).lines, "line_id", "e1")["uncertainty"].to_pylist()

    if expected is None:
        assert found is None
        return
    assert found is not None
    assert {k: v for k, v in found.items() if v is not None} == approx_numbers(expected, rel=1e-12)


def test_a_lognormal_gives_its_squared_geometric_standard_deviation() -> None:
    from headwater.io.simapro_csv.records import to_uncertainty

    found = to_uncertainty("Lognormal", -3.0, 1.21, 0.0, 0.0)

    assert found is not None and found.scale is not None
    assert found.loc == pytest.approx(math.log(3), rel=1e-15)
    assert found.scale == pytest.approx(math.log(1.1), rel=1e-15)  # sqrt(1.21) = 1.1
    assert math.exp(found.scale) ** 2 == pytest.approx(1.21, rel=1e-15)


@pytest.mark.parametrize("cells", ["Lognormal;1;0;0", "Lognormal;0;0;0", "Normal;0;0;0"])
def test_a_distribution_with_no_spread_is_a_fixed_amount_with_a_warning(
    sp: Any, cells: str
) -> None:
    path = sp.write(
        sp.process(
            "P1",
            ["fabric {GLO}| weaving;kg;1;100;not defined;Textiles;"],
            lines={"Electricity/heat": [f"{ELECTRICITY};kWh;3;{cells};"]},
        )
    )

    source = read(path, sp.OPTIONS)

    assert source.lines["uncertainty"].null_count == source.lines.num_rows
    assert [w.code for w in source.warnings] == ["zero-variance"]


def test_a_lognormal_of_an_amount_of_0_is_a_fixed_amount_with_a_warning(sp: Any) -> None:
    path = sp.write(
        sp.process(
            "P1",
            ["fabric {GLO}| weaving;kg;1;100;not defined;Textiles;"],
            lines={"Electricity/heat": [f"{ELECTRICITY};kWh;0;Lognormal;1.21;0;0;"]},
        )
    )

    source = read(path, sp.OPTIONS)

    assert source.lines["uncertainty"].null_count == source.lines.num_rows
    assert [w.code for w in source.warnings] == ["lognormal-of-zero"]


@pytest.mark.parametrize("cells", ["Lognormal;0.5;0;0", "Uniform;0;4;2", "Beta;1;0;0"])
def test_a_distribution_that_makes_no_sense_is_refused(sp: Any, cells: str) -> None:
    path = sp.write(
        sp.process(
            "P1",
            ["fabric {GLO}| weaving;kg;1;100;not defined;Textiles;"],
            lines={"Electricity/heat": [f"{ELECTRICITY};kWh;3;{cells};"]},
        )
    )

    with pytest.raises(InvalidDataError) as raised:
        read(path, sp.OPTIONS)

    assert _codes(raised) == ["bad-uncertainty"]
    assert "process P1 line e1" in str(raised.value) and "export.csv:" in str(raised.value)


def test_an_amount_given_as_a_formula_is_refused(sp: Any) -> None:
    path = sp.write(
        sp.process(
            "P1",
            ["fabric {GLO}| weaving;kg;1;100;not defined;Textiles;"],
            lines={
                "Electricity/heat": [f"{ELECTRICITY};kWh;kwh_per_kg*2;Undefined;0;0;0;"],
                "Input parameters": ["kwh_per_kg;1.5;Undefined;0;0;0;No;"],
                "Calculated parameters": ["twice;kwh_per_kg*2;"],
            },
        )
    )

    with pytest.raises(InvalidDataError) as raised:
        read(path, sp.OPTIONS)

    assert _codes(raised) == ["formula-amount"]
    assert "Convert expressions to constants" in str(raised.value)


def test_parameter_blocks_are_read_and_not_stored(sp: Any) -> None:
    path = sp.write(
        sp.process("P1", ["fabric {GLO}| weaving;kg;1;100;not defined;Textiles;"]),
        sp.table("Database Input parameters", "yield;0.9;Undefined;0;0;0;No;"),
        sp.table("Project Calculated parameters", "loss;1-yield;"),
    )

    assert read(path, sp.OPTIONS).processes.num_rows == 1


@pytest.mark.parametrize(
    ("change", "code"),
    [
        (lambda b: b.replace("Avoided products", "Avoided product"), "unknown-section"),
        (lambda b: b.replace("End\n", ""), "missing-end"),
        (lambda b: b + "\nProcesses\n\nEnd\n", "unknown-block"),
        (lambda b: b.replace("material", "waste scenario"), "waste-scenario"),
        (lambda b: b + "\n" + b, "duplicate-process"),
        (lambda b: b.replace("TOY", ""), "missing-identifier"),
    ],
)
def test_what_the_reader_does_not_understand_stops_the_import(
    sp: Any, change: Any, code: str
) -> None:
    block = sp.process("TOY", ["fabric {GLO}| weaving;kg;1;100;not defined;Textiles;"])

    with pytest.raises(InvalidDataError) as raised:
        read(sp.write(change(block)), sp.OPTIONS)

    assert code in _codes(raised)


def test_one_flow_as_a_product_and_a_waste_is_refused(sp: Any) -> None:
    path = sp.write(
        sp.process(
            "P1",
            ["fabric {GLO}| weaving;kg;1;100;not defined;Textiles;"],
            lines={
                "Materials/fuels": ["scraps {GLO}| market for;kg;1;Undefined;0;0;0;"],
                "Waste to treatment": ["scraps {GLO}| market for;kg;1;Undefined;0;0;0;"],
            },
        )
    )

    with pytest.raises(InvalidDataError) as raised:
        read(path, sp.OPTIONS)

    assert _codes(raised) == ["flow-kind-conflict"]


def test_one_flow_in_units_of_two_quantities_is_refused(sp: Any) -> None:
    path = sp.write(
        sp.process(
            "P1",
            ["fabric {GLO}| weaving;kg;1;100;not defined;Textiles;"],
            lines={"Emissions to air": [
                "Carbon dioxide, fossil;;kg;1;Undefined;0;0;0;",
                "Carbon dioxide, fossil;;MJ;1;Undefined;0;0;0;",
            ]},
        )
    )  # fmt: skip

    with pytest.raises(InvalidDataError) as raised:
        read(path, sp.OPTIONS)

    assert _codes(raised) == ["flow-unit-conflict"]


@pytest.mark.parametrize(
    ("options", "flags"),
    [
        ({"version": None}, ["--version"]),
        ({"source": None, "licence": None}, ["--source", "--licence"]),
    ],
)
def test_version_source_and_licence_are_required(
    sp: Any, options: dict[str, Any], flags: list[str]
) -> None:
    with pytest.raises(InvalidDataError) as raised:
        read(sp.TOY, replace(sp.OPTIONS, **options))

    assert [(p.code, p.item) for p in raised.value.problems] == [
        ("missing-option", flag) for flag in flags
    ]


def test_a_method_export_is_not_an_inventory(sp: Any) -> None:
    with pytest.raises(InvalidDataError) as raised:
        read(sp.TOY_METHOD, sp.OPTIONS)

    assert _codes(raised) == ["wrong-export-kind"]
    assert "headwater method import" in str(raised.value)


def test_detection_reads_the_first_line_only(sp: Any, tmp_path: Any) -> None:
    other = tmp_path / "other.csv"
    other.write_text("a;b\n1;2\n", encoding="utf-8")

    assert FORMAT.detect(sp.TOY) and FORMAT.detect(sp.TOY_METHOD)
    assert not FORMAT.detect(other)
    assert not FORMAT.detect(tmp_path)


def test_with_a_decimal_comma_a_dot_in_a_number_is_refused(sp: Any) -> None:
    header = sp.HEADER.replace("{Decimal separator: .}", "{Decimal separator: ,}")
    path = sp.write(
        sp.process(
            "P1",
            ["fabric {GLO}| weaving;kg;1;100;not defined;Textiles;"],
            lines={"Emissions to air": ["Carbon dioxide, fossil;;kg;1.500;Undefined;0;0;0;"]},
        ),
        header=header,
    )

    with pytest.raises(InvalidDataError) as raised:
        read(path, sp.OPTIONS)

    assert _codes(raised) == ["formula-amount"]


def test_a_bounded_distribution_without_its_bounds_is_refused(sp: Any) -> None:
    path = sp.write(
        sp.process(
            "P1",
            ["fabric {GLO}| weaving;kg;1;100;not defined;Textiles;"],
            lines={"Electricity/heat": [f"{ELECTRICITY};kWh;3;Uniform;;;;"]},
        )
    )

    with pytest.raises(InvalidDataError) as raised:
        read(path, sp.OPTIONS)

    assert _codes(raised) == ["bad-uncertainty"]


def test_products_with_a_blank_allocation_are_an_open_issue(sp: Any) -> None:
    path = sp.write(
        sp.process(
            "GIN",
            [
                "cotton fibre {IN}| cotton ginning;kg;0.35;;not defined;Textiles;",
                "cotton seed {IN}| cotton ginning;kg;0.6;;not defined;Textiles;",
            ],
        )
    )

    source = read(path, sp.OPTIONS)

    assert source.allocation.num_rows == 0
    assert [p.code for p in link_inventory(source).problems] == ["not-allocated"]


def test_a_product_given_in_units_of_two_quantities_is_two_flows(sp: Any) -> None:
    gas = "natural gas {CH}| market for"
    path = sp.write(
        sp.process(
            "P1",
            ["heat {CH}| boiler;MJ;1;100;not defined;Energy;"],
            lines={
                "Materials/fuels": [
                    f"{gas};m3;0.03;Undefined;0;0;0;",
                    f"{gas};kg;0.02;Undefined;0;0;0;",
                ]
            },
        ),
        sp.table("Units", "kg;Mass;1;kg", "m3;Volume;1;m3", "MJ;Energy;1;MJ"),
    )

    source = read(path, sp.OPTIONS)

    found = {(f.unit, f.id) for f in source.flows if f.name == "natural gas"}
    assert found == {
        ("m3", product_flow_id(FLOWS, "natural gas", "volume")),
        ("kg", product_flow_id(FLOWS, "natural gas", "mass")),
    }
