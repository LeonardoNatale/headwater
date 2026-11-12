"""What a folder of ecoSpold1 files gives: processes, flows, units, ids and the errors."""

import shutil
import uuid
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from headwater.io.exclude import Exclusion
from headwater.io.formats import check_options, find, inventory_from
from headwater.io.source import InventoryOptions, SourceKind, flow_id, product_flow_id
from headwater.model import FlowKind, InvalidDataError, Severity


def _exclusion() -> Exclusion:
    return Exclusion(
        file="a.xml",
        reason="no reason",
        resolved_by="someone",
        resolved_at=datetime(2026, 10, 25, tzinfo=UTC),
    )


def _codes(raised: pytest.ExceptionInfo[InvalidDataError]) -> list[str]:
    return [p.code for p in raised.value.problems]


def test_the_release_is_named_by_its_source_and_version(es1) -> None:
    source = es1.read(es1.make(es1.maker("widget")))

    assert source.info.model_dump() == {
        "name": "test-1",
        "source": "test",
        "version": "1",
        "system_model": None,
        "licence": "CC0-1.0",
        "flow_list": "test-1",
    }


@pytest.mark.parametrize("missing", ["version", "source", "licence"])
def test_the_source_version_and_licence_are_required(es1, missing: str) -> None:
    options = replace(es1.options, **{missing: None})

    with pytest.raises(InvalidDataError) as raised:
        es1.read(es1.make(es1.maker("widget")), options)

    [problem] = raised.value.problems
    assert problem.code == "missing-option" and problem.item == f"--{missing}"


def test_the_options_it_takes_are_checked_by_the_registry() -> None:
    chosen = find("ecospold1")
    assert chosen is not None

    check_options(chosen, SourceKind.inventory, InventoryOptions(version="1", workers=2))
    with pytest.raises(InvalidDataError) as raised:
        check_options(chosen, SourceKind.inventory, InventoryOptions(exclude=(_exclusion(),)))

    assert [p.item for p in raised.value.problems] == ["--exclude"]


def test_a_process_has_the_reference_functions_name_location_period_and_source(es1) -> None:
    folder = es1.make(
        es1.dataset(
            "widget",
            es1.exchange(1, "widget", "kg", 1, "out0"),
            number=42,
            location="DE",
            time="<startYear>2001</startYear><endYear>2004</endYear>",
        )
    )

    [process] = es1.read(folder).processes.to_pylist()

    assert process["name"] == "widget" and process["location"] == "DE"
    assert (process["time_start"], process["time_end"]) == (date(2001, 1, 1), date(2004, 12, 31))
    assert process["kind"] == "ordinary_transforming_activity"
    assert process["source_ref"] == "000.xml#42"


@pytest.mark.parametrize(
    ("time", "start", "end"),
    [
        ("<startYear>2001</startYear><endYear>2004</endYear>", (2001, 1, 1), (2004, 12, 31)),
        (
            "<startYearMonth>2001-03</startYearMonth><endYearMonth>2004-02</endYearMonth>",
            (2001, 3, 1),
            (2004, 2, 29),
        ),
        (
            "<startDate>2001-03-05</startDate><endDate>2004-02-10</endDate>",
            (2001, 3, 5),
            (2004, 2, 10),
        ),
        ("<startDate>2000-01</startDate><endDate>2000-12</endDate>", (2000, 1, 1), (2000, 12, 31)),
        (
            "<startDate>2000-01-01+01:00</startDate><endDate>2000-12-31+01:00</endDate>",
            (2000, 1, 1),
            (2000, 12, 31),
        ),
        ("", None, None),
    ],
)
def test_the_time_period_is_read_in_every_form_files_write(es1, time, start, end) -> None:
    """The ecoSpold1 schema allows a year, a year and month, or a date. openLCA writes a year and
    month, or a date with a time zone, in `startDate`."""
    folder = es1.make(es1.dataset("widget", es1.exchange(1, "widget", "kg", 1, "out0"), time=time))

    [process] = es1.read(folder).processes.to_pylist()

    assert process["time_start"] == (date(*start) if start else None)
    assert process["time_end"] == (date(*end) if end else None)


