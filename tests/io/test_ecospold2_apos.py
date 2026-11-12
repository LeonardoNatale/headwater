"""The APOS system model: its own ids, release name and hash, its treatments of Recyclable and
allocatable flows, from the APOS fixture source."""

import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

from headwater.calc.signs import Matrix, matrix_entry
from headwater.io.ecospold2 import import_ecospold2, read_ecospold2
from headwater.io.ecospold2.dataset import ROLES, SYSTEM_MODELS
from headwater.io.exclude import read_exclude
from headwater.model import Catalog, FlowKind, InvalidDataError, Role
from headwater.model.uncertainty import LinkStatus
from headwater.release import ReleaseInfo

APOS_ID = "06590a66-662a-4885-8494-ad0cf410f956"
CUTOFF_ID = "290c1f85-4cc4-4fa1-b0c8-2cb7f4276dce"

# (activity, product, line, role, supplier as (activity, product))
ROWS: list[tuple[int, int, int, Role, tuple[int, int] | None]] = [
    (1, 1, 1, Role.reference, None),
    (1, 1, 2, Role.input, (2, 2)),
    (1, 1, 3, Role.input, (6, 3)),
    (1, 1, 4, Role.waste_to_treatment, (4, 4)),
    (1, 1, 5, Role.waste_to_treatment, (14, 5)),
    (1, 1, 6, Role.emission, None),
    (1, 1, 7, Role.emission, None),
    (1, 1, 8, Role.resource, None),
    (2, 2, 1, Role.reference, None),
    (2, 2, 2, Role.own_use, (2, 2)),
    (2, 2, 3, Role.input, (3, 2)),
    (3, 2, 1, Role.reference, None),
    (3, 2, 2, Role.input, (6, 3)),
    (3, 2, 3, Role.emission, None),
    (3, 2, 4, Role.waste_to_treatment, (15, 6)),
    (4, 4, 1, Role.reference, None),
    (4, 4, 2, Role.emission, None),
    (6, 3, 1, Role.reference, None),
    (6, 3, 2, Role.input, (7, 3)),
    (7, 3, 1, Role.reference, None),
    (7, 3, 2, Role.emission, None),
    (14, 5, 1, Role.reference, None),
    (14, 5, 2, Role.emission, None),
    (15, 6, 1, Role.reference, None),
    (15, 6, 2, Role.emission, None),
]
"""Every line of the APOS fixture. Widget production sends 0.02 kg of scrap metal (Recyclable) to
its recycling, 14, and electricity production 0.01 spare part (allocatable) to its reuse, 15."""

APOS_RELEASE_HASH = "4c5002ebdd80378ba195942890a20f26e82bf049b0dfccbf1d3b714580b923a6"
"""The APOS fixture frozen. Change it only with a deliberate change of what the importer reads."""


def test_an_apos_source_reads_with_its_own_ids_and_name(fx) -> None:
    found = read_ecospold2(fx.apos, version="3.10", workers=1)

    assert found.info == ReleaseInfo(
        name="ecoinvent-3.10-apos",
        source="ecoinvent",
        version="3.10",
        system_model="apos",
        licence="ecoinvent EULA",
        flow_list="ecoinvent-3.10",
    )
    lines = {(e.process_id, e.line_id): e for p in found.processes for e in p.exchanges}
    assert len(lines) == len(ROWS)
    for a, p, n, role, supplier in ROWS:
        e = lines[(fx.pid(a, p, "apos"), fx.line(a, n))]
        assert (e.role, e.supplier_id) == (role, fx.pid(*supplier, "apos") if supplier else None)


def test_a_flow_a_dataset_treats_is_a_waste_whatever_its_class(fx) -> None:
    found = read_ecospold2(fx.apos, version="3.10", workers=1)

    kinds = {f.id: f.kind for f in found.flows if f.kind is not FlowKind.elementary}
    assert kinds == {
        fx.product(1): FlowKind.product,
        fx.product(2): FlowKind.product,
        fx.product(3): FlowKind.product,
        fx.product(4): FlowKind.waste,
        fx.product(5): FlowKind.waste,
        fx.product(6): FlowKind.waste,
    }


