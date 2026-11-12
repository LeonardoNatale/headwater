"""Mapping a method source onto a release: its own flow list, a mapping file, factor units."""

from collections.abc import Callable
from pathlib import Path

import pytest

from headwater.io.inventory import link_inventory
from headwater.io.method import indicator_id, map_method, method_id, method_version, write_method
from headwater.io.source import FactorRow, MethodSource
from headwater.link import FlowKey, read_flow_mapping
from headwater.model import Flow, FlowKind, Indicator, InvalidDataError
from headwater.release import Release, ReleaseInfo, build
from headwater.study import read_method

CLIMATE = Indicator(id="climate", name="Climate", unit="kg CO2-eq")


def _source(*factors: FactorRow, flow_list: str = "gin") -> MethodSource:
    return MethodSource(
        name="climate test",
        id="climate",
        version="1",
        variant=None,
        indicators=(CLIMATE,),
        factors=factors,
        flow_list=flow_list,
        origin="Written by a test.",
        keyed_by="id",
    )


@pytest.fixture
def release(gin, tmp_path: Path) -> Release:
    return link_inventory(gin.source()).freeze(into=tmp_path / "releases")


def test_factors_by_id_map_and_write_a_method_folder(release: Release, tmp_path: Path) -> None:
    mapped = map_method(_source(FactorRow("climate", FlowKey(id="co2"), 1.0)), release)

    folder = write_method(mapped, tmp_path / "climate")

    assert read_method(folder) == mapped.method
    assert mapped.method.flow_list == "gin"
    assert (
        (folder / "method.yaml")
        .read_text()
        .startswith("# Written by a test.\n# Each factor's flow was found by id in release\n")
    )


def test_another_flow_list_is_refused_without_a_mapping_file(release: Release) -> None:
    source = _source(FactorRow("climate", FlowKey(id="toy:co2"), 1.0), flow_list="toy")

    with pytest.raises(InvalidDataError) as raised:
        map_method(source, release)

    [problem] = raised.value.problems
    assert problem.code == "flow-list-mismatch"
    assert "toy" in problem.message and "gin" in problem.message


def test_a_mapping_file_maps_another_flow_list(
    release: Release, tmp_path: Path, csv_file: Callable[[str], Path]
) -> None:
    mapping = read_flow_mapping(
        csv_file(
            "source_id,target,who,when\ntoy:co2,co2,leo,2026-10-24\ntoy:n2o,co2,leo,2026-10-24\n"
        )
    )
    source = _source(
        FactorRow("climate", FlowKey(id="toy:co2"), 1.0),
        FactorRow("climate", FlowKey(id="toy:ch4"), 29.8, where="row 3"),
        flow_list="toy",
    )

    mapped = map_method(source, release, flows=mapping)

    assert [(f.flow_id, f.factor) for f in mapped.method.factors] == [("co2", 1.0)]
    [report] = mapped.indicators
    assert [(u.key, u.where) for u in report.unmapped] == [(FlowKey(id="toy:ch4"), "row 3")]
    assert "mapping.csv" in report.unmapped[0].reason
    assert [w.code for w in mapped.warnings] == ["row-not-needed"]
    text = (write_method(mapped, tmp_path / "climate") / "method.yaml").read_text()
    assert "The rows of mapping.csv decided the flows they name." in text


def test_a_factor_per_another_unit_is_converted_within_one_quantity(release: Release) -> None:
    source = _source(
        FactorRow("climate", FlowKey(id="co2"), 0.001, unit="g"),
    )

    mapped = map_method(source, release)

    [factor] = mapped.method.factors
    assert factor.factor == pytest.approx(1.0, rel=1e-12)


def test_a_factor_per_a_unit_of_another_quantity_is_unmapped(release: Release) -> None:
    mapped = map_method(_source(FactorRow("climate", FlowKey(id="co2"), 1.0, unit="kWh")), release)

    [report] = mapped.indicators
    [unmapped] = report.unmapped
    assert "does not convert" in unmapped.reason
    assert mapped.method.factors == ()