def test_a_time_period_that_runs_backwards_is_dropped_with_a_warning(es1) -> None:
    folder = es1.make(
        es1.dataset(
            "widget",
            es1.exchange(1, "widget", "kg", 1, "out0"),
            time="<startDate>2021-01</startDate><endDate>2010-12</endDate>",
        )
    )

    source = es1.read(folder)

    [process] = source.processes.to_pylist()
    assert (process["time_start"], process["time_end"]) == (None, None)
    [warning] = source.warnings
    assert warning.code == "backwards-period" and warning.severity is Severity.warning
    assert "2010-12-31" in warning.message and "2021-01-01" in warning.message


def test_a_process_id_is_made_from_what_the_reference_function_says_not_from_the_path(
    es1, tmp_path: Path
) -> None:
    folder = es1.make(es1.maker("widget"))
    moved = tmp_path / "elsewhere" / "renamed"
    moved.mkdir(parents=True)
    shutil.copy(folder / "000.xml", moved / "anything.xml")

    [first] = es1.read(folder).processes.to_pylist()
    [second] = es1.read(moved).processes.to_pylist()

    key = "widget\x00CH\x00kg\x00materials\x00unspecified\x00False"
    assert first["id"] == second["id"]
    assert first["id"] == f"test:1:{uuid.uuid5(uuid.NAMESPACE_URL, key)}"


def test_two_processes_with_one_reference_function_are_refused_naming_both_files(es1) -> None:
    folder = es1.make(es1.maker("widget"), es1.maker("widget"))

    with pytest.raises(InvalidDataError) as raised:
        es1.read(folder)

    [problem] = raised.value.problems
    assert problem.code == "duplicate-process"
    assert "000.xml" in problem.message and "001.xml" in problem.message


def test_a_file_can_hold_several_datasets(es1, tmp_path: Path) -> None:
    folder = tmp_path / "many"
    es1.write(folder, "all.xml", es1.maker("one", number=1), es1.maker("two", number=2))

    processes = es1.read(folder).processes.to_pylist()

    assert sorted((p["name"], p["source_ref"]) for p in processes) == [
        ("one", "all.xml#1"),
        ("two", "all.xml#2"),
    ]


def test_files_without_the_namespace_read_the_same(es1) -> None:
    datasets = (es1.maker("widget"), es1.maker("gadget", number=2))

    plain = es1.read(es1.make(*datasets, namespace=None))
    namespaced = es1.read(es1.make(*datasets))

    assert plain.processes.equals(namespaced.processes)
    assert plain.lines.equals(namespaced.lines)
    assert plain.flows == namespaced.flows


def test_folders_are_searched_and_the_order_of_files_does_not_matter(es1, tmp_path: Path) -> None:
    deep = tmp_path / "deep"
    es1.write(deep / "b" / "c", "z.xml", es1.maker("zed", number=1))
    es1.write(deep, "a.xml", es1.maker("aye", number=2))
    flat = tmp_path / "flat"
    es1.write(flat, "1.xml", es1.maker("aye", number=2))
    es1.write(flat, "2.xml", es1.maker("zed", number=1))

    found = es1.read(deep).processes.sort_by("id").select(["id", "name"])
    assert found.equals(es1.read(flat).processes.sort_by("id").select(["id", "name"]))
    assert sorted(found.column("name").to_pylist()) == ["aye", "zed"]


def test_a_product_is_one_flow_by_name_whatever_its_location_and_category(es1) -> None:
    folder = es1.make(
        es1.maker("electricity", "kWh", location="CH", number=1),
        es1.dataset(
            "electricity",
            es1.exchange(1, "electricity", "kWh", 1, "out0", location="DE"),
            number=2,
            location="DE",
            unit="kWh",
            category="other",
        ),
    )

    source = es1.read(folder)

    [flow] = source.flows
    assert (flow.name, flow.kind, flow.unit, flow.compartment) == (
        "electricity",
        FlowKind.product,
        "kWh",
        None,
    )
    assert flow.id == product_flow_id("test-1", "electricity", "energy")


