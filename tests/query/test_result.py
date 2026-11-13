from collections.abc import Callable
from pathlib import Path

import headwater as hw
from tests.conftest import add_lines


def test_print_shows_the_process_the_score_the_indicator_and_the_method(
    calc: Callable[..., hw.Result],
) -> None:
    result = calc("tshirt")

    assert repr(result) == (
        "Result(tshirt:tshirt = 1.19205 kg CO2-eq, indicator climate-change, method toy-climate 1)"
    )
    assert str(result) == repr(result)


def test_the_score_is_shown_with_six_significant_digits(calc: Callable[..., hw.Result]) -> None:
    result = calc("tshirt-waste")

    assert "= 1.25205 kg CO2-eq" in repr(result)


def test_print_says_how_many_flows_have_no_factor(
    calc: Callable[..., hw.Result], toy: Path, load_toy: Callable[[Path], hw.Background]
) -> None:
    with (toy / "background" / "flows.csv").open("a") as f:
        f.write("toy:n2o,Dinitrogen monoxide,elementary,kg,air,10024-97-2\n")
    add_lines(toy / "background", "electricity-production", "e04,emission,toy:n2o,0.0001,kg,")

    result = calc("tshirt", root=toy, background=load_toy(toy))

    assert repr(result).endswith(", 1 flow not characterised)")
