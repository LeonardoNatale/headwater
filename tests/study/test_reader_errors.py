"""Mistakes in the files themselves: each becomes an error that names the file and the item."""

from collections.abc import Callable
from pathlib import Path

import pytest

from headwater.model import InvalidDataError, Problem
from headwater.study import read_database, read_method, read_process_folder

Edit = Callable[[Path, str, str], None]

TSHIRT_CSV = "tshirt/tshirt/processes/tshirt.csv"
DATABASE_YAML = "tshirt/tshirt/database.yaml"
CUT_OFF_YAML = "tshirt-cut-off/tshirt/database.yaml"


def problems_of(
    path: Path, reader: Callable[[Path], object] = read_database
) -> tuple[Problem, ...]:
    with pytest.raises(InvalidDataError) as raised:
        reader(path)
    return raised.value.problems


def codes(problems: tuple[Problem, ...]) -> list[str]:
    return [p.code for p in problems]


# --- process CSV files ---------------------------------------------------------------------


def test_amount_with_a_decimal_comma(toy: Path, edit: Edit) -> None:
    edit(toy / TSHIRT_CSV, "0.25,kg", '"0,25",kg')

    (problem,) = problems_of(toy / "tshirt/tshirt")

    assert problem.code == "bad-number"
    assert problem.item == "tshirt:tshirt line e02"
    assert "'0,25'" in problem.message
    assert problem.location == "processes/tshirt.csv:3"


@pytest.mark.parametrize("text", ["nan", "inf", "-inf", ""])
def test_amount_that_is_not_a_finite_number(toy: Path, edit: Edit, text: str) -> None:
    edit(toy / TSHIRT_CSV, "0.25,kg", f"{text},kg")

    (problem,) = problems_of(toy / "tshirt/tshirt")

    assert problem.code == "bad-number"
    assert problem.item == "tshirt:tshirt line e02"


def test_unknown_role(toy: Path, edit: Edit) -> None:
    edit(toy / TSHIRT_CSV, "e02,input,", "e02,inputt,")

    (problem,) = problems_of(toy / "tshirt/tshirt")

    assert problem.code == "unknown-role"
    assert problem.item == "tshirt:tshirt line e02"
    assert "'inputt'" in problem.message
    assert "input" in problem.message  # the valid roles are listed


def test_duplicate_line_id(toy: Path, edit: Edit) -> None:
    edit(toy / TSHIRT_CSV, "e03,input", "e02,input")

    (problem,) = problems_of(toy / "tshirt/tshirt")

    assert problem.code == "duplicate-line"
    assert problem.item == "tshirt:tshirt line e02"


def test_process_without_a_reference_line(toy: Path, edit: Edit) -> None:
    edit(toy / TSHIRT_CSV, "e01,reference,tshirt:tshirt,1,unit,\n", "")

    (problem,) = problems_of(toy / "tshirt/tshirt")

    assert problem.code == "reference-count"
    assert problem.item == "process tshirt:tshirt"
    assert "none" in problem.message


def test_process_with_two_reference_lines(toy: Path, edit: Edit) -> None:
    edit(toy / TSHIRT_CSV, "e02,input,toy:cotton-fabric", "e02,reference,toy:cotton-fabric")

    (problem,) = problems_of(toy / "tshirt/tshirt")

    assert problem.code == "reference-count"
    assert "e01" in problem.message
    assert "e02" in problem.message


def test_reference_amount_must_be_above_zero(toy: Path, edit: Edit) -> None:
    edit(toy / TSHIRT_CSV, "e01,reference,tshirt:tshirt,1,", "e01,reference,tshirt:tshirt,0,")

    (problem,) = problems_of(toy / "tshirt/tshirt")

    assert problem.code == "reference-amount"
    assert problem.item == "tshirt:tshirt line e01"


def test_missing_column(toy: Path) -> None:
    (toy / TSHIRT_CSV).write_text(
        "line,role,flow,amount,supplier\ne01,reference,tshirt:tshirt,1,\n"
    )

    (problem,) = problems_of(toy / "tshirt/tshirt")

    assert problem.code == "bad-columns"
    assert "unit" in problem.message
    assert problem.location == "processes/tshirt.csv:1"


