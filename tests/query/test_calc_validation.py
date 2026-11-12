import importlib
from collections.abc import Callable

import pytest

import headwater as hw
from headwater.model import Exchange

validate_module = importlib.import_module("headwater.study.validate")


def spy_on_exchange_checks(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The id of the process of every line the validation checks, in order."""
    checked: list[str] = []
    check = validate_module._check_exchange

    def spy(exchange: Exchange, catalog, scope=None):
        checked.append(exchange.process_id)
        return check(exchange, catalog, scope)

    monkeypatch.setattr(validate_module, "_check_exchange", spy)
    return checked


def test_loading_a_background_validates_it(
    monkeypatch: pytest.MonkeyPatch, load_toy: Callable[..., hw.Background]
) -> None:
    checked = spy_on_exchange_checks(monkeypatch)

    load_toy()

    assert set(checked) == {
        "toy:electricity-production",
        "toy:fabric-production",
        "toy:incineration-textile-waste",
    }


def test_calc_checks_the_lines_of_the_study_not_the_background_again(
    monkeypatch: pytest.MonkeyPatch,
    background: hw.Background,
    calc: Callable[..., hw.Result],
) -> None:
    checked = spy_on_exchange_checks(monkeypatch)

    result = calc("tshirt")

    assert result.score == pytest.approx(1.19205, rel=1e-9)
    assert checked == ["tshirt:tshirt"] * 3
