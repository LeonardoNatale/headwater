"""Finding a background process by name: `bg.find(text)` and the search table behind it.

The toy background has three processes: electricity production (a kWh), cotton fabric production
(a kg) and incineration of textile waste (a kg), all in GLO. The rules are tried in this order,
and a process is listed once, at the first rule it meets: name is the text, name starts with it,
name has every word of it, then the same three on the reference product.
"""

import re
from dataclasses import replace
from pathlib import Path

import pytest

import headwater as hw
from headwater.model import InvalidDataError, Process, ProcessKind
from headwater.query import background as background_module
from headwater.query import search
from headwater.query.search import Match, SearchTable
from headwater.release import pull
from headwater.study import ProcessFolder

ELECTRICITY = "toy:electricity-production"
FABRIC = "toy:fabric-production"
INCINERATION = "toy:incineration-textile-waste"
ORDINARY = ProcessKind.ordinary_transforming_activity.value
MARKET = ProcessKind.market_activity.value


def ids(matches: tuple[Match, ...]) -> list[str]:
    return [m.process_id for m in matches]


def whys(matches: tuple[Match, ...]) -> list[str]:
    return [m.why for m in matches]


def table(*rows: tuple[str, str, str, str], kind: str = ORDINARY) -> SearchTable:
    """A search table from (id, name, product, location) rows, every process in kg."""
    return SearchTable(
        ids=tuple(r[0] for r in rows),
        flow_ids=tuple(f"flow:{r[0]}" for r in rows),
        names=tuple(r[1] for r in rows),
        products=tuple(r[2] for r in rows),
        locations=tuple(r[3] for r in rows),
        units=tuple("kg" for _ in rows),
        kinds=tuple(kind for _ in rows),
    )


def renamed(records, **names: str):  # pyright: ignore[reportMissingParameterType, reportUnknownParameterType]
    """The toy records with some processes renamed, by the end of their id: `fabric="weaving"`.
    The name of a product comes from its flow, so it stays as it is."""

    def rename(process: Process) -> Process:
        name = names.get(process.id.removeprefix("toy:").split("-")[0])
        return process if name is None else process.model_copy(update={"name": name})

    return replace(records, processes=tuple(rename(p) for p in records.processes))


@pytest.fixture
def product_background(records, make_release) -> hw.Background:
    """The toy with its processes renamed so that no name holds its product: the product rules
    are then the only ones that can match the products."""
    names = {"fabric": "weaving mill", "incineration": "waste burning"}
    release = make_release(renamed(records, **names))
    return hw.load(release.path, method="toy-climate")


# --- one test for each rule, on the toy ------------------------------------------------------


def test_name_is_the_text(either: hw.Background) -> None:
    found = either.find("electricity production")

    assert found == (
        Match(
            process_id=ELECTRICITY,
            flow_id="toy:electricity",
            name="electricity production",
            reference_product="electricity",
            location="GLO",
            unit="kWh",
            kind=ORDINARY,
            why="name is",
        ),
    )


def test_name_starts_with_the_text(either: hw.Background) -> None:
    found = either.find("electricity")

    assert ids(found) == [ELECTRICITY]
    assert whys(found) == ["name starts with"]


def test_name_has_every_word_of_the_text_in_any_order(either: hw.Background) -> None:
    found = either.find("production cotton")

    assert ids(found) == [FABRIC]
    assert whys(found) == ["every word in name"]


def test_a_word_is_part_of_a_word_so_a_prefix_finds_the_process(either: hw.Background) -> None:
    assert ids(either.find("textile wa")) == [INCINERATION]
    assert ids(either.find("fabri")) == [FABRIC]


def test_every_word_is_needed(either: hw.Background) -> None:
    assert either.find("production incineration") == ()


def test_matching_ignores_case_and_extra_spaces(either: hw.Background) -> None:
    assert either.find("  ELECTRICITY   Production ") == either.find("electricity production")


