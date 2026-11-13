"""Linking each line of a workbook: to the release, to the workbook's own activities, to a pinned
study, and to the elementary flows of nature. And what each line becomes: its role, its amount and
its unit."""

import json
from pathlib import Path

import pytest

from headwater.io.excel import ExcelImport, Outcome, Status, import_excel
from headwater.io.excel.write import MANIFEST
from headwater.model import FlowKind, InvalidDataError, Role
from headwater.release import Release
from headwater.study import read_database

from .conftest import Sheet, Workbooks, block, database, line

TOY_AMOUNT = 1.0125
"""A toy production amount other than 1."""


def run(
    workbooks: Workbooks,
    release: Release,
    out: Path,
    *activities: Sheet,
    mapping: Path | None = None,
) -> ExcelImport:
    rows = [*database(), *(row for activity in activities for row in activity)]
    return import_excel(workbooks.write({"a": rows}), release, out, mapping=mapping)


def outcome(done: ExcelImport, text: str) -> Outcome:
    """The outcome of the one line, not a reference line, whose description starts with `text`."""
    (found,) = [
        o for o in done.report.outcomes if o.line.startswith(text) and o.code != "reference"
    ]
    return found


def exchanges(done: ExcelImport, process: str):  # noqa: ANN201
    return next(p for p in read_database(done.folder).processes if p.name == process).exchanges


# --- by name, reference product and location --------------------------------------------------


def test_a_line_links_by_exact_name_reference_product_and_location(
    workbooks: Workbooks, mini_release: Release, out: Path
) -> None:
    done = run(
        workbooks,
        mini_release,
        out,
        block("widget", line("market for steel", 2, "kilogram", "RER", product="steel")),
    )
    found = outcome(done, "market for steel")
    assert (found.status, found.code) == (Status.mapped, "release")
    _, steel = exchanges(done, "widget")
    assert (steel.role, steel.supplier_id, steel.flow_id) == (
        Role.input,
        "mini:steel-rer",
        "mini:steel",
    )
    assert (steel.amount, steel.unit, steel.flow_name) == (2.0, "kg", "steel")


def test_the_same_name_and_location_with_another_product_is_another_process(
    workbooks: Workbooks, mini_release: Release, out: Path
) -> None:
    done = run(
        workbooks,
        mini_release,
        out,
        block(
            "widget",
            line("market for steel", 1, "kilogram", "GLO", product="steel, low-alloyed"),
            line("market for steel", 1, "kilogram", "GLO", product="steel"),
        ),
    )
    assert exchanges(done, "widget")[1].supplier_id == "mini:alloy-glo"
    found = outcome(done, "market for steel | steel | GLO")
    assert found.code == "not-in-release"
    assert any("mini:alloy-glo" in s for s in found.suggestions)  # same name: shown, not used


def test_a_line_with_no_reference_product_links_only_when_one_process_has_the_rest(
    workbooks: Workbooks, mini_release: Release, out: Path
) -> None:
    done = run(
        workbooks,
        mini_release,
        out,
        block(
            "widget",
            line("market for steel", 1, "kilogram", "RER", product=None),
            line("market for steel", 1, "kilogram", "GLO", product=None),
            line("market for steel", 1, "kilogram", None, product=None),
        ),
    )
    a, b, c = (o for o in done.report.outcomes if o.line.startswith("market for steel"))
    assert (a.status, a.code) == (Status.mapped, "release-partial")
    assert (b.status, b.code) == (Status.mapped, "release-partial")  # GLO: only the alloy one
    assert (c.status, c.code) == (Status.unmapped, "ambiguous-release")
    assert len(c.suggestions) == 3
    assert any("mini:steel-rer" in s for s in c.suggestions)
    # What was left out is in the import's record, not in the modeller's comment.
    rer, glo, _ = exchanges(done, "widget")[1:]
    assert rer.comment is None and glo.comment is None
    assert glo.supplier_id == "mini:alloy-glo"
    record = json.loads((out / MANIFEST).read_text())
    assert [(n["line"], n["supplier"], n["note"]) for n in record["notes"]] == [
        (
            rer.line_id,
            "mini:steel-rer",
            "linked without the workbook's reference product: the only process that fits",
        ),
        (
            glo.line_id,
            "mini:alloy-glo",
            "linked without the workbook's reference product: the only process that fits",
        ),
    ]
    assert done.report.mapped_by["release-partial"] == 2


