"""Detection reads the first bytes of one XML file: the root must be `ecoSpold`, in the ecoSpold1
namespace or in none (openLCA writes none). It never claims an ecoSpold2 folder, and ecoSpold2
never claims this."""

from pathlib import Path

import pytest

from headwater.io.ecospold1 import FORMAT
from headwater.io.formats import choose
from headwater.io.source import SourceKind
from headwater.model import InvalidDataError

FORMATS = Path(__file__).resolve().parents[2] / "fixtures" / "formats"
TOY = FORMATS / "ecospold1" / "toy"
ECOSPOLD2_TOY = FORMATS / "ecospold2" / "toy"


def _ecospold2():
    from headwater.io.ecospold2 import FORMAT as ECOSPOLD2

    return ECOSPOLD2


def test_the_ecospold1_toy_is_detected_and_not_claimed_by_ecospold2() -> None:
    assert FORMAT.detect(TOY)
    assert not _ecospold2().detect(TOY)
    assert choose(TOY, SourceKind.inventory).name == "ecospold1"


def test_an_ecospold2_folder_is_not_detected_and_still_is_ecospold2() -> None:
    assert not FORMAT.detect(ECOSPOLD2_TOY)
    assert _ecospold2().detect(ECOSPOLD2_TOY)
    assert choose(ECOSPOLD2_TOY, SourceKind.inventory).name == "ecospold2"


def test_a_file_with_no_namespace_is_detected(es1) -> None:
    folder = es1.make(es1.maker("widget"), namespace=None)

    assert FORMAT.detect(folder)


@pytest.mark.parametrize(
    "namespace",
    [
        "http://www.EcoInvent.org/EcoSpold02",
        "http://www.EcoInvent.org/EcoSpold01Impact",
        "http://www.EcoInvent.org/EcoSpold01Elementary",
        "http://example.org/other",
    ],
)
def test_another_namespace_is_not_detected(es1, namespace: str) -> None:
    folder = es1.make(es1.maker("widget"), namespace=namespace)

    assert not FORMAT.detect(folder)


def test_a_byte_order_mark_a_declaration_and_comments_do_not_hide_the_root(tmp_path: Path) -> None:
    path = tmp_path / "a.xml"
    path.write_bytes(
        b"\xef\xbb\xbf<?xml version='1.0' encoding='UTF-8'?>\n<!-- exported -->\n"
        b'<ecoSpold xmlns="http://www.EcoInvent.org/EcoSpold01"><dataset number="1"/></ecoSpold>'
    )

    assert FORMAT.detect(tmp_path)


def test_a_single_file_and_nested_folders_are_detected(es1, tmp_path: Path) -> None:
    es1.write(tmp_path / "pkg" / "deep", "a.xml", es1.maker("widget"))

    assert FORMAT.detect(tmp_path / "pkg")
    assert FORMAT.detect(tmp_path / "pkg" / "deep" / "a.xml")


def test_other_paths_are_not_detected(tmp_path: Path) -> None:
    (tmp_path / "empty").mkdir()
    (tmp_path / "text.xml").write_text("not xml at all")
    (tmp_path / "other").mkdir()
    (tmp_path / "other" / "x.spold").write_text("<ecoSpold/>")

    assert not FORMAT.detect(tmp_path / "empty")
    assert not FORMAT.detect(tmp_path / "text.xml")
    assert not FORMAT.detect(tmp_path / "other")
    assert not FORMAT.detect(tmp_path / "missing")
    with pytest.raises(InvalidDataError):
        choose(tmp_path / "empty", SourceKind.inventory)