def test_product_is_the_text(product_background: hw.Background) -> None:
    found = product_background.find("cotton fabric")

    assert ids(found) == [FABRIC]
    assert whys(found) == ["product is"]
    assert found[0].name == "weaving mill"
    assert found[0].reference_product == "cotton fabric"


def test_product_starts_with_the_text(product_background: hw.Background) -> None:
    found = product_background.find("cotton")

    assert ids(found) == [FABRIC]
    assert whys(found) == ["product starts with"]


def test_product_has_every_word_of_the_text(product_background: hw.Background) -> None:
    found = product_background.find("fabric cotton")

    assert ids(found) == [FABRIC]
    assert whys(found) == ["every word in product"]


def test_a_process_is_listed_once_at_its_first_rule(product_background: hw.Background) -> None:
    """`waste burning` starts with `waste`, and its product `textile waste` has the word too."""
    found = product_background.find("waste")

    assert ids(found) == [INCINERATION]
    assert whys(found) == ["name starts with"]


def test_nothing_matches(either: hw.Background) -> None:
    assert either.find("steel") == ()


# --- order -----------------------------------------------------------------------------------


def test_the_six_rules_come_in_order() -> None:
    rows = table(
        ("p6", "x3", "low steel mix", "GLO"),
        ("p3", "market for steel", "m", "GLO"),
        ("p1", "steel", "q", "GLO"),
        ("p5", "x2", "steel, chromium", "GLO"),
        ("p2", "steel, low-alloyed", "r", "GLO"),
        ("p4", "x1", "steel", "GLO"),
    )

    found = rows.find("steel")

    assert ids(found) == ["p1", "p2", "p3", "p4", "p5", "p6"]
    assert whys(found) == [
        "name is",
        "name starts with",
        "every word in name",
        "product is",
        "product starts with",
        "every word in product",
    ]


def test_a_name_rule_beats_a_product_rule_for_the_same_process() -> None:
    rows = table(("a", "market for steel", "steel", "GLO"), ("b", "other", "steel", "GLO"))

    found = rows.find("steel")

    assert ids(found) == ["a", "b"]
    assert whys(found) == ["every word in name", "product is"]


def test_ties_go_by_name_then_location_then_product_then_id() -> None:
    rows = table(
        ("id-9", "steel b", "p", "RER"),
        ("id-8", "steel b", "p", "GLO"),
        ("id-7", "steel b", "o", "RER"),
        ("id-6", "steel b", "o", "RER"),
        ("id-5", "steel a", "p", "RoW"),
    )

    found = rows.find("steel")

    assert ids(found) == ["id-5", "id-8", "id-6", "id-7", "id-9"]


def test_ties_by_name_do_not_depend_on_case() -> None:
    rows = table(("b", "Steel, b", "p", "GLO"), ("a", "steel, a", "p", "GLO"))

    assert ids(rows.find("steel")) == ["a", "b"]


def test_the_order_does_not_depend_on_the_order_of_the_table() -> None:
    rows = [
        ("a", "steel x", "p", "GLO"),
        ("b", "steel x", "p", "RER"),
        ("c", "steel w", "q", "GLO"),
    ]

    forward = table(*rows).find("steel")
    backward = table(*reversed(rows)).find("steel")

    assert forward == backward
    assert ids(forward) == ["c", "a", "b"]


# --- filters and limit -----------------------------------------------------------------------


@pytest.fixture
def mixed(toy: Path, edit, load_toy) -> hw.Background:
    """The toy with electricity in RER, and the fabric a market."""
    processes = toy / "background" / "processes.csv"
    edit(processes, "electricity production,GLO", "electricity production,RER")
    edit(
        processes,
        "cotton fabric production,GLO,ordinary_transforming_activity",
        "cotton fabric production,GLO,market_activity",
    )
    return load_toy(toy)


def test_location_keeps_the_processes_there(mixed: hw.Background) -> None:
    assert ids(mixed.find("production")) == [FABRIC, ELECTRICITY]
    assert ids(mixed.find("production", location="RER")) == [ELECTRICITY]
    assert ids(mixed.find("production", location="GLO")) == [FABRIC]


