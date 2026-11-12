"""Exit tests of T3, matrices from a release: the toy, built as a release, through the public API.

1. Every M0 number comes out of a release: 1.19205, 1.25205 with the waste line, 0.13245, tree
   sums and sensitivity. Each is also compared with the same calculation from the toy folder.
2. The indicators file of the toy matches calc's scores, and building it leaves the release it
   comes from unchanged.
3. 1,000 synthetic SKUs on the toy match a loop over single calcs.
4. Cut-off and data-gap lines in assembly and validation: tests/query/test_cut_off_lines.py.
"""

import shutil
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pytest

import headwater as hw
from headwater.calc import scores
from headwater.model import (
    Database,
    Exchange,
    Flow,
    FlowKind,
    Pin,
    Process,
    ProcessKind,
    Role,
)
from headwater.query._internals import internals
from headwater.release import Release, ReleaseInfo, build, content_hash
from headwater.study import DatabaseFolder, read_method, read_process_folder
from tests.conftest import approx_numbers, open_database, toy_database

TOY = Path(__file__).resolve().parent / "fixtures" / "toy"
REL = 1e-9  # AGENTS.md: relative 1e-9 for toy fixtures

INFO = ReleaseInfo(
    name="toy-background",
    source="toy",
    version="1",
    system_model="cutoff",
    licence="CC0-1.0",
    flow_list="toy",
)


def build_toy(into: Path, root: Path = TOY) -> Release:
    """Freeze the toy background folder and its method into a release."""
    folder = read_process_folder(root / "background", namespace="toy")
    return build(
        INFO,
        processes=folder.processes,
        flows=folder.flows,
        flow_properties=folder.flow_properties,
        methods=[read_method(root / "methods" / "toy-climate")],
        into=into,
    )


def load_folder() -> hw.Background:
    return hw.load(TOY / "background", method=TOY / "methods" / "toy-climate", namespace="toy")


def pinned(study: hw.Database, background: hw.Background) -> hw.Database:
    """The study pinned to the release `background` was read from: the toy studies pin a label,
    a real study pins its release."""
    release = background.release
    if release is None:
        return study
    pin = Pin(name=release.name, version=release.hash[:12])
    folder = replace(
        study.folder, database=study.folder.database.model_copy(update={"background": pin})
    )
    return hw.Database(folder)


def calc(study: str, background: hw.Background, root: Path = TOY) -> hw.Result:
    return pinned(open_database(toy_database(root, study)), background).calc(
        background, "tshirt:tshirt"
    )


def snapshot(path: Path) -> dict[Path, bytes]:
    """Every file under a folder, with its bytes. Equal snapshots mean nothing changed."""
    return {p.relative_to(path): p.read_bytes() for p in path.rglob("*") if p.is_file()}


def walk(node: hw.Contribution) -> Iterator[hw.Contribution]:
    yield node
    for child in node.children:
        yield from walk(child)


def lever(levers: tuple[hw.Sensitivity, ...], process_id: str, line_id: str) -> hw.Sensitivity:
    (found,) = [x for x in levers if (x.process_id, x.line_id) == (process_id, line_id)]
    return found


@pytest.fixture(scope="module")
def release(tmp_path_factory: pytest.TempPathFactory) -> Release:
    return build_toy(tmp_path_factory.mktemp("releases"))


@pytest.fixture(scope="module")
def background(release: Release) -> hw.Background:
    return hw.load(release.path, method="toy-climate")


@pytest.fixture(scope="module")
def folder_background() -> hw.Background:
    return load_folder()


# 1. The M0 numbers, from a release


def test_tshirt_scores_1_19205_from_a_release(background: hw.Background) -> None:
    result = calc("tshirt", background)

    assert result.score == pytest.approx(1.19205, rel=REL)
    assert result.process_id == "tshirt:tshirt"
    assert (result.indicator, result.unit) == ("climate-change", "kg CO2-eq")


def test_tshirt_with_the_waste_line_scores_1_25205_from_a_release(
    background: hw.Background,
) -> None:
    assert calc("tshirt-waste", background).score == pytest.approx(1.25205, rel=REL)


@pytest.mark.parametrize("study", ["tshirt", "tshirt-waste"])
def test_forward_road_equals_backward_road_from_a_release(
    background: hw.Background, study: str
) -> None:
    result = calc(study, background)

    assert result.forward_score == pytest.approx(result.score, rel=1e-12)


