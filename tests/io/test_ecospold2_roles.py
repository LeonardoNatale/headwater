"""Ids, kinds and roles from the fixture source, and the matrix signs that follow from the roles."""

from collections.abc import Callable
from datetime import date
from pathlib import Path

import pytest

from headwater.calc.signs import Matrix, matrix_entry
from headwater.io.ecospold2 import read_ecospold2
from headwater.io.exclude import read_exclude
from headwater.io.inventory import Inventory
from headwater.model import Catalog, InvalidDataError, ProcessKind, Role
from headwater.model.uncertainty import LinkStatus

# (activity, product, line, role, flow, amount, supplier as (activity, product), amount in the file)
# The flow is ("p", n) for product n and ("e", n) for elementary flow n.
type Row = tuple[int, int, int, Role, tuple[str, int], float, tuple[int, int] | None, float]

ROWS: list[Row] = [
    (1, 1, 1, Role.reference, ("p", 1), 1.0, None, 1.0),
    (1, 1, 2, Role.input, ("p", 2), 2.0, (2, 2), 2.0),
    (1, 1, 3, Role.input, ("p", 3), 0.1, (6, 3), 0.1),
    (1, 1, 4, Role.waste_to_treatment, ("p", 4), 0.05, (4, 4), -0.05),
    (1, 1, 5, Role.recyclable, ("p", 5), 0.02, None, -0.02),
    (1, 1, 6, Role.emission, ("e", 1), 0.5, None, 0.5),
    (1, 1, 7, Role.emission, ("e", 2), -0.000001, None, -0.000001),
    (1, 1, 8, Role.resource, ("e", 3), 0.3, None, 0.3),
    (2, 2, 1, Role.reference, ("p", 2), 1.0, None, 1.0),
    (2, 2, 2, Role.own_use, ("p", 2), 0.02, (2, 2), 0.02),
    (2, 2, 3, Role.input, ("p", 2), 1.0, (3, 2), 1.0),
    (3, 2, 1, Role.reference, ("p", 2), 1.0, None, 1.0),
    (3, 2, 2, Role.input, ("p", 3), 0.25, (6, 3), 0.25),
    (3, 2, 3, Role.emission, ("e", 1), 0.45, None, 0.45),
    (4, 4, 1, Role.reference, ("p", 4), 1.0, None, -1.0),
    (4, 4, 2, Role.emission, ("e", 1), 1.1, None, 1.1),
    (5, 5, 1, Role.reference, ("p", 5), 1.0, None, 1.0),
    (6, 3, 1, Role.reference, ("p", 3), 1.0, None, 1.0),
    (6, 3, 2, Role.input, ("p", 3), 0.6, (7, 3), 0.6),
    (6, 3, 3, Role.input, ("p", 3), 0.4, None, 0.4),
    (7, 3, 1, Role.reference, ("p", 3), 1.0, None, 1.0),
    (7, 3, 2, Role.emission, ("e", 1), 0.05, None, 0.05),
    (9, 5, 1, Role.reference, ("p", 5), 1.0, None, 1.0),
    (9, 5, 2, Role.input, ("p", 5), 1.0, (5, 5), 1.0),
]
"""Every line of the fixture source. Line 3 of the natural gas market buys from the excluded
file, so it has no supplier."""

UNITS = {("p", 1): "kg", ("p", 2): "kWh", ("p", 3): "m3", ("p", 4): "kg", ("p", 5): "kg"}


def _flow(fx, flow: tuple[str, int]) -> str:
    kind, n = flow
    return fx.product(n) if kind == "p" else fx.elementary(n)


def test_every_line_gets_its_role_amount_and_supplier(source: Inventory, fx) -> None:
    found = {(e.process_id, e.line_id): e for p in source.processes for e in p.exchanges}
    assert len(found) == len(ROWS)
    for a, p, n, role, flow, amount, supplier, _ in ROWS:
        e = found[(fx.pid(a, p), fx.line(a, n))]
        expected_supplier = fx.pid(*supplier) if supplier else None
        assert (e.role, e.flow_id, e.supplier_id) == (role, _flow(fx, flow), expected_supplier)
        assert e.amount == pytest.approx(amount, rel=1e-9), e.label
        assert e.unit == UNITS.get(flow, "kg"), e.label
        if expected_supplier:
            assert e.link_status is LinkStatus.linked, e.label


