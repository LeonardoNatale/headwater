"""The rules that find the successor of a dataset in a new release, in the order they decide.

Example: `cotton fabric production | cotton fabric | RoW` is `toy:1:fabric-a` in toy-1. No mapping
row names it, toy-2 has no `toy:2:fabric-a`, and no correspondence row names it, so the rule that
decides is the same dataset: toy-2's process with the same name, product and location,
`toy:2:fabric-b`.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum

from headwater.link import Decision, Supplier, SupplierMapping, SupplierRow
from headwater.model import InvalidDataError, Problem
from headwater.upgrade._datasets import Dataset, Datasets

_SUGGESTED = 5
"""Suggestions listed for a supplier no rule maps."""

type Correspondence = Mapping[str, Sequence[str]]
"""ecoinvent's correspondence between two versions, by id: each old process with the ids of its
successors. One successor maps it; several are a split; none, a dataset that was deleted."""


class Rule(StrEnum):
    """What decided a successor, in the order the rules are tried."""

    mapping = "mapping"
    """A row of the upgrade mapping: a decision somebody made, with who and when."""
    same_id = "same-id"
    """The same ids, with the new release's version: `toy:1:elec` is `toy:2:elec`."""
    correspondence = "correspondence"
    """A row of ecoinvent's correspondence files."""
    same_dataset = "same-dataset"
    """The one process with the same name, product and location."""
    unmapped = "unmapped"


@dataclass(frozen=True)
class Match:
    """One supplier of a study and its successor, or why it has none.

    Example: `toy:1:tape`, unmapped with code `not-in-release`, suggestions `toy:2:tape-rer`,
    bought at `tshirt:tshirt line e06`.
    """

    old: Dataset
    rule: Rule
    new: Dataset | None = None
    code: str | None = None
    """Why it is unmapped: `not-in-release`, `split`, `deleted` or `not-a-process`."""
    reason: str | None = None
    decision: Decision | None = None
    """Who decided, when, and on which row, for a mapping row."""
    automatic: Dataset | None = None
    """What the rules would have given, when a mapping row decided otherwise."""
    suggestions: tuple[Dataset, ...] = ()
    """Processes a person might mean, for an unmapped supplier. Never applied."""
    lines: tuple[str, ...] = ()
    """The places of the studies that name it: `tshirt:tshirt line e06`,
    `override of toy:1:elec line e02`."""

    @property
    def row(self) -> str:
        """A row of the upgrade mapping to fill in: `toy:1:tape,,,,,<supplier>,,<who>,<when>,`."""
        return f"{self.old.id},,,,,<supplier>,,<who>,<when>,"


def reason_rows(mapping: SupplierMapping) -> None:
    """Refuse a row that gives a reason instead of a supplier. In a release it makes a data gap,
    which a study cannot hold."""
    rows = [*mapping.by_id.values(), *mapping.by_name.values()]
    problems = [
        Problem(
            "reason-row",
            f"mapping {row.decision.source}",
            f"gives a reason ({row.reason}) and no supplier. A study holds no data gap: remove "
            "the lines that buy from it, or cut them off in cut_offs.csv, then upgrade.",
        )
        for row in rows
        if row.supplier is None
    ]
    if problems:
        raise InvalidDataError(problems)