def test_sensitivity_to_kwh_per_kg_fabric_is_0_13245_from_a_release(
    background: hw.Background,
) -> None:
    found = lever(calc("tshirt", background).sensitivity(), "toy:fabric-production", "e02")

    assert (found.flow_id, found.supplier_id, found.unit) == (
        "toy:electricity",
        "toy:electricity-production",
        "kWh",
    )
    assert found.amount == pytest.approx(3.0, rel=REL)
    assert found.sensitivity == pytest.approx(0.13245, rel=REL)
    assert found.elasticity == pytest.approx(1 / 3, rel=REL)


def test_sensitivity_matches_a_finite_difference_from_a_release(
    tmp_path: Path, background: hw.Background
) -> None:
    sensitivity = lever(
        calc("tshirt", background).sensitivity(), "toy:fabric-production", "e02"
    ).sensitivity
    root = tmp_path / "toy"
    shutil.copytree(TOY, root)
    csv = root / "background" / "processes" / "fabric-production.csv"
    text = csv.read_text()
    assert "e02,input,toy:electricity,3,kWh" in text
    csv.write_text(
        text.replace("e02,input,toy:electricity,3,kWh", "e02,input,toy:electricity,4,kWh")
    )
    changed = hw.load(build_toy(tmp_path / "releases", root).path, method="toy-climate")

    after = calc("tshirt", changed, root)

    assert after.score == pytest.approx(1.32450, rel=REL)
    assert after.score - 1.19205 == pytest.approx(sensitivity * 1.0, rel=REL)


def test_the_levers_table_of_calculation_md_from_a_release(background: hw.Background) -> None:
    levers = calc("tshirt", background).sensitivity(top=5)

    # docs/calculation.md, "Python API": process, line, elasticity, sensitivity.
    assert [
        (x.process_id, x.line_id, round(x.elasticity, 4), round(x.sensitivity, 5)) for x in levers
    ] == [
        ("tshirt:tshirt", "e02", 0.7778, 3.7086),
        ("toy:electricity-production", "e02", 0.5243, 1.25),
        ("toy:fabric-production", "e03", 0.4194, 0.25),
        ("toy:fabric-production", "e02", 0.3333, 0.13245),
        ("tshirt:tshirt", "e03", 0.2222, 0.5298),
    ]
    assert [x.role for x in levers] == [
        Role.input,
        Role.emission,
        Role.emission,
        Role.input,
        Role.input,
    ]
    emissions = [x for x in calc("tshirt", background).sensitivity() if x.role is Role.emission]
    assert sum(x.elasticity for x in emissions) == pytest.approx(1.0, rel=REL)


def test_sensitivity_of_the_waste_line_from_a_release(background: hw.Background) -> None:
    found = lever(calc("tshirt-waste", background).sensitivity(), "tshirt:tshirt", "e04")

    assert (found.flow_id, found.supplier_id, found.unit) == (
        "toy:textile-waste",
        "toy:incineration-textile-waste",
        "kg",
    )
    assert found.amount == pytest.approx(0.05, rel=REL)
    assert found.sensitivity == pytest.approx(1.2, rel=REL)
    assert found.elasticity == pytest.approx(0.06 / 1.25205, rel=REL)


@pytest.mark.parametrize("study", ["tshirt", "tshirt-waste"])
@pytest.mark.parametrize(("min_share", "max_depth"), [(0.0, 10), (0.03, 3), (0.5, 1)])
def test_explanation_sums_to_the_score_at_every_level_from_a_release(
    background: hw.Background, study: str, min_share: float, max_depth: int
) -> None:
    result = calc(study, background)

    root = result.contributions(min_share=min_share, max_depth=max_depth)

    assert root.score == pytest.approx(result.score, rel=1e-12)
    for node in walk(root):
        parts = node.direct + sum(child.score for child in node.children) + node.rest
        assert parts == pytest.approx(node.score, rel=1e-12, abs=1e-15)
        assert node.score == pytest.approx(node.amount * node.per_unit, rel=1e-12)