def test_the_line_buying_from_the_excluded_file_is_a_data_gap(source: Inventory, fx) -> None:
    e = fx.exchange(source, fx.pid(6, 3), fx.line(6, 3))

    assert (e.supplier_id, e.link_status) == (None, LinkStatus.data_gap)


def test_lines_that_buy_nothing_have_no_link_status(source: Inventory) -> None:
    for e in (e for p in source.processes for e in p.exchanges):
        if e.role in (Role.reference, Role.recyclable, Role.emission, Role.resource):
            assert (e.supplier_id, e.link_status) == (None, None), e.label


def test_matrix_signs_follow_ecoinvent(source: Inventory, fx) -> None:
    """The roles give ecoinvent's own matrix: a reference is the amount in the file, a bought or
    sent line is minus the amount in the file. A recyclable gets no entry, because the dataset
    ecoinvent sends it to carries no burden."""
    catalog = Catalog(source.flows, source.processes, source.flow_properties, source.units)
    for a, p, n, role, flow, _, _, in_file in ROWS:
        process = fx.process(source, fx.pid(a, p))
        e = fx.exchange(source, process.id, fx.line(a, n))
        entry = matrix_entry(e.role, e.amount, treatment=catalog.is_treatment(process))
        if role is Role.recyclable:
            assert entry is None
        elif flow[0] == "e":
            assert entry is not None and entry.matrix is Matrix.biosphere
            assert entry.value == pytest.approx(in_file, rel=1e-9), e.label
        else:
            assert entry is not None and entry.matrix is Matrix.technosphere
            expected = in_file if role is Role.reference else -in_file
            assert entry.value == pytest.approx(expected, rel=1e-9), e.label


def test_only_the_waste_treatment_is_a_treatment(source: Inventory, fx) -> None:
    catalog = Catalog(source.flows, source.processes, source.flow_properties, source.units)

    treatments = [p.id for p in source.processes if catalog.is_treatment(p)]

    assert treatments == [fx.pid(4, 4)]


def test_ids_names_kinds_places_and_times(source: Inventory, fx) -> None:
    found = {
        p.id: (p.name, p.kind, p.location, p.time_start, p.time_end, p.source_ref)
        for p in source.processes
    }
    usual = (date(2020, 1, 1), date(2025, 12, 31))
    ordinary, market = ProcessKind.ordinary_transforming_activity, ProcessKind.market_activity
    expected = {
        (1, 1): ("widget production", ordinary, "GLO", *usual),
        (2, 2): ("market for electricity", market, "GLO", *usual),
        (3, 2): ("electricity production, natural gas", ordinary, "GLO", *usual),
        (4, 4): ("treatment of waste widget, incineration", ordinary, "GLO", *usual),
        (5, 5): ("scrap metal, Recycled Content cut-off", ordinary, "GLO", *usual),
        (6, 3): ("market for natural gas", market, "ES", *usual),
        (7, 3): ("natural gas production", ordinary, "RoW", date(2018, 1, 1), date(2023, 12, 31)),
        (9, 5): ("market for scrap metal", market, "GLO", *usual),
    }
    assert found == {fx.pid(*k): (*v, fx.file_name(*k)) for k, v in expected.items()}
    assert (source.info.version, source.info.system_model) == ("3.10", "cutoff")


def test_exchange_names_are_kept_for_the_check_against_the_flow(source: Inventory, fx) -> None:
    e = fx.exchange(source, fx.pid(1, 1), fx.line(1, 4))

    assert e.flow_name == "waste widget"


def _read_broken(folder: Path) -> InvalidDataError:
    with pytest.raises(InvalidDataError) as raised:
        read_ecospold2(
            folder, version="3.10", exclude=read_exclude(folder / "exclude.csv"), workers=1
        )
    return raised.value


def _codes(error: InvalidDataError) -> list[tuple[str, str]]:
    return [(p.code, p.item) for p in error.problems]


