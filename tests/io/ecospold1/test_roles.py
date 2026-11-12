"""The role of each line, from its inputGroup or outputGroup and the sign of its amount.

| Group in the file                    | Role                                                    |
|--------------------------------------|---------------------------------------------------------|
| out0 reference product, positive     | reference                                               |
| out0 reference product, negative     | reference, its flow a waste: a treatment                |
| out2 allocated by-product            | reference (the process makes several products)          |
| out1 avoided product system          | avoided_product                                         |
| out3 waste to treatment              | waste_to_treatment, its flow a waste                    |
| out4 to nature                       | emission                                                |
| in1 in2 in3 in5, zero or positive    | input, or own_use when it buys the process's own product|
| in1 in2 in3 in5, negative            | avoided_product, or waste_to_treatment for a waste flow |
| in4 from nature                      | resource                                                |
"""

import pytest

from headwater.model import FlowKind, InvalidDataError, Role


def _codes(raised: pytest.ExceptionInfo[InvalidDataError]) -> list[str]:
    return [p.code for p in raised.value.problems]


@pytest.mark.parametrize("group", ["in1", "in2", "in3", "in5"])
def test_a_technosphere_input_buys_by_name_location_and_unit(es1, group: str) -> None:
    lines = es1.lines_of(
        es1.dataset(
            "widget",
            es1.exchange(1, "widget", "kg", 1, "out0", location="CH"),
            es1.exchange(2, "electricity", "kWh", 3, group, location="DE"),
        )
    )

    line = lines["widget", 2]

    assert line["role"] == Role.input
    assert line["amount"] == 3
    assert line["unit"] == "kWh"
    assert (line["supplier_name"], line["supplier_product"]) == (None, "electricity")
    assert (line["supplier_location"], line["supplier_unit"]) == ("DE", "kWh")
    assert line["supplier_id"] is None
    assert lines.flow("widget", 2).kind is FlowKind.product


def test_an_input_with_no_location_gives_the_name_and_unit_only(es1) -> None:
    lines = es1.lines_of(
        es1.dataset(
            "widget",
            es1.exchange(1, "widget", "kg", 1, "out0"),
            es1.exchange(2, "electricity", "kWh", 3, "in5"),
        )
    )

    line = lines["widget", 2]

    assert (line["supplier_product"], line["supplier_location"], line["supplier_unit"]) == (
        "electricity",
        None,
        "kWh",
    )


def test_a_zero_input_is_an_input(es1) -> None:
    lines = es1.lines_of(
        es1.dataset(
            "widget",
            es1.exchange(1, "widget", "kg", 1, "out0"),
            es1.exchange(2, "sand", "kg", 0, "in5", location="CH"),
        )
    )

    assert (lines["widget", 2]["role"], lines["widget", 2]["amount"]) == (Role.input, 0)


def test_the_reference_product_is_the_reference(es1) -> None:
    lines = es1.lines_of(es1.maker("widget"))

    assert lines["widget", 1]["role"] == Role.reference
    assert lines["widget", 1]["amount"] == 1
    assert lines["widget", 1]["supplier_product"] is None


def test_emissions_and_resources_keep_their_sign(es1) -> None:
    lines = es1.lines_of(
        es1.dataset(
            "widget",
            es1.exchange(1, "widget", "kg", 1, "out0"),
            es1.exchange(2, "Carbon dioxide", "kg", 2, "out4", category="air", sub_category="low"),
            es1.exchange(3, "Cadmium", "kg", -1e-6, "out4", category="soil", sub_category="x"),
            es1.exchange(4, "Water", "m3", 5, "in4", category="resource", sub_category="in water"),
            es1.exchange(5, "Carbon dioxide, in air", "kg", -2, "in4", category="resource"),
        )
    )

    assert [lines["widget", n]["role"] for n in (2, 3, 4, 5)] == [
        Role.emission,
        Role.emission,
        Role.resource,
        Role.resource,
    ]
    assert [lines["widget", n]["amount"] for n in (2, 3, 4, 5)] == [2, -1e-6, 5, -2]
    assert all(lines["widget", n]["supplier_product"] is None for n in (2, 3, 4, 5))
    assert lines.flow("widget", 2).kind is FlowKind.elementary