def test_nothing_matches_by_lower_case_or_by_a_close_name(
    workbooks: Workbooks, mini_release: Release, out: Path
) -> None:
    done = run(
        workbooks,
        mini_release,
        out,
        block(
            "widget",
            line("Market for steel", 1, "kilogram", "RER", product="steel"),
            line("market for steel ", 1, "kilogram", "RER", product="steels"),
        ),
    )
    first, second = (o for o in done.report.outcomes if "teel" in o.line)
    assert first.code == "not-in-release"
    assert second.code == "not-in-release"  # spaces around a cell are stripped, the product is not
    assert any("mini:steel-rer" in s for s in first.suggestions)


# --- roles and signs --------------------------------------------------------------------------


def test_a_negative_amount_to_a_treatment_is_waste_sent_for_treatment(
    workbooks: Workbooks, mini_release: Release, out: Path
) -> None:
    done = run(
        workbooks,
        mini_release,
        out,
        block(
            "widget",
            line("treatment of wastewater", -0.5, "cubic meter", "RoW", product="wastewater"),
        ),
    )
    assert outcome(done, "treatment of wastewater").status is Status.mapped
    waste = exchanges(done, "widget")[1]
    assert (waste.role, waste.amount, waste.unit) == (Role.waste_to_treatment, 0.5, "m3")
    assert waste.supplier_id == "mini:wastewater-row"


def test_a_positive_amount_to_a_treatment_is_not_guessed(
    workbooks: Workbooks, mini_release: Release, out: Path
) -> None:
    done = run(
        workbooks,
        mini_release,
        out,
        block(
            "widget",
            line("treatment of wastewater", 0.5, "cubic meter", "RoW", product="wastewater"),
        ),
    )
    assert outcome(done, "treatment of wastewater").code == "positive-to-treatment"


def test_a_negative_amount_to_a_product_is_a_credit_that_is_not_guessed(
    workbooks: Workbooks, mini_release: Release, out: Path
) -> None:
    done = run(
        workbooks,
        mini_release,
        out,
        block("widget", line("market for steel", -1, "kilogram", "RER", product="steel")),
    )
    assert outcome(done, "market for steel").code == "negative-to-product"
    assert exchanges(done, "widget")[1].supplier_id is None  # written, with no supplier


def test_a_substitution_is_an_avoided_product(
    workbooks: Workbooks, mini_release: Release, out: Path
) -> None:
    done = run(
        workbooks,
        mini_release,
        out,
        block(
            "widget",
            line("market for steel", 1, "kilogram", "RER", "substitution", product="steel"),
            line("market for steel", -1, "kilogram", "RER", "substitution", product="steel"),
            line(
                "treatment of wastewater",
                1,
                "cubic meter",
                "RoW",
                "substitution",
                product="wastewater",
            ),
        ),
    )
    ok, negative, waste = (o for o in done.report.outcomes if o.line != "widget | widget | GLO")
    assert ok.status is Status.mapped
    assert negative.code == "negative-substitution"
    assert waste.code == "role-flow-kind"
    assert exchanges(done, "widget")[1].role is Role.avoided_product


def test_a_line_with_a_zero_amount_to_a_treatment_is_waste_and_to_a_product_is_an_input(
    workbooks: Workbooks, mini_release: Release, out: Path
) -> None:
    done = run(
        workbooks,
        mini_release,
        out,
        block(
            "widget",
            line("treatment of wastewater", 0, "cubic meter", "RoW", product="wastewater"),
            line("market for steel", 0, "kilogram", "RER", product="steel"),
        ),
    )
    _, waste, steel = exchanges(done, "widget")
    assert (waste.role, steel.role) == (Role.waste_to_treatment, Role.input)


# --- units ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("written", "unit"),
    [("gram", "g"), ("kilogram", "kg"), ("kg", "kg"), ("ton", "t"), ("KG", "kg"), ("pound", None)],
)
def test_a_unit_of_the_same_quantity_is_kept_as_written_and_converted_by_headwater(
    written: str, unit: str | None, workbooks: Workbooks, mini_release: Release, out: Path
) -> None:
    done = run(
        workbooks,
        mini_release,
        out,
        block("widget", line("market for sand", 250, written, "GLO", product="sand")),
    )
    found = outcome(done, "market for sand")
    if unit is None:
        assert found.code == "unknown-unit"
    else:
        assert found.status is Status.mapped
        assert exchanges(done, "widget")[1].unit == unit


