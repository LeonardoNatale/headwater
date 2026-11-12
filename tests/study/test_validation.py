"""Exit tests: one test per validation error, each asserting the error names the item.

Each test breaks one thing in a copy of the toy files. The error code is the stable part
of an error; the message must also name the process, the line and the bad value.
"""

import shutil
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from headwater.model import InvalidDataError, Problem, Report
from headwater.study import build_catalog, read_database, read_method, read_process_folder, validate
from headwater.study.validate import validate_database
from tests.conftest import toy_database

Edit = Callable[[Path, str, str], None]


def validate_toy(toy: Path, variant: str = "tshirt") -> Report:
    background = read_process_folder(toy / "background", namespace="toy")
    method = read_method(toy / "methods" / "toy-climate")
    database = read_database(toy_database(toy, variant))
    return validate(database, background=background, method=method)


def only(problems: tuple[Problem, ...], code: str) -> Problem:
    found = [p for p in problems if p.code == code]
    assert len(found) == 1, f"expected one {code!r} problem, got {[str(p) for p in problems]}"
    return found[0]


def test_unknown_unit(toy: Path, edit: Edit) -> None:
    edit(toy / "tshirt/tshirt/processes/tshirt.csv", "0.25,kg,", "0.25,kgg,")

    report = validate_toy(toy)

    problem = only(report.errors, "unknown-unit")
    assert problem.item == "tshirt:tshirt line e02"
    assert "'kgg'" in problem.message
    assert "toy:cotton-fabric" in problem.message
    with pytest.raises(InvalidDataError) as raised:
        report.raise_if_errors()
    assert "tshirt:tshirt line e02" in str(raised.value)
    assert "'kgg'" in str(raised.value)


def test_unit_of_the_wrong_quantity_with_no_flow_property(toy: Path, edit: Edit) -> None:
    # Cotton fabric is counted in kg (mass). 0.25 kWh is energy, and the flow has no property
    # that says how many kWh are in a kg.
    edit(toy / "tshirt/tshirt/processes/tshirt.csv", "0.25,kg,", "0.25,kWh,")

    report = validate_toy(toy)

    problem = only(report.errors, "unit-quantity-mismatch")
    assert problem.item == "tshirt:tshirt line e02"
    for part in ("toy:cotton-fabric", "'kWh'", "energy", "'kg'", "mass"):
        assert part in problem.message
    with pytest.raises(InvalidDataError, match="tshirt:tshirt line e02"):
        report.raise_if_errors()


def test_a_flow_property_makes_the_other_quantity_valid(toy: Path, edit: Edit) -> None:
    # With a net heating value of 18 MJ per kg, 4.5 MJ of cotton fabric is 0.25 kg.
    edit(toy / "tshirt/tshirt/processes/tshirt.csv", "0.25,kg,", "4.5,MJ,")
    (toy / "tshirt/tshirt/flow_properties.csv").write_text(
        "flow,property,value,unit\ntoy:cotton-fabric,heating_value_net,18,MJ\n"
    )

    report = validate_toy(toy)

    assert report.problems == ()


def test_input_whose_supplier_does_not_exist(toy: Path, edit: Edit) -> None:
    edit(
        toy / "tshirt/tshirt/processes/tshirt.csv",
        "toy:fabric-production",
        "toy:fabric-productoin",
    )

    report = validate_toy(toy)

    problem = only(report.errors, "supplier-not-found")
    assert problem.item == "tshirt:tshirt line e02"
    assert "'toy:fabric-productoin'" in problem.message
    assert "toy:fabric-production" in problem.message  # the closest existing id is suggested
    with pytest.raises(InvalidDataError, match="toy:fabric-productoin"):
        report.raise_if_errors()


