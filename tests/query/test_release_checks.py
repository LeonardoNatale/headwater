"""What `hw.load` still checks of a release's lines, and how it reads the ones that need a unit.

`build` refuses a bad line when it freezes a release (tests/release/test_freeze_lines.py), so
`hw.load` does not check the same things again. It still refuses the lines with no matrix entry,
which a release frozen by an older version can hold, and converts the amounts that need it.
"""

import pytest

import headwater as hw
from headwater.calc import SingularMatrixError
from headwater.model import (
    CutOffCriterion,
    Factor,
    FlowProperty,
    FlowPropertyName,
    InvalidDataError,
    Issue,
    IssueStatus,
    Method,
)
from headwater.query import _release
from headwater.release import checks

from .conftest import RESOLVED_AT, Records, toy_records

REL = 1e-9


def problems(error: pytest.ExceptionInfo[InvalidDataError]) -> list[tuple[str, str]]:
    return [(p.code, p.item) for p in error.value.problems]


def score(release, calc) -> float:
    return calc("tshirt", hw.load(release.path, method="toy-climate")).score


def test_own_use_with_the_right_supplier_loses_a_share_of_the_output(
    make_release, records, calc
) -> None:
    # A market-like loss: the electricity process uses 10% of its own output.
    release = make_release(
        records.with_new_line(
            "toy:electricity-production",
            "e04",
            role="own_use",
            flow_id="toy:electricity",
            amount=0.1,
            unit="kWh",
            supplier_id="toy:electricity-production",
        )
    )

    # Each kWh needs 1/0.9 kWh made, so every emission of electricity scales by 1/0.9.
    folder = hw.load(release.path, method="toy-climate")
    expected = 0.25 * (2.0 + 3 / 0.9 * 0.5 + 0.004 * 29.8 + 3 / 0.9 * 0.001 * 29.8)
    expected += 0.5 / 0.9 * (0.5 + 0.001 * 29.8)
    assert calc("tshirt", folder).score == pytest.approx(expected, rel=REL)


def test_a_unit_of_another_quantity_converts_through_a_property(
    make_release, records, calc
) -> None:
    # Fabric production loses 0.1 kg of fabric per kg made. Written as 0.0005 m3 of fabric, with
    # a density of 0.005 m3 per kg, it is the same line: converted by the catalog's one rule.
    plain = make_release(
        records.with_new_line(
            "toy:fabric-production",
            "e05",
            role="own_use",
            flow_id="toy:cotton-fabric",
            amount=0.1,
            unit="kg",
            supplier_id="toy:fabric-production",
        )
    )
    in_volume = make_release(
        records.with_properties(
            FlowProperty(
                flow_id="toy:cotton-fabric",
                property=FlowPropertyName.density,
                value=0.005,
                unit="m3",
            )
        ).with_new_line(
            "toy:fabric-production",
            "e05",
            role="own_use",
            flow_id="toy:cotton-fabric",
            amount=0.0005,
            unit="m3",
            supplier_id="toy:fabric-production",
        )
    )

    assert score(in_volume, calc) == pytest.approx(score(plain, calc), rel=REL)
    assert score(plain, calc) > 1.19205  # the loss costs something


def test_a_line_in_another_unit_of_the_same_quantity_is_converted(
    make_release, records, calc
) -> None:
    # Fabric production buys 3000 Wh of electricity: the same 3 kWh. The lever reports the
    # amount in the flow's own unit, so it comes from the same number that went into A.
    release = make_release(
        records.with_line("toy:fabric-production", "e02", unit="Wh", amount=3000.0)
    )
    result = calc("tshirt", hw.load(release.path, method="toy-climate"))

    assert result.score == pytest.approx(1.19205, rel=REL)
    (found,) = [
        x
        for x in result.sensitivity()
        if (x.process_id, x.line_id) == ("toy:fabric-production", "e02")
    ]
    assert (found.amount, found.unit) == (pytest.approx(3.0, rel=REL), "kWh")
    assert found.sensitivity == pytest.approx(0.13245, rel=REL)


@pytest.fixture
def old_freeze(monkeypatch: pytest.MonkeyPatch) -> None:
    """Freezing without the check of a role's sign, as before it existed: `build` now refuses an
    own use in a treatment, but releases frozen earlier can hold one."""
    monkeypatch.setattr(
        checks, "_LINE_CHECKS", [c for c in checks._LINE_CHECKS if c.code != "role-no-sign"]
    )


