"""`build_tables`: the same release from Arrow tables, with the same rules as the records.

An importer hands over its lines as Arrow tables, so no record is made per line. Records refuse
a bad line when they are made; tables are checked when they are frozen. Each case below is a row
that a record refuses, and the table path refuses it too, with the same code. A rule that is not
the record's own (a field's type or range) is `invalid-field` on both sides.
"""

import math
from datetime import date
from pathlib import Path
from typing import Any

import pyarrow as pa
import pytest
from pydantic import ValidationError

from headwater.model import Exchange, InvalidDataError, RecordError
from headwater.release.schema import read_exchange

E02 = ("toy:fabric-production", "e02")
"""Fabric production buys 3 kWh of electricity, with uncertainty, pedigree and properties."""

LOGNORMAL = {
    "distribution": "lognormal",
    "loc": 1.0,
    "scale": 0.1,
    "shape": None,
    "minimum": None,
    "maximum": None,
}
PEDIGREE = {
    "reliability": 2,
    "completeness": 3,
    "temporal_correlation": 1,
    "geographical_correlation": 2,
    "further_technological_correlation": 4,
}


def _problems(error: pytest.ExceptionInfo[InvalidDataError]) -> list[tuple[str, str]]:
    return [(p.code, p.item) for p in error.value.problems]


def _changed(table: pa.Table, key: tuple[str, str], change: dict[str, Any]) -> pa.Table:
    rows = table.to_pylist()
    found = [r for r in rows if (r["process_id"], r["line_id"]) == key]
    assert len(found) == 1, key
    found[0].update(change)
    return pa.Table.from_pylist(rows, schema=table.schema)


def _record_code(row: dict[str, Any]) -> str:
    """The code a record gives the row: its validator's own, or invalid-field."""
    with pytest.raises(ValidationError) as raised:
        read_exchange(dict(row))
    for err in raised.value.errors():
        inner = (err.get("ctx") or {}).get("error")
        if isinstance(inner, RecordError):
            return inner.problems[0].code
    return "invalid-field"


def test_records_and_tables_give_the_same_release(records, tmp_path: Path) -> None:
    from_records = records.build(tmp_path / "a")
    from_tables = records.build_tables(tmp_path / "b")

    assert from_tables.hash == from_records.hash
    assert from_tables.path.name == from_records.path.name
    for name, sha in from_records.manifest.files.items():
        assert from_tables.manifest.files[name] == sha, name


def test_the_flow_name_column_is_optional_checked_and_not_stored(records, tmp_path: Path) -> None:
    exchanges = records.tables()["exchanges"]
    without = exchanges.drop_columns(["flow_name"])
    wrong = _changed(exchanges, E02, {"flow_name": "gas"})

    release = records.build_tables(tmp_path / "a", exchanges=without)
    with pytest.raises(InvalidDataError) as raised:
        records.build_tables(tmp_path / "b", exchanges=wrong)

    assert release.hash == records.build(tmp_path / "c").hash
    assert "flow_name" not in release.table("exchanges").column_names
    assert _problems(raised) == [("flow-name-mismatch", "toy:fabric-production line e02")]


def test_a_table_with_a_missing_column_names_the_table(records, tmp_path: Path) -> None:
    exchanges = records.tables()["exchanges"].drop_columns(["unit"])

    with pytest.raises(InvalidDataError) as raised:
        records.build_tables(tmp_path, exchanges=exchanges)

    assert _problems(raised) == [("bad-table", "exchanges.parquet")]
    assert "missing column(s) unit" in str(raised.value)