def test_elementary_flow_with_no_factor_in_the_method_is_a_warning(toy: Path, edit: Edit) -> None:
    # Nitrous oxide is emitted by electricity production, but toy-climate has no factor for it.
    background = toy / "background"
    with (background / "flows.csv").open("a") as f:
        f.write('toy:n2o,"Dinitrogen monoxide",elementary,kg,air,10024-97-2\n')
    with (background / "processes/electricity-production.csv").open("a") as f:
        f.write("e04,emission,toy:n2o,0.0001,kg,\n")

    report = validate_toy(toy)

    assert report.errors == ()
    problem = only(report.warnings, "no-factor")
    assert problem.item == "flow toy:n2o"
    assert "toy:electricity-production" in problem.message
    assert "toy-climate" in problem.message


def test_a_flow_with_a_factor_is_not_reported(toy: Path) -> None:
    report = validate_toy(toy)

    assert report.warnings == ()


def test_negative_amount_on_a_role_that_does_not_allow_it(toy: Path, edit: Edit) -> None:
    edit(
        toy / "tshirt/tshirt/processes/tshirt.csv",
        "e03,input,toy:electricity,0.5,",
        "e03,input,toy:electricity,-0.5,",
    )

    with pytest.raises(InvalidDataError) as raised:
        read_database(toy / "tshirt" / "tshirt")

    problem = only(raised.value.problems, "negative-amount")
    assert problem.item == "tshirt:tshirt line e03"
    assert "-0.5" in problem.message
    assert "input" in problem.message
    assert problem.location == "processes/tshirt.csv:4"


def test_emission_and_resource_may_be_negative(toy: Path, edit: Edit) -> None:
    # A crop takes CO2 from the air: a negative emission. Same for a negative resource.
    edit(
        toy / "background/processes/fabric-production.csv",
        "e03,emission,toy:co2,2.0",
        "e03,emission,toy:co2,-2.0",
    )
    with (toy / "background/processes/fabric-production.csv").open("a") as f:
        f.write("e05,resource,toy:co2,-1,kg,\n")

    background = read_process_folder(toy / "background", namespace="toy")

    amounts = {
        e.line_id: e.amount
        for p in background.processes
        for e in p.exchanges
        if p.id == "toy:fabric-production"
    }
    assert amounts["e03"] == pytest.approx(-2.0, rel=1e-9)
    assert amounts["e05"] == pytest.approx(-1.0, rel=1e-9)


def test_input_with_no_supplier_is_its_own_error(toy: Path, edit: Edit) -> None:
    edit(toy / "tshirt/tshirt/processes/tshirt.csv", "kg,toy:fabric-production", "kg,")

    report = validate_toy(toy)

    problem = only(report.errors, "supplier-missing")
    assert problem.item == "tshirt:tshirt line e02"
    assert "toy:cotton-fabric" in problem.message


def test_supplier_that_makes_a_different_flow(toy: Path, edit: Edit) -> None:
    edit(
        toy / "tshirt/tshirt/processes/tshirt.csv",
        "kg,toy:fabric-production",
        "kg,toy:electricity-production",
    )

    problem = only(validate_toy(toy).errors, "supplier-wrong-flow")

    assert problem.item == "tshirt:tshirt line e02"
    assert "toy:electricity-production" in problem.message
    assert "toy:electricity" in problem.message
    assert "toy:cotton-fabric" in problem.message


@pytest.mark.parametrize("role", ["emission", "resource", "recyclable"])
def test_supplier_on_a_role_that_takes_none(toy: Path, edit: Edit, role: str) -> None:
    edit(
        toy / "background/processes/fabric-production.csv",
        "e03,emission,toy:co2,2.0,kg,",
        f"e03,{role},toy:co2,2.0,kg,toy:electricity-production",
    )

    problem = only(validate_toy(toy).errors, "supplier-not-allowed")

    assert problem.item == "toy:fabric-production line e03"
    assert "'toy:electricity-production'" in problem.message
    assert role in problem.message


