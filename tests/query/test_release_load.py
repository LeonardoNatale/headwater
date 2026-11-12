"""`hw.load` takes a release, by path or by name in the cache, and still takes the toy folder."""

import re
from pathlib import Path

import pytest

import headwater as hw
from headwater.model import InvalidDataError, Method
from headwater.query._internals import internals
from headwater.query._system import Block
from headwater.release import Release, pull
from headwater.study import read_method
from tests.conftest import open_database, toy_database

from .conftest import pinned

TOY = Path(__file__).resolve().parent.parent / "fixtures" / "toy"
METHOD = TOY / "methods" / "toy-climate"
REL = 1e-9


def test_a_release_folder_loads_with_a_method_id(release: Release, calc) -> None:
    background = hw.load(release.path, method="toy-climate")

    assert background.release is not None
    assert background.release.hash == release.hash
    assert background.method.id == "toy-climate"
    assert calc("tshirt", background).score == pytest.approx(1.19205, rel=REL)


def test_a_release_loads_by_name_from_the_cache(
    release: Release, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HEADWATER_HOME", str(tmp_path / "home"))
    pulled = pull(release.path)

    background = hw.load(f"{release.name}@{release.hash[:12]}", method="toy-climate")

    assert background.release is not None
    assert background.release.path == pulled.path


def test_a_name_that_is_not_in_the_cache_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HEADWATER_HOME", str(tmp_path / "home"))

    with pytest.raises(InvalidDataError, match="Pull it first") as info:
        hw.load("toy-background@0123456789ab", method="toy-climate")

    assert [p.code for p in info.value.problems] == ["release-not-found"]


def test_the_toy_folder_keeps_loading(release: Release) -> None:
    folder = hw.load(TOY / "background", method=METHOD, namespace="toy")
    as_text = hw.load(str(TOY / "background"), method=str(METHOD), namespace="toy")

    assert folder.release is None
    assert as_text.release is None
    assert open_database(TOY / "tshirt" / "tshirt").calc(
        folder, "tshirt:tshirt"
    ).score == pytest.approx(1.19205, rel=REL)


def test_a_folder_needs_a_method() -> None:
    with pytest.raises(ValueError, match="method"):
        hw.load(TOY / "background")


def test_a_release_with_one_method_needs_no_method_argument(release: Release) -> None:
    assert hw.load(release.path).method.id == "toy-climate"


def test_a_release_with_two_methods_needs_one_named(make_release, records) -> None:
    co2_only = Method.model_validate(
        {
            **read_method(METHOD).model_dump(),
            "id": "toy-co2-only",
            "factors": [f for f in read_method(METHOD).factors if f.flow_id == "toy:co2"],
        }
    )
    release = make_release(records.with_methods(*records.methods, co2_only))

    with pytest.raises(ValueError, match="toy-climate, toy-co2-only"):
        hw.load(release.path)

    background = hw.load(release.path, method="toy-co2-only")
    # A database names its methods, so score the T-shirt's purchases as SKU lines: 0.25 kg of fabric
    # at 2.0 + 3 × 0.5 = 3.5 per kg, and 0.5 kWh at 0.5.
    skus = hw.score_many(
        background,
        {
            "sku": ["tshirt", "tshirt"],
            "product_id": ["toy:fabric-production", "toy:electricity-production"],
            "amount": [0.25, 0.5],
        },
    )
    assert skus["climate-change"].tolist() == pytest.approx([0.25 * 3.5 + 0.5 * 0.5], rel=REL)


def test_an_unknown_method_id_lists_what_the_release_holds(release: Release) -> None:
    with pytest.raises(ValueError, match=r"toy-climat.*toy-climate") as info:
        hw.load(release.path, method="toy-climat")

    assert "Did you mean 'toy-climate'" in str(info.value)


def test_a_method_folder_works_on_a_release_too(release: Release, calc) -> None:
    background = hw.load(release.path, method=METHOD)

    assert background.method == read_method(METHOD)
    assert calc("tshirt", background).score == pytest.approx(1.19205, rel=REL)


def test_a_method_on_another_flow_list_is_refused(release: Release, tmp_path: Path) -> None:
    method = tmp_path / "method"
    method.mkdir()
    for name in ("method.yaml", "factors.csv"):
        (method / name).write_text((METHOD / name).read_text())
    yaml = method / "method.yaml"
    yaml.write_text(yaml.read_text().replace("flow_list: toy", "flow_list: ecoinvent-3.12"))

    with pytest.raises(InvalidDataError, match="ecoinvent-3.12") as info:
        hw.load(release.path, method=method)

    assert [p.code for p in info.value.problems] == ["flow-list-mismatch"]


def test_a_namespace_is_for_folders_not_releases(release: Release) -> None:
    with pytest.raises(ValueError, match="namespace"):
        hw.load(release.path, method="toy-climate", namespace="toy")


def test_an_indicators_release_is_refused_naming_its_parent(release_background) -> None:
    derived = release_background.write_indicators()

    with pytest.raises(InvalidDataError, match=re.escape(str(derived.parent))) as info:
        hw.load(derived.path)

    assert [p.code for p in info.value.problems] == ["indicators-release"]
    assert "score_many" in str(info.value)


def test_loading_a_release_reads_no_records_and_builds_no_line_objects(
    release: Release, monkeypatch: pytest.MonkeyPatch, calc
) -> None:
    def no_records(self: Release) -> None:
        raise AssertionError("load read every process and every line as a record")

    monkeypatch.setattr(Release, "processes", no_records)

    background = hw.load(release.path, method="toy-climate")

    # The lines are columns of numbers, read by the same query as A and B: no Python object per
    # line, and levers read nothing more from the release.
    block: Block = internals(background).solved.block
    assert len(block.lines.row) == len(block.lines.col) == len(block.lines.amount) == block.A.nnz
    assert len(block.emissions.row) == len(block.emissions.col) == block.B.nnz
    result = calc("tshirt", background)
    assert result.sensitivity()


def test_the_lines_of_a_release_process_carry_the_names_of_their_flows(
    release_background: hw.Background,
) -> None:
    (fabric,) = release_background.processes(["toy:fabric-production"]).values()

    assert [e.flow_name for e in fabric.exchanges] == [
        "cotton fabric",
        "electricity",
        "Carbon dioxide, fossil",
        "Methane, fossil",
    ]


def test_the_flow_names_change_no_input_hash(release_background: hw.Background) -> None:
    """A local override copies a background process from these lines: the names are filled in
    at read time and hash nothing."""
    hashes = {
        "tshirt": "7e13aa79f929f4cf010cf25793a5391b7d7efe8e783b0a1409b704511fa2b471",
        "tshirt-local-electricity": (
            "d8aae898b215b6fa39919e679b6efb75169f7262f35147691949557724c4de08"
        ),
    }
    for name, expected in hashes.items():
        database = pinned(open_database(toy_database(TOY, name)), release_background)
        assert database.calc(release_background, "tshirt:tshirt").provenance.input_hash == expected
