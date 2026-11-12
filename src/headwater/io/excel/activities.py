"""Activities: each activity of the workbook as the head of a process, with an id of its own.

An activity that cannot be a process is skipped, and every row of it is reported.
"""

import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import replace

from headwater.io.excel.records import RELATIVE, Head, Refusal, number, process_stem
from headwater.io.excel.report import Note, Outcome, Status
from headwater.io.excel.units import resolve_unit
from headwater.io.excel.workbook import Activity, BrightwayWorkbook
from headwater.model import Flow, FlowKind, Units


class ActivityReader:
    def __init__(self, book: BrightwayWorkbook, units: Units, study: str) -> None:
        self.book = book
        self.units = units
        self.study = study
        self.notes: list[Note] = []
        self.by_activity: dict[str, list[Outcome]] = defaultdict(list)
        """The outcomes of the rows of each activity, by where it was read. Here: the rows of an
        activity that was skipped."""
        self.skipped: list[Outcome] = []
        self.skipped_names: dict[str, str] = {}
        self.skipped_sources: dict[str, str] = {}
        """Why each activity that was skipped was, by where it was read."""
        self.flows: dict[str, Flow] = {}
        """The flow each process makes, by id."""
        self.heads: list[Head] = []

    def read(self) -> None:
        """Each activity as a process, with an id of its own. An activity that cannot be one is
        skipped, and every row of it is reported."""
        found = [(a, self._head(a)) for a in self.book.activities]
        valid = [h for _, h in found if isinstance(h, Head)]
        for head, pid in zip(valid, self._ids(valid), strict=True):
            self.heads.append(replace(head, id=pid))
            self.flows[pid] = Flow(id=pid, name=head.label, kind=head.kind, unit=head.unit)
        for activity, head in found:
            if isinstance(head, Refusal):
                self._skip(activity, head)

    def _head(self, activity: Activity) -> Head | Refusal:
        production = [e for e in activity.exchanges if e.type == "production"]
        if len(production) > 1:
            return Refusal(
                Status.unmapped,
                "co-products",
                f"has {len(production)} production exchanges, and a headwater process makes one "
                "product. Split the activity in one per product, or allocate its inputs, then "
                "import again.",
            )
        row = production[0] if production else None
        product = (
            activity.text("reference product")
            or (row.reference_product if row else None)
            or activity.name
        )
        location = activity.text("location") or (row.location if row else None)
        if location is None:
            return Refusal(
                Status.unmapped,
                "no-location",
                "has no location. Write one in the activity's `location` row.",
            )
        unit_text = activity.text("unit") or (row.unit if row else None)
        unit = resolve_unit(unit_text, self.units) if unit_text else None
        if unit is None:
            why = (
                f"unit {unit_text!r} is not a unit headwater knows" if unit_text else "has no unit"
            )
            return Refusal(
                Status.unmapped,
                "unknown-unit",
                f"{why}. Write the unit the product is counted in.",
            )
        cell = row.amount if row is not None else None
        if cell is None:
            cell = activity.metadata.get("production amount")
        amount, problem = number(cell)
        if amount is None:
            return Refusal(
                Status.unmapped,
                problem or "no-amount",
                "has no usable production amount, in its production exchange or in the "
                "`production amount` row.",
            )
        stated, _ = number(activity.metadata.get("production amount"))
        if (
            row is not None
            and stated is not None
            and not math.isclose(stated, amount, rel_tol=RELATIVE)
        ):
            self.notes.append(
                Note(
                    f"activity {activity.name!r}: the `production amount` row says {stated:g}, "
                    f"but its production exchange says {amount:g}. The exchange is used.",
                    activity.source,
                )
            )
        if row is None:
            self.notes.append(
                Note(
                    f"activity {activity.name!r} has no production exchange, so its reference "
                    f"line is made from its `production amount` row ({amount:g} {unit}).",
                    activity.source,
                )
            )
        if amount == 0:
            return Refusal(
                Status.unmapped, "no-amount", "has a production amount of 0, so it makes nothing."
            )
        # A treatment makes a negative amount of a waste, in ecoinvent's convention.
        kind = FlowKind.waste if amount < 0 else FlowKind.product
        return Head(activity, "", product, location, unit, kind, abs(amount), row)

    def _skip(self, activity: Activity, refusal: Refusal) -> None:
        self.skipped_names[activity.name] = refusal.message
        self.skipped_sources[activity.source] = refusal.message
        self.skipped.append(
            Outcome(
                source=activity.source,
                activity=activity.name,
                line="(the whole activity)",
                status=refusal.status,
                code=refusal.code,
                message=f"activity {activity.name!r} was not converted: it {refusal.message}",
            )
        )
        for row in activity.exchanges:
            self.by_activity[activity.source].append(
                Outcome(
                    source=row.source,
                    activity=activity.name,
                    line=row.description,
                    status=Status.unmapped,
                    code="activity-not-converted",
                    message=f"its activity {activity.name!r} was not converted: it "
                    f"{refusal.message}",
                )
            )

    def _ids(self, heads: Sequence[Head]) -> list[str]:
        """An id for each activity, `<database>:<name>-<location>`, from the activity alone (see
        `process_stem`). Activities whose names and locations give one id, such as the steps of
        a process all named `assembly`, take it from their reference products instead, every one
        of them, so the ids say which step is which: `upper-glo`, `sole-glo`. Only an id still
        taken by an earlier activity gets a number, `-2`, in the order of the workbook: `gear`
        and `Gear` in RER that both make `gear`."""
        bases = [process_stem(h.activity.name, h.location) for h in heads]
        repeated = {b for b in bases if bases.count(b) > 1}
        # an activity whose name is its own keeps its id, whatever comes before it
        reserved = {b for b in bases if b not in repeated}
        taken: set[str] = set()
        ids: list[str] = []
        for head, base in zip(heads, bases, strict=True):
            by_product = base in repeated
            if by_product:
                base = process_stem(head.product, head.location)
            stem, n = base, 1
            while stem in taken or (by_product and stem in reserved):
                n += 1
                stem = f"{base}-{n}"
            if by_product:
                self.notes.append(
                    Note(
                        f"activity {head.activity.name!r} is process {self.study}:{stem}, from "
                        "its reference product, because another activity has the same name and "
                        "location, give or take case and punctuation",
                        head.activity.source,
                    )
                )
            elif stem != base:
                self.notes.append(
                    Note(
                        f"activity {head.activity.name!r} is process {self.study}:{stem}, "
                        "because an activity before it has the id of its name and location",
                        head.activity.source,
                    )
                )
            taken.add(stem)
            ids.append(f"{self.study}:{stem}")
        return ids