def test_supplier_on_the_reference_line(toy: Path, edit: Edit) -> None:
    edit(
        toy / "tshirt/tshirt/processes/tshirt.csv",
        "e01,reference,tshirt:tshirt,1,unit,",
        "e01,reference,tshirt:tshirt,1,unit,toy:fabric-production",
    )

    problem = only(validate_toy(toy).errors, "supplier-not-allowed")

    assert problem.item == "tshirt:tshirt line e01"


def test_unknown_flow(toy: Path, edit: Edit) -> None:
    edit(toy / "tshirt/tshirt/processes/tshirt.csv", "toy:electricity,0.5", "toy:electrcity,0.5")

    problem = only(validate_toy(toy).errors, "unknown-flow")

    assert problem.item == "tshirt:tshirt line e03"
    assert "'toy:electrcity'" in problem.message
    assert "toy:electricity" in problem.message


def test_emission_on_a_product_flow(toy: Path, edit: Edit) -> None:
    edit(
        toy / "background/processes/fabric-production.csv",
        "e03,emission,toy:co2",
        "e03,emission,toy:electricity",
    )

    problem = only(validate_toy(toy).errors, "role-flow-kind")

    assert problem.item == "toy:fabric-production line e03"
    assert "emission" in problem.message
    assert "toy:electricity" in problem.message
    assert "product" in problem.message


def test_flow_name_next_to_the_id_must_match(toy: Path) -> None:
    (toy / "tshirt/tshirt/processes/tshirt.csv").write_text(
        "line,role,flow,flow_name,amount,unit,supplier\n"
        "e01,reference,tshirt:tshirt,T-shirt,1,unit,\n"
        "e02,input,toy:cotton-fabric,cotton fabric,0.25,kg,toy:fabric-production\n"
        "e03,input,toy:electricity,natural gas,0.5,kWh,toy:electricity-production\n"
    )

    problem = only(validate_toy(toy).errors, "flow-name-mismatch")

    assert problem.item == "tshirt:tshirt line e03"
    assert "'natural gas'" in problem.message
    assert "'electricity'" in problem.message
    assert "toy:electricity" in problem.message


def test_a_database_asks_for_another_method(toy: Path, edit: Edit) -> None:
    edit(toy / "tshirt/tshirt/database.yaml", "methods: [toy-climate]", "methods: [toy-climat]")

    problem = only(validate_toy(toy).errors, "method-mismatch")

    assert "'toy-climat'" in problem.message
    assert "'toy-climate'" in problem.message


def test_all_problems_are_listed_together(toy: Path, edit: Edit) -> None:
    edit(toy / "tshirt/tshirt/processes/tshirt.csv", "0.25,kg,", "0.25,kgg,")
    edit(
        toy / "tshirt/tshirt/processes/tshirt.csv",
        "toy:electricity-production",
        "toy:electricity-prod",
    )

    report = validate_toy(toy)

    assert sorted(p.code for p in report.errors) == ["supplier-not-found", "unknown-unit"]
    with pytest.raises(InvalidDataError) as raised:
        report.raise_if_errors()
    assert "'kgg'" in str(raised.value)
    assert "'toy:electricity-prod'" in str(raised.value)


def test_a_database_process_may_not_reuse_a_background_process_id(toy: Path) -> None:
    # Read the background under the database's namespace, so both have tshirt:fabric-production.
    (toy / "tshirt/tshirt/processes/fabric-production.csv").write_text(
        "line,role,flow,amount,unit,supplier\ne01,reference,toy:cotton-fabric,1,kg,\n"
    )
    shutil.copy(
        toy / "background/processes/fabric-production.yaml",
        toy / "tshirt/tshirt/processes/fabric-production.yaml",
    )
    background = read_process_folder(toy / "background", namespace="tshirt")
    method = read_method(toy / "methods" / "toy-climate")
    database = read_database(toy / "tshirt" / "tshirt")

    problem = only(
        validate(database, background=background, method=method).errors, "duplicate-process"
    )

    assert "tshirt:fabric-production" in problem.item