def test_an_elementary_flow_is_named_by_name_category_and_subcategory(es1) -> None:
    lines = es1.lines_of(
        es1.dataset(
            "widget",
            es1.exchange(1, "widget", "kg", 1, "out0"),
            es1.exchange(
                2,
                "Carbon dioxide, fossil",
                "kg",
                2,
                "out4",
                category="air",
                sub_category="low population density",
                CASNumber="000124-38-9",
            ),
            es1.exchange(3, "Water", "m3", 1, "in4", category="resource", sub_category=None),
            es1.exchange(
                4,
                "Carbon dioxide, fossil",
                "kg",
                1,
                "out4",
                category="air",
                sub_category="unspecified",
            ),
        )
    )

    co2, water, other = lines.flow("widget", 2), lines.flow("widget", 3), lines.flow("widget", 4)

    assert co2.compartment == "air/low population density"
    assert co2.id == flow_id("test-1", "Carbon dioxide, fossil", "air/low population density")
    assert water.compartment == "resource"
    assert other.id != co2.id
    assert (co2.cas, co2.kind) == ("124-38-9", FlowKind.elementary)


@pytest.mark.parametrize(
    ("written", "stored"),
    [("000124-38-9", "124-38-9"), ("124-38-9", "124-38-9"), ("7439921", "7439921"), ("", None)],
)
def test_the_cas_number_loses_its_zero_padding(es1, written: str, stored: str | None) -> None:
    lines = es1.lines_of(
        es1.dataset(
            "widget",
            es1.exchange(1, "widget", "kg", 1, "out0"),
            es1.exchange(2, "x", "kg", 1, "out4", category="air", CASNumber=written),
        )
    )

    assert lines.flow("widget", 2).cas == stored


def test_a_flows_unit_is_the_unit_of_the_line_that_makes_it(es1) -> None:
    buyer = es1.dataset(
        "widget",
        es1.exchange(1, "widget", "kg", 1, "out0"),
        es1.exchange(2, "electricity", "MJ", 3.6, "in2", location="CH"),
        number=1,
    )
    folder = es1.make(buyer, es1.maker("electricity", "kWh", number=2))

    [electricity] = [f for f in es1.read(folder).flows if f.name == "electricity"]

    assert electricity.unit == "kWh"


def test_units_are_mapped_to_headwaters_and_the_others_kept(es1) -> None:
    lines = es1.lines_of(
        es1.dataset(
            "widget",
            es1.exchange(1, "widget", "p", 1, "out0"),
            es1.exchange(2, "Radon", "kBq", 5, "out4", category="air"),
            es1.exchange(3, "Radon", "Bq", 5, "out4", category="air"),
            es1.exchange(4, "Land", "m2a", 5, "in4", category="resource"),
            unit="p",
        )
    )

    assert lines["widget", 1]["unit"] == "unit"
    assert [lines["widget", n]["unit"] for n in (2, 3, 4)] == ["kBq", "Bq", "m2a"]


def test_the_unit_table_holds_exactly_the_units_the_source_uses(es1) -> None:
    source = es1.read(
        es1.make(
            es1.dataset(
                "widget",
                es1.exchange(1, "widget", "p", 1, "out0"),
                es1.exchange(2, "Radon", "kBq", 5, "out4", category="air"),
                es1.exchange(3, "Radon", "Bq", 5, "out4", category="air"),
                es1.exchange(4, "Land", "m2a", 5, "in4", category="resource"),
                unit="p",
            )
        )
    )

    assert source.units.names == ["Bq", "kBq", "m2a", "unit"]
    assert source.units.convert(5, "kBq", "Bq") == 5000
    assert source.units.quantity_of("m2a") == "m2a"


