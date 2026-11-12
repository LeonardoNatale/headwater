"""Processes that make several products: the file's allocation factors become `ALLOCATION` rows,
and the shared stage splits the process, or refuses it.

A combined heat and power plant makes heat (line 1, the reference product) and electricity
(line 2, an allocated by-product) from natural gas (line 3) and emits CO2 (line 4).
"""

import pytest

from headwater.model import InvalidDataError, IssueStatus, Role

HEAT, ELECTRICITY, GAS, CO2 = 1, 2, 3, 4


def _gas(es1):
    return es1.maker("natural gas", "m3", number=2)


def _chp(es1, *allocations: str, extra: tuple[str, ...] = ()):
    return es1.dataset(
        "chp",
        es1.exchange(HEAT, "heat", "MJ", 5, "out0", location="CH"),
        es1.exchange(ELECTRICITY, "electricity", "kWh", 1, "out2", location="CH"),
        es1.exchange(GAS, "natural gas", "m3", 1.0, "in5", location="CH"),
        es1.exchange(CO2, "Carbon dioxide", "kg", 2.0, "out4", category="air", sub_category="u"),
        *extra,
        allocations=allocations,
        unit="MJ",
    )


def _factors(source) -> dict[tuple[str, str | None], float]:
    return {(r["product"], r["line_id"]): r["factor"] for r in source.allocation.to_pylist()}


def test_a_fraction_per_co_product_is_its_factor_for_every_other_line(es1) -> None:
    folder = es1.make(
        _chp(es1, es1.allocation(HEAT, 60, GAS, CO2), es1.allocation(ELECTRICITY, 40, GAS, CO2))
    )

    source = es1.read(folder)

    assert _factors(source) == {("1", None): 0.6, ("2", None): 0.4}
    assert {r["process_id"] for r in source.allocation.to_pylist()} == {
        r["id"] for r in source.processes.to_pylist()
    }


def test_a_line_with_its_own_fractions_gets_rows_of_its_own(es1) -> None:
    """The gas is split 70 to 30, the CO2 and the methane 60 to 40. The factor most lines share
    is the product's, and the gas, which differs, gets a row for its line."""
    methane = es1.exchange(5, "Methane", "kg", 0.1, "out4", category="air", sub_category="u")
    folder = es1.make(
        _chp(
            es1,
            es1.allocation(HEAT, 70, GAS),
            es1.allocation(HEAT, 60, CO2, 5),
            es1.allocation(ELECTRICITY, 30, GAS),
            es1.allocation(ELECTRICITY, 40, CO2, 5),
            extra=(methane,),
        )
    )

    factors = _factors(es1.read(folder))

    assert factors == pytest.approx(
        {("1", None): 0.6, ("2", None): 0.4, ("1", "3"): 0.7, ("2", "3"): 0.3}
    )


def test_the_stage_splits_the_process_by_the_factors(es1) -> None:
    folder = es1.make(
        _chp(es1, es1.allocation(HEAT, 60, GAS, CO2), es1.allocation(ELECTRICITY, 40, GAS, CO2)),
        _gas(es1),
    )

    inventory = es1.load(folder)

    by_id = {p.id.rsplit(":", 1)[-1]: p for p in inventory.processes if p.name == "chp"}
    assert by_id["1"].exchanges[0].amount == 5 and by_id["2"].exchanges[0].amount == 1
    gas = {k: next(e for e in p.exchanges if e.line_id == str(GAS)) for k, p in by_id.items()}
    assert gas["1"].amount == pytest.approx(0.6) and gas["2"].amount == pytest.approx(0.4)
    assert inventory.problems == ()


def test_a_reference_to_a_product_line_is_not_a_factor_for_a_line(es1) -> None:
    folder = es1.make(
        _chp(
            es1,
            es1.allocation(HEAT, 60, HEAT, ELECTRICITY, GAS, CO2),
            es1.allocation(ELECTRICITY, 40, HEAT, ELECTRICITY, GAS, CO2),
        )
    )

    factors = _factors(es1.read(folder))

    assert factors == {("1", None): 0.6, ("2", None): 0.4}