def test_elementary_flow_with_no_factor_in_a_database_process_is_an_error(toy: Path) -> None:
    # The database has no no_factor.csv row for it: tests/study/test_no_factor_file.py has the rest.
    with (toy / "tshirt/tshirt/flows.csv").open("a") as f:
        f.write("toy:n2o,Dinitrogen monoxide,elementary,kg,air,\n")
    with (toy / "tshirt/tshirt/processes/tshirt.csv").open("a") as f:
        f.write("e04,emission,toy:n2o,0.0001,kg,\n")

    problem = only(validate_toy(toy).errors, "no-factor")

    assert problem.item == "flow toy:n2o"
    assert "tshirt:tshirt" in problem.message


def test_a_flow_used_by_two_processes_is_reported_once_with_both(toy: Path) -> None:
    with (toy / "background/flows.csv").open("a") as f:
        f.write("toy:n2o,Dinitrogen monoxide,elementary,kg,air,\n")
    for name in ("electricity-production", "fabric-production"):
        with (toy / f"background/processes/{name}.csv").open("a") as f:
            f.write("e09,emission,toy:n2o,0.0001,kg,\n")

    problem = only(validate_toy(toy).warnings, "no-factor")

    assert "toy:electricity-production" in problem.message
    assert "toy:fabric-production" in problem.message


def test_flow_with_an_unknown_unit_is_reported_once(toy: Path, edit: Edit) -> None:
    edit(
        toy / "background/flows.csv",
        "toy:cotton-fabric,cotton fabric,product,kg",
        "toy:cotton-fabric,cotton fabric,product,kgg",
    )

    problem = only(validate_toy(toy).errors, "unknown-unit")

    assert problem.item == "flow toy:cotton-fabric"
    assert "'kgg'" in problem.message


def test_flow_property_of_a_flow_that_does_not_exist(toy: Path) -> None:
    (toy / "background/flow_properties.csv").write_text(
        "flow,property,value,unit\ntoy:cotton-fabrik,heating_value_net,18,MJ\n"
    )

    problem = only(validate_toy(toy).errors, "unknown-flow")

    assert "toy:cotton-fabrik" in problem.item
    assert "toy:cotton-fabric" in problem.message


def test_flow_property_with_an_unknown_unit(toy: Path) -> None:
    (toy / "background/flow_properties.csv").write_text(
        "flow,property,value,unit\ntoy:cotton-fabric,heating_value_net,18,MJ/kg\n"
    )

    problem = only(validate_toy(toy).errors, "unknown-unit")

    assert "toy:cotton-fabric" in problem.item
    assert "'MJ/kg'" in problem.message


def test_a_database_flow_that_disagrees_with_the_background(toy: Path) -> None:
    with (toy / "tshirt/tshirt/flows.csv").open("a") as f:
        f.write("toy:electricity,electricity,product,MJ,,\n")

    report = validate_toy(toy)

    assert [p.code for p in report.problems] == ["duplicate-flow"]
    assert report.problems[0].item == "flow toy:electricity"


def test_a_database_may_repeat_a_background_flow_exactly(toy: Path) -> None:
    with (toy / "tshirt/tshirt/flows.csv").open("a") as f:
        f.write("toy:electricity,electricity,product,kWh,,\n")

    assert validate_toy(toy).problems == ()


def test_a_unit_from_units_csv_is_known_to_validation(toy: Path, edit: Edit) -> None:
    (toy / "tshirt/tshirt/units.csv").write_text(
        "unit,quantity,factor_to_reference\nlb,mass,0.45359237\n"
    )
    edit(toy / "tshirt/tshirt/processes/tshirt.csv", "0.25,kg,", "0.5511,lb,")

    assert validate_toy(toy).problems == ()


def test_background_units_are_known_to_the_database(toy: Path, edit: Edit) -> None:
    (toy / "background/units.csv").write_text(
        "unit,quantity,factor_to_reference\nlb,mass,0.45359237\n"
    )
    edit(toy / "tshirt/tshirt/processes/tshirt.csv", "0.25,kg,", "0.5511,lb,")

    assert validate_toy(toy).problems == ()


