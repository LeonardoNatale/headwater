"""An Excel import made with headwater 0.1.0, in the layout before D48, once converted, is the
same files as the import made now, byte for byte, its record of the import included. The fixture
tests/fixtures/old-layout/excel-tshirt is what 0.1.0 wrote for the toy T-shirt workbook."""

import shutil
from pathlib import Path

import pytest

from headwater.io.excel import import_excel
from headwater.model import InvalidDataError
from headwater.release import Release
from headwater.study._convert import convert
from tests.io.excel.conftest import FIXTURES, Workbooks

OLD_IMPORT = FIXTURES / "old-layout" / "excel-tshirt"


def files(folder: Path) -> dict[str, bytes]:
    return {
        p.relative_to(folder).as_posix(): p.read_bytes() for p in folder.rglob("*") if p.is_file()
    }


def test_an_old_import_converted_is_the_import_of_now(
    workbooks: Workbooks, toy_release: Release, tmp_path: Path
) -> None:
    workbook = workbooks.write()
    assert workbook.name == "inventory 1.xlsx"  # the name 0.1.0's comment lines give
    new = import_excel(workbook, toy_release, tmp_path / "new")
    assert new.clean
    old = tmp_path / "old"
    shutil.copytree(OLD_IMPORT, old)
    convert(old)
    assert files(old) == files(tmp_path / "new")


def test_an_old_import_converted_is_replaced_by_a_new_import(
    workbooks: Workbooks, toy_release: Release, tmp_path: Path
) -> None:
    """The record of the import names the two tables, so importing again replaces them."""
    old = tmp_path / "old"
    shutil.copytree(OLD_IMPORT, old)
    convert(old)
    done = import_excel(workbooks.write(), toy_release, old)
    assert done.clean
    assert sorted(files(old)) == [
        ".headwater-import.json",
        "database.yaml",
        "exchanges.csv",
        "flows.csv",
        "parameters.yaml",
        "processes.csv",
    ]


def test_an_edited_old_import_converted_refuses_a_new_import(
    workbooks: Workbooks, toy_release: Release, tmp_path: Path
) -> None:
    old = tmp_path / "old"
    shutil.copytree(OLD_IMPORT, old)
    csv = old / "processes" / "t-shirt-glo.csv"
    csv.write_text(csv.read_text().replace(",0.5,", ",0.6,"))
    convert(old)
    with pytest.raises(InvalidDataError, match="was edited") as error:
        import_excel(workbooks.write(), toy_release, old)
    assert {p.item for p in error.value.problems} == {"file exchanges.csv", "file processes.csv"}
