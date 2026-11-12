"""How SimaPro's product text maps onto the supplier's name, product and location."""

import pytest

from headwater.io.simapro_csv.text import ProductText


@pytest.mark.parametrize(
    ("text", "name", "product", "location"),
    [
        (
            "Electricity, medium voltage {FR}| market for | Cut-off, U",
            "market for | Cut-off, U",
            "Electricity, medium voltage",
            "FR",
        ),
        (
            "Transport, freight, lorry 16-32 metric ton, EURO5 {RER}| transport, freight, lorry "
            "16-32 metric ton, EURO5 | Cut-off, U",
            "transport, freight, lorry 16-32 metric ton, EURO5 | Cut-off, U",
            "Transport, freight, lorry 16-32 metric ton, EURO5",
            "RER",
        ),
        (
            "Heat, district or industrial {RoW}| heat production | Cut-off, S",
            "heat production | Cut-off, S",
            "Heat, district or industrial",
            "RoW",
        ),
        (
            "Soft wheat grain, conventional, 15% moisture, at farm gate {FR} U",
            None,
            "Soft wheat grain, conventional, 15% moisture, at farm gate",
            "FR",
        ),
        ("Tap water {RER w/o CH+DE}", None, "Tap water", "RER w/o CH+DE"),
        ("Dyeing of cotton fabric", None, "Dyeing of cotton fabric", None),
    ],
)
def test_the_text_gives_what_it_holds_and_nothing_else(
    text: str, name: str | None, product: str, location: str | None
) -> None:
    found = ProductText.parse(text)

    assert (found.name, found.product, found.location) == (name, product, location)


def test_unit_and_system_processes_of_one_product_stay_apart() -> None:
    unit = ProductText.parse("Electricity, medium voltage {FR}| market for | Cut-off, U")
    system = ProductText.parse("Electricity, medium voltage {FR}| market for | Cut-off, S")

    assert unit.product == system.product
    assert unit != system


def test_spaces_around_the_parts_are_not_part_of_them() -> None:
    found = ProductText.parse("  Tap water  {CH} |  market for  ")

    assert (found.name, found.product, found.location) == ("market for", "Tap water", "CH")