def test_two_folders_that_define_a_unit_two_ways(toy: Path) -> None:
    (toy / "tshirt/tshirt/units.csv").write_text(
        "unit,quantity,factor_to_reference\nlb,mass,0.45359237\n"
    )
    (toy / "background/units.csv").write_text("unit,quantity,factor_to_reference\nlb,mass,0.5\n")

    report = validate_toy(toy)

    assert [p.code for p in report.problems] == ["unit-conflict"]
    assert report.problems[0].item == "unit 'lb'"


def test_a_mistake_in_the_background_is_found_when_validating_the_database(
    toy: Path, edit: Edit
) -> None:
    edit(toy / "background/processes/fabric-production.csv", "3,kWh", "3,kWhh")

    problem = only(validate_toy(toy).errors, "unknown-unit")

    assert problem.item == "toy:fabric-production line e02"


def test_validating_the_background_alone(toy: Path, edit: Edit) -> None:
    edit(
        toy / "background/processes/fabric-production.csv",
        "toy:electricity-production",
        "toy:nothing",
    )
    background = read_process_folder(toy / "background", namespace="toy")

    report = validate(background, method=read_method(toy / "methods/toy-climate"))

    assert only(report.errors, "supplier-not-found").item == "toy:fabric-production line e02"


# Own use: the supplier must be the process itself


def add_second_electricity_maker(toy: Path) -> None:
    """A second maker of electricity, so that a wrong supplier exists and makes the right flow."""
    processes = toy / "background/processes"
    for suffix in (".csv", ".yaml"):
        shutil.copy(
            processes / f"electricity-production{suffix}",
            processes / f"electricity-production-2{suffix}",
        )


def test_own_use_with_another_process_as_supplier(toy: Path) -> None:
    add_second_electricity_maker(toy)
    with (toy / "background/processes/electricity-production.csv").open("a") as f:
        f.write("e04,own_use,toy:electricity,0.02,kWh,toy:electricity-production-2\n")

    report = validate_toy(toy)

    problem = only(report.errors, "own-use-supplier")
    assert [p.code for p in report.errors] == ["own-use-supplier"]
    assert problem.item == "toy:electricity-production line e04"
    assert "'toy:electricity-production'" in problem.message
    assert "'toy:electricity-production-2'" in problem.message
    with pytest.raises(InvalidDataError, match="toy:electricity-production line e04"):
        report.raise_if_errors()


def test_own_use_with_a_supplier_that_does_not_exist_is_an_own_use_error(toy: Path) -> None:
    with (toy / "background/processes/electricity-production.csv").open("a") as f:
        f.write("e04,own_use,toy:electricity,0.02,kWh,toy:nothing\n")

    report = validate_toy(toy)

    assert [p.code for p in report.errors] == ["own-use-supplier"]


def test_own_use_with_the_process_itself_as_supplier_is_valid(toy: Path) -> None:
    with (toy / "background/processes/electricity-production.csv").open("a") as f:
        f.write("e04,own_use,toy:electricity,0.02,kWh,toy:electricity-production\n")

    assert validate_toy(toy).problems == ()


# A database against a background that was already validated


def validate_database_toy(toy: Path, variant: str = "tshirt") -> Report:
    background = read_process_folder(toy / "background", namespace="toy")
    method = read_method(toy / "methods" / "toy-climate")
    database = read_database(toy_database(toy, variant))
    return validate_database(database, background, method=method)


def test_validating_a_database_does_not_check_the_background_again(toy: Path, edit: Edit) -> None:
    edit(toy / "background/processes/fabric-production.csv", "3,kWh", "3,kWhh")

    assert only(validate_toy(toy).errors, "unknown-unit").item == "toy:fabric-production line e02"
    assert validate_database_toy(toy).problems == ()


