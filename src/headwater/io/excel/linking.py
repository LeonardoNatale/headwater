"""Linking: who supplies a line, and which flow of nature it is.

Linking follows the order in which Brightway users run `match_database`, and never guesses:

1. A line with a decision in the mapping file takes it.
2. A line whose `database` is this workbook's links to one of its activities, by exact name,
   reference product and location. A line of a Brightway project holds the key of the activity it
   buys from, and links to that activity, even when another one has the same name, reference
   product and location. One whose database is the background (the release) links to
   one of the release's processes the same way. One with no database is looked up in both, and
   is refused if both match.
3. A line whose database is another foreground database ("Textile Chemicals") needs a decision in
   the mapping file: the study folder it comes from, next to the study written
   (`textile-chemicals`). When that folder holds a study, the line links to one of its processes
   by exact name, reference product and location. When it does not yet, the line waits for it.
4. A biosphere line links to the elementary flow of the release with its name and categories.
   Brightway drops the subcompartment `unspecified`, so `water` also means `water/unspecified`.
"""

from collections.abc import Mapping, Sequence

from headwater.io.excel.index import ElementaryFlow, ReleaseIndex, Supplier
from headwater.io.excel.mapping import DatabaseDecision, ImportMapping
from headwater.io.excel.records import Head, Refusal, Target
from headwater.io.excel.report import Status
from headwater.io.excel.workbook import BrightwayWorkbook, ExchangeRow

_NO_SUPPLIERS: tuple[Supplier, ...] = ()


