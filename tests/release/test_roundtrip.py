import json
import re
from pathlib import Path

import pyarrow as pa
import pytest

from headwater.model import Method, Process
from headwater.model.uncertainty import Distribution, LinkStatus
from headwater.release import Release


def _sorted_processes(processes: tuple[Process, ...]) -> list[Process]:
    return [
        Process.model_validate(
            {
                **p.model_dump(exclude={"exchanges"}),
                "exchanges": sorted(p.exchanges, key=lambda e: e.line_id),
            }
        )
        for p in sorted(processes, key=lambda p: p.id)
    ]


def _sorted_method(method: Method) -> Method:
    return Method.model_validate(
        {
            **method.model_dump(exclude={"indicators", "factors"}),
            "indicators": sorted(method.indicators, key=lambda i: i.id),
            "factors": sorted(
                method.factors, key=lambda f: (f.indicator, f.flow_id, f.location or "")
            ),
        }
    )


def test_toy_background_to_release_and_back_gives_the_same_records(
    records, info, toy_release: Release
) -> None:
    again = Release.open(toy_release.path)

    assert list(again.processes()) == _sorted_processes(records.processes)
    assert list(again.flows()) == sorted(records.flows, key=lambda f: f.id)
    assert list(again.flow_properties()) == sorted(
        records.flow_properties, key=lambda p: (p.flow_id, p.property.value)
    )
    assert list(again.issues()) == sorted(records.issues, key=lambda i: (i.process_id, i.line_id))
    assert list(again.methods()) == [_sorted_method(m) for m in records.methods]
    assert again.info == info


def test_the_test_exchange_keeps_uncertainty_pedigree_properties_and_link_status(
    toy_release: Release,
) -> None:
    fabric = next(p for p in toy_release.processes() if p.id == "toy:fabric-production")
    line = next(e for e in fabric.exchanges if e.line_id == "e02")
    dye = next(e for e in fabric.exchanges if e.line_id == "e05")

    assert line.uncertainty is not None
    assert line.uncertainty.distribution is Distribution.lognormal
    assert line.uncertainty.loc == pytest.approx(1.0986122886681098, rel=1e-9)
    assert line.uncertainty.scale == pytest.approx(0.0953, rel=1e-9)
    assert line.pedigree is not None
    assert line.pedigree.further_technological_correlation == 4
    assert [(p.name, p.value, p.unit) for p in line.properties] == [
        ("heating value, net", pytest.approx(3.6, rel=1e-9), "MJ"),
        ("price", pytest.approx(0.11, rel=1e-9), "EUR2005"),
    ]
    assert line.link_status is LinkStatus.linked
    assert dye.link_status is LinkStatus.cut_off
    assert dye.supplier_id is None
    assert dye.comment == "no dye dataset yet"


def test_only_the_units_the_records_use_are_stored(toy_release: Release) -> None:
    assert sorted(u.name for u in toy_release.units()) == ["kWh", "kg"]


def test_the_release_folder_is_named_by_its_hash(toy_release: Release) -> None:
    assert re.fullmatch(r"[0-9a-f]{64}", toy_release.hash)
    assert toy_release.path.name == f"toy-background@{toy_release.hash[:12]}"


def test_release_json_lists_identity_files_and_hash(info, toy_release: Release) -> None:
    manifest = json.loads((toy_release.path / "release.json").read_text())

    assert manifest["format"] == "headwater-release/2"
    assert manifest["kind"] == "source"
    assert {k: manifest[k] for k in info.model_dump()} == info.model_dump()
    assert manifest["hash"] == toy_release.hash
    assert re.fullmatch(r"\d+\.\d+\.\d+.*", manifest["engine_version"])
    assert sorted(manifest["files"]) == [
        "exchanges.parquet",
        "flow_properties.parquet",
        "flows.parquet",
        "geographies.parquet",
        "issues.parquet",
        "methods/toy-climate/factors.parquet",
        "processes.parquet",
        "units.parquet",
    ]
    assert sorted(manifest["tables"]) == sorted(manifest["files"])
    assert manifest["tables"]["exchanges.parquet"]["rows"] == 10
    assert [m["id"] for m in manifest["methods"]] == ["toy-climate"]
    assert (manifest["parent"], manifest["recipe"], manifest["provenance"]) == (None, None, None)


def test_tables_are_arrow_with_float64_numbers(toy_release: Release) -> None:
    exchanges = toy_release.table("exchanges")

    assert exchanges.num_rows == 10
    assert exchanges.schema.field("amount").type == pa.float64()
    assert exchanges.schema.field("uncertainty").type.field("scale").type == pa.float64()
    keys = exchanges.select(["process_id", "line_id"]).to_pylist()
    assert keys == sorted(keys, key=lambda r: (r["process_id"], r["line_id"]))
    processes = toy_release.table("processes")
    assert processes.column("reference_flow_id").to_pylist() == [
        "toy:electricity",
        "toy:cotton-fabric",
        "toy:textile-waste",
    ]


def test_duckdb_has_one_view_per_table(toy_release: Release) -> None:
    con = toy_release.connect()

    count = con.execute("SELECT count(*) FROM exchanges").fetchone()
    price = con.execute(
        "SELECT properties['price'].value FROM exchanges WHERE line_id = 'e02' "
        "AND process_id = 'toy:fabric-production'"
    ).fetchone()
    factors = con.execute(
        'SELECT flow_id, factor FROM "methods/toy-climate/factors" ORDER BY flow_id'
    ).fetchall()

    assert count == (10,)
    assert price == (pytest.approx(0.11, rel=1e-9),)
    assert factors == [
        ("toy:ch4", pytest.approx(29.8, rel=1e-9)),
        ("toy:co2", pytest.approx(1.0, rel=1e-9)),
    ]


def test_a_release_can_be_opened_from_a_string_path(toy_release: Release) -> None:
    assert Release.open(str(toy_release.path)).hash == toy_release.hash
    assert isinstance(toy_release.path, Path)


def test_duckdb_reads_this_release_even_when_its_path_looks_like_a_glob(
    records, tmp_path: Path
) -> None:
    release = records.build(tmp_path / "data [old]")
    decoy = tmp_path / "data o" / release.path.name
    decoy.mkdir(parents=True)
    (decoy / "exchanges.parquet").write_bytes((release.path / "flows.parquet").read_bytes())

    count = release.connect().execute("SELECT count(*) FROM exchanges").fetchone()

    assert count == (10,)


def test_a_release_built_without_geographies_has_an_empty_geographies_table(
    toy_release: Release,
) -> None:
    table = toy_release.table("geographies")

    assert (table.num_rows, table.column_names) == (0, ["code", "name"])
