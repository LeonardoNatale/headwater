"""Mapping the fixture workbook's EF v3.1 to the flow list of the ecoSpold2 fixture release.

Factors are keyed by flow id in the result. A workbook row names its flow by name, compartment
and subcompartment, which must match exactly one elementary flow of the release.
"""

from pathlib import Path

import pytest

from headwater.io.lcia import UnmappedFactor, import_method, read_workbook
from headwater.link import FlowKey
from headwater.model import Factor, Flow, FlowKind, Indicator, InvalidDataError
from headwater.release import Release, ReleaseInfo, build

URBAN = "air/urban air close to ground"
GWP = "global warming potential (GWP100)"
NOT_FOUND = "no elementary flow of the flow list has this name and compartment"


def _unmapped(name: str, compartment: str, where: str) -> UnmappedFactor:
    return UnmappedFactor(FlowKey(name=name, compartment=compartment), where, NOT_FOUND)


def _codes(error: pytest.ExceptionInfo[InvalidDataError]) -> list[str]:
    return [p.code for p in error.value.problems]


def test_the_fixture_converts_with_the_expected_factors(
    workbook: Path, release: Release, fx
) -> None:
    method = import_method(read_workbook(workbook, "EF v3.1"), release).method

    assert (method.id, method.version, method.variant, method.flow_list) == (
        "EF-3.1",
        "3.1",
        "EF v3.1",
        "ecoinvent-3.10",
    )
    gwp = "global warming potential (GWP100)"
    assert method.indicators == (
        Indicator(
            id="climate-change", name="climate change", category_indicator=gwp, unit="kg CO2-Eq"
        ),
        Indicator(
            id="climate-change-fossil",
            name="climate change: fossil",
            category_indicator=gwp,
            unit="kg CO2-Eq",
        ),
        Indicator(
            id="ecotoxicity-freshwater",
            name="ecotoxicity: freshwater",
            category_indicator="comparative toxic unit for ecosystems (CTUe)",
            unit="CTUe",
        ),
    )
    assert method.factors == (
        Factor(indicator="climate-change", flow_id=fx.elementary(1), factor=1.0),
        Factor(indicator="climate-change", flow_id=fx.elementary(3), factor=0.0),
        Factor(indicator="climate-change", flow_id=fx.elementary(4), factor=29.8),
        Factor(indicator="climate-change-fossil", flow_id=fx.elementary(1), factor=1.0),
        Factor(indicator="climate-change-fossil", flow_id=fx.elementary(4), factor=29.8),
    )


def test_the_report_counts_and_lists_per_indicator(workbook: Path, release: Release, fx) -> None:
    result = import_method(read_workbook(workbook, "EF v3.1"), release)
    e1, e2, e3, e4 = (fx.elementary(n) for n in (1, 2, 3, 4))

    assert result.flows == 4
    assert [
        (r.indicator.id, r.mapped, r.unmapped, r.flows_without_factor) for r in result.indicators
    ] == [
        (
            "climate-change",
            3,
            (
                _unmapped("Carbon dioxide, fossil", "air", "CFs!A2"),
                _unmapped("Dinitrogen monoxide", URBAN, "CFs!A5"),
            ),
            (e2,),
        ),
        (
            "climate-change-fossil",
            2,
            (_unmapped("Methane, fossil", "air/non-urban air or from high stacks", "CFs!A8"),),
            (e2, e3),
        ),
        (
            "ecotoxicity-freshwater",
            0,
            (_unmapped("Zinc", "soil/agricultural", "CFs!A10"),),
            (e1, e2, e3, e4),
        ),
    ]


def test_flows_with_no_factor_for_any_indicator_are_listed(
    workbook: Path, release: Release, fx
) -> None:
    result = import_method(read_workbook(workbook, "EF v3.1"), release)

    # Carbon dioxide, in air has a factor of 0 for climate change: it is covered.
    assert [(f.id, f.name) for f in result.not_characterised] == [(fx.elementary(2), "Cadmium")]


def test_names_match_exactly_with_no_case_folding(workbooks, release: Release) -> None:
    row = ["EF v3.1", "climate change", GWP, "methane, fossil", "air", "urban air close to ground"]
    path = workbooks.write(cfs=[*workbooks.sheet("cfs"), [*row, 29.8]])

    result = import_method(read_workbook(path, "EF v3.1"), release)

    [climate, *_] = result.indicators
    assert _unmapped("methane, fossil", URBAN, "CFs!A13") in climate.unmapped


def test_metadata_comes_from_the_workbook_method_name(workbook: Path, release: Release) -> None:
    method = import_method(read_workbook(workbook, "EF v3.1 no LT"), release).method

    assert (method.id, method.version, method.variant) == ("EF-3.1-no-LT", "3.1", "EF v3.1 no LT")
    assert [i.id for i in method.indicators] == ["climate-change-no-lt"]


def test_metadata_can_be_given(workbook: Path, release: Release) -> None:
    method = import_method(
        read_workbook(workbook, "IPCC 2021"),
        release,
        method_id="ipcc",
        version="AR6",
        variant="GWP100",
    ).method

    assert (method.id, method.version, method.variant) == ("ipcc", "AR6", "GWP100")