class Successors:
    """The rules between two releases: `match` finds the successor of one dataset.

    `mapping` and `correspondence` are optional: without them only the same id and the same
    dataset decide, which is what a diff across a release change needs.
    """

    def __init__(
        self,
        old: Datasets,
        new: Datasets,
        *,
        mapping: SupplierMapping | None = None,
        correspondence: Correspondence | None = None,
    ) -> None:
        self.old = old
        self.new = new
        self._mapping = mapping or SupplierMapping()
        self._correspondence = correspondence or {}
        self.used: set[SupplierRow] = set()
        """The mapping rows that decided something."""

    def match(self, old: Dataset, lines: tuple[str, ...] = ()) -> Match:
        row = self._row(old)
        automatic = self.automatic(old)
        if row is not None:
            self.used.add(row)
            assert row.supplier is not None  # reason rows are refused first
            new = self.new.by_id.get(row.supplier)
            if new is None:
                return Match(
                    old,
                    Rule.unmapped,
                    code="not-a-process",
                    reason=f"the mapping row at {row.decision.source} names {row.supplier}, "
                    f"which is not a process of {self.new.pin}",
                    decision=row.decision,
                    suggestions=self.suggestions(old),
                    lines=lines,
                )
            other = automatic.new if automatic.new is not None and automatic.new != new else None
            return Match(
                old, Rule.mapping, new, decision=row.decision, automatic=other, lines=lines
            )
        if automatic.rule is Rule.unmapped and not automatic.suggestions:
            automatic = replace(automatic, suggestions=self.suggestions(old))
        return replace(automatic, lines=lines)

    def automatic(self, old: Dataset) -> Match:
        """The successor by the rules that need no person: the same id, a correspondence row,
        the same dataset. Unmapped, with its reason, when none decides."""
        if old.id.startswith(self.old.prefix):
            same = self.new.by_id.get(self.new.prefix + old.id.removeprefix(self.old.prefix))
            if same is not None:
                return Match(old, Rule.same_id, same)
        successors = self._correspondence.get(old.id)
        if successors is not None:
            found = [self.new.by_id[i] for i in successors if i in self.new.by_id]
            if len(successors) == 1 and found:
                return Match(old, Rule.correspondence, found[0])
            if len(successors) > 1:
                return Match(
                    old,
                    Rule.unmapped,
                    code="split",
                    reason=f"the correspondence splits it into {len(successors)} processes",
                    suggestions=tuple(found),
                )
            if not successors:
                return Match(
                    old,
                    Rule.unmapped,
                    code="deleted",
                    reason=f"the correspondence says it is not in {self.new.pin}",
                )
        same = self.new.by_key.get(old.key)
        if same is not None:
            return Match(old, Rule.same_dataset, same)
        return Match(
            old,
            Rule.unmapped,
            code="not-in-release",
            reason=f"no process of {self.new.pin} is {old}, with the same product id, and no "
            "rule maps it",
        )

    def suggestions(self, old: Dataset) -> tuple[Dataset, ...]:
        """What a person might mean: the processes that make the same product, the nearest
        first, then what `find` gives for its name and for its product."""
        same = sorted(
            self.new.making(old.product),
            key=lambda d: (d.location != old.location, d.name != old.name, d.id),
        )
        found: dict[str, Dataset] = {d.id: d for d in same[:_SUGGESTED]}
        for text in (old.name, old.product_name):
            if len(found) >= _SUGGESTED:
                break
            for match in self.new.search.find(text, limit=_SUGGESTED):
                found.setdefault(match.process_id, self.new.by_id[match.process_id])
        return tuple(list(found.values())[:_SUGGESTED])

    def unused(self) -> tuple[Problem, ...]:
        """A warning for each mapping row that decided nothing."""
        rows = [*self._mapping.by_id.values(), *self._mapping.by_name.values()]
        return tuple(
            Problem(
                "row-not-needed",
                f"mapping {row.decision.source}",
                f"{row.key} is not a supplier of the studies upgraded",
            )
            for row in sorted(rows, key=lambda r: r.decision.source)
            if row not in self.used
        )

    def _row(self, old: Dataset) -> SupplierRow | None:
        """The mapping row for `old`: by its id, else by the fields of its key that a row gives.
        Two rows that both fit are refused."""
        row = self._mapping.by_id.get(old.id)
        if row is not None:
            return row
        supplier = Supplier(old.id, old.name, old.product_name, old.location, old.unit)
        fitting = [r for key, r in self._mapping.by_name.items() if supplier.fits(key)]
        if len(fitting) > 1:
            sources = ", ".join(r.decision.source for r in fitting)
            raise InvalidDataError(
                [Problem("ambiguous-rows", f"supplier {old.label}", f"rows {sources} all fit it")]
            )
        return fitting[0] if fitting else None