def test_a_database_is_still_checked_against_what_it_links_to(toy: Path, edit: Edit) -> None:
    edit(
        toy / "tshirt/tshirt/processes/tshirt.csv",
        "toy:fabric-production",
        "toy:fabric-productoin",
    )

    problem = only(validate_database_toy(toy).errors, "supplier-not-found")

    assert problem.item == "tshirt:tshirt line e02"
    assert "toy:fabric-production" in problem.message  # the closest id of the background


def test_a_database_is_checked_the_same_way_as_in_validate(toy: Path, edit: Edit) -> None:
    edit(toy / "tshirt/tshirt/processes/tshirt.csv", "0.25,kg,", "0.25,kgg,")
    edit(toy / "tshirt/tshirt/database.yaml", "methods: [toy-climate]", "methods: [toy-climat]")

    report = validate_database_toy(toy)

    assert report.problems == validate_toy(toy).problems
    assert sorted(p.code for p in report.errors) == ["method-mismatch", "unknown-unit"]


def test_a_database_process_may_not_reuse_a_background_process_id_when_validating_the_database(
    toy: Path,
) -> None:
    (toy / "tshirt/tshirt/processes/fabric-production.csv").write_text(
        "line,role,flow,amount,unit,supplier\ne01,reference,toy:cotton-fabric,1,kg,\n"
    )
    shutil.copy(
        toy / "background/processes/fabric-production.yaml",
        toy / "tshirt/tshirt/processes/fabric-production.yaml",
    )
    background = read_process_folder(toy / "background", namespace="tshirt")
    method = read_method(toy / "methods" / "toy-climate")
    database = read_database(toy / "tshirt" / "tshirt")

    problem = only(
        validate_database(database, background, method=method).errors, "duplicate-process"
    )

    assert "tshirt:fabric-production" in problem.item


def test_only_the_flows_of_the_database_are_warned_about(toy: Path) -> None:
    # The background emits a flow the method has no factor for: validate warns, because it checks
    # the background. The database does not emit it, so validating the database has nothing to say.
    with (toy / "background/flows.csv").open("a") as f:
        f.write("toy:n2o,Dinitrogen monoxide,elementary,kg,air,\n")
    with (toy / "background/processes/electricity-production.csv").open("a") as f:
        f.write("e04,emission,toy:n2o,0.0001,kg,\n")
    assert only(validate_toy(toy).warnings, "no-factor").item == "flow toy:n2o"
    assert validate_database_toy(toy).problems == ()

    # Now the database emits it too: it is an error that names the database's process, and only that
    # one, since the database has no no_factor.csv row for it.
    with (toy / "tshirt/tshirt/flows.csv").open("a") as f:
        f.write("toy:n2o,Dinitrogen monoxide,elementary,kg,air,\n")
    with (toy / "tshirt/tshirt/processes/tshirt.csv").open("a") as f:
        f.write("e04,emission,toy:n2o,0.0001,kg,\n")

    problem = only(validate_database_toy(toy).errors, "no-factor")

    assert problem.item == "flow toy:n2o"
    assert "tshirt:tshirt" in problem.message
    assert "toy:electricity-production" not in problem.message


def test_validate_database_can_take_a_catalog_that_was_already_built(
    toy: Path, edit: Edit, monkeypatch: pytest.MonkeyPatch
) -> None:
    edit(toy / "tshirt/tshirt/processes/tshirt.csv", "0.25,kg,", "0.25,kgg,")
    background = read_process_folder(toy / "background", namespace="toy")
    database = read_database(toy / "tshirt/tshirt")
    expected = validate_database(database, background)
    catalog = build_catalog(database, background)

    def refuse(*folders: object) -> None:
        raise AssertionError("the catalog was built again")

    # `headwater.study.validate` is also the name of the function the package exports.
    monkeypatch.setattr(sys.modules["headwater.study.validate"], "build_catalog", refuse)

    assert validate_database(database, background, catalog=catalog) == expected
    assert [p.code for p in expected.errors] == ["unknown-unit"]
