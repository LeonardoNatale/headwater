"""Every product of a database is scored: a change to any product shows up.

Toy numbers (docs/calculation.md): electricity 0.5298 kg CO2-eq per kWh. The database makes, besides
the T-shirt, a bag from 2 kWh of electricity (1.0596) and a tote from 1 kWh (0.5298). The T-shirt
buys neither, so a change to the bag leaves its 1.19205 as it is.
"""

from dataclasses import replace
from pathlib import Path

import pytest

import headwater as hw
from headwater.diff import DiffError, Source, diff_databases, render_json, render_text
from headwater.study import DatabaseFolder, read_database

TOY = Path(__file__).resolve().parent.parent / "fixtures" / "toy"
REL = 1e-9
OLD, NEW = Source("main", "a" * 40), Source("branch", "b" * 40)


def edit(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    assert old in text, f"{old!r} not found in {path}"
    path.write_text(text.replace(old, new, 1))


def study(root: Path) -> DatabaseFolder:
    return read_database(root / "tshirt" / "tshirt")


def folder(root: Path) -> Path:
    return root / "tshirt" / "tshirt"


def add_product(root: Path, name: str, kwh: float) -> None:
    """A product the database makes from `kwh` of grid electricity, which nothing in it buys."""
    processes = folder(root) / "processes"
    (processes / f"{name}.yaml").write_text(
        f"name: {name}\nlocation: GLO\nkind: ordinary_transforming_activity\n"
    )
    (processes / f"{name}.csv").write_text(
        "line,role,flow,amount,unit,supplier\n"
        f"e01,reference,tshirt:{name},1,kg,\n"
        f"e02,input,toy:electricity,{kwh},kWh,toy:electricity-production\n"
    )
    with (folder(root) / "flows.csv").open("a") as handle:
        handle.write(f"tshirt:{name},{name},product,kg,,\n")


def add_twin(root: Path) -> None:
    """A second process, `bag-b`, that makes the flow of the bag."""
    add_product(root, "bag-b", 3)
    flows = folder(root) / "flows.csv"
    flows.write_text(flows.read_text().replace("tshirt:bag-b,bag-b,product,kg,,\n", ""))
    edit(folder(root) / "processes" / "bag-b.csv", "tshirt:bag-b", "tshirt:bag")


@pytest.fixture
def impacts(toy: Path) -> hw.Background:
    """The toy background with the three indicators of toy-impacts, which the T-shirt names."""
    edit(folder(toy) / "database.yaml", "methods: [toy-climate]", "methods: [toy-impacts]")
    return hw.load(TOY / "background", method=TOY / "methods" / "toy-impacts", namespace="toy")


@pytest.fixture
def made(toy: Path) -> Path:
    """The toy with a bag and a tote added to the T-shirt database. Products come in the order of
    the processes: bag, tote, tshirt."""
    add_product(toy, "bag", 2)
    add_product(toy, "tote", 1)
    return toy


def text_and_json(
    old: DatabaseFolder, new: DatabaseFolder, background: hw.Background, **options: object
) -> tuple[str, dict[str, object]]:
    diff = diff_databases(old, new, background, **options)  # type: ignore[arg-type]
    text = render_text(diff, path="tshirt/tshirt", old=OLD, new=NEW)
    return text, render_json(diff, path="tshirt/tshirt", old=OLD, new=NEW)


# --- a change to another product ---------------------------------------------------------------


def test_a_change_to_one_product_shows_its_block_and_the_others_as_no_change(
    made: Path, background: hw.Background
) -> None:
    old = study(made)
    edit(folder(made) / "processes" / "bag.csv", "toy:electricity,2,", "toy:electricity,3,")

    text, _ = text_and_json(old, study(made), background, products="all")

    *blocks, effects = text.split("\n\n")[1:]
    assert blocks == [
        "bag (tshirt:bag)\n  Climate change: 1.0596 → 1.5894 kg CO2-eq, change +0.5298 (+50.0%)",
        "2 other products, no change",
        "Lines that differ:\n  e02  tshirt:bag line e02 (electricity): amount 2 → 3 kWh",
    ]
    title, header, row = effects.splitlines()
    assert title == "Effect of each line alone on each score of bag (tshirt:bag) that changed:"
    assert header.split() == ["e02"]
    assert row.split() == ["Climate", "change", "(kg", "CO2-eq):", "+0.5298"]


def test_the_json_lists_every_product_in_the_order_of_the_processes(
    made: Path, background: hw.Background
) -> None:
    old = study(made)
    edit(folder(made) / "processes" / "bag.csv", "toy:electricity,2,", "toy:electricity,3,")

    _, data = text_and_json(old, study(made), background, products="all")

    assert data["products"] == [
        {"id": f"tshirt:{stem}", "name": name, "process": name, "status": "both", "amount": None}
        for stem, name in (("bag", "bag"), ("tote", "tote"), ("tshirt", "T-shirt"))
    ]
    results = {r["product"]["id"]: r for r in data["results"]}  # type: ignore[attr-defined]
    assert [r["changed"] for r in results.values()] == [True, False, False]
    bag = results["tshirt:bag"]
    assert bag["old"] == pytest.approx(1.0596, rel=REL)
    assert bag["new"] == pytest.approx(1.5894, rel=REL)
    assert [d["contribution"] for d in bag["drivers"]] == [pytest.approx(0.5298, rel=REL)]


def test_a_change_to_one_product_leaves_the_others_as_one_line(
    made: Path, background: hw.Background
) -> None:
    old = study(made)
    edit(folder(made) / "processes" / "tshirt.csv", "toy:electricity,0.5", "toy:electricity,0.6")

    text, _ = text_and_json(old, study(made), background, products="all")

    blocks = text.split("\n\n")
    assert blocks[1].startswith("T-shirt (tshirt:tshirt)\n  Climate change: 1.19205 → 1.24503")
    assert blocks[2] == "2 other products, no change"
    assert "bag (" not in text and "tote (" not in text


def test_a_change_that_moves_no_product_counts_them_all(made: Path, impacts: hw.Background) -> None:
    old = study(made)
    (folder(made) / "database.yaml").write_text(
        (folder(made) / "database.yaml").read_text() + "description: Carbon footprint\n"
    )

    text, _ = text_and_json(old, study(made), impacts, products="all")

    assert text.split("\n\n")[1] == "3 other products, no change"
    assert "Climate change:" not in text


def test_products_come_in_the_order_of_the_database(made: Path, background: hw.Background) -> None:
    old = study(made)
    edit(folder(made) / "processes" / "tote.csv", "toy:electricity,1,", "toy:electricity,2,")
    edit(folder(made) / "processes" / "bag.csv", "toy:electricity,2,", "toy:electricity,3,")

    text, _ = text_and_json(old, study(made), background, products="all")

    heads = [line for line in text.splitlines() if line.endswith(")") and ":" in line]
    assert [h.split(" (")[0] for h in heads if h.startswith(("T-shirt", "bag", "tote"))] == [
        "bag",
        "tote",
    ]


def test_a_line_that_moves_no_product_is_still_listed(
    made: Path, background: hw.Background
) -> None:
    old = study(made)
    edit(folder(made) / "processes" / "bag.csv", "toy:electricity,2,", "toy:electricity,3,")
    diff = diff_databases(old, study(made), background, products="all")
    # the same lines and effects of 0, as when a line buys from a supplier that scores 0
    still = replace(diff, rows=tuple(replace(r, new=r.old) for r in diff.rows))

    text = render_text(still, path="tshirt/tshirt", old=OLD, new=NEW)

    assert "3 other products, no change" in text
    assert "Lines that differ:\n  e02  tshirt:bag line e02" in text
    assert "Effect of each line alone" not in text


# --- a product on one side only ----------------------------------------------------------------


def test_a_product_the_new_study_adds_is_listed_as_new(
    toy: Path, background: hw.Background
) -> None:
    old = study(toy)
    add_product(toy, "bag", 2)

    text, data = text_and_json(old, study(toy), background, products="all")

    assert (
        "bag (tshirt:bag), new product\n  Climate change: 1.0596 kg CO2-eq, only in branch" in text
    )
    assert "1 other product, no change" in text
    assert data["products"][1] == {  # type: ignore[index]
        "id": "tshirt:bag",
        "name": "bag",
        "process": "bag",
        "status": "new",
        "amount": None,
    }


def test_a_product_the_new_study_removes_is_listed_as_removed(
    toy: Path, background: hw.Background
) -> None:
    add_product(toy, "bag", 2)
    old = study(toy)
    (folder(toy) / "processes" / "bag.yaml").unlink()
    (folder(toy) / "processes" / "bag.csv").unlink()

    text, data = text_and_json(old, study(toy), background, products="all")

    assert (
        "bag (tshirt:bag), removed product\n  Climate change: 1.0596 kg CO2-eq, only in main"
        in text
    )
    assert data["products"][0]["status"] == "removed"  # type: ignore[index]


# --- a flow that two processes make -----------------------------------------------------------


def test_two_processes_that_make_one_flow_are_two_products(
    made: Path, background: hw.Background
) -> None:
    """Every process is a product (D46), so two that make the same flow are scored apart."""
    add_twin(made)
    old = study(made)
    edit(folder(made) / "processes" / "bag-b.csv", "toy:electricity,3,", "toy:electricity,4,")

    text, data = text_and_json(old, study(made), background, products="all")

    assert "bag-b | bag (tshirt:bag-b)\n  Climate change: 1.5894 → 2.1192" in text
    assert "3 other products, no change" in text
    assert [p["id"] for p in data["products"]] == [  # type: ignore[attr-defined]
        "tshirt:bag",
        "tshirt:bag-b",
        "tshirt:tote",
        "tshirt:tshirt",
    ]
    assert "not_scored" not in data


def test_asking_for_a_flow_names_the_process_that_makes_it(
    made: Path, background: hw.Background
) -> None:
    add_product(made, "bag-sewing", 2)
    edit(folder(made) / "processes" / "bag-sewing.csv", "tshirt:bag-sewing", "tshirt:sewn-bag")
    old = study(made)

    with pytest.raises(DiffError, match="tshirt:sewn-bag is the flow of the process "):
        diff_databases(old, study(made), background, products=["tshirt:sewn-bag"])


# --- which products ----------------------------------------------------------------------------


def test_products_given_limit_what_is_scored(made: Path, background: hw.Background) -> None:
    old = study(made)
    edit(folder(made) / "processes" / "bag.csv", "toy:electricity,2,", "toy:electricity,3,")
    edit(folder(made) / "processes" / "tote.csv", "toy:electricity,1,", "toy:electricity,2,")

    text, data = text_and_json(old, study(made), background, products=["tshirt:tote"])

    assert [p["id"] for p in data["products"]] == ["tshirt:tote"]  # type: ignore[attr-defined]
    assert "tote (tshirt:tote)" in text and "bag (" not in text and "T-shirt" not in text


def test_a_product_the_database_does_not_make_is_an_error_that_lists_the_ones_it_does(
    made: Path, background: hw.Background
) -> None:
    old = study(made)

    with pytest.raises(DiffError) as error:
        diff_databases(old, study(made), background, products=["tshirt:bagg"])

    message = str(error.value)
    assert "tshirt:bagg" in message
    assert "tshirt:bag, tshirt:tote, tshirt:tshirt" in message


# --- one product -------------------------------------------------------------------------------


def test_a_one_product_database_reads_as_before_whatever_the_products_asked_for(
    toy: Path, impacts: hw.Background
) -> None:
    old = study(toy)
    edit(toy / "tshirt" / "tshirt" / "processes" / "tshirt.csv", "0.25,kg", "0.3,kg")

    before, _ = text_and_json(old, study(toy), impacts)
    every, _ = text_and_json(old, study(toy), impacts, products="all")
    named, _ = text_and_json(old, study(toy), impacts, products=["tshirt:tshirt"])

    assert before == every == named
    assert "other product" not in before and "no change" not in before
