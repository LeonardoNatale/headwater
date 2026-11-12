"""What `Database.validate` and `Database.calc` check against a background: the database's records
against only the part of the background they name, and its locations against the release's."""

from pathlib import Path

import pyarrow as pa
import pytest

import headwater as hw
from headwater.model import Catalog
from headwater.query._hash import records_of
from headwater.release import ReleaseInfo, build
from tests.conftest import toy_database

REL = 1e-9
INFO = ReleaseInfo(
    name="toy-background",
    source="toy",
    version="1",
    system_model="cutoff",
    licence="CC0-1.0",
    flow_list="toy",
)
GEOGRAPHIES = pa.table({"code": ["GLO", "RER", "RoW"], "name": ["Global", "Europe", "Rest"]})


@pytest.fixture
def placed(tmp_path: Path, records) -> hw.Background:
    """The toy release with a geographies table."""
    release = build(
        INFO,
        processes=records.processes,
        flows=records.flows,
        methods=records.methods,
        geographies=GEOGRAPHIES,
        into=tmp_path,
    )
    return hw.load(release.path, method="toy-climate")


def set_location(root: Path, location: str) -> None:
    path = root / "tshirt" / "tshirt" / "processes" / "tshirt.yaml"
    path.write_text(path.read_text().replace("location: GLO", f"location: {location}"))


def test_a_location_the_release_does_not_name_is_refused(placed, calc, toy: Path) -> None:
    set_location(toy, "ROW")

    with pytest.raises(hw.InvalidDataError) as raised:
        calc("tshirt", placed, root=toy)

    (problem,) = raised.value.problems
    assert (problem.code, problem.item) == ("unknown-location", "process tshirt:tshirt")
    assert "'ROW'" in problem.message
    assert "Did you mean 'RoW'?" in problem.message


def test_a_location_the_release_names_is_accepted(placed, calc, toy: Path) -> None:
    set_location(toy, "RER")

    assert calc("tshirt", placed, root=toy).score == pytest.approx(1.19205, rel=REL)


def test_with_no_geographies_there_is_nothing_to_check(either, calc, toy: Path) -> None:
    set_location(toy, "XX")

    assert calc("tshirt", either, root=toy).score == pytest.approx(1.19205, rel=REL)


def test_a_database_flow_that_differs_from_the_background_is_refused(
    either, calc, toy: Path
) -> None:
    # The database defines the background's electricity again, in MJ.
    flows = toy / "tshirt" / "tshirt" / "flows.csv"
    flows.write_text(flows.read_text() + "toy:electricity,electricity,product,MJ,,\n")

    with pytest.raises(hw.InvalidDataError) as raised:
        calc("tshirt", either, root=toy)

    assert [(p.code, p.item) for p in raised.value.problems] == [
        ("duplicate-flow", "flow toy:electricity")
    ]


def test_a_supplier_the_background_does_not_hold_gets_a_suggestion(either, calc, toy: Path) -> None:
    csv = toy / "tshirt" / "tshirt" / "processes" / "tshirt.csv"
    csv.write_text(csv.read_text().replace(",toy:fabric-production", ",toy:fabric-productoin"))

    with pytest.raises(hw.InvalidDataError) as raised:
        calc("tshirt", either, root=toy)

    (problem,) = raised.value.problems
    assert problem.code == "supplier-not-found"
    assert "Did you mean 'toy:fabric-production'?" in problem.message


def test_validate_reports_what_calc_would_refuse(either, open_study, toy: Path) -> None:
    set_location(toy, "XX")
    csv = toy / "tshirt" / "tshirt" / "processes" / "tshirt.csv"
    csv.write_text(csv.read_text().replace(",0.5,kWh,", ",0.5,kg,"))

    report = open_study("tshirt", either, root=toy).validate(either)

    assert [p.code for p in report.errors] == ["unit-quantity-mismatch"]


@pytest.mark.parametrize(
    "name",
    [
        "tshirt",
        "tshirt-local-electricity",
        "tshirt-local-fabric",
        "tshirt-grid-scenario",
        "tshirt-parameter",
        "tshirt-cut-off",
        "tshirt-no-factor",
    ],
)
def test_every_y1_database_validates_with_nothing_to_report(either, open_study, name: str) -> None:
    assert open_study(name, either).validate(either).problems == ()


def test_validation_uses_the_catalog_of_what_the_database_names(
    either: hw.Background, open_study, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No catalog of every flow of the background is built for a database."""
    database = open_study("tshirt-local-fabric", either)
    sizes: list[int] = []
    real = Catalog.__init__

    def counting(self, flows, *args, **kwargs):
        flows = list(flows)
        sizes.append(len(flows))
        real(self, flows, *args, **kwargs)

    monkeypatch.setattr(Catalog, "__init__", counting)

    database.calc(either, "tshirt:tshirt")

    # The T-shirt, and the flows of its lines and of the fabric it overrides: not the waste.
    assert sizes == [5]


# --- the input hash --------------------------------------------------------------------------


def input_hash(calc, root: Path, case: str = "tshirt", **kwargs) -> str:
    return calc(case, root=root, **kwargs).provenance.input_hash


@pytest.mark.parametrize(
    ("case", "path", "old", "new"),
    [
        ("tshirt-parameter", "parameters.yaml", "value: 0.25", "value: 0.250000001"),
        ("tshirt-local-electricity", "overrides/own-wind-power.csv", "Wind power", "Wind"),
        ("tshirt-cut-off", "cut_offs.csv", ",No supplier", ",None in the background"),
        ("tshirt-cut-off", "cut_offs.csv", "0.0384615384615385", "0.04"),
        ("tshirt-no-factor", "no_factor.csv", "not characterised", "not covered"),
        ("tshirt-no-factor", "no_factor.csv", "Sam", "Ana"),
    ],
)
def test_the_input_hash_covers_every_file_of_a_database(
    calc, toy: Path, case: str, path: str, old: str, new: str
) -> None:
    before = input_hash(calc, toy, case)
    file = toy_database(toy, case) / path
    text = file.read_text()
    assert old in text
    file.write_text(text.replace(old, new))

    assert input_hash(calc, toy, case) != before


def test_a_scenario_counts_in_the_input_hash_only_when_it_is_calculated(calc, toy: Path) -> None:
    case = "tshirt-grid-scenario"
    base = input_hash(calc, toy, case)
    before = input_hash(calc, toy, case, scenario="clean-grid")
    file = toy / case / "tshirt-footprint" / "scenarios" / "clean-grid.yaml"
    text = file.read_text()
    assert "instead of 0.5" in text
    file.write_text(text.replace("instead of 0.5", "not 0.5"))

    assert input_hash(calc, toy, case, scenario="clean-grid") != before
    assert input_hash(calc, toy, case) == base


def test_the_records_of_a_database_hold_what_it_says(open_study, background) -> None:
    for name, key in [
        ("tshirt-parameter", "parameters"),
        ("tshirt-local-electricity", "overrides"),
        ("tshirt-cut-off", "cut_offs"),
    ]:
        records = records_of(open_study(name, background).folder)
        assert records[key], name
    assert "scenarios" not in records_of(open_study("tshirt-grid-scenario", background).folder)
