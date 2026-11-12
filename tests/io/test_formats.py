"""The registry: one line per format, `--format` by name, and detection when unambiguous."""

import zipfile
from collections.abc import Callable
from pathlib import Path

import pytest

from headwater.io import formats as formats_module
from headwater.io.formats import FORMATS, check_options, choose, find, formats
from headwater.io.source import (
    Format,
    InventoryOptions,
    InventorySource,
    MethodOptions,
    MethodSource,
    SourceKind,
)
from headwater.model import InvalidDataError

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
ECOSPOLD2 = FIXTURES / "formats" / "ecospold2" / "toy"
OLCA = FIXTURES / "formats" / "olca-jsonld" / "toy"


def _codes(raised: pytest.ExceptionInfo[InvalidDataError]) -> list[str]:
    return [p.code for p in raised.value.problems]


def test_names_are_unique_and_every_format_reads_something() -> None:
    names = [f.name for f in FORMATS]

    assert len(names) == len(set(names))
    assert all(f.read_inventory or f.read_method for f in FORMATS)
    assert [f.name for f in formats(SourceKind.inventory)] == [
        "ecospold2",
        "ecospold1",
        "olca-jsonld",
        "simapro-csv",
    ]
    assert [f.name for f in formats(SourceKind.method)] == [
        "ecoinvent-lcia",
        "olca-jsonld",
        "simapro-csv",
    ]


def test_an_ecospold2_folder_is_detected() -> None:
    assert choose(ECOSPOLD2, SourceKind.inventory).name == "ecospold2"


@pytest.fixture
def olca_paths(
    tmp_path: Path,
    zip_package: Callable[[Path, Path], Path],
    database_backup: Callable[[Path], Path],
) -> list[Path]:
    """The JSON-LD toy as a folder, a zip and a .zolca, and a .zolca that holds an openLCA
    database backup (Derby files) instead."""
    return [
        OLCA,
        zip_package(OLCA, tmp_path / "toy.zip"),
        zip_package(OLCA, tmp_path / "toy.zolca"),
        database_backup(tmp_path / "database.zolca"),
    ]


@pytest.mark.parametrize("kind", list(SourceKind))
def test_a_json_ld_package_is_detected_and_claimed_by_no_other_format(
    olca_paths: list[Path], kind: SourceKind
) -> None:
    for path in olca_paths:
        assert choose(path, kind).name == "olca-jsonld", path.name
        assert [f.name for f in FORMATS if f.detect(path)] == ["olca-jsonld"], path.name


def test_json_ld_claims_neither_an_ecospold2_folder_nor_a_plain_zip(tmp_path: Path) -> None:
    olca = find("olca-jsonld")
    assert olca is not None
    plain = tmp_path / "plain.zip"
    with zipfile.ZipFile(plain, "w") as other:
        other.writestr("datasets/a.spold", "<x/>")

    assert not olca.detect(ECOSPOLD2)
    assert not olca.detect(plain)
    assert not olca.detect(tmp_path)


def test_the_workbook_is_detected_from_its_sheets(workbook: Path, tmp_path: Path) -> None:
    other = tmp_path / "other.xlsx"
    other.write_bytes(b"not a workbook")

    assert choose(workbook, SourceKind.method).name == "ecoinvent-lcia"
    with pytest.raises(InvalidDataError) as raised:
        choose(other, SourceKind.method)
    assert _codes(raised) == ["unknown-format"]


def test_a_path_no_format_recognises_names_the_formats(tmp_path: Path) -> None:
    with pytest.raises(InvalidDataError) as raised:
        choose(tmp_path, SourceKind.inventory)

    assert _codes(raised) == ["unknown-format"]
    assert "--format" in str(raised.value) and "ecospold2" in str(raised.value)


def _never(path: Path, options: InventoryOptions) -> InventorySource:
    raise AssertionError("not read")


def test_a_path_two_formats_recognise_needs_format(monkeypatch: pytest.MonkeyPatch) -> None:
    def twin(name: str) -> Format:
        return Format(name, name, lambda _: True, read_inventory=_never)

    monkeypatch.setattr(formats_module, "FORMATS", (twin("a"), twin("b")))

    with pytest.raises(InvalidDataError) as raised:
        choose(ECOSPOLD2, SourceKind.inventory)
    assert _codes(raised) == ["ambiguous-format"]
    assert choose(ECOSPOLD2, SourceKind.inventory, "b").name == "b"


@pytest.mark.parametrize(
    ("name", "kind", "code"),
    [
        ("simapro", SourceKind.inventory, "unknown-format"),
        ("ecoinvent-lcia", SourceKind.inventory, "wrong-format-kind"),
        ("ecospold2", SourceKind.method, "wrong-format-kind"),
    ],
)
def test_a_format_given_by_name_must_exist_and_read_that_kind(
    name: str, kind: SourceKind, code: str
) -> None:
    with pytest.raises(InvalidDataError) as raised:
        choose(ECOSPOLD2, kind, name)

    assert _codes(raised) == [code]


def test_an_option_the_format_does_not_take_is_named_by_its_flag() -> None:
    lcia = find("ecoinvent-lcia")
    assert lcia is not None

    check_options(lcia, SourceKind.method, MethodOptions(method="EF v3.1", categories=("a",)))
    with pytest.raises(InvalidDataError) as raised:
        check_options(_two_kinds(), SourceKind.inventory, InventoryOptions(version="1", workers=2))

    assert [(p.code, p.item) for p in raised.value.problems] == [("option-not-taken", "--workers")]


def test_source_and_licence_are_options_a_format_takes_or_refuses() -> None:
    ecospold2 = find("ecospold2")
    assert ecospold2 is not None
    given = InventoryOptions(version="1", source="bafu", licence="CC-BY-4.0")

    check_options(_takes_source_and_licence(), SourceKind.inventory, given)
    with pytest.raises(InvalidDataError) as raised:
        check_options(ecospold2, SourceKind.inventory, given)

    assert [p.item for p in raised.value.problems] == ["--source", "--licence"]


def _takes_source_and_licence() -> Format:
    return Format(
        "package",
        "package",
        lambda _: False,
        read_inventory=_never,
        inventory_options=frozenset({"version", "source", "licence"}),
    )


def test_options_are_checked_against_the_reader_of_that_kind() -> None:
    both = _two_kinds()

    check_options(both, SourceKind.method, MethodOptions(categories=("climate change",)))
    with pytest.raises(InvalidDataError) as raised:
        check_options(both, SourceKind.method, MethodOptions(method="EF 3.1", version="2"))

    assert [p.item for p in raised.value.problems] == ["--method", "--version"]


def _two_kinds() -> Format:
    """A format that reads both kinds, with different options for each."""
    return Format(
        "both",
        "both",
        lambda _: False,
        read_inventory=_never,
        inventory_options=frozenset({"version"}),
        read_method=_never_method,
        method_options=frozenset({"categories"}),
    )


def _never_method(path: Path, options: MethodOptions) -> MethodSource:
    raise AssertionError("not read")


@pytest.fixture
def workbook(tmp_path: Path) -> Path:
    import openpyxl

    book = openpyxl.Workbook()
    book.active.title = "CFs"  # pyright: ignore[reportOptionalMemberAccess]
    path = tmp_path / "LCIA Implementation.xlsx"
    book.save(path)
    return path