def test_two_factors_on_one_flow_are_refused(
    release: Release, csv_file: Callable[[str], Path]
) -> None:
    mapping = read_flow_mapping(csv_file("source_id,target,who,when\ntoy:co2,co2,leo,2026-10-24\n"))
    source = _source(
        FactorRow("climate", FlowKey(id="co2"), 1.0),
        FactorRow("climate", FlowKey(id="toy:co2"), 1.0),
    )

    with pytest.raises(InvalidDataError) as raised:
        map_method(source, release, flows=mapping)

    assert [p.code for p in raised.value.problems] == ["duplicate-factor"]


@pytest.mark.parametrize(
    ("name", "version", "id"),
    [
        ("EF v3.1", "3.1", "EF-3.1"),
        ("EF v3.1 no LT", "3.1", "EF-3.1-no-LT"),
        ("EF 3.1", "3.1", "EF-3.1"),
        ("IPCC 2021 (incl. biogenic CO2)", "2021", "IPCC-2021-incl-biogenic-CO2"),
        ("ReCiPe 2016 v1.03, midpoint (H)", "1.03", "ReCiPe-2016-1.03-midpoint-H"),
        ("toy climate v1", "1", "toy-climate-1"),
    ],
)
def test_a_methods_name_gives_its_default_version_and_id(name: str, version: str, id: str) -> None:
    assert method_version(name) == version
    assert method_id(name, version) == id


def test_a_name_without_a_version_gives_none_and_an_id_with_the_version_given() -> None:
    assert method_version("EPS 2020d") is None
    assert method_id("AR6", "2021") == "AR6-2021"
    assert indicator_id("climate change: fossil") == "climate-change-fossil"


CO2 = "Carbon dioxide, fossil"


def _air(tmp_path: Path, *flows: tuple[str, str]) -> Release:
    """A release of elementary flows only, by name and compartment, with the flow list `gin`."""
    info = ReleaseInfo(name="air", source="air", version="1", licence="CC0-1.0", flow_list="gin")
    return build(
        info,
        processes=(),
        flows=[
            Flow(id=f"{n}|{c}", name=n, kind=FlowKind.elementary, unit="kg", compartment=c)
            for n, c in flows
        ],
        into=tmp_path,
    )


def _covering(compartment: str, factor: float, *, covers: bool = True) -> FactorRow:
    flow = FlowKey(name=CO2, compartment=compartment)
    return FactorRow("climate", flow, factor, covers_subcompartments=covers)


def test_a_factor_that_covers_subcompartments_goes_to_those_with_none_of_their_own(
    tmp_path: Path,
) -> None:
    release = _air(
        tmp_path,
        (CO2, "air"),
        (CO2, "air/high. pop."),
        (CO2, "air/urban air close to ground"),
        (CO2, "water/river"),
        ("Methane, fossil", "air/high. pop."),
    )
    source = _source(_covering("air", 1.0), _covering("air/high. pop.", 2.0, covers=False))

    mapped = map_method(source, release)
    folder = write_method(mapped, tmp_path / "climate")

    assert {f.flow_id: f.factor for f in mapped.method.factors} == {
        f"{CO2}|air": 1.0,
        f"{CO2}|air/high. pop.": 2.0,
        f"{CO2}|air/urban air close to ground": 1.0,
    }
    [report] = mapped.indicators
    assert (report.factors, report.mapped, report.copied, report.unmapped) == (2, 2, 1, ())
    assert "1 factor was copied to subcompartments" in (folder / "method.yaml").read_text()


def test_a_factor_whose_own_flow_is_absent_still_covers_the_subcompartments(
    tmp_path: Path,
) -> None:
    release = _air(tmp_path, (CO2, "air/urban air close to ground"), (CO2, "air/high. pop."))

    mapped = map_method(_source(_covering("air", 1.0)), release)

    assert [f.factor for f in mapped.method.factors] == [1.0, 1.0]
    [report] = mapped.indicators
    assert (report.mapped, report.copied) == (0, 2)
    [unmapped] = report.unmapped
    assert "copied to 2 flows of its subcompartments" in unmapped.reason


def test_a_factor_without_the_flag_covers_its_own_flow_only(tmp_path: Path) -> None:
    release = _air(tmp_path, (CO2, "air"), (CO2, "air/urban air close to ground"))

    mapped = map_method(_source(_covering("air", 1.0, covers=False)), release)

    assert [f.flow_id for f in mapped.method.factors] == [f"{CO2}|air"]
    [report] = mapped.indicators
    assert report.copied == 0
    assert report.flows_without_factor == (f"{CO2}|air/urban air close to ground",)
