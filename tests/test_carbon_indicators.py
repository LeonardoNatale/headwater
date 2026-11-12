"""Exit test Z1 of M3: carbon indicators from ecoinvent's LCIA workbook, on the carbon toy.

The toy, `tests/fixtures/carbon`, is a release in ecoinvent's shape with two processes:

- cotton fabric production makes 1 kg of fabric. It takes 1.5 kg `Carbon dioxide, in air`,
  emits 0.5 kg `Carbon dioxide, fossil` and 0.2 kg `Carbon dioxide, from soil or biomass
  stock`, and sends 1 kg of residue to treatment.
- treating 1 kg of residue emits 0.4 kg `Carbon dioxide, non-fossil`.

The toy workbook has three methods, as ecoinvent's 3.12 workbook has them: EF v3.1, which has
no factor for carbon dioxide in air or non-fossil; `IPCC 2021 (incl. biogenic CO2)`, with
biogenic removals, emissions and their sum; and `IPCC 2021`, with direct land-use change. Each
category's factors are the public IPCC AR6 values: 1 for CO2, -1 for CO2 taken from the air.

Hand-checked, per kg of fabric: biogenic removals -1.5, biogenic emissions 0.4, their sum -1.1,
direct land-use change 0.2, EF climate change 0.7. The study `garment` buys 1 kg of fabric. In
its scenario `burnt` the residue emits 1.5 kg, so the sum is 0, of parts +1.5 and -1.5, and the
result is flagged as cancelling.
"""

import csv
import shutil
from dataclasses import replace
from pathlib import Path

import openpyxl
import pytest
from typer.testing import CliRunner

import headwater as hw
from headwater.cli import app
from headwater.io.lcia import MethodImport, import_method, read_workbook, write_method
from headwater.model import InvalidDataError, Pin
from headwater.query._internals import internals
from headwater.release import Release, ReleaseInfo, build, method_hash
from headwater.study import read_method, read_process_folder
from tests.conftest import open_database, open_study_folder

CARBON = Path(__file__).resolve().parent / "fixtures" / "carbon"
REL = 1e-9  # AGENTS.md: relative 1e-9 for toy fixtures

INFO = ReleaseInfo(
    name="ecoinvent-3.12-cutoff",
    source="ecoinvent",
    version="3.12",
    system_model="cutoff",
    licence="CC0-1.0",
    flow_list="ecoinvent-3.12",
)

BIOGENIC = "IPCC 2021 (incl. biogenic CO2)"
REMOVALS = "climate change: biogenic removals (incl. CO2)"
EMISSIONS = "climate change: biogenic emissions (incl. CO2)"
NET = "climate change: biogenic (incl. CO2)"
LUC = "climate change: direct land use change"

FABRIC = "carbon:fabric-production"


def _cell(text: str) -> str | int | float | None:
    if not text:
        return None
    for kind in (int, float):
        try:
            return kind(text)
        except ValueError:
            pass
    return text