EXCHANGE_CASES: list[tuple[str, tuple[str, str], dict[str, Any], str]] = [
    ("role that is not a role", E02, {"role": "buyer"}, "invalid-field"),
    ("amount NaN", E02, {"amount": math.nan}, "invalid-field"),
    ("amount infinite", E02, {"amount": math.inf}, "invalid-field"),
    ("negative input", E02, {"amount": -3.0}, "negative-amount"),
    ("reference of zero", ("toy:fabric-production", "e01"), {"amount": 0.0}, "reference-amount"),
    ("empty unit", E02, {"unit": ""}, "invalid-field"),
    ("empty flow", E02, {"flow_id": ""}, "invalid-field"),
    ("link status that is not one", E02, {"link_status": "maybe"}, "invalid-field"),
    ("linked with no supplier", E02, {"supplier_id": None}, "link-status"),
    ("cut off with a supplier", E02, {"link_status": "cut_off"}, "link-status"),
    (
        "link status on an emission",
        ("toy:electricity-production", "e02"),
        {"link_status": "linked"},
        "link-status",
    ),
    (
        "property twice",
        E02,
        {
            "properties": [
                ("price", {"value": 0.1, "unit": "EUR2005"}),
                ("price", {"value": 0.2, "unit": "EUR2005"}),
            ]
        },
        "duplicate-property",
    ),
    (
        "property of NaN",
        E02,
        {"properties": [("price", {"value": math.nan, "unit": "EUR2005"})]},
        "invalid-field",
    ),
    (
        "property with no name",
        E02,
        {"properties": [("", {"value": 0.1, "unit": "EUR2005"})]},
        "invalid-field",
    ),
    (
        "property with no unit",
        E02,
        {"properties": [("price", {"value": 0.1, "unit": None})]},
        "invalid-field",
    ),
    (
        "lognormal with no scale",
        E02,
        {"uncertainty": {**LOGNORMAL, "scale": None}},
        "incomplete-uncertainty",
    ),
    ("scale of zero", E02, {"uncertainty": {**LOGNORMAL, "scale": 0.0}}, "bad-uncertainty"),
    ("infinite loc", E02, {"uncertainty": {**LOGNORMAL, "loc": math.inf}}, "invalid-field"),
    (
        "unknown distribution",
        E02,
        {"uncertainty": {**LOGNORMAL, "distribution": "weibull"}},
        "invalid-field",
    ),
    (
        "uniform upside down",
        E02,
        {
            "uncertainty": {
                **LOGNORMAL,
                "distribution": "uniform",
                "loc": None,
                "scale": None,
                "minimum": 4.0,
                "maximum": 2.0,
            }
        },
        "bad-uncertainty",
    ),
    (
        "triangular mode outside",
        E02,
        {
            "uncertainty": {
                **LOGNORMAL,
                "distribution": "triangular",
                "loc": 5.0,
                "scale": None,
                "minimum": 2.0,
                "maximum": 4.0,
            }
        },
        "bad-uncertainty",
    ),
    ("pedigree score of 6", E02, {"pedigree": {**PEDIGREE, "reliability": 6}}, "invalid-field"),
    (
        "pedigree with a score missing",
        E02,
        {"pedigree": {**PEDIGREE, "completeness": None}},
        "invalid-field",
    ),
]


@pytest.mark.parametrize(
    ("key", "change", "code"),
    [case[1:] for case in EXCHANGE_CASES],
    ids=[case[0] for case in EXCHANGE_CASES],
)
def test_a_line_a_record_refuses_is_refused_with_the_same_code(
    records, tmp_path: Path, key: tuple[str, str], change: dict[str, Any], code: str
) -> None:
    exchanges = _changed(records.tables()["exchanges"], key, change)
    (row,) = [r for r in exchanges.to_pylist() if (r["process_id"], r["line_id"]) == key]

    with pytest.raises(InvalidDataError) as raised:
        records.build_tables(tmp_path, exchanges=exchanges)

    assert _record_code(row) == code
    assert _problems(raised) == [(code, f"{key[0]} line {key[1]}")]
    assert not any(tmp_path.iterdir())


def test_the_valid_line_reads_back_as_a_record(records) -> None:
    rows = records.tables()["exchanges"].to_pylist()
    lines = [read_exchange(dict(r)) for r in rows]

    assert all(isinstance(e, Exchange) for e in lines)


def test_a_process_needs_exactly_one_reference_line(records, tmp_path: Path) -> None:
    exchanges = _changed(
        records.tables()["exchanges"],
        ("toy:fabric-production", "e01"),
        {"role": "input", "supplier_id": "toy:fabric-production", "link_status": "linked"},
    )
    two = _changed(
        records.tables()["exchanges"],
        ("toy:electricity-production", "e03"),
        {"role": "reference", "flow_id": "toy:electricity", "unit": "kWh"},
    )

    with pytest.raises(InvalidDataError) as none:
        records.build_tables(tmp_path, exchanges=exchanges)
    with pytest.raises(InvalidDataError) as twice:
        records.build_tables(tmp_path, exchanges=two)

    assert _problems(none) == [("reference-count", "process toy:fabric-production")]
    assert _problems(twice) == [("reference-count", "process toy:electricity-production")]


def test_a_line_id_used_twice_is_refused(records, tmp_path: Path) -> None:
    exchanges = records.tables()["exchanges"]
    twice = pa.concat_tables([exchanges, exchanges.slice(3, 1)])

    with pytest.raises(InvalidDataError) as raised:
        records.build_tables(tmp_path, exchanges=twice)

    (row,) = exchanges.slice(3, 1).to_pylist()
    assert _problems(raised) == [
        ("duplicate-line", f"{row['process_id']} line {row['line_id']}"),
    ]


