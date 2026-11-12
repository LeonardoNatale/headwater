"""Several methods used together, as one method record."""

import pytest

from headwater.model import Factor, Indicator, InvalidDataError, Method, combine

CO2 = "kg CO2-Eq"


def _method(id: str, version: str, *indicators: str, flow_list: str = "ecoinvent-3.12") -> Method:
    return Method(
        id=id,
        version=version,
        variant=id,
        flow_list=flow_list,
        indicators=tuple(Indicator(id=i, name=i, unit=CO2) for i in indicators),
        factors=tuple(Factor(indicator=i, flow_id=f"flow:{i}", factor=1.0) for i in indicators),
    )


EF = _method("EF-3.1", "3.1", "climate-change", "climate-change-fossil")
LUC = _method("IPCC-2021", "2021", "climate-change-direct-land-use-change")


def test_methods_used_together_keep_every_indicator_and_factor_in_order() -> None:
    both = combine([EF, LUC])

    assert (both.id, both.version, both.variant, both.flow_list) == (
        "EF-3.1+IPCC-2021",
        "3.1+2021",
        None,
        "ecoinvent-3.12",
    )
    assert both.indicators == EF.indicators + LUC.indicators
    assert both.factors == EF.factors + LUC.factors


def test_one_method_is_returned_as_it_is() -> None:
    assert combine([EF]) is EF


def test_an_indicator_in_two_methods_is_refused_naming_both() -> None:
    fossil = _method("IPCC-2021", "2021", "climate-change-fossil")

    with pytest.raises(InvalidDataError) as error:
        combine([EF, fossil])

    [problem] = error.value.problems
    assert (problem.code, problem.item) == (
        "duplicate-indicator",
        "indicator climate-change-fossil",
    )
    assert "EF-3.1" in problem.message
    assert "IPCC-2021" in problem.message


def test_methods_of_two_flow_lists_are_refused() -> None:
    old = _method("IPCC-2021", "2021", "climate-change-biogenic", flow_list="ecoinvent-3.10")

    with pytest.raises(InvalidDataError) as error:
        combine([EF, old])

    assert [p.code for p in error.value.problems] == ["flow-list-mismatch"]
    assert "ecoinvent-3.10" in str(error.value)


def test_a_method_given_twice_is_refused() -> None:
    with pytest.raises(InvalidDataError) as error:
        combine([EF, LUC, EF])

    assert [p.code for p in error.value.problems] == ["duplicate-method"]


def test_no_method_is_an_error() -> None:
    with pytest.raises(ValueError, match="at least one"):
        combine([])


def test_methods_used_together_cover_the_flows_of_each() -> None:
    """Built new, never copied: what each part covered, read before, is not carried over."""
    covered = (EF.characterised_flow_ids, LUC.characterised_flow_ids)

    both = combine([EF, LUC])

    assert both.characterised_flow_ids == covered[0] | covered[1]
    assert "flow:climate-change-direct-land-use-change" in both.characterised_flow_ids