def test_misspelled_column(toy: Path, edit: Edit) -> None:
    edit(toy / TSHIRT_CSV, "unit,supplier", "unit,suplier")

    problems = problems_of(toy / "tshirt/tshirt")

    assert codes(problems) == ["bad-columns"]
    assert "suplier" in problems[0].message
    assert "supplier" in problems[0].message


def test_row_with_the_wrong_number_of_cells(toy: Path, edit: Edit) -> None:
    edit(
        toy / TSHIRT_CSV,
        "e03,input,toy:electricity,0.5,kWh,",
        "e03,input,toy:electricity,0.5,kWh,x,y,",
    )

    (problem,) = problems_of(toy / "tshirt/tshirt")

    assert problem.code == "bad-row"
    assert problem.location == "processes/tshirt.csv:4"


def test_process_file_that_is_empty(toy: Path) -> None:
    (toy / TSHIRT_CSV).write_text("")

    (problem,) = problems_of(toy / "tshirt/tshirt")

    assert problem.code == "bad-columns"
    assert problem.location == "processes/tshirt.csv"


def test_all_rows_are_checked_not_only_the_first_bad_one(toy: Path, edit: Edit) -> None:
    edit(toy / TSHIRT_CSV, "0.25,kg", "abc,kg")
    edit(toy / TSHIRT_CSV, "e03,input", "e03,inputt")

    problems = problems_of(toy / "tshirt/tshirt")

    assert sorted((p.code, p.item) for p in problems) == [
        ("bad-number", "tshirt:tshirt line e02"),
        ("unknown-role", "tshirt:tshirt line e03"),
    ]


def test_database_without_processes(toy: Path) -> None:
    (toy / TSHIRT_CSV).unlink()
    (toy / "tshirt/tshirt/processes/tshirt.yaml").unlink()

    (problem,) = problems_of(toy / "tshirt/tshirt")

    assert problem.code == "no-processes"


# --- database.yaml ----------------------------------------------------------------------------


def test_missing_database_yaml(toy: Path) -> None:
    (toy / DATABASE_YAML).unlink()

    (problem,) = problems_of(toy / "tshirt/tshirt")

    assert problem.code == "missing-file"
    assert "database.yaml" in problem.message


def test_a_folder_with_a_study_yaml_says_to_read_database_yaml(toy: Path) -> None:
    (toy / DATABASE_YAML).rename(toy / "tshirt/tshirt/study.yaml")

    (problem,) = problems_of(toy / "tshirt/tshirt")

    assert problem.code == "missing-file"
    assert "database.yaml" in problem.message
    assert "study.yaml" in problem.message


def test_folder_that_does_not_exist(tmp_path: Path) -> None:
    (problem,) = problems_of(tmp_path / "nope")

    assert problem.code == "missing-file"
    assert "nope" in problem.message


def test_misspelled_key_in_database_yaml(toy: Path, edit: Edit) -> None:
    edit(toy / DATABASE_YAML, "name:", "names:")

    problems = problems_of(toy / "tshirt/tshirt")

    assert sorted((p.code, p.item) for p in problems) == [
        ("invalid-field", "database.yaml: name"),
        ("invalid-field", "database.yaml: names"),
    ]


@pytest.mark.parametrize("field", ["goal", "functional_unit", "reference_flow", "boundary"])
def test_a_field_of_a_study_is_not_a_field_of_a_database(toy: Path, field: str) -> None:
    with (toy / DATABASE_YAML).open("a") as f:
        f.write(f"{field}: x\n")

    (problem,) = problems_of(toy / "tshirt/tshirt")

    assert problem.code == "invalid-field"
    assert problem.item == f"database.yaml: {field}"


def test_value_that_is_not_allowed(toy: Path, edit: Edit) -> None:
    edit(toy / CUT_OFF_YAML, "criteria: [mass, energy]", "criteria: [mass, weight]")

    (problem,) = problems_of(toy / "tshirt-cut-off/tshirt")

    assert problem.code == "invalid-field"
    assert problem.item == "database.yaml: cut_off.criteria.1"
    assert "weight" in problem.message
    assert "environmental_significance" in problem.message  # the allowed values are listed


def test_background_pin_without_a_version(toy: Path, edit: Edit) -> None:
    edit(toy / DATABASE_YAML, "toy-background@fixture", "toy-background")

    (problem,) = problems_of(toy / "tshirt/tshirt")

    assert problem.item == "database.yaml: background"
    assert "name@version" in problem.message


