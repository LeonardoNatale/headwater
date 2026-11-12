"""A Contribution prints as a tree, with each node's share of the total."""

import pytest

import headwater as hw

TSHIRT = """\
1 unit of T-shirt: 1.19205 (100.0%), direct 0.00000
  0.25 kg of cotton fabric production: 0.92715 (77.8%), direct 0.52980
    0.75 kWh of electricity production: 0.39735 (33.3%), direct 0.39735
  0.5 kWh of electricity production: 0.26490 (22.2%), direct 0.26490"""


def test_the_tshirt_explanation_prints_as_a_tree_with_shares(calc) -> None:
    # 1.19205 = 0.25 kg fabric x 3.7086 + 0.5 kWh x 0.5298, and the fabric holds 0.75 kWh x
    # 0.5298 = 0.39735 of its own score: docs/calculation.md, the toy background.
    assert str(calc("tshirt").contributions()) == TSHIRT


def test_what_was_left_out_is_printed_as_rest_so_every_node_adds_up(calc) -> None:
    text = str(calc("tshirt").contributions(min_share=0.5))

    assert text == "\n".join(
        [
            "1 unit of T-shirt: 1.19205 (100.0%), direct 0.00000",
            "  0.25 kg of cotton fabric production: 0.92715 (77.8%), direct 0.52980",
            "    rest: 0.39735 (33.3%)",
            "  rest: 0.26490 (22.2%)",
        ]
    )


def test_a_tree_cut_by_max_depth_ends_in_leaves_with_no_rest_line(calc) -> None:
    # The fabric is at max_depth 1: its 0.39735 of electricity is in its score, not left out.
    root = calc("tshirt").contributions(max_depth=1)

    assert str(root) == "\n".join(
        [
            "1 unit of T-shirt: 1.19205 (100.0%), direct 0.00000",
            "  0.25 kg of cotton fabric production: 0.92715 (77.8%), direct 0.52980",
            "  0.5 kWh of electricity production: 0.26490 (22.2%), direct 0.26490",
        ]
    )
    fabric = root.children[0]
    assert fabric.score == pytest.approx(fabric.direct + fabric.rest, rel=1e-12)  # adds up


def test_a_rest_line_stays_where_min_share_left_something_out(calc) -> None:
    text = str(calc("tshirt").contributions(min_share=0.5, max_depth=1))

    assert text == "\n".join(
        [
            "1 unit of T-shirt: 1.19205 (100.0%), direct 0.00000",
            "  0.25 kg of cotton fabric production: 0.92715 (77.8%), direct 0.52980",
            "  rest: 0.26490 (22.2%)",
        ]
    )


def test_a_background_product_cut_at_depth_zero_is_one_line(background) -> None:
    text = str(background.contributions("toy:fabric-production", max_depth=0))

    assert text == "1 kg of cotton fabric production: 3.70860 (100.0%), direct 2.11920"


def test_the_waste_line_prints_as_kg_sent_and_burden_per_kg_treated(calc) -> None:
    text = str(calc("tshirt-waste").contributions())

    # 0.05 kg of scraps sent x 1.2 per kg treated = 0.06 (4.8% of 1.25205).
    assert "  0.05 kg of incineration of textile waste: 0.06000 (4.8%), direct 0.06000" in text


def test_a_subtree_shows_its_shares_of_its_own_score(calc) -> None:
    fabric = calc("tshirt").contributions().children[0]

    assert str(fabric).splitlines() == [
        "0.25 kg of cotton fabric production: 0.92715 (100.0%), direct 0.52980",
        "  0.75 kWh of electricity production: 0.39735 (42.9%), direct 0.39735",
    ]


def test_an_explanation_of_a_score_of_zero_prints_no_share(toy, load_toy, edit) -> None:
    # Incineration that emits nothing scores exactly 0: there is no share of nothing.
    path = toy / "background" / "processes" / "incineration-textile-waste.csv"
    edit(path, "e02,emission,toy:co2,1.2,kg", "e02,emission,toy:co2,0,kg")
    background = load_toy(toy)

    text = str(background.contributions("toy:incineration-textile-waste"))

    assert text == "1 kg of incineration of textile waste: 0.00000, direct 0.00000"


def test_the_notebook_shows_the_tree_for_a_bare_expression(calc) -> None:
    class Printer:
        def __init__(self) -> None:
            self.out = ""

        def text(self, value: str) -> None:
            self.out += value

    printer = Printer()
    root = calc("tshirt").contributions()

    root._repr_pretty_(printer, cycle=False)  # IPython calls this for a bare expression

    assert printer.out == TSHIRT


def test_the_dataclass_repr_is_unchanged(calc) -> None:
    root = calc("tshirt").contributions()

    assert repr(root).startswith("Contribution(process_id='tshirt:tshirt'")
    assert isinstance(root, hw.Contribution)