@pytest.fixture(scope="module")
def workbook(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The toy workbook, written to an .xlsx from its three CSV sheets."""
    book = openpyxl.Workbook()
    book.remove(book.worksheets[0])
    for title, name in (("Read me", "read_me"), ("CFs", "cfs"), ("Indicators", "indicators")):
        sheet = book.create_sheet(title)
        with (CARBON / "workbook" / f"{name}.csv").open(encoding="utf-8", newline="") as file:
            for row in csv.reader(file):
                sheet.append([_cell(text) for text in row])
    path = tmp_path_factory.mktemp("workbook") / "LCIA Implementation 3.12.xlsx"
    book.save(path)
    return path


@pytest.fixture(scope="module")
def release(tmp_path_factory: pytest.TempPathFactory) -> Release:
    folder = read_process_folder(CARBON / "background", namespace="carbon")
    return build(
        INFO,
        processes=folder.processes,
        flows=folder.flows,
        into=tmp_path_factory.mktemp("releases"),
    )


@pytest.fixture(scope="module")
def methods(workbook: Path, release: Release, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The three methods, each imported on its own into `methods/<id>`."""
    root = tmp_path_factory.mktemp("methods")
    for name, categories in (
        ("EF v3.1", None),
        (BIOGENIC, [REMOVALS, EMISSIONS, NET]),
        ("IPCC 2021", [LUC]),
    ):
        imported = import_method(read_workbook(workbook, name, categories=categories), release)
        write_method(imported, root / imported.method.id)
    return root


def _imported(workbook: Path, release: Release, name: str, *categories: str) -> MethodImport:
    return import_method(read_workbook(workbook, name, categories=categories or None), release)


# --- importing ------------------------------------------------------------------------------


def test_the_chosen_categories_are_imported_each_in_its_own_method(
    workbook: Path, release: Release
) -> None:
    biogenic = _imported(workbook, release, BIOGENIC, REMOVALS, EMISSIONS, NET)
    land_use = _imported(workbook, release, "IPCC 2021", LUC)

    assert (biogenic.method.id, biogenic.method.version) == ("IPCC-2021-incl-biogenic-CO2", "2021")
    assert [(r.indicator.id, r.factors, r.mapped) for r in biogenic.indicators] == [
        ("climate-change-biogenic-emissions-incl-co2", 2, 2),
        ("climate-change-biogenic-incl-co2", 3, 3),
        ("climate-change-biogenic-removals-incl-co2", 1, 1),
    ]
    assert biogenic.workbook.left_out == ("climate change: total (incl. biogenic CO2)",)
    assert (land_use.method.id, land_use.method.version) == ("IPCC-2021", "2021")
    assert [(r.indicator.id, r.factors, r.mapped) for r in land_use.indicators] == [
        ("climate-change-direct-land-use-change", 2, 2)
    ]


def test_ef_has_no_factor_for_carbon_dioxide_in_air_or_non_fossil(
    workbook: Path, release: Release
) -> None:
    ef = _imported(workbook, release, "EF v3.1")

    assert {f.name for f in ef.not_characterised} == {
        "Carbon dioxide, in air",
        "Carbon dioxide, non-fossil",
        "Carbon dioxide, to soil or biomass stock",
    }


# --- scores of the background ---------------------------------------------------------------


def test_the_fabric_s_biogenic_removals_and_emissions(release: Release, methods: Path) -> None:
    bg = hw.load(release.path, method=methods / "IPCC-2021-incl-biogenic-CO2")
    fabric = internals(bg).index[FABRIC]

    removals = internals(bg).solved.x["climate-change-biogenic-removals-incl-co2"].values[fabric]
    emissions = internals(bg).solved.x["climate-change-biogenic-emissions-incl-co2"].values[fabric]
    net = internals(bg).solved.x["climate-change-biogenic-incl-co2"].values[fabric]
    assert removals == pytest.approx(-1.5, rel=REL)
    assert emissions == pytest.approx(0.4, rel=REL)
    assert net == pytest.approx(-1.1, rel=REL)


def test_the_fabric_s_direct_land_use_change(release: Release, methods: Path) -> None:
    bg = hw.load(release.path, method=methods / "IPCC-2021")

    luc = (
        internals(bg)
        .solved.x["climate-change-direct-land-use-change"]
        .values[internals(bg).index[FABRIC]]
    )
    assert luc == pytest.approx(0.2, rel=REL)


# --- a study, and the cancellation flag -----------------------------------------------------


def _garment(bg: hw.Background) -> hw.Database:
    """The garment, pinned to the release `bg` was read from and to the method it was loaded
    with, as a real study is."""
    return _repinned(open_database(CARBON / "studies" / "garment"), bg)


def _repinned(study: hw.Database, bg: hw.Background) -> hw.Database:
    release = bg.release
    assert release is not None
    pin = Pin(name=release.name, version=release.hash[:12])
    folder = study.folder
    update = {"background": pin, "methods": bg.method.ids}
    return hw.Database(replace(folder, database=folder.database.model_copy(update=update)))


def test_the_garment_s_biogenic_parts_do_not_cancel(release: Release, methods: Path) -> None:
    bg = hw.load(release.path, method=methods / "IPCC-2021-incl-biogenic-CO2")
    study = _garment(bg)

    removals = study.calc(
        bg, "garment:garment", indicator="climate-change-biogenic-removals-incl-co2"
    )
    emissions = study.calc(
        bg, "garment:garment", indicator="climate-change-biogenic-emissions-incl-co2"
    )
    net = study.calc(bg, "garment:garment", indicator="climate-change-biogenic-incl-co2")

    assert removals.score == pytest.approx(-1.5, rel=REL)
    assert emissions.score == pytest.approx(0.4, rel=REL)
    assert net.score == pytest.approx(-1.1, rel=REL)
    parts = net.provenance.cancellation
    assert (parts.positive, parts.negative) == pytest.approx((0.4, -1.5), rel=REL)
    assert not parts.cancels
    assert not removals.provenance.cancellation.cancels


def _garment_study(bg: hw.Background) -> hw.Study:
    """The study `garment-footprint`, with its databases pinned to the release `bg` was read
    from and its methods set to the ones `bg` was loaded with."""
    release = bg.release
    assert release is not None
    pin = Pin(name=release.name, version=release.hash[:12])
    update = {"background": pin, "methods": bg.method.ids}
    folder = open_study_folder(CARBON / "studies" / "garment-footprint").folder
    databases = tuple(
        replace(d, database=d.database.model_copy(update=update)) for d in folder.databases
    )
    return hw.Study._of(  # pyright: ignore[reportPrivateUsage]
        replace(folder, databases=databases, methods=bg.method.ids)
    )


def test_a_sum_that_cancels_near_zero_is_flagged(release: Release, methods: Path) -> None:
    """In the scenario `burnt` the residue gives back all 1.5 kg of CO2 the fabric took."""
    bg = hw.load(release.path, method=methods / "IPCC-2021-incl-biogenic-CO2")

    [net] = _garment_study(bg).calc(
        bg, indicator="climate-change-biogenic-incl-co2", scenario="burnt"
    )

    assert net.score == pytest.approx(0.0, abs=1e-15)
    parts = net.provenance.cancellation
    assert (parts.positive, parts.negative) == pytest.approx((1.5, -1.5), rel=REL)
    assert parts.cancels
    assert ", scenario burnt, cancels: +1.5 and -1.5" in repr(net)


def test_a_score_of_one_sign_is_not_flagged_in_the_scenario(
    release: Release, methods: Path
) -> None:
    bg = hw.load(release.path, method=methods / "IPCC-2021-incl-biogenic-CO2")

    [emissions] = _garment_study(bg).calc(
        bg, indicator="climate-change-biogenic-emissions-incl-co2", scenario="burnt"
    )

    assert emissions.score == pytest.approx(1.5, rel=REL)
    assert not emissions.provenance.cancellation.cancels


# --- several methods at once ----------------------------------------------------------------

IDS = ("EF-3.1", "IPCC-2021-incl-biogenic-CO2", "IPCC-2021")
SEVERAL = "EF-3.1+IPCC-2021-incl-biogenic-CO2+IPCC-2021"
"""How results name the three methods used together."""


def _several(methods: Path) -> list[Path]:
    return [methods / m for m in IDS]


def test_the_carbon_methods_load_next_to_ef(release: Release, methods: Path) -> None:
    bg = hw.load(release.path, method=_several(methods))
    fabric = internals(bg).index[FABRIC]

    assert bg.method.id == SEVERAL
    assert bg.method.ids == IDS
    assert [m.id for m in bg.methods] == list(IDS)
    found = {i: s.values[fabric] for i, s in internals(bg).solved.x.items()}
    assert found == pytest.approx(
        {
            "climate-change": 0.7,
            "climate-change-biogenic": 0.0,
            "climate-change-fossil": 0.5,
            "climate-change-land-use-and-land-use-change": 0.2,
            "climate-change-biogenic-emissions-incl-co2": 0.4,
            "climate-change-biogenic-incl-co2": -1.1,
            "climate-change-biogenic-removals-incl-co2": -1.5,
            "climate-change-direct-land-use-change": 0.2,
        },
        rel=REL,
    )


def test_flows_one_method_covers_are_covered(release: Release, methods: Path) -> None:
    """EF v3.1 alone leaves the biogenic carbon dioxide out; next to IPCC 2021 it counts."""
    ef = hw.load(release.path, method=methods / "EF-3.1")
    several = hw.load(release.path, method=_several(methods))

    assert {f.name for f in ef.not_characterised} == {
        "Carbon dioxide, in air",
        "Carbon dioxide, non-fossil",
    }
    assert several.not_characterised == ()


def test_one_method_loads_as_before(release: Release, methods: Path) -> None:
    bg = hw.load(release.path, method=[methods / "EF-3.1"])

    assert bg.method == read_method(methods / "EF-3.1")
    assert [m.id for m in bg.methods] == ["EF-3.1"]


def test_an_indicator_in_two_methods_is_refused_naming_both(
    workbook: Path, release: Release, methods: Path, tmp_path: Path
) -> None:
    """IPCC 2021's `climate change: fossil` gives `climate-change-fossil`, as EF v3.1's does."""
    fossil = _imported(workbook, release, "IPCC 2021", "climate change: fossil")
    write_method(fossil, tmp_path / "IPCC-2021")

    with pytest.raises(InvalidDataError) as error:
        hw.load(release.path, method=[methods / "EF-3.1", tmp_path / "IPCC-2021"])

    [problem] = error.value.problems
    assert (problem.code, problem.item) == (
        "duplicate-indicator",
        "indicator climate-change-fossil",
    )
    assert "EF-3.1" in problem.message
    assert "IPCC-2021" in problem.message


def test_the_garment_names_its_methods_and_each_result_its_own(
    release: Release, methods: Path
) -> None:
    bg = hw.load(release.path, method=_several(methods))
    study = _garment(bg)

    luc = study.calc(bg, "garment:garment", indicator="climate-change-direct-land-use-change")
    climate = study.calc(bg, "garment:garment", indicator="climate-change")

    assert open_database(CARBON / "studies" / "garment").folder.database.methods == IDS
    assert luc.score == pytest.approx(0.2, rel=REL)
    assert climate.score == pytest.approx(0.7, rel=REL)
    assert (luc.provenance.method, luc.provenance.method_version) == (SEVERAL, "3.1+2021+2021")
    part = luc.provenance.indicator_method
    assert (part.id, part.version) == ("IPCC-2021", "2021")
    assert part.hash == method_hash(read_method(methods / "IPCC-2021"))
    assert climate.provenance.indicator_method.id == "EF-3.1"
    assert [(m.id, m.version, m.hash) for m in luc.provenance.methods] == [
        (m.id, m.version, method_hash(m)) for m in bg.methods
    ]


def test_one_indicators_release_holds_each_method(
    release: Release, methods: Path, tmp_path: Path
) -> None:
    bg = hw.load(release.path, method=_several(methods))

    written = bg.write_indicators(into=tmp_path)

    assert written.name == "ecoinvent-3.12-cutoff.EF-3.1.IPCC-2021-incl-biogenic-CO2.IPCC-2021"
    assert {m.id: m.hash for m in written.method_entries} == {
        m.id: method_hash(m) for m in bg.methods
    }
    skus = hw.score_many(written, {"sku": ["a"], "product_id": [FABRIC], "amount": [2.0]})
    found = dict(zip(skus.indicators, skus.scores[0].tolist(), strict=True))
    assert found["climate-change-biogenic-removals-incl-co2"] == pytest.approx(-3.0, rel=REL)
    assert found["climate-change-direct-land-use-change"] == pytest.approx(0.4, rel=REL)
    assert found["climate-change"] == pytest.approx(1.4, rel=REL)


def test_calc_on_the_command_line_scores_every_indicator_of_the_methods(
    release: Release, methods: Path, tmp_path: Path
) -> None:
    folders = tmp_path / "studies"
    shutil.copytree(CARBON / "studies", folders)
    study = folders / "garment"
    yaml = study / "database.yaml"
    pin = f"{release.name}@{release.hash[:12]}"
    yaml.write_text(yaml.read_text().replace(f"{release.name}@fixture", pin), encoding="utf-8")
    footprint = folders / "garment-footprint"
    several = [arg for path in _several(methods) for arg in ("--method", str(path))]

    result = CliRunner().invoke(
        app,
        ["calc", str(study), "--release", str(release.path), *several],
        catch_exceptions=False,
    )
    burnt = CliRunner().invoke(
        app,
        ["calc", str(footprint), "--release", str(release.path), *several, "--scenario", "burnt"],
        catch_exceptions=False,
    )
    on_a_database = CliRunner().invoke(
        app,
        ["calc", str(study), "--release", str(release.path), *several, "--scenario", "burnt"],
    )

    assert result.exit_code == 0, result.output
    rows = [line.split(":  ") for line in result.output.splitlines() if ":  " in line]
    scores = {name.strip(): value.split()[0] for name, value in rows}
    assert scores == {
        "climate change": "0.7",
        "climate change: biogenic": "0",
        "climate change: fossil": "0.5",
        "climate change: land use and land use change": "0.2",
        "climate change: biogenic emissions (incl. CO2)": "0.4",
        "climate change: biogenic (incl. CO2)": "-1.1",
        "climate change: biogenic removals (incl. CO2)": "-1.5",
        "climate change: direct land use change": "0.2",
    }
    assert f"method: {SEVERAL} 3.1+2021+2021" in result.output
    assert "not characterised flows: 0" in result.output
    assert "scores that cancel: none" in result.output
    assert on_a_database.exit_code == 2
    assert burnt.exit_code == 0, burnt.output
    assert "scenario: burnt" in burnt.output
    assert "climate change: biogenic (incl. CO2):" in burnt.output
    assert "climate change: biogenic emissions (incl. CO2):  1.5 kg CO2-Eq" in burnt.output


def test_a_study_s_own_flow_needs_no_row_when_one_of_the_methods_covers_it(
    release: Release, methods: Path, tmp_path: Path
) -> None:
    """The garment burns 0.1 kg of its own scraps: non-fossil CO2, which EF v3.1 does not cover."""
    study = tmp_path / "garment"
    shutil.copytree(CARBON / "studies" / "garment", study)
    lines = study / "processes" / "garment.csv"
    lines.write_text(
        lines.read_text() + "e03,emission,carbon:co2-non-fossil,0.1,kg,\n", encoding="utf-8"
    )
    ef = hw.load(release.path, method=methods / "EF-3.1")
    several = hw.load(release.path, method=_several(methods))

    with pytest.raises(InvalidDataError) as error:
        _repinned(open_database(study), ef).calc(ef, "garment:garment", indicator="climate-change")
    emissions = _repinned(open_database(study), several).calc(
        several, "garment:garment", indicator="climate-change-biogenic-emissions-incl-co2"
    )

    assert [p.code for p in error.value.problems] == ["no-factor"]
    assert emissions.score == pytest.approx(0.5, rel=REL)


def _with_cod(tmp_path: Path, *rows: str) -> Path:
    """The garment emitting 0.002 kg of COD to water, which no method covers, with `rows` in its
    no_factor.csv."""
    study = tmp_path / "garment"
    shutil.copytree(CARBON / "studies" / "garment", study)
    with (study / "flows.csv").open("a", encoding="utf-8") as file:
        file.write('garment:cod,"COD, Chemical Oxygen Demand",elementary,kg,water,\n')
    with (study / "processes" / "garment.csv").open("a", encoding="utf-8") as file:
        file.write("e03,emission,garment:cod,0.002,kg,\n")
    (study / "no_factor.csv").write_text(
        "flow,method,reason,who,when\n" + "".join(f"{r}\n" for r in rows), encoding="utf-8"
    )
    return study


def test_a_row_for_one_of_the_methods_stays_valid_when_methods_are_added(
    release: Release, methods: Path, tmp_path: Path
) -> None:
    """The 3.12 pilot's rows name EF-3.1: they still hold when the IPCC methods join it."""
    study = _with_cod(tmp_path, "garment:cod,EF-3.1,not characterised in EF 3.1,Sam,2026-10-12")
    ef = hw.load(release.path, method=methods / "EF-3.1")
    several = hw.load(release.path, method=_several(methods))

    alone = _repinned(open_database(study), ef).calc(
        ef, "garment:garment", indicator="climate-change"
    )
    together = _repinned(open_database(study), several)
    report = together.validate(several)
    net = together.calc(several, "garment:garment", indicator="climate-change-biogenic-incl-co2")

    assert alone.score == pytest.approx(0.7, rel=REL)
    assert report.errors == ()
    assert report.warnings == ()
    assert net.score == pytest.approx(-1.1, rel=REL)
    [cod] = net.not_characterised
    assert (cod.flow_id, cod.in_database, cod.reason) == (
        "garment:cod",
        True,
        "not characterised in EF 3.1",
    )


def test_a_row_for_a_method_not_loaded_is_not_needed(
    release: Release, methods: Path, tmp_path: Path
) -> None:
    study = _with_cod(
        tmp_path,
        "garment:cod,IPCC-2021,not characterised in IPCC 2021,Sam,2026-10-12",
    )
    ef = hw.load(release.path, method=methods / "EF-3.1")

    report = _repinned(open_database(study), ef).validate(ef)

    assert [p.code for p in report.errors] == ["no-factor"]
    [warning] = report.warnings
    assert "it is for method IPCC-2021" in warning.message