def test_energy_in_kilowatt_hours_for_a_flow_counted_in_megajoules_converts(
    workbooks: Workbooks, mini_release: Release, out: Path
) -> None:
    done = run(
        workbooks,
        mini_release,
        out,
        block(
            "widget",
            line(
                "market for heat", 2, "kilowatt hour", "GLO", product="heat, district or industrial"
            ),
        ),
    )
    assert outcome(done, "market for heat").status is Status.mapped
    assert done.validation is not None and done.validation.errors == ()


def test_mass_for_a_flow_counted_in_energy_is_not_converted(
    workbooks: Workbooks, mini_release: Release, out: Path
) -> None:
    done = run(
        workbooks,
        mini_release,
        out,
        block(
            "widget",
            line("market for heat", 2, "kilogram", "GLO", product="heat, district or industrial"),
        ),
    )
    found = outcome(done, "market for heat")
    assert found.code == "unit-mismatch"
    assert "energy" in found.message and "mass" in found.message


# --- the workbook's own activities ------------------------------------------------------------


def test_a_line_to_another_activity_of_the_workbook_links_to_its_process(
    workbooks: Workbooks, mini_release: Release, out: Path
) -> None:
    done = run(
        workbooks,
        mini_release,
        out,
        block("widget", line("gear", 3, "unit", "GLO", database="mine", product="gear")),
        block(
            "gear",
            line("market for steel", 0.2, "kilogram", "RER", product="steel"),
            unit="unit",
        ),
    )
    assert outcome(done, "gear |").code == "workbook"
    widget = exchanges(done, "widget")[1]
    assert (widget.role, widget.supplier_id, widget.flow_id) == (
        Role.input,
        "mine:gear-glo",
        "mine:gear-glo",
    )
    assert widget.unit == "unit"
    gear = next(f for f in done.conversion.flows if f.id == "mine:gear-glo")
    assert (gear.name, gear.kind, gear.unit) == ("gear", FlowKind.product, "unit")


def test_a_line_to_an_activity_the_workbook_does_not_have_is_reported(
    workbooks: Workbooks, mini_release: Release, out: Path
) -> None:
    done = run(
        workbooks,
        mini_release,
        out,
        block("widget", line("gear", 3, "unit", "GLO", database="mine", product="gear")),
    )
    assert outcome(done, "gear |").code == "not-in-workbook"


def test_a_line_with_no_database_is_looked_up_in_the_workbook_then_in_the_release(
    workbooks: Workbooks, mini_release: Release, out: Path
) -> None:
    done = run(
        workbooks,
        mini_release,
        out,
        block(
            "widget",
            line("gear", 3, "unit", "GLO", database=None, product="gear"),
            line("market for sand", 1, "kilogram", "GLO", database=None, product="sand"),
        ),
        block("gear", unit="unit"),
    )
    assert outcome(done, "gear |").code == "workbook"
    assert outcome(done, "market for sand").code == "release"
    assert {d.name for d in done.report.databases} == {"(none)"}


def test_a_line_with_no_database_that_two_databases_have_is_not_guessed(
    workbooks: Workbooks, mini_release: Release, out: Path
) -> None:
    done = run(
        workbooks,
        mini_release,
        out,
        block(
            "widget", line("market for sand", 1, "kilogram", "GLO", database=None, product="sand")
        ),
        block("market for sand", product="sand"),
    )
    found = outcome(done, "market for sand | sand | GLO")
    assert found.code == "ambiguous-database"
    assert "mini:sand-glo" in " ".join(found.suggestions)


def test_two_activities_with_the_same_name_make_a_line_to_it_ambiguous(
    workbooks: Workbooks, mini_release: Release, out: Path
) -> None:
    done = run(
        workbooks,
        mini_release,
        out,
        block("widget", line("gear", 1, "unit", None, database="mine", product="gear")),
        block("gear", location="GLO", unit="unit"),
        block("gear", location="RER", unit="unit"),
    )
    assert outcome(done, "gear |").code == "ambiguous-workbook"
    ids = sorted(p.id for p in done.conversion.processes)
    assert ids == ["mine:gear-glo", "mine:gear-rer", "mine:widget-glo"]
    assert not any("mine:gear-glo" in n.message for n in done.report.notes)  # its own id