def test_normal_cubic_metres_are_renamed_cubic_metres_with_a_warning(es1) -> None:
    folder = es1.make(
        es1.dataset(
            "widget",
            es1.exchange(1, "widget", "kg", 1, "out0"),
            es1.exchange(2, "Gas, natural, in ground", "Nm3", 2, "in4", category="resource"),
            es1.exchange(3, "Gas, natural, in ground", "m3", 3, "in4", category="resource"),
        )
    )

    source = es1.read(folder)
    inventory = es1.load(folder)

    [warning] = source.warnings
    assert warning.code == "approximate-unit" and warning.severity is Severity.warning
    assert "'Nm3'" in warning.item and "1 line uses it" in warning.message
    assert len([f for f in source.flows if f.name.startswith("Gas")]) == 1
    assert {r["unit"] for r in source.lines.to_pylist() if r["line_id"] in ("2", "3")} == {"m3"}
    assert source.units.names == ["kg", "m3"]
    assert inventory.problems == ()


def test_a_product_counted_in_two_quantities_is_two_flows(es1) -> None:
    """Gas in `m3`, in kg or in MJ: no unit converts them, so a supplier that makes one is not the
    supplier of the other. In kWh and in MJ it is one flow."""
    gas = es1.dataset(
        "widget",
        es1.exchange(1, "widget", "kg", 1, "out0"),
        es1.exchange(2, "gas", "m3", 1, "in5", location="CH"),
        es1.exchange(3, "gas", "kg", 1, "in5", location="CH"),
        es1.exchange(4, "gas", "MJ", 1, "in5", location="CH"),
        es1.exchange(5, "gas", "kWh", 1, "in5", location="CH"),
    )
    lines = es1.lines_of(gas)

    ids = [lines["widget", n]["flow_id"] for n in (2, 3, 4, 5)]

    assert len(set(ids)) == 3 and ids[2] == ids[3]
    assert [f.name for f in lines.flows.values()] == ["widget", "gas", "gas", "gas"]


def test_an_elementary_flow_in_units_of_two_quantities_is_refused_by_the_unit_check(es1) -> None:
    folder = es1.make(
        es1.dataset(
            "widget",
            es1.exchange(1, "widget", "kg", 1, "out0"),
            es1.exchange(2, "Radon", "kBq", 1, "out4", category="air"),
            es1.exchange(3, "Radon", "m3", 1, "out4", category="air"),
        )
    )

    with pytest.raises(InvalidDataError) as raised:
        es1.load(folder)

    assert "line 3" in raised.value.problems[0].item


def test_what_is_not_an_ecospold1_file_is_skipped_with_a_warning(es1, tmp_path: Path) -> None:
    folder = es1.make(es1.maker("widget"))
    es1.write(
        folder,
        "impact.xml",
        es1.maker("method"),
        namespace="http://www.EcoInvent.org/EcoSpold01Impact",
    )
    (folder / "notes.txt").write_text("not xml")

    source = es1.read(folder)

    assert len(source.processes) == 1
    [warning] = source.warnings
    assert warning.code == "skipped-file" and warning.severity is Severity.warning
    assert "impact.xml" in warning.item


def test_a_file_that_is_not_xml_is_refused_naming_it(es1) -> None:
    folder = es1.make(es1.maker("widget"))
    (folder / "broken.xml").write_text("<ecoSpold><dataset")

    with pytest.raises(InvalidDataError) as raised:
        es1.read(folder)

    [problem] = raised.value.problems
    assert problem.code == "unreadable-file" and "broken.xml" in problem.item


@pytest.mark.parametrize(
    ("dataset", "what"),
    [
        ("no-location", "geography"),
        ("no-name", "name"),
        ("no-unit", "unit"),
        ("no-amount", "meanValue"),
        ("no-number", "number"),
        ("bad-amount", "meanValue"),
    ],
)
def test_a_dataset_missing_what_it_needs_is_refused_naming_it(es1, dataset: str, what: str) -> None:
    d = es1.dataset
    ref = es1.exchange(1, "widget", "kg", 1, "out0")
    text = {
        "no-location": d("widget", ref, location=None),
        "no-name": d("widget", ref).replace(
            'name="widget" unit="kg" amount="1"', 'unit="kg" amount="1"'
        ),
        "no-unit": d("widget", ref).replace(' unit="kg" amount="1"', ' amount="1"'),
        "no-amount": d("widget", ref.replace('meanValue="1"', "")),
        "no-number": d("widget", ref).replace('<dataset number="1">', "<dataset>"),
        "bad-amount": d("widget", ref.replace('meanValue="1"', 'meanValue="lots"')),
    }[dataset]

    with pytest.raises(InvalidDataError) as raised:
        es1.read(es1.make(text))

    assert any(what in p.message or what in p.item for p in raised.value.problems)
    assert all(p.location is not None for p in raised.value.problems)


