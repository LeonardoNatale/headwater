"""The diff as text and as JSON: suppliers named in words, each line listed once, comments apart.

Toy numbers (docs/calculation.md): electricity 0.5298 kg CO2-eq per kWh. The solar electricity
made here emits 0.05 kg of CO2 per kWh, so moving the T-shirt's 0.5 kWh to it changes its climate
score by 0.5 × (0.05 - 0.5298) = -0.2399.
"""

import shutil
from pathlib import Path

import pytest

import headwater as hw
from headwater.diff import Source, diff_databases, render_json, render_text
from headwater.study import DatabaseFolder, read_database
from tests.conftest import add_lines, add_process

TOY = Path(__file__).resolve().parent.parent / "fixtures" / "toy"
REL = 1e-9
OLD, NEW = Source("main", "a" * 40), Source("solar", "b" * 40)


def edit(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    assert old in text, f"{old!r} not found in {path}"
    path.write_text(text.replace(old, new, 1))


def study(root: Path) -> DatabaseFolder:
    return read_database(root / "tshirt" / "tshirt")


def csv(root: Path) -> Path:
    return root / "tshirt" / "tshirt" / "exchanges.csv"


def solar(root: Path, rest: str = "") -> None:
    """The T-shirt's electricity bought from a solar panel of its own, in France. `rest` ends
    each of its lines, for the columns that exchanges.csv has beyond the supplier: `","` for an
    empty comment."""
    add_process(
        root / "tshirt" / "tshirt",
        "solar",
        f"e01,reference,toy:electricity,1,kWh,{rest}\ne02,emission,toy:co2,0.05,kg,{rest}",
        details="solar electricity,FR,ordinary_transforming_activity",
    )
    edit(csv(root), "0.5,kWh,toy:electricity-production", "0.5,kWh,tshirt:solar")


@pytest.fixture
def impacts(toy: Path) -> hw.Background:
    """The toy background with the three indicators of toy-impacts, which the T-shirt names."""
    edit(
        toy / "tshirt" / "tshirt" / "database.yaml",
        "methods: [toy-climate]",
        "methods: [toy-impacts]",
    )
    return hw.load(TOY / "background", method=TOY / "methods" / "toy-impacts", namespace="toy")


def text_and_json(
    old: DatabaseFolder, new: DatabaseFolder, background: hw.Background
) -> tuple[str, dict[str, object]]:
    diff = diff_databases(old, new, background)
    text = render_text(diff, path="tshirt/tshirt", old=OLD, new=NEW)
    return text, render_json(diff, path="tshirt/tshirt", old=OLD, new=NEW)


# --- suppliers in words ------------------------------------------------------------------------


def test_a_supplier_swap_names_both_suppliers_by_name_and_location(
    toy: Path, background: hw.Background
) -> None:
    old = study(toy)
    solar(toy)

    diff = diff_databases(old, study(toy), background)
    text = render_text(diff, path="tshirt/tshirt", old=OLD, new=NEW)
    data = render_json(diff, path="tshirt/tshirt", old=OLD, new=NEW)

    assert diff.rows[0].change == pytest.approx(-0.2399, rel=REL)
    expected = "supplier electricity production (GLO) → solar electricity (FR)"
    assert f"tshirt:tshirt line e03 (electricity): {expected}" in text
    assert "toy:electricity-production" not in text
    by_id = {d["id"]: d for d in data["differences"]}
    line = by_id["line tshirt:tshirt line e03"]
    # the ids stay in the JSON, the words are added
    assert line["fields"] == [
        {"name": "supplier", "old": "toy:electricity-production", "new": "tshirt:solar"}
    ]
    assert line["description"] == "supplier toy:electricity-production → tshirt:solar"
    assert line["text"] == expected
    assert data["processes"]["toy:electricity-production"] == {
        "name": "electricity production",
        "location": "GLO",
    }
    assert data["processes"]["tshirt:solar"] == {"name": "solar electricity", "location": "FR"}


# --- each line once ----------------------------------------------------------------------------


def test_each_line_is_listed_once_with_its_effect_on_every_indicator(
    toy: Path, impacts: hw.Background
) -> None:
    old = study(toy)
    edit(csv(toy), "toy:cotton-fabric,0.25", "toy:cotton-fabric,0.3")
    edit(csv(toy), "toy:electricity,0.5", "toy:electricity,0.6")

    text, _ = text_and_json(old, study(toy), impacts)

    assert text.count("tshirt:tshirt line e02") == 1
    assert text.count("tshirt:tshirt line e03") == 1
    assert text.count("Lines that differ") == 1
    effects = text.split("Effect of each line alone on each score that changed:")[1]
    header, *rows = [line for line in effects.splitlines() if line.strip()]
    assert header.split() == ["e02", "e03"]
    assert [r.split(":")[0].strip() for r in rows] == [
        "Climate change (kg CO2-eq)",
        "Climate change, fossil (kg CO2-eq)",
        "Photochemical ozone formation (kg NMVOC-eq)",
    ]
    # 0.05 kg more fabric at 3.7086, 0.1 kWh more electricity at 0.5298
    assert rows[0].split()[-2:] == ["+0.18543", "+0.05298"]


def test_scores_keep_the_methods_order_and_lines_the_order_of_the_table(
    toy: Path, impacts: hw.Background
) -> None:
    old = study(toy)
    # 1 g of methane: +0.0298 kg CO2-eq, more than the 0.0005 kWh of electricity
    edit(csv(toy), "toy:electricity,0.5", "toy:electricity,0.5005")
    add_lines(toy / "tshirt" / "tshirt", "tshirt", "e04,emission,toy:ch4,0.001,kg,")

    text, _ = text_and_json(old, study(toy), impacts)

    scores = text.split("\n\n")[1].splitlines()[1:]
    assert [s.split(":")[0].strip() for s in scores] == [
        "Climate change",
        "Climate change, fossil",
        "Photochemical ozone formation",
    ]
    listed = text.split("Lines that differ:\n")[1].split("\n\n")[0].splitlines()
    assert [line.split()[0] for line in listed] == ["e03", "e04"]


def test_the_lines_with_the_largest_share_of_a_change_get_the_columns(
    toy: Path, impacts: hw.Background
) -> None:
    old = study(toy)
    # six emissions of CO2, from 1 g to 6 g, and one of methane that alone moves ozone
    add_lines(
        toy / "tshirt" / "tshirt",
        "tshirt",
        *(f"e{10 + i},emission,toy:co2,{i / 1000},kg," for i in range(1, 7)),
        "e20,emission,toy:ch4,0.000001,kg,",
    )

    text, _ = text_and_json(old, study(toy), impacts)

    effects = text.split("Effect of each line alone on each score that changed:")[1]
    header = effects.splitlines()[1].split()
    # e20 is a tiny share of climate change but all of the ozone change: it keeps its column
    assert header == ["e13", "e14", "e15", "e16", "e20", "2", "others"]


def test_each_product_has_its_own_table_with_the_columns_of_the_lines_that_moved_it(
    toy: Path, impacts: hw.Background
) -> None:
    folder = toy / "tshirt" / "tshirt"
    add_process(
        folder,
        "fabric",
        "e01,reference,tshirt:fabric,1,kg,\n"
        "e02,input,toy:electricity,3,kWh,toy:electricity-production",
        details="own fabric,GLO,ordinary_transforming_activity",
    )
    with (folder / "flows.csv").open("a") as handle:
        handle.write("tshirt:fabric,own fabric,product,kg,,\n")
    add_lines(folder, "tshirt", "e04,input,tshirt:fabric,0.1,kg,tshirt:fabric")
    old = study(toy)
    edit(csv(toy), "toy:electricity,3,", "toy:electricity,4,")
    edit(csv(toy), "toy:electricity,0.5", "toy:electricity,0.6")

    diff = diff_databases(old, study(toy), impacts, products="all")
    text = render_text(diff, path="tshirt/tshirt", old=OLD, new=NEW)

    assert "T-shirt (tshirt:tshirt)" in text and "own fabric (tshirt:fabric)" in text
    assert text.count("tshirt:fabric line e02") == 1
    tables = {
        name: text.split(
            f"Effect of each line alone on each score of {name} ({flow}) that changed:"
        )[1]
        .split("\n\n")[0]
        .splitlines()[1:]
        for name, flow in (("T-shirt", "tshirt:tshirt"), ("own fabric", "tshirt:fabric"))
    }
    for rows in tables.values():
        assert [r.split(":")[0].strip() for r in rows[1:]] == [
            "Climate change (kg CO2-eq)",
            "Climate change, fossil (kg CO2-eq)",
            "Photochemical ozone formation (kg NMVOC-eq)",
        ]
    # the fabric moves with its own electricity, the T-shirt with its own and the fabric's
    assert tables["own fabric"][0].split() == ["e02"]
    assert tables["T-shirt"][0].split() == ["e02", "e03"]


def test_one_indicator_reads_as_before(toy: Path, background: hw.Background) -> None:
    old = study(toy)
    edit(csv(toy), "toy:cotton-fabric,0.25", "toy:cotton-fabric,0.22")

    text, _ = text_and_json(old, study(toy), background)

    assert text.endswith(
        "T-shirt (tshirt:tshirt)\n"
        "  Climate change: 1.19205 → 1.08079 kg CO2-eq, change -0.111258 (-9.3%)\n"
        "\n"
        "Lines that differ, ranked by their effect on Climate change (kg CO2-eq):\n"
        "  -0.111258  tshirt:tshirt line e02 (cotton fabric): amount 0.25 → 0.22 kg"
    )


# --- comments apart ----------------------------------------------------------------------------


def test_a_comment_edit_is_no_cause_and_is_listed_as_a_comment_change(
    toy: Path, background: hw.Background
) -> None:
    rows = csv(toy).read_text().splitlines()
    csv(toy).write_text("\n".join([rows[0] + ",comment", *(r + "," for r in rows[1:])]) + "\n")
    old = study(toy)
    edit(csv(toy), "toy:fabric-production,", "toy:fabric-production,Weighed at the factory")

    text, data = text_and_json(old, study(toy), background)

    assert "Lines that differ" not in text
    assert "Comments changed:\n" in text
    assert (
        '  tshirt:tshirt line e02 (cotton fabric): comment none → "Weighed at the factory"'
    ) in text
    assert data["results"][0]["drivers"] == []  # type: ignore[index]
    [comment] = data["differences"]  # type: ignore[misc]
    assert (comment["kind"], comment["scored"]) == ("comment", False)


def test_a_supplier_swap_with_a_new_comment_is_one_cause_and_one_comment(
    toy: Path, background: hw.Background
) -> None:
    rows = csv(toy).read_text().splitlines()
    csv(toy).write_text("\n".join([rows[0] + ",comment", *(r + ",-" for r in rows[1:])]) + "\n")
    old = study(toy)
    solar(toy, rest=",")
    edit(csv(toy), "tshirt:solar,-", "tshirt:solar,Our own panels")

    text, _ = text_and_json(old, study(toy), background)

    causes = text.split("Lines that differ")[1].split("\n\n")[0]
    assert "supplier electricity production (GLO) → solar electricity (FR)" in causes
    assert "comment" not in causes
    comments = text.split("Comments changed:")[1]
    assert 'tshirt:tshirt line e03 (electricity): comment "-" → "Our own panels"' in comments


def test_a_comment_edit_needs_no_calculation_of_its_own(
    toy: Path, background: hw.Background, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = csv(toy).read_text().splitlines()
    csv(toy).write_text("\n".join([rows[0] + ",comment", *(r + ",a" for r in rows[1:])]) + "\n")
    old = study(toy)
    edit(csv(toy), "toy:fabric-production,a", "toy:fabric-production,b")
    scored: list[object] = []
    real = hw.Database.scores
    monkeypatch.setattr(
        hw.Database, "scores", lambda self, *a, **k: scored.append(self) or real(self, *a, **k)
    )

    diff_databases(old, study(toy), background)

    assert scored == []  # both sides are calculated in full, and nothing else


def test_two_lines_with_one_line_id_are_numbered_in_the_list_and_the_table(
    toy: Path, impacts: hw.Background
) -> None:
    old = study(toy)
    edit(csv(toy), "toy:cotton-fabric,0.25", "toy:cotton-fabric,0.3")
    shutil.copytree(
        TOY / "tshirt-local-electricity" / "tshirt" / "overrides",
        toy / "tshirt" / "tshirt" / "overrides",
    )

    text, _ = text_and_json(old, study(toy), impacts)

    listed = text.split("Lines that differ:\n")[1].split("\n\n")[0]
    assert "  1  tshirt:tshirt line e02 (cotton fabric)" in listed
    assert "  2  override of toy:electricity-production line e02" in listed
    effects = text.split("Effect of each line alone on each score that changed:")[1]
    assert effects.splitlines()[1].split() == ["1", "2"]