def test_location_ignores_case(mixed: hw.Background) -> None:
    assert ids(mixed.find("production", location="rer")) == [ELECTRICITY]


def test_location_is_the_whole_code_not_a_part_of_it(mixed: hw.Background) -> None:
    with pytest.raises(ValueError, match="no process is in 'RE'"):
        mixed.find("production", location="RE")


def test_an_unknown_location_is_an_error_that_lists_the_places(mixed: hw.Background) -> None:
    with pytest.raises(ValueError, match=r"'GL0'.*Did you mean 'GLO'\?.*GLO, RER"):
        mixed.find("production", location="GL0")


def test_kind_keeps_the_processes_of_that_kind(mixed: hw.Background) -> None:
    assert ids(mixed.find("production", kind="market_activity")) == [FABRIC]
    assert ids(mixed.find("production", kind=ProcessKind.ordinary_transforming_activity)) == [
        ELECTRICITY
    ]
    assert mixed.find("production", kind="market_activity")[0].kind == MARKET


def test_kind_ignores_case(mixed: hw.Background) -> None:
    assert ids(mixed.find("production", kind=" Market_Activity ")) == [FABRIC]


def test_a_kind_with_no_process_is_an_empty_answer(mixed: hw.Background) -> None:
    assert mixed.find("production", kind="production_mix") == ()


def test_an_unknown_kind_is_an_error_that_lists_the_kinds(mixed: hw.Background) -> None:
    with pytest.raises(ValueError, match=r"'market' is not a kind.*Kinds: .*market_activity"):
        mixed.find("production", kind="market")
    with pytest.raises(ValueError, match=r"Did you mean 'market_activity'\?"):
        mixed.find("production", kind="market_activty")


def test_product_keeps_the_processes_whose_product_has_every_word(either: hw.Background) -> None:
    assert ids(either.find("production", product="cotton")) == [FABRIC]
    assert ids(either.find("production", product="FABRIC cot")) == [FABRIC]
    assert either.find("production", product="cotton electricity") == ()


def test_filters_combine(mixed: hw.Background) -> None:
    found = mixed.find("production", location="GLO", product="fabric", kind="market_activity")

    assert ids(found) == [FABRIC]
    assert mixed.find("production", location="RER", kind="market_activity") == ()


def test_limit_keeps_the_best_first(either: hw.Background) -> None:
    both = either.find("production")
    assert len(both) == 2

    assert either.find("production", limit=1) == both[:1]
    assert either.find("production", limit=0) == ()
    assert either.find("production", limit=None) == both


def test_the_default_limit_is_20() -> None:
    rows = table(*((f"p{i:02}", f"steel {i:02}", "p", "GLO") for i in range(30)))

    assert len(rows.find("steel")) == 20
    assert len(rows.find("steel", limit=None)) == 30
    assert ids(rows.find("steel"))[:2] == ["p00", "p01"]


# --- bad input -------------------------------------------------------------------------------


@pytest.mark.parametrize("text", ["", "   "])
def test_empty_text_is_an_error(either: hw.Background, text: str) -> None:
    with pytest.raises(ValueError, match="text to look for"):
        either.find(text)


def test_empty_product_is_an_error(either: hw.Background) -> None:
    with pytest.raises(ValueError, match="product is empty"):
        either.find("production", product=" ")


def test_a_negative_limit_is_an_error(either: hw.Background) -> None:
    with pytest.raises(ValueError, match="limit must be zero or positive, got -1"):
        either.find("production", limit=-1)


# --- the table -------------------------------------------------------------------------------


def test_the_release_and_the_background_search_the_same_table(
    release_background: hw.Background,
) -> None:
    release = release_background.release
    assert release is not None

    from_release = search.table_of_release(release)

    for text in ("e", "production", "waste", "cotton fabric", "electricity production"):
        assert from_release.find(text, limit=None) == release_background.find(text, limit=None)
        assert release_background.find(text, limit=None)