def test_explanation_matches_the_worked_example_from_a_release(background: hw.Background) -> None:
    root = calc("tshirt-waste", background).contributions(min_share=0.0)

    assert (root.process_id, root.name, root.unit) == ("tshirt:tshirt", "T-shirt", "unit")
    by_id = {c.process_id: c for c in root.children}
    fabric = by_id["toy:fabric-production"]
    assert fabric.per_unit == pytest.approx(3.7086, rel=REL)
    assert fabric.score == pytest.approx(0.92715, rel=REL)
    assert by_id["toy:electricity-production"].score == pytest.approx(0.26490, rel=REL)
    # Names and units of the background come from the release.
    assert (fabric.name, fabric.unit) == ("cotton fabric production", "kg")
    # The waste is positive kg sent, and positive burden per kg treated.
    incineration = by_id["toy:incineration-textile-waste"]
    assert (incineration.amount, incineration.per_unit, incineration.score) == (
        pytest.approx(0.05, rel=REL),
        pytest.approx(1.2, rel=REL),
        pytest.approx(0.06, rel=REL),
    )


@pytest.mark.parametrize("study", ["tshirt", "tshirt-waste"])
def test_a_release_gives_what_the_toy_folder_gives(
    background: hw.Background, folder_background: hw.Background, study: str
) -> None:
    from_release = calc(study, background)
    from_folder = calc(study, folder_background)

    assert from_release.score == pytest.approx(from_folder.score, rel=1e-12)
    assert from_release.forward_score == pytest.approx(from_folder.forward_score, rel=1e-12)

    def levers(result: hw.Result) -> list[tuple[object, ...]]:
        return [
            (x.process_id, x.line_id, x.role, x.flow_id, x.supplier_id, x.unit)
            for x in result.sensitivity(include_reference=True)
        ]

    assert levers(from_release) == levers(from_folder)
    for a, b in zip(
        from_release.sensitivity(include_reference=True),
        from_folder.sensitivity(include_reference=True),
        strict=True,
    ):
        assert a.sensitivity == pytest.approx(b.sensitivity, rel=1e-9, abs=1e-15)
        assert a.elasticity == pytest.approx(b.elasticity, rel=1e-9, abs=1e-15)
    assert [
        (n.process_id, n.name, n.unit, n.score)
        for n in walk(from_release.contributions(min_share=0))
    ] == approx_numbers(
        [
            (n.process_id, n.name, n.unit, n.score)
            for n in walk(from_folder.contributions(min_share=0))
        ]
    )


def test_the_matrices_of_a_release_are_those_of_the_toy_folder(
    background: hw.Background, folder_background: hw.Background
) -> None:
    from_release, from_folder = internals(background).solved, internals(folder_background).solved

    assert from_release.block.ids == from_folder.block.ids
    assert from_release.block.names == from_folder.block.names
    assert from_release.block.flow_ids == from_folder.block.flow_ids
    assert from_release.block.units == from_folder.block.units
    assert from_release.block.flows == from_folder.block.flows
    np.testing.assert_array_equal(from_release.block.A.toarray(), from_folder.block.A.toarray())
    np.testing.assert_array_equal(from_release.block.B.toarray(), from_folder.block.B.toarray())
    np.testing.assert_array_equal(
        from_release.block.reference_sign, from_folder.block.reference_sign
    )
    for indicator, solution in from_folder.x.items():
        np.testing.assert_allclose(from_release.x[indicator].values, solution.values, rtol=1e-14)
    assert background.not_characterised == folder_background.not_characterised


# 2. The indicators file


def test_the_indicators_file_matches_calcs_scores(
    background: hw.Background, release: Release
) -> None:
    derived = background.write_indicators()

    table = Release.open(derived.path).table("indicators/toy-climate")
    stored = dict(
        zip(
            table.column("product_id").to_pylist(),
            table.column("climate-change").to_pylist(),
            strict=True,
        )
    )
    # calc solves Aᵀ x = h. A treatment's x is the negative of its burden per kg treated, so the
    # file holds x times the sign of the process's reference: score per unit of what it makes
    # or treats.
    solved = internals(background).solved
    x = scores(solved.factorization, solved.h["climate-change"]).values
    expected = dict(zip(solved.ids, x * solved.block.reference_sign, strict=True))
    assert stored == pytest.approx(expected, rel=1e-12)
    # The hand-checked numbers of docs/calculation.md.
    assert stored == pytest.approx(
        {
            "toy:electricity-production": 0.5298,
            "toy:fabric-production": 3.7086,
            "toy:incineration-textile-waste": 1.2,
        },
        rel=REL,
    )
    assert table.schema.field("climate-change").type == pa.float64()
    assert derived.kind.value == "indicators"
    assert derived.parent == release.ref