def test_cut_off_share_above_one(toy: Path, edit: Edit) -> None:
    edit(toy / CUT_OFF_YAML, "max_cumulative: 0.05", "max_cumulative: 3")

    (problem,) = problems_of(toy / "tshirt-cut-off/tshirt")

    assert problem.item == "database.yaml: cut_off.max_cumulative"


def test_duplicate_key_in_yaml(toy: Path) -> None:
    with (toy / DATABASE_YAML).open("a") as f:
        f.write("methods: [toy-other]\n")

    (problem,) = problems_of(toy / "tshirt/tshirt")

    assert problem.code == "bad-yaml"
    assert "methods" in problem.message
    assert problem.location is not None
    assert problem.location.startswith("database.yaml:")


def test_yaml_that_does_not_parse(toy: Path) -> None:
    (toy / DATABASE_YAML).write_text("name: [tshirt\n")

    (problem,) = problems_of(toy / "tshirt/tshirt")

    assert problem.code == "bad-yaml"


def test_yaml_that_is_not_a_mapping(toy: Path) -> None:
    (toy / DATABASE_YAML).write_text("- tshirt\n")

    (problem,) = problems_of(toy / "tshirt/tshirt")

    assert problem.code == "bad-yaml"


# --- optional files, flows, units -----------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("parameters.yaml", ""),
        ("parameters.yaml", "# nothing yet\n"),
        ("cut_offs.csv", "process,line,flow,criterion,share,reason,who,when\n"),
        ("overrides/.gitkeep", ""),
    ],
)
def test_empty_optional_files_are_fine(toy: Path, name: str, content: str) -> None:
    path = toy / "tshirt/tshirt" / name
    path.parent.mkdir(exist_ok=True)
    path.write_text(content)

    folder = read_database(toy / "tshirt/tshirt")

    assert [p.id for p in folder.processes] == ["tshirt:tshirt"]


def test_a_cut_offs_file_with_no_header_is_an_error_naming_the_columns(
    toy: Path,
) -> None:
    (toy / "tshirt/tshirt/cut_offs.csv").write_text("")

    (problem,) = problems_of(toy / "tshirt/tshirt")

    assert problem.code == "bad-columns"
    assert "cut_offs.csv" in problem.item
    assert "criterion" in problem.message


def test_flow_with_an_unknown_kind(toy: Path, edit: Edit) -> None:
    edit(
        toy / "background/flows.csv",
        'toy:co2,"Carbon dioxide, fossil",elementary',
        'toy:co2,"Carbon dioxide, fossil",gas',
    )

    problems = problems_of(toy / "background", lambda p: read_process_folder(p, namespace="toy"))

    assert codes(problems) == ["invalid-field"]
    assert problems[0].item == "flow toy:co2"
    assert "gas" in problems[0].message


def test_duplicate_flow_id_in_one_file(toy: Path) -> None:
    with (toy / "background/flows.csv").open("a") as f:
        f.write("toy:co2,CO2 again,elementary,kg,air,\n")

    problems = problems_of(toy / "background", lambda p: read_process_folder(p, namespace="toy"))

    assert codes(problems) == ["duplicate-flow"]
    assert problems[0].item == "flow toy:co2"


def test_flow_property_with_an_unknown_name(toy: Path) -> None:
    (toy / "background/flow_properties.csv").write_text(
        "flow,property,value,unit\ntoy:cotton-fabric,heating_vlaue_net,18,MJ\n"
    )

    problems = problems_of(toy / "background", lambda p: read_process_folder(p, namespace="toy"))

    assert codes(problems) == ["invalid-field"]
    assert "toy:cotton-fabric" in problems[0].item
    assert "heating_vlaue_net" in problems[0].message


def test_units_csv_adds_a_unit(toy: Path, edit: Edit) -> None:
    (toy / "tshirt/tshirt/units.csv").write_text(
        "unit,quantity,factor_to_reference\nlb,mass,0.45359237\n"
    )
    edit(toy / TSHIRT_CSV, "0.25,kg,", "0.5511,lb,")

    folder = read_database(toy / "tshirt/tshirt")

    assert [u.name for u in folder.units] == ["lb"]