@pytest.mark.parametrize(
    ("buyer", "line", "treatment", "sent"),
    [((1, 1), 5, (14, 5), 0.02), ((3, 2), 4, (15, 6), 0.01)],
    ids=["Recyclable", "allocatable"],
)
def test_a_line_sent_to_a_treatment_gets_the_signs_of_a_waste_sent(
    fx, buyer: tuple[int, int], line: int, treatment: tuple[int, int], sent: float
) -> None:
    # As in ecoinvent's matrix: the treatment's reference is -1, the line sent is +kg in its row.
    found = read_ecospold2(fx.apos, version="3.10", workers=1)
    catalog = Catalog(found.flows, found.processes, found.flow_properties, found.units)
    treats = fx.process(found, fx.pid(*treatment, "apos"))
    e = fx.exchange(found, fx.pid(*buyer, "apos"), fx.line(buyer[0], line))

    reference = matrix_entry(Role.reference, 1.0, treatment=catalog.is_treatment(treats))
    entry = matrix_entry(e.role, e.amount)

    assert reference is not None and reference.value == -1.0
    assert entry is not None and entry.matrix is Matrix.technosphere
    assert entry.value == pytest.approx(sent, rel=1e-9)


def test_every_system_model_has_a_role_table() -> None:
    assert set(ROLES) == set(SYSTEM_MODELS.values()) == {"cutoff", "apos"}


def test_a_flow_sent_to_an_excluded_treatment_is_still_a_waste(fx, tmp_path: Path) -> None:
    # The widget sends scrap metal to 14, which is left out: the line is a data gap, and the
    # flow is a waste because the widget sends it to treatment.
    folder = _apos_copy(fx, tmp_path)
    (folder / "exclude.csv").write_text(
        "file,reason,resolved_by,resolved_at\n"
        f"{fx.file_name(14, 5)},left out,fixture author,2026-11-01T09:00:00+00:00\n",
        encoding="utf-8",
    )

    found = read_ecospold2(
        folder, version="3.10", exclude=read_exclude(folder / "exclude.csv"), workers=1
    )
    release = found.freeze(into=tmp_path / "releases")

    e = fx.exchange(found, fx.pid(1, 1, "apos"), fx.line(1, 5))
    assert (e.role, e.supplier_id, e.link_status) == (
        Role.waste_to_treatment,
        None,
        LinkStatus.data_gap,
    )
    assert {f.id: f.kind for f in release.flows()}[fx.product(5)] is FlowKind.waste


def test_cut_off_refuses_the_lines_only_apos_reads(fx, tmp_path: Path) -> None:
    # Cut-off's table is not loosened: the two treatments and the allocatable product sent to one
    # have no role there. The Recyclable sent away is cut off, so it is no error.
    folder = _apos_copy(fx, tmp_path, system_model_id=CUTOFF_ID)

    with pytest.raises(InvalidDataError) as raised:
        read_ecospold2(folder, version="3.10", workers=1)

    assert [(p.code, p.item) for p in raised.value.problems] == [
        ("unexpected-sign", f"{fx.pid(3, 2)} line {fx.line(3, 4)}"),
        ("unexpected-sign", f"{fx.pid(14, 5)} line {fx.line(14, 1)}"),
        ("unexpected-sign", f"{fx.pid(15, 6)} line {fx.line(15, 1)}"),
    ]