def test_a_fraction_for_a_line_the_process_does_not_have_is_refused(es1) -> None:
    folder = es1.make(
        _chp(es1, es1.allocation(HEAT, 60, GAS, 99), es1.allocation(ELECTRICITY, 40, GAS))
    )

    with pytest.raises(InvalidDataError) as raised:
        es1.read(folder)

    [problem] = raised.value.problems
    assert problem.code == "unknown-reference" and "99" in problem.message


def test_a_fraction_for_something_that_is_not_a_product_is_refused(es1) -> None:
    folder = es1.make(_chp(es1, es1.allocation(GAS, 60, CO2), es1.allocation(ELECTRICITY, 40, CO2)))

    with pytest.raises(InvalidDataError) as raised:
        es1.read(folder)

    assert [p.code for p in raised.value.problems] == ["unknown-reference"]


def test_a_multi_output_process_with_no_factors_is_an_open_issue(es1) -> None:
    inventory = es1.load(es1.make(_chp(es1), _gas(es1)))

    [problem] = inventory.problems
    assert problem.code == "not-allocated" and "no allocation factor" in problem.message
    assert [i.status for i in inventory.issues] == [IssueStatus.open]


def test_factors_that_do_not_sum_to_100_are_an_open_issue(es1) -> None:
    folder = es1.make(
        _chp(es1, es1.allocation(HEAT, 60, GAS, CO2), es1.allocation(ELECTRICITY, 30, GAS, CO2)),
        _gas(es1),
    )

    inventory = es1.load(folder)

    [problem] = inventory.problems
    assert problem.code == "not-allocated" and "sum" in problem.message


def test_a_multi_output_process_that_uses_its_own_product_is_split_with_the_use_kept(es1) -> None:
    """The reader writes the line as own use. The stage splits it: the split that makes the
    electricity keeps its share as own use, and the heat's split buys its share from it."""
    own = es1.exchange(5, "electricity", "kWh", 0.1, "in2", location="CH")
    folder = es1.make(
        _chp(
            es1,
            es1.allocation(HEAT, 60, GAS, CO2, 5),
            es1.allocation(ELECTRICITY, 40, GAS, CO2, 5),
            extra=(own,),
        ),
        _gas(es1),
    )

    source = es1.read(folder)
    inventory = es1.load(folder)

    assert {r["role"] for r in source.lines.to_pylist() if r["line_id"] == "5"} == {Role.own_use}
    assert inventory.problems == ()
    split = {p.id.rsplit(":", 1)[-1]: p for p in inventory.processes if p.name == "chp"}
    use = {k: next(e for e in p.exchanges if e.line_id == "5") for k, p in split.items()}
    assert (use["2"].role, use["2"].supplier_id) == (Role.own_use, split["2"].id)
    assert use["2"].amount == pytest.approx(0.04)
    assert (use["1"].role, use["1"].supplier_id) == (Role.input, split["2"].id)
    assert use["1"].amount == pytest.approx(0.06)


def test_a_single_product_with_a_fraction_of_100_is_not_split(es1) -> None:
    folder = es1.make(
        es1.dataset(
            "widget",
            es1.exchange(1, "widget", "kg", 1, "out0"),
            es1.exchange(2, "Carbon dioxide", "kg", 2, "out4", category="air"),
            es1.allocation(1, 100, 2),
        )
    )

    inventory = es1.load(folder)

    assert len(inventory.processes) == 1 and inventory.problems == ()


def test_fractions_are_percent(es1) -> None:
    folder = es1.make(
        _chp(
            es1,
            es1.allocation(HEAT, 33.3333, GAS, CO2),
            es1.allocation(ELECTRICITY, 66.6667, GAS, CO2),
        )
    )

    factors = _factors(es1.read(folder))

    assert factors[("1", None)] == pytest.approx(0.333333)


def test_a_line_named_twice_by_one_allocation_counts_once(es1) -> None:
    folder = es1.make(
        _chp(
            es1,
            es1.allocation(HEAT, 60, GAS, GAS, CO2),
            es1.allocation(ELECTRICITY, 40, GAS, CO2, CO2),
        )
    )

    factors = _factors(es1.read(folder))

    assert factors == pytest.approx({("1", None): 0.6, ("2", None): 0.4})