def test_a_negative_reference_is_a_treatment_of_a_waste(es1) -> None:
    """As in ecoSpold2: the reference is stored positive, its flow is a waste, and the distribution
    is negated with the amount."""
    lines = es1.lines_of(
        es1.dataset(
            "incineration",
            es1.exchange(
                1, "textile waste", "kg", -1, "out0", uncertaintyType=2, standardDeviation95=0.2
            ),
            es1.exchange(2, "Carbon dioxide", "kg", 1.2, "out4", category="air"),
        )
    )

    line = lines["incineration", 1]

    assert (line["role"], line["amount"]) == (Role.reference, 1)
    assert lines.flow("incineration", 1).kind is FlowKind.waste
    assert line["uncertainty"]["loc"] == 1
    assert line["uncertainty"]["scale"] == pytest.approx(0.1)


def test_a_negative_input_of_a_waste_is_waste_to_treatment(es1) -> None:
    incineration = es1.dataset(
        "incineration",
        es1.exchange(1, "textile waste", "kg", -1, "out0", location="CH"),
    )
    widget = es1.dataset(
        "widget",
        es1.exchange(1, "widget", "kg", 1, "out0"),
        es1.exchange(2, "textile waste", "kg", -0.05, "in5", location="CH"),
        number=2,
    )

    lines = es1.lines_of(incineration, widget)

    line = lines["widget", 2]
    assert (line["role"], line["amount"]) == (Role.waste_to_treatment, 0.05)
    assert line["supplier_product"] == "textile waste"
    assert lines.flow("widget", 2).kind is FlowKind.waste


def test_an_output_to_treatment_is_waste_to_treatment_of_a_waste_flow(es1) -> None:
    lines = es1.lines_of(
        es1.dataset(
            "widget",
            es1.exchange(1, "widget", "kg", 1, "out0"),
            es1.exchange(2, "offcut", "kg", 0.05, "out3", location="CH"),
        )
    )

    line = lines["widget", 2]

    assert (line["role"], line["amount"]) == (Role.waste_to_treatment, 0.05)
    assert line["supplier_product"] == "offcut"
    assert lines.flow("widget", 2).kind is FlowKind.waste


def test_a_negative_input_of_a_product_is_an_avoided_product(es1) -> None:
    """ecoSpold1 has no avoided-product marker: a negative input is how it writes a credit. It
    is the same matrix entry as the negative input, so scores match engines that read it so."""
    lines = es1.lines_of(
        es1.dataset(
            "widget",
            es1.exchange(1, "widget", "kg", 1, "out0"),
            es1.exchange(
                2,
                "scrap steel",
                "kg",
                -0.2,
                "in5",
                location="CH",
                uncertaintyType=2,
                standardDeviation95=0.1,
            ),
        )
    )

    line = lines["widget", 2]

    assert (line["role"], line["amount"]) == (Role.avoided_product, 0.2)
    assert line["supplier_product"] == "scrap steel"
    assert line["uncertainty"]["loc"] == 0.2


def test_an_avoided_product_output_is_an_avoided_product(es1) -> None:
    lines = es1.lines_of(
        es1.dataset(
            "widget",
            es1.exchange(1, "widget", "kg", 1, "out0"),
            es1.exchange(2, "grid electricity", "kWh", 0.4, "out1", location="CH"),
        )
    )

    line = lines["widget", 2]

    assert (line["role"], line["amount"]) == (Role.avoided_product, 0.4)
    assert (line["supplier_product"], line["supplier_unit"]) == ("grid electricity", "kWh")


def test_a_by_product_makes_a_second_reference(es1) -> None:
    lines = es1.lines_of(
        es1.dataset(
            "chp",
            es1.exchange(1, "heat", "MJ", 5, "out0"),
            es1.exchange(2, "electricity", "kWh", 1, "out2"),
        )
    )

    assert [lines["chp", n]["role"] for n in (1, 2)] == [Role.reference, Role.reference]
    assert lines["chp", 2]["supplier_product"] is None