def test_building_the_indicators_file_leaves_the_parent_unchanged(
    tmp_path: Path, release: Release
) -> None:
    before = snapshot(release.path)
    names_before = sorted(p.name for p in release.path.parent.iterdir())

    background = hw.load(release.path, method="toy-climate")
    derived = background.write_indicators(into=tmp_path / "derived")

    assert snapshot(release.path) == before
    assert sorted(p.name for p in release.path.parent.iterdir()) == names_before
    assert content_hash(release.path) == release.hash
    assert Release.open(release.path).hash == release.hash
    assert derived.path.parent == tmp_path / "derived"
    assert derived.parent == release.ref
    # The same background gives the same derived release.
    again = background.write_indicators(into=tmp_path / "derived")
    assert again.hash == derived.hash


# 3. Many SKUs


def sku_study(name: str, fabric: float, electricity: float, waste: float) -> hw.Database:
    """A study of one process that buys from the toy background, built in memory."""
    flow = Flow(id=f"{name}:sku", name="SKU", kind=FlowKind.product, unit="unit")
    process_id = f"{name}:sku"
    lines = [
        ("e01", Role.reference, flow.id, 1.0, "unit", None),
        ("e02", Role.input, "toy:cotton-fabric", fabric, "kg", "toy:fabric-production"),
        ("e03", Role.input, "toy:electricity", electricity, "kWh", "toy:electricity-production"),
        (
            "e04",
            Role.waste_to_treatment,
            "toy:textile-waste",
            waste,
            "kg",
            "toy:incineration-textile-waste",
        ),
    ]
    process = Process(
        id=process_id,
        name="SKU",
        location="GLO",
        kind=ProcessKind.ordinary_transforming_activity,
        exchanges=tuple(
            Exchange(
                process_id=process_id,
                line_id=line,
                role=role,
                flow_id=flow_id,
                amount=amount,
                unit=unit,
                supplier_id=supplier,
            )
            for line, role, flow_id, amount, unit, supplier in lines
        ),
    )
    database = Database.model_validate(
        {"name": name, "background": "toy-background@fixture", "methods": ["toy-climate"]}
    )
    folder = DatabaseFolder(
        path=TOY,
        namespace=name,
        processes=(process,),
        flows=(flow,),
        flow_properties=(),
        units=(),
        database=database,
    )
    return hw.Database(folder)


def test_1000_skus_match_a_loop_over_single_calcs(background: hw.Background) -> None:
    n = 1000
    rng = np.random.default_rng(20261011)
    fabric = rng.uniform(0.0, 2.0, n)
    electricity = rng.uniform(0.0, 5.0, n)
    waste = np.where(rng.random(n) < 0.5, rng.uniform(0.0, 0.3, n), 0.0)  # half send no waste
    products = [
        ("toy:fabric-production", fabric),
        ("toy:electricity-production", electricity),
        ("toy:incineration-textile-waste", waste),
    ]
    columns: dict[str, Any] = {
        "sku": [f"sku{i:04d}" for _ in products for i in range(n)],
        "product_id": [pid for pid, amounts in products for _ in amounts],
        "amount": np.concatenate([amounts for _, amounts in products]),
    }
    table = pa.table(columns)

    skus = hw.score_many(background, table)

    assert skus.skus == tuple(f"sku{i:04d}" for i in range(n))
    assert skus.indicators == ("climate-change",)
    assert skus["climate-change"].shape == (n,)
    # One calc per SKU. Its score is the stored scores times the amounts (the backward road),
    # and `forward_score` is h · s from a forward solve: the other road, which reads no scores.
    for i in range(n):
        study = pinned(sku_study(f"sku{i:04d}", fabric[i], electricity[i], waste[i]), background)
        result = study.calc(background, f"sku{i:04d}:sku")
        assert skus["climate-change"][i] == pytest.approx(result.score, rel=1e-9, abs=1e-12)
        assert skus["climate-change"][i] == pytest.approx(result.forward_score, rel=1e-9, abs=1e-12)
    # A hand check on SKU 0: 0.92715 per 0.25 kg of fabric is 3.7086 per kg, and so on.
    expected = 3.7086 * fabric[0] + 0.5298 * electricity[0] + 1.2 * waste[0]
    assert skus["climate-change"][0] == pytest.approx(expected, rel=REL)