def own_use_in_a_treatment(*line_ids: str) -> Records:
    """The toy with an own use in the incineration process, cut off. An own use has no sign in a
    process that treats waste, which `build` refuses only when `old_freeze` is not used."""
    process = "toy:incineration-textile-waste"
    records = toy_records()
    for line_id in line_ids:
        records = records.with_new_line(
            process,
            line_id,
            role="own_use",
            flow_id="toy:cotton-fabric",
            amount=0.1,
            unit="kg",
            link_status="cut_off",
        ).with_issues(
            Issue(
                process_id=process,
                line_id=line_id,
                flow_id="toy:cotton-fabric",
                status=IssueStatus.cut_off,
                reason="a test",
                criterion=CutOffCriterion.mass,
                share=0.001,
                cumulative_share=0.001,
                resolved_by="test",
                resolved_at=RESOLVED_AT,
            )
        )
    return records


def test_a_role_with_no_sign_in_its_process_is_refused_not_dropped(
    make_release, old_freeze
) -> None:
    release = make_release(own_use_in_a_treatment("e09"))

    with pytest.raises(InvalidDataError) as info:
        hw.load(release.path, method="toy-climate")

    assert problems(info) == [("no-sign", "toy:incineration-textile-waste line e09")]
    assert "role 'own_use' has no sign in a treatment" in str(info.value)


def test_every_problem_is_reported_not_only_the_first(make_release, old_freeze) -> None:
    release = make_release(own_use_in_a_treatment("e09", "e10"))

    with pytest.raises(InvalidDataError) as info:
        hw.load(release.path, method="toy-climate")

    assert problems(info) == [
        ("no-sign", "toy:incineration-textile-waste line e09"),
        ("no-sign", "toy:incineration-textile-waste line e10"),
    ]


def test_a_long_list_of_problems_is_cut_short_and_says_how_many_more(
    make_release, old_freeze, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_release, "SHOWN_PROBLEMS", 1)
    release = make_release(own_use_in_a_treatment("e09", "e10"))

    with pytest.raises(InvalidDataError) as info:
        hw.load(release.path, method="toy-climate")

    assert [p.code for p in info.value.problems] == ["no-sign", "more-problems"]
    assert "1 more" in str(info.value.problems[-1])


def test_factors_for_one_location_are_refused_on_a_release_too(make_release, records) -> None:
    method = records.methods[0]
    located = Method.model_validate(
        {
            **method.model_dump(),
            "factors": [
                *method.factors,
                Factor(indicator="climate-change", flow_id="toy:co2", location="CH", factor=1.1),
            ],
        }
    )
    release = make_release(records.with_methods(located))

    with pytest.raises(InvalidDataError, match="climate-change/toy:co2 at CH") as info:
        hw.load(release.path, method="toy-climate")

    assert [p.code for p in info.value.problems] == ["not-supported-yet"]


def test_a_singular_release_names_the_process_by_id(make_release, records) -> None:
    # The fabric process uses up all it makes: no net output, so the matrix cannot be solved.
    release = make_release(
        records.with_new_line(
            "toy:fabric-production",
            "e05",
            role="own_use",
            flow_id="toy:cotton-fabric",
            amount=1.0,
            unit="kg",
            supplier_id="toy:fabric-production",
        )
    )

    with pytest.raises(SingularMatrixError, match="toy:fabric-production") as info:
        hw.load(release.path, method="toy-climate")

    assert info.value.indices == ()  # matrix indices never leave the calculation


def test_an_empty_value_where_the_release_needs_one_is_an_error_not_a_hidden_number() -> None:
    import pyarrow as pa

    column = pa.Table.from_pydict({"row": [0, None, 2]})

    with pytest.raises(InvalidDataError, match="1 empty values") as info:
        _release._column(column, "row")

    assert [(p.code, p.item) for p in info.value.problems] == [("release-incomplete", "column row")]
    assert _release._column(pa.Table.from_pydict({"row": [0, 1]}), "row").to_pylist() == [0, 1]


def test_a_line_that_would_be_dropped_is_refused_not_dropped(
    release, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A process missing from the index: its lines, and every purchase from it, have nowhere to go.
    original = _release._Query._number_processes

    def without_electricity(self: _release._Query) -> None:
        original(self)
        self.con.execute("DELETE FROM proc WHERE id = 'toy:electricity-production'")

    monkeypatch.setattr(_release._Query, "_number_processes", without_electricity)

    with pytest.raises(InvalidDataError) as info:
        hw.load(release.path, method="toy-climate")

    assert problems(info) == [
        ("unknown-process", f"toy:electricity-production line {line}")
        for line in ("e01", "e02", "e03")
    ]