def test_an_activity_that_buys_its_own_product_uses_its_own_product(
    workbooks: Workbooks, mini_release: Release, out: Path
) -> None:
    done = run(
        workbooks,
        mini_release,
        out,
        block(
            "market",
            line("market", 0.02, "kilogram", "GLO", database="mine", product="market"),
            line("market for steel", 1, "kilogram", "RER", product="steel"),
        ),
    )
    own = exchanges(done, "market")[1]
    assert (own.role, own.supplier_id) == (Role.own_use, "mine:market-glo")


def test_a_treatment_made_in_the_workbook_takes_waste_with_a_negative_amount(
    workbooks: Workbooks, mini_release: Release, out: Path
) -> None:
    done = run(
        workbooks,
        mini_release,
        out,
        block(
            "widget",
            line("burning", -0.1, "kilogram", "GLO", database="mine", product="scraps"),
        ),
        block("burning", product="scraps", amount=-1),
    )
    assert outcome(done, "burning | scraps").status is Status.mapped
    burning = next(p for p in read_database(done.folder).processes if p.name == "burning")
    reference = burning.reference_exchange
    assert (reference.role, reference.amount, reference.flow_id) == (
        Role.reference,
        1.0,
        "mine:burning-glo",
    )
    flow = next(f for f in done.conversion.flows if f.id == "mine:burning-glo")
    assert flow.kind is FlowKind.waste  # a treatment makes a negative amount of a waste
    waste = exchanges(done, "widget")[1]
    assert (waste.role, waste.amount) == (Role.waste_to_treatment, 0.1)


# --- elementary flows -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "categories", "unit", "flow", "role", "code"),
    [
        (
            "Carbon dioxide, fossil",
            "air::urban air close to ground",
            "kilogram",
            "mini:co2",
            Role.emission,
            "biosphere",
        ),
        (
            "Water",
            "water",
            "cubic meter",
            "mini:water-ground",
            Role.emission,
            "biosphere-unspecified",
        ),
        (
            "Water",
            "air::urban air close to ground",
            "cubic meter",
            "mini:water-air",
            Role.emission,
            "biosphere",
        ),
        (
            "Carbon dioxide, in air",
            "natural resource::in air",
            "kilogram",
            "mini:co2-in-air",
            Role.resource,
            "biosphere",
        ),
        ("Cadmium", "soil::agricultural", "gram", "mini:cadmium", Role.emission, "biosphere"),
    ],
)
def test_a_flow_of_nature_links_by_name_and_categories(
    name: str,
    categories: str,
    unit: str,
    flow: str,
    role: Role,
    code: str,
    workbooks: Workbooks,
    mini_release: Release,
    out: Path,
) -> None:
    done = run(
        workbooks,
        mini_release,
        out,
        block(
            "widget",
            line(name, 0.1, unit, None, "biosphere", "mini-biosphere", categories=categories),
        ),
    )
    found = outcome(done, name)
    assert (found.status, found.code) == (Status.mapped, code)
    line_ = exchanges(done, "widget")[1]
    assert (line_.flow_id, line_.role, line_.amount) == (flow, role, 0.1)


def test_an_amount_to_nature_may_be_negative(
    workbooks: Workbooks, mini_release: Release, out: Path
) -> None:
    done = run(
        workbooks,
        mini_release,
        out,
        block(
            "widget",
            line(
                "Carbon dioxide, in air",
                -0.5,
                "kilogram",
                None,
                "biosphere",
                "b",
                categories="natural resource::in air",
            ),
        ),
    )
    assert exchanges(done, "widget")[1].amount == -0.5


@pytest.mark.parametrize(
    ("name", "categories", "code"),
    [
        ("Carbon dioxide, fossil", "water", "biosphere-not-found"),
        ("carbon dioxide, fossil", "air::urban air close to ground", "biosphere-not-found"),
        ("Occupation", "economic", "biosphere-role"),
        ("Water", None, "no-categories"),
    ],
)
def test_a_flow_of_nature_that_does_not_link_is_reported(
    name: str,
    categories: str | None,
    code: str,
    workbooks: Workbooks,
    mini_release: Release,
    out: Path,
) -> None:
    unit = "square meter" if name == "Occupation" else "kilogram"
    done = run(
        workbooks,
        mini_release,
        out,
        block("widget", line(name, 0.1, unit, None, "biosphere", "b", categories=categories)),
    )
    assert outcome(done, name).code == code


