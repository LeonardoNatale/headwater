"""Finding a background process by part of its name, product or place.

Example, the toy: `find("electricity")` lists `toy:electricity-production`, because its name
starts with `electricity`. The id is what a study file needs.

The table is read once, from the release's `processes` and `flows` tables, or from the records
of a folder: no index file, nothing to rebuild, and nothing to go stale, because a release never
changes. It loads no method and solves nothing. Matching is by rules, with no fuzzy matching and
no synonyms, so a result can always be explained by one of them.
"""

import heapq
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import cast

import pyarrow as pa

from headwater.model import InvalidDataError, Problem, ProcessKind, did_you_mean
from headwater.query._open import open_source
from headwater.release import Release
from headwater.study import ProcessFolder, read_process_folder

WHY = (
    "name is",
    "name starts with",
    "every word in name",
    "product is",
    "product starts with",
    "every word in product",
)
"""The rules, in the order they rank. Each says what a match did: the name (or the reference
product) is the text, starts with it, or has every word of it, in any order, each as a part of
a word. A process is listed once, at the first rule it meets."""

KINDS = tuple(kind.value for kind in ProcessKind)

_SHOWN = 30
"""At most this many places, or processes, are listed in an error."""

_CLOSEST = 5
"""How many processes `get` suggests when none has the name: the first that `find` gives."""


@dataclass(frozen=True)
class Match:
    """One process found, and the rule that found it.

    Example, on the toy: process `toy:electricity-production`, flow `toy:electricity`, name
    `electricity production`, reference product `electricity`, location `GLO`, unit `kWh`, kind
    `ordinary_transforming_activity`, why `name starts with`. `process_id` is the supplier to
    write in a study file, and `flow_id` the flow of its line: `new_exchange("input",
    match.flow_id, 0.5, match.unit, supplier=match.process_id)`. `unit` is the unit of the
    reference product, as for `bg.contributions`.
    """

    process_id: str
    flow_id: str
    """The id of the reference product: the flow the process makes, or the waste it treats."""
    name: str
    reference_product: str
    location: str
    unit: str
    kind: str
    why: str

    def __repr__(self) -> str:
        """One line, as `headwater find` lists it: `Match(toy:electricity-production: electricity
        production | electricity | GLO, kWh, name starts with)`."""
        return (
            f"Match({self.process_id}: {self.name} | {self.reference_product} | {self.location}, "
            f"{self.unit}, {self.why})"
        )