WIDGET = "datasets/a0000000-0000-4000-8000-000000000001_b0000000-0000-4000-8000-000000000001.spold"
GAS_POWER = (
    "datasets/a0000000-0000-4000-8000-000000000003_b0000000-0000-4000-8000-000000000002.spold"
)
TREATMENT = (
    "datasets/a0000000-0000-4000-8000-000000000004_b0000000-0000-4000-8000-000000000004.spold"
)
RECYCLED = (
    "datasets/a0000000-0000-4000-8000-000000000005_b0000000-0000-4000-8000-000000000005.spold"
)


@pytest.mark.parametrize(
    ("file", "old", "new", "activity", "product", "n"),
    [
        (TREATMENT, 'amount="-1.0"', 'amount="1.0"', 4, 4, 1),
        (WIDGET, 'amount="-0.05"', 'amount="0.05"', 1, 1, 4),
        (WIDGET, 'amount="2.0"', 'amount="-2.0"', 1, 1, 2),
    ],
    ids=["positive waste reference", "positive waste input", "negative product input"],
)
def test_a_sign_that_fits_no_role_fails_naming_the_line(
    source_copy: Callable[..., Path],
    fx,
    file: str,
    old: str,
    new: str,
    activity: int,
    product: int,
    n: int,
) -> None:
    error = _read_broken(source_copy(edit={file: [(old, new)]}))

    assert _codes(error) == [
        ("unexpected-sign", f"{fx.pid(activity, product)} line {fx.line(activity, n)}")
    ]


def test_an_exchange_group_without_a_role_fails_naming_the_line(
    source_copy: Callable[..., Path], fx
) -> None:
    folder = source_copy(
        edit={GAS_POWER: [("<inputGroup>5</inputGroup>", "<outputGroup>2</outputGroup>")]}
    )

    error = _read_broken(folder)

    assert _codes(error) == [("unsupported-group", f"{fx.pid(3, 2)} line {fx.line(3, 2)}")]


def test_a_line_without_a_by_product_classification_fails(
    source_copy: Callable[..., Path], fx
) -> None:
    folder = source_copy(edit={RECYCLED: [("By-product classification", "CPC")]})

    error = _read_broken(folder)

    assert _codes(error) == [("missing-classification", f"{fx.pid(5, 5)} line {fx.line(5, 1)}")]


@pytest.mark.parametrize(
    ("old", "new", "code"),
    [
        ('specialActivityType="0"', 'specialActivityType="11"', "unknown-activity-type"),
        (
            'systemModelId="290c1f85-4cc4-4fa1-b0c8-2cb7f4276dce"',
            'systemModelId="dd7f13f5-0658-489c-a19c-f2ff8a00bdd9"',
            "unsupported-system-model",
        ),
        (
            'id="a0000000-0000-4000-8000-000000000005"',
            'id="a0000000-0000-4000-8000-000000000055"',
            "file-name-mismatch",
        ),
        ('id="c0000005-0000-4000-8000-000000000001" ', "", "invalid-dataset"),
    ],
)
def test_dataset_level_problems_name_the_file(
    source_copy: Callable[..., Path], old: str, new: str, code: str
) -> None:
    error = _read_broken(source_copy(edit={RECYCLED: [(old, new)]}))

    assert _codes(error) == [(code, RECYCLED)]


def test_a_dataset_last_changed_in_an_older_release_is_fine(
    source_copy: Callable[..., Path], fx
) -> None:
    """A dataset's file attributes give the release it last changed in, not the database's."""
    folder = source_copy(edit={RECYCLED: [('minorRelease="10"', 'minorRelease="0"')]})

    found = read_ecospold2(
        folder, version="3.10", exclude=read_exclude(folder / "exclude.csv"), workers=1
    )

    assert fx.pid(5, 5) in {p.id for p in found.processes}


def test_a_patch_version_goes_into_the_ids(fx) -> None:
    found = read_ecospold2(fx.source, version="3.10.1", exclude=read_exclude(fx.exclude), workers=1)

    assert found.info.version == "3.10.1"
    assert all(p.id.startswith("ecoinvent:3.10.1:cutoff:") for p in found.processes)