def test_a_line_that_buys_its_own_product_is_own_use(es1) -> None:
    lines = es1.lines_of(
        es1.dataset(
            "grid",
            es1.exchange(1, "electricity", "kWh", 1, "out0", location="CH"),
            es1.exchange(2, "electricity", "kWh", 0.02, "in2", location="CH"),
            es1.exchange(3, "electricity", "kWh", 0.01, "in2", location="DE"),
            es1.exchange(4, "electricity", "MJ", 0.01, "in2", location="CH"),
            location="CH",
            unit="kWh",
        )
    )

    assert [lines["grid", n]["role"] for n in (2, 3, 4)] == [
        Role.own_use,
        Role.input,
        Role.input,
    ]
    assert lines["grid", 2]["supplier_product"] == "electricity"


def test_own_use_links_to_the_process_itself(es1) -> None:
    folder = es1.make(
        es1.dataset(
            "grid",
            es1.exchange(1, "electricity", "kWh", 1, "out0", location="CH"),
            es1.exchange(2, "electricity", "kWh", 0.02, "in2", location="CH"),
            location="CH",
            unit="kWh",
        )
    )

    inventory = es1.load(folder)

    [process] = inventory.processes
    own = process.exchanges[1]
    assert (own.role, own.supplier_id, own.link_status.value) == (
        Role.own_use,
        process.id,
        "linked",
    )


@pytest.mark.parametrize(
    ("line", "code"),
    [
        (("in7", 1), "unsupported-group"),
        (("out5", 1), "unsupported-group"),
        (("out2", -1), "unexpected-sign"),
        (("out1", -1), "unexpected-sign"),
        (("out3", -1), "unexpected-sign"),
    ],
)
def test_a_group_or_sign_with_no_role_is_refused_naming_the_line(
    es1, line: tuple[str, float], code: str
) -> None:
    group, amount = line
    folder = es1.make(
        es1.dataset(
            "widget",
            es1.exchange(1, "widget", "kg", 1, "out0"),
            es1.exchange(2, "thing", "kg", amount, group, location="CH"),
        )
    )

    with pytest.raises(InvalidDataError) as raised:
        es1.read(folder)

    [problem] = raised.value.problems
    assert problem.code == code
    assert "line 2" in problem.item and "000.xml" in str(problem.location)


def test_a_line_with_no_group_or_two_groups_is_refused(es1) -> None:
    both = (
        '<exchange number="2" name="x" unit="kg" meanValue="1">'
        "<inputGroup>5</inputGroup><outputGroup>2</outputGroup></exchange>"
    )
    neither = '<exchange number="3" name="y" unit="kg" meanValue="1"/>'
    folder = es1.make(
        es1.dataset("widget", es1.exchange(1, "widget", "kg", 1, "out0"), both, neither)
    )

    with pytest.raises(InvalidDataError) as raised:
        es1.read(folder)

    assert _codes(raised) == ["unsupported-group", "unsupported-group"]


def test_a_flow_that_is_a_waste_in_one_place_and_a_product_in_another_is_refused(
    es1,
) -> None:
    folder = es1.make(
        es1.dataset("incineration", es1.exchange(1, "textile waste", "kg", -1, "out0")),
        es1.dataset(
            "widget",
            es1.exchange(1, "widget", "kg", 1, "out0"),
            es1.exchange(2, "textile waste", "kg", 0.05, "in5", location="CH"),
            number=2,
        ),
    )

    with pytest.raises(InvalidDataError) as raised:
        es1.read(folder)

    [problem] = raised.value.problems
    assert problem.code == "waste-and-product"
    assert "textile waste" in problem.message and "line 2" in problem.item


def test_an_elementary_line_needs_a_category(es1) -> None:
    folder = es1.make(
        es1.dataset(
            "widget",
            es1.exchange(1, "widget", "kg", 1, "out0"),
            es1.exchange(2, "Carbon dioxide", "kg", 1, "out4"),
        )
    )

    with pytest.raises(InvalidDataError) as raised:
        es1.read(folder)

    assert _codes(raised) == ["missing-category"]


@pytest.mark.parametrize("references", [0, 2])
def test_a_process_has_exactly_one_reference_product(es1, references: int) -> None:
    lines = [es1.exchange(n, f"p{n}", "kg", 1, "out0") for n in range(1, references + 1)]
    lines.append(es1.exchange(9, "thing", "kg", 1, "in5"))
    folder = es1.make(es1.dataset("widget", *lines))

    with pytest.raises(InvalidDataError) as raised:
        es1.read(folder)

    [problem] = raised.value.problems
    assert problem.code == "reference-count"
    assert f"{references}" in problem.message