@dataclass(frozen=True, eq=False)
class SearchTable:
    """The processes of a background as columns of text, one entry per process.

    Each tuple has the same length, in any order. `find` sorts what it returns. The lower case
    copies that matching needs are made when first used.
    """

    ids: tuple[str, ...]
    flow_ids: tuple[str, ...]
    """The id of each process's reference product."""
    names: tuple[str, ...]
    products: tuple[str, ...]
    """The name of each process's reference product."""
    locations: tuple[str, ...]
    units: tuple[str, ...]
    kinds: tuple[str, ...]
    """The `ProcessKind` value of each process, such as `market_activity`."""

    @cached_property
    def _names(self) -> tuple[str, ...]:
        return tuple(name.casefold() for name in self.names)

    @cached_property
    def _products(self) -> tuple[str, ...]:
        return tuple(product.casefold() for product in self.products)

    @cached_property
    def _named(self) -> dict[str, list[int]]:
        """The rows of each name, as written: what `get` matches exactly."""
        rows: dict[str, list[int]] = {}
        for i, name in enumerate(self.names):
            rows.setdefault(name, []).append(i)
        return rows

    @cached_property
    def _places(self) -> dict[str, str]:
        """The places the table holds, by their lower case code."""
        return {place.casefold(): place for place in sorted(set(self.locations))}

    def find(
        self,
        text: str,
        *,
        location: str | None = None,
        product: str | None = None,
        kind: str | None = None,
        limit: int | None = 20,
    ) -> tuple[Match, ...]:
        """The processes that match `text`, best first. See `Background.find`."""
        words = text.casefold().split()
        if not words:
            raise ValueError("find needs some text to look for: at least one word")
        if limit is not None and limit < 0:
            raise ValueError(f"limit must be zero or positive, got {limit}")
        phrase = " ".join(words)
        kept = self._kept(location, product, kind)
        names, products = self._names, self._products
        hits: list[tuple[int, int]] = []
        for i in kept:
            rank = _rank(names[i], phrase, words)
            if rank is None:
                rank = _rank(products[i], phrase, words)
                if rank is not None:
                    rank += 3
            if rank is not None:
                hits.append((rank, i))

        def order(hit: tuple[int, int]) -> tuple[int, str, str, str, str]:
            rank, i = hit
            return (rank, names[i], self.locations[i], products[i], self.ids[i])

        best = sorted(hits, key=order) if limit is None else heapq.nsmallest(limit, hits, order)
        return tuple(self._match(i, rank) for rank, i in best)

    def get(self, name: str, *, product: str | None = None, location: str | None = None) -> Match:
        """The one process with this name, and this reference product and location when given,
        each matched exactly, case included. See `Background.get_process`."""
        named = self._named.get(name, [])
        found = [
            i
            for i in named
            if (product is None or self.products[i] == product)
            and (location is None or self.locations[i] == location)
        ]
        if len(found) == 1:
            return self._match(found[0], 0)
        asked = f"named {name!r}" + (f", making {product!r}" if product is not None else "")
        asked += f", in {location!r}" if location is not None else ""
        if found:
            raise ValueError(
                f"{len(found)} processes are {asked}: give product or location to choose one."
                f"{self._listed(found)}"
            )
        if named:
            hint = f" Processes named {name!r}:{self._listed(named)}"
        else:
            row = {process_id: i for i, process_id in enumerate(self.ids)}
            close = (
                [row[m.process_id] for m in self.find(name, limit=_CLOSEST)] if name.split() else []
            )
            hint = f" The closest names:{self._listed(close)}" if close else ""
        raise ValueError(f"no process is {asked}.{hint}")

    def _listed(self, rows: Sequence[int]) -> str:
        """`\n  toy:x: name | product | location` for each row, at most `_SHOWN`."""
        lines = [
            f"\n  {self.ids[i]}: {self.names[i]} | {self.products[i]} | {self.locations[i]}"
            for i in sorted(rows, key=lambda i: (self.locations[i], self.products[i], self.ids[i]))
        ]
        more = f"\n  and {len(lines) - _SHOWN} more" if len(lines) > _SHOWN else ""
        return "".join(lines[:_SHOWN]) + more

    def _match(self, i: int, rank: int) -> Match:
        return Match(
            process_id=self.ids[i],
            flow_id=self.flow_ids[i],
            name=self.names[i],
            reference_product=self.products[i],
            location=self.locations[i],
            unit=self.units[i],
            kind=self.kinds[i],
            why=WHY[rank],
        )

    def _kept(self, location: str | None, product: str | None, kind: str | None) -> Iterable[int]:
        """The rows the filters keep. A place or a kind that does not exist is an error."""
        kept: Iterable[int] = range(len(self.ids))
        if location is not None:
            place = self._place(location)
            kept = (i for i in kept if self.locations[i] == place)
        if kind is not None:
            wanted = str(kind).strip().casefold()
            if wanted not in KINDS:
                raise ValueError(
                    f"{wanted!r} is not a kind of process.{did_you_mean(wanted, KINDS)} "
                    f"Kinds: {', '.join(KINDS)}"
                )
            kept = (i for i in kept if self.kinds[i] == wanted)
        if product is not None:
            words = product.casefold().split()
            if not words:
                raise ValueError("product is empty: leave it out to search every product")
            products = self._products
            kept = (i for i in kept if all(word in products[i] for word in words))
        return kept

    def _place(self, location: str) -> str:
        found = self._places.get(location.strip().casefold())
        if found is not None:
            return found
        places = list(self._places.values())
        listed = ", ".join(places[:_SHOWN])
        more = f" and {len(places) - _SHOWN} more" if len(places) > _SHOWN else ""
        raise ValueError(
            f"no process is in {location!r}.{did_you_mean(location, places)} Places: {listed}{more}"
        )