@pytest.mark.parametrize(
    ("edit", "add", "code_item", "how"),
    [
        (
            {"3_2": [('amount="-0.01"', 'amount="0.01"')]},
            False,
            (3, 2, 4),
            "buys",
        ),
        ({}, True, (16, 6, 1), "makes"),
    ],
    ids=["buys", "makes"],
)
def test_a_treated_flow_made_or_bought_as_a_product_fails_naming_the_line(
    fx,
    tmp_path: Path,
    edit: dict[str, list[tuple[str, str]]],
    add: bool,
    code_item: tuple[int, int, int],
    how: str,
) -> None:
    folder = _apos_copy(fx, tmp_path, edit=edit)
    if add:
        # A spare part factory: a positive reference of the flow that 15 treats.
        text = (folder / "datasets" / fx.file_name(15, 6)).read_text(encoding="utf-8")
        text = text.replace(fx.activity(15), fx.activity(16)).replace("c0000015-", "c0000016-")
        text = text.replace('amount="-1.0"', 'amount="1.0"')
        (folder / "datasets" / fx.file_name(16, 6)).write_text(text, encoding="utf-8")

    with pytest.raises(InvalidDataError) as raised:
        read_ecospold2(folder, version="3.10", workers=1)

    a, p, n = code_item
    [problem] = raised.value.problems
    assert (problem.code, problem.item) == (
        "treated-flow-as-product",
        f"{fx.pid(a, p, 'apos')} line {fx.line(a, n)}",
    )
    assert problem.message.startswith(f"{how} flow {fx.product(6)} (spare part)")
    assert fx.file_name(15, 6) in problem.message


def test_the_apos_release_hash_is_pinned_and_does_not_depend_on_workers(fx, tmp_path: Path) -> None:
    one = import_ecospold2(fx.apos, version="3.10", workers=1, into=tmp_path / "one")
    two = import_ecospold2(fx.apos, version="3.10", workers=2, into=tmp_path / "two")

    assert one.path.name == f"ecoinvent-3.10-apos@{one.hash[:12]}"
    assert one.hash == two.hash == APOS_RELEASE_HASH


def test_a_folder_that_mixes_system_models_fails_naming_the_files(
    source_copy: Callable[..., Path], fx
) -> None:
    widget = f"datasets/{fx.file_name(1, 1)}"
    folder = source_copy(edit={widget: [(CUTOFF_ID, APOS_ID)]})

    with pytest.raises(InvalidDataError) as raised:
        read_ecospold2(
            folder, version="3.10", exclude=read_exclude(folder / "exclude.csv"), workers=1
        )

    problems = [p for p in raised.value.problems if p.code == "mixed-system-models"]
    assert [p.item for p in problems] == ["datasets/"]
    assert "apos: 1 file" in problems[0].message
    assert fx.file_name(1, 1) in problems[0].message
    assert "cutoff: 7 files" in problems[0].message


def _apos_copy(
    fx,
    tmp_path: Path,
    *,
    system_model_id: str = APOS_ID,
    edit: dict[str, list[tuple[str, str]]] | None = None,
    remove: tuple[str, ...] = (),
) -> Path:
    """A copy of the APOS fixture. `edit` maps `<activity>_<product>` to text replacements."""
    target = tmp_path / "apos"
    _copy_with_system_model(fx.apos, target, system_model_id)
    for name in remove:
        (target / "datasets" / name).unlink()
    for key, replacements in (edit or {}).items():
        path = target / "datasets" / fx.file_name(*map(int, key.split("_")))
        text = path.read_text(encoding="utf-8")
        for old, new in replacements:
            assert old in text, f"{old!r} is not in {path.name}"
            text = text.replace(old, new)
        path.write_text(text, encoding="utf-8")
    return target


def _copy_with_system_model(source: Path, target: Path, system_model_id: str) -> None:
    shutil.copytree(source / "MasterData", target / "MasterData")
    (target / "datasets").mkdir()
    for path in (source / "datasets").iterdir():
        text = path.read_text(encoding="utf-8").replace(APOS_ID, system_model_id)
        (target / "datasets" / path.name).write_text(text, encoding="utf-8")