def test_blank_metadata_takes_the_defaults(workbook: Path, release: Release) -> None:
    method = import_method(
        read_workbook(workbook, "EF v3.1"), release, method_id=" ", version=" ", variant=""
    ).method

    assert (method.id, method.version, method.variant) == ("EF-3.1", "3.1", "EF v3.1")


def test_a_year_in_the_name_is_the_version_when_there_is_no_v_version(
    workbook: Path, release: Release
) -> None:
    method = import_method(read_workbook(workbook, "IPCC 2021"), release).method

    assert (method.id, method.version, method.variant) == ("IPCC-2021", "2021", "IPCC 2021")


def test_a_default_id_drops_brackets_and_dots_that_are_not_in_a_number(
    workbooks, release: Release
) -> None:
    name = "IPCC 2021 (incl. biogenic CO2)"
    row = [name, "climate change: biogenic (incl. CO2)", GWP, "Carbon dioxide, in air"]
    path = workbooks.write(
        cfs=[*workbooks.sheet("cfs"), [*row, "natural resource", "in air", -1]],
        indicators=[*workbooks.sheet("indicators"), [*row[:3], "kg CO2-Eq"]],
    )

    method = import_method(read_workbook(path, name), release).method

    assert (method.id, method.version) == ("IPCC-2021-incl-biogenic-CO2", "2021")
    assert [i.id for i in method.indicators] == ["climate-change-biogenic-incl-co2"]


def test_a_method_name_with_no_version_and_no_year_needs_one(workbooks, release: Release) -> None:
    row = ["ReCiPe", "climate change", GWP, "Methane, fossil", "air", "urban air close to ground"]
    path = workbooks.write(
        cfs=[*workbooks.sheet("cfs"), [*row, 29.8]],
        indicators=[*workbooks.sheet("indicators"), [*row[:3], "kg CO2-Eq"]],
    )

    with pytest.raises(InvalidDataError) as error:
        import_method(read_workbook(path, "ReCiPe"), release)

    assert _codes(error) == ["missing-method-version"]


def test_a_method_id_must_be_a_folder_name(workbook: Path, release: Release) -> None:
    with pytest.raises(InvalidDataError) as error:
        import_method(read_workbook(workbook, "EF v3.1"), release, method_id="EF 3.1")

    assert _codes(error) == ["bad-method-id"]


def test_a_workbook_of_another_version_is_refused(workbooks, release: Release) -> None:
    path = workbooks.write(read_me=[["Database Version", "3.12, test fixture"]])

    with pytest.raises(InvalidDataError) as error:
        import_method(read_workbook(path, "EF v3.1"), release)

    assert _codes(error) == ["flow-list-mismatch"]
    assert "3.12" in str(error.value)
    assert "3.10" in str(error.value)


def _flow_list(
    tmp_path: Path, *flows: Flow, source: str = "ecoinvent", version: str = "3.10"
) -> Release:
    info = ReleaseInfo(
        name="flows",
        source=source,
        version=version,
        system_model=None,
        licence="CC0-1.0",
        flow_list=f"{source}-{version}",
    )
    return build(info, processes=(), flows=flows, into=tmp_path)


def _elementary(uuid: str, name: str, compartment: str) -> Flow:
    return Flow(
        id=f"ecoinvent:flow:{uuid}",
        name=name,
        kind=FlowKind.elementary,
        unit="kg",
        compartment=compartment,
    )


def test_a_release_of_another_source_is_refused(workbook: Path, tmp_path: Path) -> None:
    release = _flow_list(tmp_path, _elementary("e1", "Carbon dioxide, fossil", URBAN), source="toy")

    with pytest.raises(InvalidDataError) as error:
        import_method(read_workbook(workbook, "EF v3.1"), release)

    assert _codes(error) == ["flow-list-mismatch"]
    assert "toy-3.10" in str(error.value)
    assert "ecoinvent-3.10" in str(error.value)


def test_a_factor_whose_name_and_compartment_fits_two_flows_is_refused(
    workbook: Path, tmp_path: Path
) -> None:
    release = _flow_list(
        tmp_path,
        _elementary("e1", "Carbon dioxide, fossil", URBAN),
        _elementary("e9", "Carbon dioxide, fossil", URBAN),
    )

    with pytest.raises(InvalidDataError) as error:
        import_method(read_workbook(workbook, "EF v3.1"), release)

    assert _codes(error) == ["ambiguous-flow"]
    assert "ecoinvent:flow:e1" in str(error.value)
    assert "ecoinvent:flow:e9" in str(error.value)


def test_product_flows_are_not_matched(workbook: Path, tmp_path: Path) -> None:
    product = Flow(
        id="ecoinvent:product:p1",
        name="Zinc",
        kind=FlowKind.product,
        unit="kg",
        compartment="soil/agricultural",
    )
    release = _flow_list(tmp_path, _elementary("e1", "Carbon dioxide, fossil", URBAN), product)

    result = import_method(read_workbook(workbook, "EF v3.1"), release)

    ecotoxicity = result.indicators[-1]
    assert ecotoxicity.mapped == 0
    assert result.flows == 1