class Linker:
    def __init__(
        self,
        book: BrightwayWorkbook,
        index: ReleaseIndex,
        mapping: ImportMapping,
        studies: Mapping[str, Sequence[Supplier]],
        heads: list[Head],
        skipped_names: Mapping[str, str],
        skipped_sources: Mapping[str, str],
    ) -> None:
        self.book = book
        self.index = index
        self.mapping = mapping
        self.studies = studies
        self.heads = heads
        self.skipped_names = skipped_names
        self.skipped_sources = skipped_sources
        self._by_source: dict[str, Head] | None = None
        self.used_mapping: set[str] = set()
        """The rows of the mapping file that some line needed, by where they are."""
        self.uses: dict[str, None] = {}
        """The studies a line was linked to, in order."""

    def database_kind(self, name: str | None) -> tuple[str, DatabaseDecision | None]:
        """What a line's database column means: `own` (this workbook), `release` (the
        background), `blank`, `study` (another foreground database the mapping file pins) or
        `foreign` (another one, not decided)."""
        if name is None:
            return "blank", None
        decided = self.mapping.database(name)
        if decided is not None:
            return decided.kind, decided
        if name.casefold() == self.book.database.casefold():
            return "own", None
        info = self.index.release.info
        folded = name.casefold()
        for prefix in (info.source.casefold(), self.index.release.name.casefold()):
            # `ecoinvent-3.12` starts with `ecoinvent`, but `toyota` does not start with `toy`.
            if folded.startswith(prefix) and not folded[len(prefix) :][:1].isalnum():
                return "release", None
        return "foreign", None

    def target(self, head: Head, row: ExchangeRow) -> Target | Refusal:
        name, product, location = row.name, row.reference_product, row.location
        decided = self.mapping.technosphere_row(name, product, location)
        if decided is not None:
            self.used_mapping.add(decided.source)
            supplier = self.index.supplier(decided.target)
            if supplier is None:
                return Refusal(
                    Status.unmapped,
                    "mapping-target-missing",
                    f"{decided.source} sends it to {decided.target}, which is not a process of "
                    f"release {self.index.release.name}",
                )
            return self._supplier(supplier, "mapping")
        kind, decision = self.database_kind(row.database)
        if decision is not None:
            self.used_mapping.add(decision.row.source)
        if kind == "own" and row.key is not None:
            return self._by_key(row, row.key)
        if kind == "database":
            assert decision is not None and decision.study is not None
            return self._in_study(row, decision)
        if kind == "foreign":
            info = self.index.release.info
            return Refusal(
                Status.unmapped,
                "database-unresolved",
                f"buys from database {row.database!r}, which is not this workbook's "
                f"({self.book.database}) and not the background ({info.source}). If it is a "
                "database of your own, name its folder, next to this one, in the mapping file: "
                f"`database,{row.database},,,,<database folder>,<who>,<when>`.",
            )
        mine = self._own(name, product, location) if kind in ("own", "blank") else []
        theirs = (
            self.index.suppliers(name, product, location)
            if kind in ("release", "blank")
            else _NO_SUPPLIERS
        )
        missing = [
            label
            for label, value in (("reference product", product), ("location", location))
            if value is None
        ]
        note = (
            f"linked without the workbook's {' and '.join(missing)}: the only process that fits"
            if missing
            else None
        )
        if len(mine) + len(theirs) == 1:
            if mine:
                return self._own_target(
                    mine[0], "workbook-partial" if missing else "workbook", note
                )
            return self._supplier(theirs[0], "release-partial" if missing else "release", note)
        return self._unmatched(row, kind, mine, theirs)

    def _by_key(self, row: ExchangeRow, key: str) -> Target | Refusal:
        """A line of a Brightway project to an activity of its own database: linked to the
        activity whose key it holds, never to another one with the same name."""
        if self._by_source is None:
            self._by_source = {h.activity.source: h for h in self.heads}
        head = self._by_source.get(key)
        if head is not None:
            return self._own_target(head, "key")
        why = self.skipped_sources.get(key)
        if why is not None:
            return Refusal(
                Status.unmapped,
                "activity-not-converted",
                f"buys from {key}, which was not converted: it {why}",
            )
        return Refusal(
            Status.unmapped,
            "not-in-workbook",
            f"buys from {key}, which is not a process of database {self.book.database}.",
        )

    def _in_study(self, row: ExchangeRow, decision: DatabaseDecision) -> Target | Refusal:
        """A line of a database that the mapping file sends to a study folder: linked to one of
        its processes by exact name, reference product and location, when the folder holds a
        study, and waiting for it when it does not."""
        study = decision.study
        assert study is not None
        processes = self.studies.get(study)
        if processes is None:
            return Refusal(
                Status.waiting,
                "waiting-for-database",
                f"buys from database {row.database!r}, which {decision.row.source} sends to the "
                f"database {study}. There is no folder {study} next to this one, so the "
                "supplier is not checked, and is not guessed. Import again once it is there.",
                study=study,
            )
        self.uses.setdefault(study)
        name, product, location = row.name, row.reference_product, row.location
        found = [
            s
            for s in processes
            if s.name == name
            and (product is None or s.product == product)
            and (location is None or s.location == location)
        ]
        if len(found) == 1:
            return self._supplier(found[0], "database")
        asked = f"{name!r}, reference product {product!r}, location {location!r}"
        if found:
            return Refusal(
                Status.unmapped,
                "ambiguous-in-database",
                f"{asked} matches {len(found)} processes of the database {study}. Write its "
                "reference product and location, or decide it in the mapping file.",
                tuple(s.label for s in found[:3]),
            )
        return Refusal(
            Status.unmapped,
            "not-in-database",
            f"{asked} is not a process of the database {study}, in the folder {study} next to "
            "this one.",
            tuple(s.label for s in processes if s.name == name)[:3],
        )

    def _unmatched(
        self,
        row: ExchangeRow,
        kind: str,
        mine: list[Head],
        theirs: Sequence[Supplier],
    ) -> Refusal:
        name, product, location = row.name, row.reference_product, row.location
        asked = f"{name!r}, reference product {product!r}, location {location!r}"
        if mine and theirs:
            return Refusal(
                Status.unmapped,
                "ambiguous-database",
                f"{asked} matches {len(mine)} activity of the workbook and {len(theirs)} process "
                "of the release. Write its database.",
                tuple(h.id for h in mine) + tuple(s.label for s in theirs[:3]),
            )
        if len(mine) > 1:
            return Refusal(
                Status.unmapped,
                "ambiguous-workbook",
                f"{asked} matches {len(mine)} activities of the workbook. Write its reference "
                "product and location.",
                tuple(h.id for h in mine),
            )
        if len(theirs) > 1:
            return Refusal(
                Status.unmapped,
                "ambiguous-release",
                f"{asked} matches {len(theirs)} processes of the release. Write its reference "
                "product and location, or decide it in the mapping file.",
                tuple(s.label for s in theirs[:3]),
            )
        release = self.index.release.name
        if kind == "own":
            if name in self.skipped_names:
                return Refusal(
                    Status.unmapped,
                    "activity-not-converted",
                    f"its activity {name!r} was not converted: it {self.skipped_names[name]}",
                )
            same = tuple(
                f"{h.id} ({h.activity.name} | {h.product} | {h.location})"
                for h in self.heads
                if h.activity.name == name
            )
            return Refusal(
                Status.unmapped,
                "not-in-workbook",
                f"{asked} is not an activity of this workbook ({self.book.database}).",
                same,
            )
        found = self.index.suggestions(name, product, location)
        if kind == "blank":
            return Refusal(
                Status.unmapped,
                "not-found",
                f"{asked} matches no activity of the workbook and no process of release {release}.",
                found,
            )
        return Refusal(
            Status.unmapped,
            "not-in-release",
            f"{asked} is not a process of release {release}. If it was renamed or moved, decide "
            "it in the mapping file.",
            found,
        )

    def _own(self, name: str, product: str | None, location: str | None) -> list[Head]:
        return [
            h
            for h in self.heads
            if h.activity.name == name
            and (product is None or h.product == product)
            and (location is None or h.location == location)
        ]

    def _supplier(self, supplier: Supplier, how: str, note: str | None = None) -> Target:
        return Target(
            supplier.id,
            supplier.flow_id,
            supplier.product,
            supplier.kind,
            supplier.unit,
            how,
            note=note,
        )

    def _own_target(self, head: Head, how: str, note: str | None = None) -> Target:
        return Target(head.id, head.id, head.label, head.kind, head.unit, how, own=True, note=note)

    def flow_of(self, row: ExchangeRow) -> tuple[ElementaryFlow, str] | Refusal:
        decided = self.mapping.biosphere_row(row.name, row.categories)
        if decided is not None:
            self.used_mapping.add(decided.source)
            flow = self.index.flow(decided.target)
            if flow is None:
                return Refusal(
                    Status.unmapped,
                    "mapping-target-missing",
                    f"{decided.source} sends it to {decided.target}, which is not an elementary "
                    f"flow of release {self.index.release.name}",
                )
            return flow, "biosphere-mapping"
        how = "biosphere"
        found = self.index.flows(row.name, "/".join(row.categories))
        if not found and len(row.categories) == 1:
            # Brightway writes ecoinvent's `water/unspecified` as ('water',).
            found = self.index.flows(row.name, f"{row.categories[0]}/unspecified")
            how = "biosphere-unspecified"
        if len(found) > 1:
            return Refusal(
                Status.unmapped,
                "biosphere-ambiguous",
                f"{row.name!r} with categories {'::'.join(row.categories)!r} matches "
                f"{len(found)} flows of the release. Decide it in the mapping file.",
                tuple(f.label for f in found[:3]),
            )
        if not found:
            return Refusal(
                Status.unmapped,
                "biosphere-not-found",
                f"{row.name!r} with categories {'::'.join(row.categories)!r} is not an "
                f"elementary flow of release {self.index.release.name}. Names are matched "
                "exactly.",
                self.index.flow_suggestions(row.name),
            )
        return found[0], how