def test_lines_that_share_a_number_get_ids_of_their_own(es1) -> None:
    """openLCA writes one number for several exchanges of a dataset. The first keeps it, the
    others add their count, and an allocation to the number reaches every line that has it."""
    lines = es1.lines_of(
        es1.dataset(
            "widget",
            es1.exchange(1, "widget", "kg", 1, "out0"),
            es1.exchange(7, "Carbon dioxide", "kg", 1, "out4", category="air", sub_category="a"),
            es1.exchange(7, "Carbon dioxide", "kg", 2, "out4", category="air", sub_category="b"),
            es1.exchange(7, "Carbon dioxide", "kg", 3, "out4", category="air", sub_category="c"),
        )
    )

    assert [lines["widget", n]["line_id"] for n in (1, 7)] == ["1", "7"]
    assert [row["amount"] for (_, line), row in lines.rows.items() if line.startswith("7")] == [
        1,
        2,
        3,
    ]
    assert {line for (_, line) in lines.rows} == {"1", "7", "7-2", "7-3"}


def test_an_allocation_to_a_shared_number_reaches_every_line_with_it(es1) -> None:
    chp = es1.dataset(
        "chp",
        es1.exchange(1, "heat", "MJ", 5, "out0"),
        es1.exchange(2, "electricity", "kWh", 1, "out2"),
        es1.exchange(7, "Carbon dioxide", "kg", 1, "out4", category="air", sub_category="a"),
        es1.exchange(7, "Carbon dioxide", "kg", 2, "out4", category="air", sub_category="b"),
        es1.allocation(1, 60, 7),
        es1.allocation(2, 40, 7),
    )

    source = es1.read(es1.make(chp))

    rows = {(r["product"], r["line_id"]): r["factor"] for r in source.allocation.to_pylist()}
    assert rows == {("1", None): pytest.approx(0.6), ("2", None): pytest.approx(0.4)}


def test_a_folder_with_no_dataset_is_refused(tmp_path: Path, es1) -> None:
    (tmp_path / "empty").mkdir()

    with pytest.raises(InvalidDataError) as raised:
        es1.read(tmp_path / "empty")

    assert _codes(raised) == ["missing-file"]


def test_the_result_does_not_depend_on_the_number_of_workers(es1) -> None:
    folder = es1.make(
        *(es1.maker(f"product {n}", number=n) for n in range(1, 8)),
        es1.dataset(
            "buyer",
            es1.exchange(1, "buyer", "kg", 1, "out0"),
            es1.exchange(
                2,
                "product 3",
                "kg",
                2,
                "in5",
                location="CH",
                uncertaintyType=1,
                standardDeviation95=1.5,
            ),
            number=9,
        ),
    )

    one = es1.read(folder, replace(es1.options, workers=1))
    two = es1.read(folder, replace(es1.options, workers=2))

    assert one.processes.equals(two.processes)
    assert one.lines.equals(two.lines)
    assert one.flows == two.flows


def test_the_same_files_give_the_same_release(es1) -> None:
    folder = es1.make(es1.maker("electricity", "kWh"), es1.maker("widget", number=2))

    first = es1.load(folder).freeze(into=es1.tmp_path / "a")
    second = es1.load(folder).freeze(into=es1.tmp_path / "b")

    assert first.hash == second.hash


def test_a_folder_is_recognised_without_a_format_name(es1) -> None:
    folder = es1.make(es1.maker("widget"))

    inventory = inventory_from(folder, es1.options)

    assert [p.name for p in inventory.processes] == ["widget"]