def test_lines_of_a_process_that_is_not_there_are_refused(records, tmp_path: Path) -> None:
    exchanges = _changed(
        records.tables()["exchanges"], ("toy:fabric-production", "e03"), {"process_id": "toy:x"}
    )

    with pytest.raises(InvalidDataError) as raised:
        records.build_tables(tmp_path, exchanges=exchanges)

    assert ("unknown-process", "toy:x line e03") in _problems(raised)


def test_the_reference_flow_of_a_process_is_its_reference_line_flow(
    records, tmp_path: Path
) -> None:
    processes = records.tables()["processes"]
    rows = processes.to_pylist()
    rows[0]["reference_flow_id"] = "toy:cotton-fabric"
    changed = pa.Table.from_pylist(rows, schema=processes.schema)

    with pytest.raises(InvalidDataError) as raised:
        records.build_tables(tmp_path, processes=changed)

    assert _problems(raised) == [("reference-flow-mismatch", f"process {rows[0]['id']}")]


@pytest.mark.parametrize(
    ("table", "column", "value", "item"),
    [
        ("processes", "kind", "factory", "process toy:electricity-production"),
        ("processes", "name", "", "process toy:electricity-production"),
        ("processes", "time_end", date(2020, 1, 1), "process toy:electricity-production"),
        ("flows", "kind", "gas", "flow toy:cotton-fabric"),
        ("flows", "unit", "", "flow toy:cotton-fabric"),
        ("flow_properties", "value", 0.0, "flow toy:cotton-fabric property dry_mass"),
        ("flow_properties", "property", "colour", "flow toy:cotton-fabric property colour"),
        ("issues", "status", "pending", "issue toy:fabric-production line e02"),
        ("issues", "share", 2.0, "issue toy:fabric-production line e02"),
    ],
)
def test_other_rows_a_record_refuses_are_refused(
    records, tmp_path: Path, table: str, column: str, value: object, item: str
) -> None:
    original = records.tables()[table]
    rows = original.to_pylist()
    rows[0][column] = value
    if column == "time_end":
        rows[0]["time_start"] = date(2024, 1, 1)
    changed = pa.Table.from_pylist(rows, schema=original.schema)

    with pytest.raises(InvalidDataError) as raised:
        records.build_tables(tmp_path, **{table: changed})

    assert [(p.code, p.item) for p in raised.value.problems][:1] == [("invalid-field", item)]


def test_a_cut_off_issue_without_its_resolution_is_refused(records, tmp_path: Path) -> None:
    issues = records.tables()["issues"]
    rows = issues.to_pylist()
    rows[1]["criterion"] = None

    with pytest.raises(InvalidDataError) as raised:
        records.build_tables(tmp_path, issues=pa.Table.from_pylist(rows, schema=issues.schema))

    assert _problems(raised) == [("incomplete-issue", "issue toy:fabric-production line e05")]


@pytest.mark.parametrize(
    ("table", "key", "column"),
    [
        ("exchanges", E02, "unit"),
        ("exchanges", E02, "comment"),
        ("exchanges", E02, "supplier_id"),
    ],
)
def test_text_with_spaces_around_it_is_refused(
    records, tmp_path: Path, table: str, key: tuple[str, str], column: str
) -> None:
    # A record strips the spaces, so the same line read from a file and from a table would
    # differ. The table must be stripped already.
    original = records.tables()[table]
    (row,) = [r for r in original.to_pylist() if (r["process_id"], r["line_id"]) == key]
    changed = _changed(original, key, {column: f" {row[column] or 'x'} "})

    with pytest.raises(InvalidDataError) as raised:
        records.build_tables(tmp_path, **{table: changed})

    assert _problems(raised) == [("invalid-field", f"{key[0]} line {key[1]}")]
    assert column in str(raised.value)


def test_a_property_name_with_spaces_around_it_is_refused(records, tmp_path: Path) -> None:
    exchanges = _changed(
        records.tables()["exchanges"],
        E02,
        {"properties": [(" price", {"value": 0.11, "unit": "EUR2005"})]},
    )

    with pytest.raises(InvalidDataError) as raised:
        records.build_tables(tmp_path, exchanges=exchanges)

    assert _problems(raised) == [("invalid-field", "toy:fabric-production line e02")]


def test_properties_must_be_sorted_by_name(records, tmp_path: Path) -> None:
    exchanges = _changed(
        records.tables()["exchanges"],
        E02,
        {
            "properties": [
                ("price", {"value": 0.11, "unit": "EUR2005"}),
                ("heating value, net", {"value": 3.6, "unit": "MJ"}),
            ]
        },
    )

    with pytest.raises(InvalidDataError) as raised:
        records.build_tables(tmp_path, exchanges=exchanges)

    assert _problems(raised) == [("invalid-field", "toy:fabric-production line e02")]
    assert "sorted by name" in str(raised.value)
