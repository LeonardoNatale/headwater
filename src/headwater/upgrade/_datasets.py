"""The processes of a release as datasets: what a person names them by, and how to find them.

ecoinvent names a dataset by its activity name, its reference product and its location:
`market for tap water | tap water | RoW`. That key is unique within one version, and it is what
carries over between versions: from 3.10 to 3.12 every activity UUID changed, while most product
UUIDs and most keys stayed.
"""

from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from functools import cached_property
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.dataset as ds

from headwater.model import Process
from headwater.query.search import SearchTable
from headwater.release import Release
from headwater.release.schema import read_processes

compute: Any = pc


@dataclass(frozen=True)
class Dataset:
    """One process of a release. Example: `toy:2:fabric-b`, `cotton fabric production`, product
    `toy:cotton-fabric` named `cotton fabric`, in `RoW`, per `kg`, an
    `ordinary_transforming_activity`."""

    id: str
    name: str
    product: str
    """The id of the flow it makes, or of the waste it treats."""
    product_name: str
    location: str
    unit: str
    """The unit of its reference line."""
    kind: str

    def __str__(self) -> str:
        """`cotton fabric production | cotton fabric | RoW`."""
        return f"{self.name} | {self.product_name} | {self.location}"

    @property
    def label(self) -> str:
        """`cotton fabric production | cotton fabric | RoW (toy:2:fabric-b)`."""
        return f"{self} ({self.id})"

    @property
    def key(self) -> tuple[str, str, str]:
        """What names the dataset across versions: its name, product id and location."""
        return (self.name, self.product, self.location)

    @property
    def is_market(self) -> bool:
        return self.kind in ("market_activity", "market_group")


class Datasets:
    """Every process of a source release as a dataset, found by id, by key or by product."""

    def __init__(self, release: Release) -> None:
        self.release = release
        con = release.connect()
        try:
            # the unit of the reference line, as the supplier mapping of the importers reads it
            rows = con.sql(
                "SELECT p.id, p.name, p.reference_flow_id, f.name, p.location, e.unit, p.kind "
                "FROM processes p JOIN flows f ON f.id = p.reference_flow_id "
                "JOIN exchanges e ON e.process_id = p.id AND e.role = 'reference' ORDER BY p.id"
            ).fetchall()
            self.flow_names: dict[str, str] = dict(con.sql("SELECT id, name FROM flows").fetchall())
            """The name of every flow of the release, by id."""
        finally:
            con.close()
        self.by_id: dict[str, Dataset] = {r[0]: Dataset(*r) for r in rows}
        keys = Counter(d.key for d in self.by_id.values())
        self.by_key: dict[tuple[str, str, str], Dataset] = {
            d.key: d for d in self.by_id.values() if keys[d.key] == 1
        }
        """The datasets by name, product and location. A key two datasets share names neither."""
        self._by_product: dict[str, list[Dataset]] = defaultdict(list)
        for dataset in self.by_id.values():
            self._by_product[dataset.product].append(dataset)
        self._full: dict[str, Process] = {}

    @property
    def prefix(self) -> str:
        """What the ids of the release start with: `ecoinvent:3.12:` for ecoinvent 3.12,
        `toy:2:` for toy-2. The rest of an id is the dataset's own UUIDs."""
        return f"{self.release.info.source}:{self.release.info.version}:"

    @property
    def pin(self) -> str:
        """`toy-2@bd46d2b98059`: how a study pins the release."""
        return f"{self.release.name}@{self.release.hash[:12]}"

    def making(self, product: str) -> tuple[Dataset, ...]:
        """The datasets that make `product`, in every place and under every name."""
        return tuple(self._by_product.get(product, ()))

    @cached_property
    def search(self) -> SearchTable:
        """The datasets as `find` searches them, built from what is read already."""
        found = tuple(self.by_id.values())
        return SearchTable(
            ids=tuple(d.id for d in found),
            flow_ids=tuple(d.product for d in found),
            names=tuple(d.name for d in found),
            products=tuple(d.product_name for d in found),
            locations=tuple(d.location for d in found),
            units=tuple(d.unit for d in found),
            kinds=tuple(d.kind for d in found),
        )

    def processes(self, ids: Iterable[str]) -> dict[str, Process]:
        """The processes `ids` with every line, read from the release's tables once each."""
        ids = list(ids)
        missing = sorted({i for i in ids if i not in self._full})
        if missing:
            wanted = pa.array(missing, pa.string())
            path = self.release.path

            def rows(table: str, key: str) -> pa.Table:
                data = ds.dataset(path / f"{table}.parquet", format="parquet")  # pyright: ignore[reportUnknownMemberType]
                return data.to_table(filter=compute.field(key).isin(wanted))  # pyright: ignore[reportUnknownMemberType]

            for p in read_processes(rows("processes", "id"), rows("exchanges", "process_id")):
                self._full[p.id] = p
        return {i: self._full[i] for i in ids if i in self._full}