@pytest.mark.parametrize(
    ("version", "code"),
    [("3.11", "version-mismatch"), ("3.9", "version-mismatch"), ("3.x", "bad-version")],
)
def test_the_version_must_be_the_newest_release_of_the_datasets(
    fx, version: str, code: str
) -> None:
    with pytest.raises(InvalidDataError) as raised:
        read_ecospold2(fx.source, version=version, exclude=read_exclude(fx.exclude), workers=1)

    assert [(p.code, p.item) for p in raised.value.problems] == [(code, f"version {version}")]


STAMP_3_10 = 'majorRelease="3" minorRelease="10"'
STAMP_3_0 = 'majorRelease="3" minorRelease="0"'


def _stamped_3_0(fx, *, keep: tuple[str, ...] = ()) -> dict[str, list[tuple[str, str]]]:
    """Edits that stamp release 3.0 on every dataset of the fixture, except the files in `keep`.
    ecoinvent 3.12 writes 3.0 in every file."""
    return {
        f"datasets/{p.name}": [(STAMP_3_10, STAMP_3_0)]
        for p in sorted((fx.source / "datasets").glob("*.spold"))
        if p.name not in keep and STAMP_3_10 in p.read_text(encoding="utf-8")
    }


def test_a_source_that_names_no_release_after_3_0_takes_the_version_as_given(
    source_copy: Callable[..., Path], fx
) -> None:
    folder = source_copy(edit=_stamped_3_0(fx))

    found = read_ecospold2(
        folder, version="3.12", exclude=read_exclude(folder / "exclude.csv"), workers=1
    )

    assert all(p.id.startswith("ecoinvent:3.12:cutoff:") for p in found.processes)
    unchecked = [w for w in found.warnings if w.code == "version-unchecked"]
    assert [w.item for w in unchecked] == ["version 3.12"]
    assert "3.0" in unchecked[0].message


def test_a_source_that_names_no_release_after_3_0_still_refuses_an_older_version(
    source_copy: Callable[..., Path], fx
) -> None:
    folder = source_copy(edit=_stamped_3_0(fx))

    with pytest.raises(InvalidDataError) as raised:
        read_ecospold2(
            folder, version="2.2", exclude=read_exclude(folder / "exclude.csv"), workers=1
        )

    assert [(p.code, p.item) for p in raised.value.problems] == [
        ("version-mismatch", "version 2.2")
    ]


def test_one_file_that_names_a_later_release_makes_the_version_checked(
    source_copy: Callable[..., Path], fx
) -> None:
    widget = fx.file_name(1, 1)
    folder = source_copy(edit=_stamped_3_0(fx, keep=(widget,)))

    with pytest.raises(InvalidDataError) as raised:
        read_ecospold2(
            folder, version="3.12", exclude=read_exclude(folder / "exclude.csv"), workers=1
        )

    [problem] = raised.value.problems
    assert (problem.code, problem.item) == ("version-mismatch", "version 3.12")
    assert widget in problem.message


def test_a_source_that_names_its_release_has_no_unchecked_version(source: Inventory) -> None:
    assert not [w for w in source.warnings if w.code == "version-unchecked"]


@pytest.mark.parametrize("group", ["1", "2", "3"])
def test_every_product_input_group_is_an_input(
    source_copy: Callable[..., Path], fx, group: str
) -> None:
    folder = source_copy(
        edit={GAS_POWER: [("<inputGroup>5</inputGroup>", f"<inputGroup>{group}</inputGroup>")]}
    )

    found = read_ecospold2(
        folder, version="3.10", exclude=read_exclude(folder / "exclude.csv"), workers=1
    )

    assert fx.exchange(found, fx.pid(3, 2), fx.line(3, 2)).role is Role.input


def test_a_waste_sent_of_zero_is_stored_as_zero(source_copy: Callable[..., Path], fx) -> None:
    folder = source_copy(edit={WIDGET: [('amount="-0.05"', 'amount="-0.0"')]})

    found = read_ecospold2(
        folder, version="3.10", exclude=read_exclude(folder / "exclude.csv"), workers=1
    )

    waste = fx.exchange(found, fx.pid(1, 1), fx.line(1, 4))
    assert waste.role is Role.waste_to_treatment
    assert str(waste.amount) == "0.0"  # not -0.0, whose bytes would change the release hash