def test_the_folder_and_the_release_search_the_same_table(
    background: hw.Background, release_background: hw.Background
) -> None:
    for text in ("e", "production", "waste", "cotton fabric"):
        assert background.find(text, limit=None) == release_background.find(text, limit=None)


def test_a_folder_of_process_files_has_a_table_without_a_method(toy: Path) -> None:
    folder = search.table_of_path(toy / "background", namespace="toy")

    assert ids(folder.find("electricity")) == [ELECTRICITY]


def test_a_namespace_is_for_a_folder_not_a_release(make_release) -> None:
    with pytest.raises(ValueError, match="namespace is for a folder of process files"):
        search.table_of_path(make_release().path, namespace="toy")


def test_a_release_is_found_by_its_ref_or_its_folder(
    make_release, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    release = make_release()
    monkeypatch.setenv("HEADWATER_HOME", str(tmp_path / "home"))
    pull(release.path)
    ref = f"{release.name}@{release.hash[:12]}"

    by_ref = search.table_of_path(ref).find("electricity")
    by_folder = search.table_of_path(release.path).find("electricity")

    assert ids(by_ref) == [ELECTRICITY]
    assert by_ref == by_folder


def test_a_process_whose_product_is_not_in_the_flows_is_named(records) -> None:
    flows = tuple(f for f in records.flows if f.id != "toy:electricity")
    broken = ProcessFolder(
        path=Path("."),
        namespace="toy",
        processes=records.processes,
        flows=flows,
        flow_properties=(),
        units=(),
    )

    with pytest.raises(InvalidDataError, match=re.escape(ELECTRICITY) + ".*toy:electricity"):
        search.table_of_folder(broken)


def test_the_table_is_built_once_for_a_background(
    load_toy, monkeypatch: pytest.MonkeyPatch
) -> None:
    built: list[object] = []
    real = background_module.table_of_folder

    def counting(folder: object) -> SearchTable:
        built.append(folder)
        return real(folder)  # pyright: ignore[reportArgumentType]

    monkeypatch.setattr(background_module, "table_of_folder", counting)
    bg = load_toy()

    bg.find("electricity")
    bg.find("fabric", location="GLO")
    bg.find("waste")

    assert len(built) == 1


# --- one exact process: get_process ------------------------------------------------------------


def test_a_match_carries_the_flow_id_of_its_reference_product(either: hw.Background) -> None:
    (match,) = either.find("electricity")
    assert match.flow_id == "toy:electricity"


def test_get_process_takes_name_product_and_location_exactly(either: hw.Background) -> None:
    found = either.get_process("cotton fabric production", product="cotton fabric", location="GLO")

    assert (found.process_id, found.flow_id, found.why) == (FABRIC, "toy:cotton-fabric", "name is")
    assert either.get_process("cotton fabric production").process_id == FABRIC


@pytest.mark.parametrize(
    ("name", "product", "location"),
    [
        ("cotton fabric", None, None),  # a part of the name is not the name
        ("Cotton fabric production", None, None),  # case counts
        ("cotton fabric production", "cotton", None),
        ("cotton fabric production", None, "RER"),
    ],
)
def test_get_process_with_no_exact_match_raises_naming_the_candidates(
    either: hw.Background, name: str, product: str | None, location: str | None
) -> None:
    with pytest.raises(ValueError, match="no process") as error:
        either.get_process(name, product=product, location=location)
    assert "cotton fabric production | cotton fabric | GLO" in str(error.value)


def test_get_process_with_several_matches_raises_naming_them() -> None:
    twins = table(
        ("a", "market for steel", "steel", "GLO"),
        ("b", "market for steel", "steel", "RER"),
        ("c", "market for steel", "steel, low-alloyed", "GLO"),
    )
    with pytest.raises(ValueError, match="3 processes") as error:
        twins.get("market for steel")
    for line in ("market for steel | steel | GLO", "market for steel | steel | RER"):
        assert line in str(error.value)
    with pytest.raises(ValueError, match="2 processes"):
        twins.get("market for steel", location="GLO")
    assert twins.get("market for steel", product="steel", location="RER").process_id == "b"