def _rank(text: str, phrase: str, words: Sequence[str]) -> int | None:
    """0 if `text` is the phrase, 1 if it starts with it, 2 if it has every word, else None."""
    if text == phrase:
        return 0
    if text.startswith(phrase):
        return 1
    if words[0] in text and all(word in text for word in words):  # most rows stop at the first
        return 2
    return None


def table_of_release(release: Release) -> SearchTable:
    """The search table of a source release: its `processes` and `flows` tables, and nothing
    else (timings: docs/appendix/benchmarks.md). Raises InvalidDataError for a process whose
    reference flow is not in the release's flows."""
    processes = release.table("processes")
    flows = release.table("flows")
    return _table(
        ids=_strings(processes, "id"),
        names=_strings(processes, "name"),
        flow_ids=_strings(processes, "reference_flow_id"),
        locations=_strings(processes, "location"),
        kinds=_strings(processes, "kind"),
        flows=zip(
            _strings(flows, "id"), _strings(flows, "name"), _strings(flows, "unit"), strict=True
        ),
    )


def table_of_folder(folder: ProcessFolder) -> SearchTable:
    """The search table of the processes of a folder, or of a background read from a release,
    from the records it already holds. Raises InvalidDataError for a process whose reference
    flow the folder does not hold."""
    processes = folder.processes
    return _table(
        ids=tuple(p.id for p in processes),
        names=tuple(p.name for p in processes),
        flow_ids=tuple(p.reference_exchange.flow_id for p in processes),
        locations=tuple(p.location for p in processes),
        kinds=tuple(p.kind.value for p in processes),
        flows=((f.id, f.name, f.unit) for f in folder.flows),
    )


def table_of_path(path: Path | str, *, namespace: str | None = None) -> SearchTable:
    """The search table of a release or of a folder of process files, with no method.

    `path` is opened as `hw.load` opens it: a release folder, a release in the local cache
    written `name@hash` (at least 12 characters of the hash), or a folder of process files,
    whose ids are prefixed with `namespace` (default: the folder's name). A namespace for a
    release raises ValueError: its ids are fixed.
    """
    source = open_source(path)
    if not isinstance(source, Release):
        return table_of_folder(read_process_folder(source, namespace=namespace))
    if namespace is not None:
        raise ValueError(
            f"namespace is for a folder of process files: the ids of release {source.name} "
            "are fixed"
        )
    return table_of_release(source)


def _strings(table: pa.Table, name: str) -> tuple[str, ...]:
    return tuple(cast(list[str], table.column(name).to_pylist()))


def _table(
    *,
    ids: tuple[str, ...],
    names: tuple[str, ...],
    flow_ids: tuple[str, ...],
    locations: tuple[str, ...],
    kinds: tuple[str, ...],
    flows: Iterable[tuple[str, str, str]],
) -> SearchTable:
    """The table of processes whose reference flows are `flow_ids`, with the name and unit of
    each flow from `flows` (id, name, unit). Raises InvalidDataError naming the processes whose
    flow is not there."""
    flow = {flow_id: (name, unit) for flow_id, name, unit in flows}
    missing = [(i, f) for i, f in zip(ids, flow_ids, strict=True) if f not in flow]
    if missing:
        raise InvalidDataError(
            Problem(
                "unknown-flow",
                f"process {process_id}",
                f"makes flow {flow_id!r}, which is not in the flows",
            )
            for process_id, flow_id in missing[:_SHOWN]
        )
    return SearchTable(
        ids=ids,
        flow_ids=flow_ids,
        names=names,
        products=tuple(flow[flow_id][0] for flow_id in flow_ids),
        locations=locations,
        units=tuple(flow[flow_id][1] for flow_id in flow_ids),
        kinds=kinds,
    )