def test_a_file_that_the_parser_cannot_handle_is_a_problem_not_a_crash(es1, monkeypatch) -> None:
    from headwater.io.ecospold1 import dataset

    def broken(self, root):
        raise RuntimeError("something nobody foresaw")

    monkeypatch.setattr(dataset._Parser, "read", broken)

    with pytest.raises(InvalidDataError) as raised:
        es1.read(es1.make(es1.maker("widget")))

    [problem] = raised.value.problems
    assert problem.code == "unreadable-file" and "RuntimeError" in problem.message
    assert "000.xml" in problem.item


def test_the_buying_lines_of_an_aggregated_dataset_are_resolved_data_gaps(es1) -> None:
    """A dataset of type 2 holds cumulative results, upstream included: linking what it buys would
    count its suppliers twice. Its other lines stay, and a line that buys in an ordinary dataset
    still links."""
    total = es1.dataset(
        "total",
        es1.exchange(1, "total", "kg", 1, "out0"),
        es1.exchange(2, "electricity", "kWh", 3, "in5", location="CH"),
        es1.exchange(3, "Carbon dioxide", "kg", 9, "out4", category="air"),
        kind=2,
        number=1,
    )
    buyer = es1.dataset(
        "buyer",
        es1.exchange(1, "buyer", "kg", 1, "out0"),
        es1.exchange(2, "electricity", "kWh", 3, "in5", location="CH"),
        number=2,
    )
    folder = es1.make(total, buyer, es1.maker("electricity", "kWh", number=3))

    inventory = es1.load(folder)

    by_name = {p.name: p for p in inventory.processes}
    gap = by_name["total"].exchanges[1]
    assert (gap.supplier_id, gap.link_status.value) == (None, "data_gap")
    assert by_name["total"].exchanges[2].amount == 9
    assert by_name["buyer"].exchanges[1].supplier_id == by_name["electricity"].id
    [issue] = inventory.issues
    assert (issue.process_id, issue.line_id) == (by_name["total"].id, "2")
    assert issue.status.value == "data_gap"
    assert issue.reason == "aggregated dataset: upstream already included"
    assert inventory.problems == () and inventory.warnings == ()
    inventory.freeze(into=es1.tmp_path / "releases")


def test_a_source_with_no_buying_line_in_an_aggregated_dataset_has_no_gap_to_report(es1) -> None:
    folder = es1.make(es1.dataset("total", es1.exchange(1, "total", "kg", 1, "out0"), kind=2))

    inventory = es1.load(folder)

    assert inventory.issues == () and inventory.warnings == ()


def test_two_datasets_of_a_file_with_one_number_are_refused(es1, tmp_path: Path) -> None:
    folder = tmp_path / "numbers"
    es1.write(folder, "a.xml", es1.maker("one", number=5), es1.maker("two", number=5))

    with pytest.raises(InvalidDataError) as raised:
        es1.read(folder)

    assert _codes(raised) == ["duplicate-dataset"]


def test_a_product_id_is_made_from_its_name_and_the_quantity_of_its_unit() -> None:
    gas = product_flow_id("bafu-2026", "natural gas", "volume")

    assert gas == product_flow_id("bafu-2026", "natural gas", "volume")
    assert gas.startswith("bafu-2026:flow:")
    assert gas != product_flow_id("bafu-2026", "natural gas", "mass")
    assert gas != product_flow_id("other-1", "natural gas", "volume")
    assert gas != flow_id("bafu-2026", "natural gas", "volume")


def test_the_ids_are_pinned() -> None:
    """A process id and a product id are made from names and unit quantities, so a change to how
    they are made moves every id of every release. These values must not move."""
    key = "widget\x00CH\x00kg\x00materials\x00unspecified\x00False"

    assert f"test:1:{uuid.uuid5(uuid.NAMESPACE_URL, key)}" == (
        "test:1:9e8b23c6-fe05-5638-b983-ea14a5b01dee"
    )
    assert product_flow_id("bafu-2026", "natural gas", "volume") == (
        "bafu-2026:flow:4c0eae8e-8ba1-59e1-8a22-d86a17d38671"
    )
    assert flow_id("toy-1.0", "Carbon dioxide, fossil", "air/unspecified") == (
        "toy-1.0:flow:3c365c3d-09c8-53ce-91df-55af28fc25d8"
    )
