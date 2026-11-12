"""Links a reviewer should look at after an upgrade: RoW, whose meaning moves with the release,
and markets whose suppliers changed. Flags are for reading: they never stop an upgrade."""

from pathlib import Path

from headwater.upgrade import plan


def test_a_link_to_row_is_flagged_with_the_places_row_now_leaves_out(
    studies: Path, to: str, mapping: Path
) -> None:
    found = plan(studies / "tshirt", to=to, mapping=mapping)

    [row] = [f for f in found.flags if f.kind == "row"]
    assert (row.old.id, row.new.id) == ("toy:1:fabric-a", "toy:2:fabric-b")
    assert row.lines == ("tshirt:tshirt line e02",)
    assert row.added == ("IN",)  # toy-2 has a dataset of its own for IN
    assert row.removed == ()
    assert "IN" in row.message
    assert found.complete


def test_a_market_whose_suppliers_changed_is_flagged(studies: Path, to: str, mapping: Path) -> None:
    found = plan(studies / "tshirt", to=to, mapping=mapping)

    [market] = [f for f in found.flags if f.kind == "market"]
    assert (market.old.id, market.new.id) == ("toy:1:market-elec", "toy:2:market-elec")
    assert market.lines == ("tshirt:tshirt line e03",)
    assert market.added == ("electricity production, wind | electricity | GLO",)
    assert market.removed == ()
    assert "100% → 50%" in market.message


def test_a_dataset_that_is_neither_row_nor_a_market_is_not_flagged(
    studies: Path, to: str, mapping: Path
) -> None:
    found = plan(studies / "tshirt", to=to, mapping=mapping)

    flagged = {f.old.id for f in found.flags}
    assert "toy:1:dye" not in flagged
    assert "toy:1:tape" not in flagged