def test_a_flow_of_nature_not_found_names_the_same_name_elsewhere(
    workbooks: Workbooks, mini_release: Release, out: Path
) -> None:
    done = run(
        workbooks,
        mini_release,
        out,
        block("widget", line("Water", 1, "cubic meter", None, "biosphere", "b", categories="soil")),
    )
    found = outcome(done, "Water")
    assert found.code == "biosphere-not-found"
    assert any("mini:water-ground" in s for s in found.suggestions)


def test_a_flow_without_a_factor_fails_validation_so_the_import_is_not_clean(
    workbooks: Workbooks, mini_release: Release, out: Path
) -> None:
    done = run(
        workbooks,
        mini_release,
        out,
        block(
            "widget",
            line("Cadmium", 1, "gram", None, "biosphere", "b", categories="soil::agricultural"),
        ),
    )
    assert done.report.clean  # every line mapped
    assert done.validation is not None
    assert [p.code for p in done.validation.errors] == ["no-factor"]
    assert [p.code for p in done.blocking()] == ["no-factor"]
    assert not done.clean


# --- the whole activity -----------------------------------------------------------------------


def test_an_activity_with_two_production_lines_is_not_converted_and_every_row_is_reported(
    workbooks: Workbooks, mini_release: Release, out: Path
) -> None:
    both = block("twins", line("twin", 1, "kilogram", "GLO", "production", "mine", "twin"))
    done = run(
        workbooks,
        mini_release,
        out,
        block("widget", line("twins", 1, "kilogram", "GLO", database="mine", product="twins")),
        both,
    )
    assert [p.name for p in done.conversion.processes] == ["widget"]
    (skipped,) = done.report.skipped_activities
    assert skipped.code == "co-products" and skipped.activity == "twins"
    twins = [o for o in done.report.outcomes if o.activity == "twins"]
    assert len(twins) == 2 and {o.code for o in twins} == {"activity-not-converted"}
    (bought,) = [
        o for o in done.report.outcomes if o.activity == "widget" and o.code != "reference"
    ]
    assert bought.code == "activity-not-converted"
    assert done.report.exchange_rows == len(done.report.outcomes)
    assert not done.report.clean


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"unit": "furlong"}, "unknown-unit"),
        ({"amount": 0}, "no-amount"),
        ({"amount": "lots"}, "bad-amount"),
    ],
)
def test_an_activity_that_cannot_make_its_product_is_skipped_and_reported(
    kwargs: dict[str, object], code: str, workbooks: Workbooks, mini_release: Release, out: Path
) -> None:
    done = run(
        workbooks,
        mini_release,
        out,
        block("widget", line("market for sand", 1, "kilogram", "GLO", product="sand")),
        block("broken", **kwargs),  # type: ignore[arg-type]
    )
    (skipped,) = done.report.skipped_activities
    assert skipped.code == code and skipped.activity == "broken"
    assert [p.name for p in done.conversion.processes] == ["widget"]


def test_a_workbook_where_no_activity_can_be_converted_is_refused(
    workbooks: Workbooks, mini_release: Release, out: Path
) -> None:
    with pytest.raises(InvalidDataError, match="unknown-unit|furlong"):
        run(workbooks, mini_release, out, block("broken", unit="furlong"))
    assert not out.exists()


def test_a_production_amount_other_than_one_is_the_amount_of_the_reference_line(
    workbooks: Workbooks, mini_release: Release, out: Path
) -> None:
    done = run(
        workbooks,
        mini_release,
        out,
        block(
            "dyed",
            line("market for sand", 0.5, "kilogram", "GLO", product="sand"),
            amount=TOY_AMOUNT,
        ),
    )
    assert exchanges(done, "dyed")[0].amount == TOY_AMOUNT


