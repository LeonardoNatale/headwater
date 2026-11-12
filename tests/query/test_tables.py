"""Contributions and `where` as tables, the location of each process in a contribution tree, and
Brightway's split of a score into direct and upstream."""

import sys
from pathlib import Path

import pytest

import headwater as hw

TIGHT = 1e-12
COLUMNS = [
    "depth",
    "process_id",
    "name",
    "location",
    "amount",
    "unit",
    "score",
    "share",
    "direct",
    "rest",
]


@pytest.fixture
def grid_in_rer(toy: Path, edit, load_toy) -> hw.Background:
    """The toy with electricity made in RER."""
    edit(toy / "background" / "processes" / "electricity-production.yaml", "GLO", "RER")
    return load_toy(toy)


@pytest.fixture
def loop(toy: Path, edit, load_toy) -> hw.Background:
    """The toy with a loop through another process: each kWh of electricity buys 0.01 kg of
    cotton fabric, which buys 3 kWh of electricity."""
    path = toy / "background" / "processes" / "electricity-production.csv"
    text = path.read_text()
    path.write_text(text + "e04,input,toy:cotton-fabric,0.01,kg,toy:fabric-production\n")
    return load_toy(toy)


def test_each_process_of_a_tree_has_its_location(calc, grid_in_rer: hw.Background) -> None:
    root = calc("tshirt").contributions()
    assert [root.location, *(c.location for c in root.children)] == ["GLO", "GLO", "GLO"]

    tree = grid_in_rer.contributions("toy:fabric-production")
    assert (tree.location, tree.children[0].location) == ("GLO", "RER")


def test_the_contribution_table_is_the_tree_one_row_per_process(calc) -> None:
    root = calc("tshirt").contributions()

    table = root.to_dataframe()

    assert list(table.columns) == COLUMNS
    assert table["process_id"].tolist() == [
        "tshirt:tshirt",
        "toy:fabric-production",
        "toy:electricity-production",
        "toy:electricity-production",
    ]
    assert table["depth"].tolist() == [0, 1, 2, 1]
    assert table["location"].tolist() == ["GLO"] * 4
    assert table["score"].tolist() == pytest.approx([1.19205, 0.92715, 0.39735, 0.2649])
    assert table["share"].tolist() == pytest.approx([1.0, 0.77778, 0.33333, 0.22222], abs=1e-5)


def test_the_inputs_alone_are_the_rows_at_depth_1(calc) -> None:
    """The example of `Contribution.to_dataframe`'s docstring."""
    df = calc("tshirt").contributions().to_dataframe()

    inputs = df[df.depth == 1]

    assert df["depth"].tolist() == [0, 1, 2, 1]
    assert inputs["process_id"].tolist() == ["toy:fabric-production", "toy:electricity-production"]
    assert inputs["score"].tolist() == pytest.approx([0.92715, 0.2649], rel=1e-9)
    assert inputs["score"].sum() == pytest.approx(df["score"][0], rel=1e-9)


@pytest.mark.parametrize(("min_share", "max_depth"), [(0.03, 3), (0.5, 3), (0.0, 1), (0.5, 1)])
def test_the_table_adds_up_as_the_tree_does(calc, min_share: float, max_depth: int) -> None:
    root = calc("tshirt-waste").contributions(min_share=min_share, max_depth=max_depth)
    rows = root.to_dataframe().to_dict("records")

    for k, row in enumerate(rows):
        below = []
        for later in rows[k + 1 :]:
            if later["depth"] <= row["depth"]:
                break
            if later["depth"] == row["depth"] + 1:
                below.append(later["score"])
        added = row["direct"] + sum(below) + row["rest"]
        assert added == pytest.approx(row["score"], rel=TIGHT, abs=TIGHT)


def test_the_where_table_has_one_row_per_process_and_adds_up(calc) -> None:
    where = calc("tshirt").where()

    table = where.to_dataframe()

    assert list(table.columns) == [
        "process_id",
        "name",
        "location",
        "amount",
        "unit",
        "score",
        "share",
    ]
    assert table["process_id"].tolist() == [
        "toy:electricity-production",
        "toy:fabric-production",
    ]
    assert table["score"].sum() + where.rest == pytest.approx(where.score, rel=TIGHT)


def test_without_pandas_the_table_names_the_extra(calc, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "pandas", None)
    result = calc("tshirt")

    for call in (result.contributions().to_dataframe, result.where().to_dataframe):
        with pytest.raises(ImportError, match=r"pip install 'headwater\[pandas\]'"):
            call()


# --- Brightway's direct and upstream ------------------------------------------------------------


def test_direct_is_the_process_own_row_of_where_loops_included(loop: hw.Background) -> None:
    fabric = "toy:fabric-production"

    direct, upstream = loop.direct_and_upstream(fabric)

    rows = loop.where(fabric, top=None).processes
    (own,) = [e for e in rows if e.process_id == fabric]
    assert direct == pytest.approx(own.score, rel=TIGHT)
    assert direct + upstream == pytest.approx(loop.score(fabric), rel=TIGHT)
    # upstream is what every other process emits for the kg
    others = sum(e.score for e in rows if e.process_id != fabric)
    assert upstream == pytest.approx(others, rel=1e-9)
    # The fabric runs again for the 0.03 kg its own electricity buys, so Brightway's direct is
    # more than the direct emissions of the 1 kg the tree starts from.
    assert direct > loop.contributions(fabric).direct * (1 + 1e-6)


def test_a_process_that_buys_its_own_product_has_one_direct(
    toy: Path, load_toy, background: hw.Background
) -> None:
    # Each kWh of electricity uses 0.1 kWh of its own: the diagonal holds it, so the tree and
    # Brightway count the same runs.
    path = toy / "background" / "processes" / "electricity-production.csv"
    text = path.read_text()
    path.write_text(text + "e04,own_use,toy:electricity,0.1,kWh,toy:electricity-production\n")
    looped = load_toy(toy)
    grid = "toy:electricity-production"

    direct, upstream = looped.direct_and_upstream(grid)

    (own,) = [e for e in looped.where(grid, top=None).processes if e.process_id == grid]
    assert direct == pytest.approx(own.score, rel=TIGHT)
    assert direct == pytest.approx(looped.contributions(grid).direct, rel=TIGHT)
    assert direct + upstream == pytest.approx(looped.score(grid), rel=TIGHT)
    assert looped.score(grid) > background.score(grid)  # the own use runs it 1/0.9 times


@pytest.mark.parametrize(
    "process_id",
    ["toy:fabric-production", "toy:electricity-production", "toy:incineration-textile-waste"],
)
def test_with_no_loop_direct_is_the_tree_direct(background: hw.Background, process_id: str) -> None:
    direct, upstream = background.direct_and_upstream(process_id)

    assert direct == pytest.approx(background.contributions(process_id).direct, rel=TIGHT)
    assert direct + upstream == pytest.approx(background.score(process_id), rel=TIGHT)


def test_a_result_splits_its_score_the_same_way(calc) -> None:
    # The T-shirt emits nothing itself: all of its 1.19205 is upstream.
    assert calc("tshirt").direct_and_upstream() == pytest.approx((0.0, 1.19205), abs=TIGHT)
