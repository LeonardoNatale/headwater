"""`validate` and `calc` work out the study's flows with no factor once, and share the answer.

Example: `tshirt-no-factor` emits COD, which `toy-climate` has no factor for. The validation
needs it to say whether `no_factor.csv` has a row for it, and `calc` to list it with the row's
reason. Both read the same list.
"""

import importlib
from pathlib import Path

import pytest

import headwater as hw
from headwater.query import database as study_module
from tests.conftest import open_database, toy_database

validate_module = importlib.import_module(
    "headwater.study.validate"
)  # the package's `validate` is a function

TOY = Path(__file__).resolve().parent.parent / "fixtures" / "toy"


@pytest.fixture
def worked_out(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The processes each call of `flows_without_factor` was given, in order."""
    calls: list[str] = []
    real = validate_module.flows_without_factor

    def counting(processes, catalog, method):  # type: ignore[no-untyped-def]
        processes = list(processes)
        calls.append(",".join(sorted(p.id for p in processes)))
        return real(processes, catalog, method)

    monkeypatch.setattr(validate_module, "flows_without_factor", counting)
    monkeypatch.setattr(study_module, "flows_without_factor", counting)
    return calls


@pytest.mark.parametrize("name", ["tshirt", "tshirt-no-factor"])
def test_calc_works_them_out_once(
    name: str, background: hw.Background, worked_out: list[str]
) -> None:
    open_database(toy_database(TOY, name)).calc(background, "tshirt:tshirt")

    assert worked_out == ["tshirt:tshirt"]


@pytest.mark.parametrize("name", ["tshirt", "tshirt-no-factor"])
def test_validate_works_them_out_once(
    name: str, background: hw.Background, worked_out: list[str]
) -> None:
    open_database(toy_database(TOY, name)).validate(background)

    assert worked_out == ["tshirt:tshirt"]


def test_the_result_lists_the_flow_with_the_reason_of_its_row(
    background: hw.Background,
) -> None:
    result = open_database(TOY / "tshirt-no-factor" / "tshirt").calc(background, "tshirt:tshirt")

    [flow] = result.not_characterised
    assert flow.flow_id == "tshirt:cod"
    assert flow.in_database is True
    assert flow.reason == "not characterised in toy-climate"