def test_units_csv_may_not_redefine_a_built_in_unit(toy: Path) -> None:
    (toy / "tshirt/tshirt/units.csv").write_text("unit,quantity,factor_to_reference\nkg,mass,2\n")

    (problem,) = problems_of(toy / "tshirt/tshirt")

    assert problem.code == "unit-conflict"
    assert "'kg'" in problem.item


# --- methods -------------------------------------------------------------------------------


def test_factor_for_an_indicator_the_method_does_not_have(toy: Path, edit: Edit) -> None:
    edit(
        toy / "methods/toy-climate/factors.csv", "climate-change,toy:ch4", "climate-chnage,toy:ch4"
    )

    (problem,) = problems_of(toy / "methods/toy-climate", read_method)

    assert problem.code == "unknown-indicator"
    assert "'climate-chnage'" in problem.message
    assert "toy:ch4" in problem.item


def test_duplicate_factor(toy: Path) -> None:
    with (toy / "methods/toy-climate/factors.csv").open("a") as f:
        f.write("climate-change,toy:co2,,2\n")

    (problem,) = problems_of(toy / "methods/toy-climate", read_method)

    assert problem.code == "duplicate-factor"
    assert "toy:co2" in problem.item


def test_factor_that_is_not_a_number(toy: Path, edit: Edit) -> None:
    edit(toy / "methods/toy-climate/factors.csv", "29.8", '"29,8"')

    (problem,) = problems_of(toy / "methods/toy-climate", read_method)

    assert problem.code == "bad-number"
    assert "toy:ch4" in problem.item
    assert "'29,8'" in problem.message


def test_version_that_yaml_turns_into_a_number(toy: Path, edit: Edit) -> None:
    # `version: 3.10` is the float 3.1 in YAML. Quote it.
    edit(toy / "methods/toy-climate/method.yaml", 'version: "1"', "version: 3.10")

    (problem,) = problems_of(toy / "methods/toy-climate", read_method)

    assert problem.item == "method.yaml: version"


def test_a_file_that_is_not_utf8_is_an_error_naming_the_file(toy: Path) -> None:
    (toy / "tshirt/tshirt/cut_offs.csv").write_bytes(b"process,line\n\xff\xfe\n")

    (problem,) = problems_of(toy / "tshirt/tshirt")

    assert problem.code == "unreadable-file"
    assert "cut_offs.csv" in problem.item


def test_a_process_file_that_is_not_utf8_is_an_error_naming_the_file(toy: Path) -> None:
    (toy / TSHIRT_CSV).write_bytes(b"line,role\n\xff\xfe\n")

    (problem,) = problems_of(toy / "tshirt/tshirt")

    assert problem.code == "unreadable-file"
    assert "processes/tshirt.csv" in problem.item


# --- quotes, YAML merge keys, columns, method files ------------------------------------------

COMMENT_HEADER = "line,role,flow,amount,unit,supplier,comment\n"


def test_unclosed_quote_does_not_swallow_the_next_lines(toy: Path) -> None:
    # Without the check, line e04 would become part of the comment of e03 and vanish.
    (toy / "tshirt-waste/tshirt/processes/tshirt.csv").write_text(
        COMMENT_HEADER
        + "e01,reference,tshirt:tshirt,1,unit,,\n"
        + "e02,input,toy:cotton-fabric,0.25,kg,toy:fabric-production,\n"
        + 'e03,input,toy:electricity,0.5,kWh,toy:electricity-production,"check this\n'
        + "e04,waste_to_treatment,toy:textile-waste,0.05,kg,toy:incineration-textile-waste,\n"
    )

    (problem,) = problems_of(toy / "tshirt-waste/tshirt")

    assert problem.code == "bad-csv"
    assert "processes/tshirt.csv" in (problem.location or "")


def test_a_quoted_value_that_spans_lines_is_an_error(toy: Path) -> None:
    (toy / TSHIRT_CSV).write_text(
        COMMENT_HEADER
        + "e01,reference,tshirt:tshirt,1,unit,,\n"
        + 'e02,input,toy:cotton-fabric,0.25,kg,toy:fabric-production,"two\nlines"\n'
        + "e03,input,toy:electricity,0.5,kWh,toy:electricity-production,\n"
    )

    (problem,) = problems_of(toy / "tshirt/tshirt")

    assert problem.code == "bad-row"
    assert "several lines" in problem.message
    assert problem.location == "processes/tshirt.csv:3"  # the line where the row starts