def test_an_activity_without_a_production_line_makes_its_reference_from_its_metadata(
    workbooks: Workbooks, mini_release: Release, out: Path
) -> None:
    rows: Sheet = [
        ["Activity", "bare"],
        ["production amount", 2],
        ["reference product", "bare"],
        ["location", "GLO"],
        ["unit", "kilogram"],
        ["Exchanges"],
        list(block("x")[6]),
        line("market for sand", 1, "kilogram", "GLO", product="sand"),
    ]
    book = workbooks.write({"a": [*database(), *rows]})
    done = import_excel(book, mini_release, out)
    reference, sand = exchanges(done, "bare")
    assert (reference.role, reference.amount) == (Role.reference, 2.0)
    assert sand.supplier_id == "mini:sand-glo"
    assert done.report.exchange_rows == len(done.report.outcomes) == 1  # the reference is no row
    assert any("no production exchange" in n.message for n in done.report.notes)


def test_a_production_amount_row_that_disagrees_with_the_exchange_is_noted(
    workbooks: Workbooks, mini_release: Release, out: Path
) -> None:
    activity = block("dyed", amount=TOY_AMOUNT)
    activity[1] = ["production amount", 1]
    done = run(workbooks, mini_release, out, activity)
    assert exchanges(done, "dyed")[0].amount == TOY_AMOUNT
    assert any(
        "production amount" in n.message and "exchange" in n.message for n in done.report.notes
    )


def test_line_ids_have_one_width_in_a_process_so_they_sort_as_they_read(
    workbooks: Workbooks, mini_release: Release, out: Path
) -> None:
    many = [line("market for sand", 0.01, "kilogram", "GLO", product="sand") for _ in range(120)]
    done = run(workbooks, mini_release, out, block("big", *many), block("small", many[0]))
    big = [e.line_id for e in exchanges(done, "big")]
    assert big[:3] == ["e001", "e002", "e003"] and big[-1] == "e121"
    assert big == sorted(big)
    assert [e.line_id for e in exchanges(done, "small")] == ["e01", "e02"]


def test_a_zero_amount_of_waste_is_written_as_zero_not_minus_zero(
    workbooks: Workbooks, mini_release: Release, out: Path
) -> None:
    done = run(
        workbooks,
        mini_release,
        out,
        block(
            "widget", line("treatment of wastewater", 0, "cubic meter", "RoW", product="wastewater")
        ),
    )
    text = (done.folder / "exchanges.csv").read_text()
    assert "-0.0" not in text
    assert ",waste_to_treatment,mini:wastewater,0," in text


@pytest.mark.parametrize("amount", ["1_5", "1,5", "lots", "nan", "inf"])
def test_a_text_amount_that_is_not_a_plain_number_is_refused(
    amount: str, workbooks: Workbooks, mini_release: Release, out: Path
) -> None:
    done = run(
        workbooks,
        mini_release,
        out,
        block("widget", line("market for sand", amount, "kilogram", "GLO", product="sand")),
    )
    assert outcome(done, "market for sand").code == "bad-amount"


def test_a_credit_is_reported_with_how_to_fix_it(
    workbooks: Workbooks, mini_release: Release, out: Path
) -> None:
    done = run(
        workbooks,
        mini_release,
        out,
        block("widget", line("market for steel", -1, "kilogram", "RER", product="steel")),
    )
    message = outcome(done, "market for steel").message
    assert "sign never decides a role" in message
    assert "`substitution` in the workbook and import again" in message
    assert "avoided_product in the CSV" in message


def test_lines_linked_by_name_only_are_counted_apart_and_listed_to_review(
    workbooks: Workbooks, mini_release: Release, out: Path
) -> None:
    from headwater.io.excel.report import format_report

    done = run(
        workbooks,
        mini_release,
        out,
        block(
            "widget",
            line("market for steel", 1, "kilogram", "RER", product=None),
            line("market for steel", 1, "kilogram", "RER", product="steel"),
            line("market for sand", 1, "kilogram", None, product=None),
        ),
    )
    by_name = done.report.linked_by_name
    assert [o.line for o in by_name] == ["market for steel | RER", "market for sand"]
    assert by_name[0].by_name is not None and by_name[0].by_name.startswith("mini:steel-rer:")
    assert "without the workbook's reference product" in by_name[0].by_name
    assert done.report.count(Status.mapped) == 4  # still mapped, and clean
    assert done.report.clean
    text = format_report(done.report)
    assert "linked by name only, to review: 2" in text
    assert "Linked by name only: 2 lines, 2 to review" in text
    assert "linked to mini:sand-glo:" in text and "reference product and location" in text
