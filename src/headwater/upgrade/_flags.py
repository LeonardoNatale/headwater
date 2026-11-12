"""Links to read again after an upgrade, even though they mapped: RoW, and markets that changed.

RoW is the world without the places that have a dataset of their own. When a new release adds
one, RoW covers less: `cotton fabric production | cotton fabric | RoW` in toy-2 leaves out IN,
which toy-1's did not. A market's suppliers and their shares change with each release too.
"""

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from headwater.model import Exchange, Process, Role
from headwater.upgrade._datasets import Dataset, Datasets

ROW = "RoW"

MOVED_SHARE = 0.01
"""A market is flagged when one of its shares moves by at least this much, 1 point."""


@dataclass(frozen=True)
class Flag:
    """A link to look at. Example: kind `row`, `toy:1:fabric-a` to `toy:2:fabric-b`, added
    `("IN",)`: toy-2 has a dataset of its own for IN, so its RoW no longer covers it."""

    kind: str
    """`row` or `market`."""
    old: Dataset
    new: Dataset
    message: str
    added: tuple[str, ...] = ()
    """For RoW, the places with a dataset of their own that the new release adds. For a market,
    the suppliers it buys from that the old one did not."""
    removed: tuple[str, ...] = ()
    lines: tuple[str, ...] = ()


def row_flag(old: Dataset, new: Dataset, before: Datasets, after: Datasets) -> Flag | None:
    """A flag when either side is in RoW: which places RoW leaves out, before and after."""
    if ROW not in (old.location, new.location):
        return None
    places = _own_places(old, before) if old.location == ROW else set[str]()
    now = _own_places(new, after) if new.location == ROW else set[str]()
    added, removed = tuple(sorted(now - places)), tuple(sorted(places - now))
    if old.location != ROW:
        text = f"now bought in RoW, which leaves out {_places(now)}"
    elif new.location != ROW:
        text = f"no longer bought in RoW, which left out {_places(places)}"
    elif added or removed:
        changes = [f"{after.pin} adds {', '.join(added)}"] if added else []
        changes += [f"no longer {', '.join(removed)}"] if removed else []
        text = "RoW leaves out the places with a dataset of their own: " + ", and ".join(changes)
    else:
        text = f"RoW leaves out the same places as before: {_places(now)}"
    return Flag("row", old, new, text, added, removed)


def market_flag(old: Dataset, new: Dataset, before: Datasets, after: Datasets) -> Flag | None:
    """A flag when a market buys from other suppliers, or a share moved by 1 point or more."""
    if not (old.is_market and new.is_market):
        return None
    shares_before = _shares(before.processes([old.id])[old.id], before.by_id)
    shares_after = _shares(after.processes([new.id])[new.id], after.by_id)
    added = tuple(sorted(shares_after.keys() - shares_before.keys()))
    removed = tuple(sorted(shares_before.keys() - shares_after.keys()))

    def shift(supplier: str) -> float:
        return abs(shares_after.get(supplier, 0.0) - shares_before.get(supplier, 0.0))

    both = shares_before.keys() & shares_after.keys()
    # on a tie, a supplier of both says more: 100% → 50% rather than 0% → 50%
    moved = max(
        sorted(shares_before.keys() | shares_after.keys()),
        key=lambda s: (shift(s), s in both),
        default=None,
    )
    largest = 0.0 if moved is None else shift(moved)
    if not added and not removed and largest < MOVED_SHARE:
        return None
    parts: list[str] = []
    if added:
        parts.append(f"buys from {_count(len(added), 'new supplier')}: {'; '.join(added)}")
    if removed:
        parts.append(f"no longer from {'; '.join(removed)}")
    if moved is not None and largest >= MOVED_SHARE:
        parts.append(
            f"largest share moved: {moved}, {_percent(shares_before.get(moved, 0.0))} → "
            f"{_percent(shares_after.get(moved, 0.0))}"
        )
    return Flag("market", old, new, ", ".join(parts), added, removed)


def _own_places(dataset: Dataset, datasets: Datasets) -> set[str]:
    """The places with a dataset of the same name and product as `dataset`, RoW and GLO aside."""
    return {
        d.location
        for d in datasets.making(dataset.product)
        if d.name == dataset.name and d.location not in (ROW, "GLO")
    }


def _shares(market: Process, by_id: Mapping[str, Dataset]) -> dict[str, float]:
    """What each supplier gives of the market's inputs of the same flow, by dataset. A supplier
    the release does not hold is named by its id."""
    amounts: dict[str, dict[str, float]] = defaultdict(dict)
    for e in _inputs(market.exchanges):
        assert e.supplier_id is not None
        name = str(by_id[e.supplier_id]) if e.supplier_id in by_id else e.supplier_id
        by_flow = amounts[e.flow_id]
        by_flow[name] = by_flow.get(name, 0.0) + e.amount
    shares: dict[str, float] = {}
    for by_flow in amounts.values():
        total = sum(by_flow.values())
        for name, amount in by_flow.items():
            shares[name] = shares.get(name, 0.0) + (amount / total if total else 0.0)
    return shares


def _inputs(lines: Iterable[Exchange]) -> list[Exchange]:
    return [e for e in lines if e.role is Role.input and e.supplier_id is not None]


def _places(places: set[str]) -> str:
    return ", ".join(sorted(places)) if places else "no place"


def _percent(share: float) -> str:
    return f"{100 * share:.3g}%"


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}{'' if n == 1 else 's'}"