def test_a_quoted_comma_is_fine(toy: Path) -> None:
    (toy / TSHIRT_CSV).write_text(
        COMMENT_HEADER
        + "e01,reference,tshirt:tshirt,1,unit,,\n"
        + 'e02,input,toy:cotton-fabric,0.25,kg,toy:fabric-production,"organic, GOTS"\n'
        + "e03,input,toy:electricity,0.5,kWh,toy:electricity-production,\n"
    )

    folder = read_database(toy / "tshirt/tshirt")

    assert folder.processes[0].exchanges[1].comment == "organic, GOTS"


def test_yaml_merge_keys_are_read(toy: Path, edit: Edit) -> None:
    edit(
        toy / CUT_OFF_YAML,
        "cut_off: {criteria: [mass, energy], max_cumulative: 0.05}",
        "defaults: &d {criteria: [mass, energy], max_cumulative: 0.05}\ncut_off:\n  <<: *d",
    )

    # `defaults` is not a field of a database, so the file is still rejected, by name, not by tag.
    (problem,) = problems_of(toy / "tshirt-cut-off/tshirt")

    assert problem.item == "database.yaml: defaults"


def test_yaml_merge_key_can_be_overridden_by_an_explicit_key(toy: Path) -> None:
    (toy / "methods/toy-climate/method.yaml").write_text(
        "id: toy-climate\n"
        'version: "1"\n'
        "flow_list: toy\n"
        "indicators:\n"
        "  - &base {id: climate-change, name: Climate change, unit: kg CO2-eq}\n"
        "  - {<<: *base, id: fossil, name: Fossil}\n"
    )
    (toy / "methods/toy-climate/factors.csv").write_text(
        "indicator,flow,location,factor\nfossil,toy:co2,,1\n"
    )

    method = read_method(toy / "methods/toy-climate")

    assert [(i.id, i.name, i.unit) for i in method.indicators] == [
        ("climate-change", "Climate change", "kg CO2-eq"),
        ("fossil", "Fossil", "kg CO2-eq"),
    ]


def test_a_repeated_key_is_still_found_next_to_a_merge_key(toy: Path) -> None:
    (toy / "methods/toy-climate/method.yaml").write_text(
        "base: &b {x: 1}\nmerged:\n  <<: *b\n  y: 1\n  y: 2\nid: a\n"
    )

    (problem,) = problems_of(toy / "methods/toy-climate", read_method)

    assert problem.code == "bad-yaml"
    assert "'y'" in problem.message


def test_a_column_that_appears_twice(toy: Path, edit: Edit) -> None:
    edit(
        toy / TSHIRT_CSV,
        "line,role,flow,amount,unit,supplier",
        "line,role,flow,amount,unit,unit,supplier",
    )

    problems = problems_of(toy / "tshirt/tshirt")

    assert codes(problems) == ["bad-columns"]
    assert "'unit' appears twice" in problems[0].message


def test_flows_csv_with_a_missing_column(toy: Path) -> None:
    (toy / "background/flows.csv").write_text("id,name,kind\ntoy:co2,CO2,elementary\n")

    problems = problems_of(toy / "background", lambda p: read_process_folder(p, namespace="toy"))

    assert codes(problems) == ["bad-columns"]
    assert "unit" in problems[0].message
    assert problems[0].location == "flows.csv:1"


def test_method_without_factors_csv(toy: Path) -> None:
    (toy / "methods/toy-climate/factors.csv").unlink()

    (problem,) = problems_of(toy / "methods/toy-climate", read_method)

    assert problem.code == "missing-file"
    assert "factors.csv" in problem.message


def test_method_without_method_yaml(toy: Path) -> None:
    (toy / "methods/toy-climate/method.yaml").unlink()

    (problem,) = problems_of(toy / "methods/toy-climate", read_method)

    assert problem.code == "missing-file"
    assert "method.yaml" in problem.message


def test_factors_belong_in_factors_csv_not_in_method_yaml(toy: Path) -> None:
    with (toy / "methods/toy-climate/method.yaml").open("a") as f:
        f.write("factors: []\n")

    (problem,) = problems_of(toy / "methods/toy-climate", read_method)

    assert problem.item == "method.yaml: factors"
