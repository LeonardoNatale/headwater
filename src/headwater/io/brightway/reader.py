"""One database of a Brightway project as the records the Excel import converts.

Each activity becomes an `Activity`, and each of its exchanges an `ExchangeRow`, so the project
goes through the same linking and the same report as a workbook. The difference is how a line
names its supplier: an exchange holds the key of the node it comes from, `(database, code)`. The
row is filled with that node's own name, reference product, location, unit and categories, so a
line is linked with exactly what Brightway linked it to, never with a name typed again.

Example: exchange 15 of `tshirt/tshirt` comes from `('toy-background', 'electricity')`. That node
is `electricity production`, product `electricity`, in GLO: the line is linked to the process of
the release with that name, product and location, as a workbook line that says them would be.
A line to an activity of the database itself also keeps the key, and links to that activity even
when another one has the same name, reference product and location.

Activities are read in the order of their codes, which are unique in a database, and lines in the
order Brightway wrote them. So the ids, `-2` for the second of two activities with the same name
and location included, and the files are the same on every import of the same project: `cut-a`
is `twins:fabric-cutting-glo` and `cut-b` is `twins:fabric-cutting-glo-2`, whatever order the rows
are in.

A key that leads to no node of the project is an error that names the exchange and the key, and
nothing is written: Brightway would not calculate that project either.
"""

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import cast

from headwater.io.brightway.project import (
    PROCESS_TYPES,
    PRODUCT_TYPES,
    BrightwayProject,
    Edge,
    Key,
    Node,
    ParameterRecord,
    text_of,
)
from headwater.io.excel.report import Note
from headwater.io.excel.uncertainty import fields_of
from headwater.io.excel.workbook import Activity, BrightwayWorkbook, ExchangeRow, ParameterRow
from headwater.model import InvalidDataError, Problem

_TECHNOSPHERE = frozenset({"technosphere", "substitution"})


@dataclass(frozen=True)
class ProjectDatabase:
    """A database of a project as records, and what the records cannot hold."""

    book: BrightwayWorkbook
    suppliers: dict[str, str] = field(default_factory=dict[str, str])
    """The databases that technosphere lines buy from, with what Brightway stores them as."""
    notes: tuple[Note, ...] = ()


def read_database(project: BrightwayProject, database: str) -> ProjectDatabase:
    """The activities of `database` as records. Raises `InvalidDataError` for a database the
    project does not have or does not store in SQLite, for nodes that are not processes making
    their own product, and for keys that lead nowhere."""
    return _Reader(project, database).read()


class _Reader:
    def __init__(self, project: BrightwayProject, database: str) -> None:
        self.project = project
        self.database = database
        self.problems: list[Problem] = []
        self.notes: list[Note] = []

    def read(self) -> ProjectDatabase:
        self._check_database()
        nodes = self.project.nodes(self.database)
        processes = [n for n in nodes if (n.type or "process") in PROCESS_TYPES]
        self._check_types(nodes)
        edges = self.project.edges(self.database)
        own = {n.key: n for n in nodes}
        suppliers = self._suppliers(edges, own)
        if self.problems:
            raise InvalidDataError(self.problems)
        formulas = self.project.formulas(e.id for e in edges)
        by_activity: dict[Key, list[Edge]] = defaultdict(list)
        for edge in edges:
            by_activity[edge.output].append(edge)
        parameters = self.project.activity_parameters(self.database)
        by_code: dict[str, list[ParameterRecord]] = defaultdict(list)
        for parameter in parameters:
            by_code[parameter.code or ""].append(parameter)

        activities: list[Activity] = []
        for node in processes:
            rows = [
                self._row(edge, suppliers[edge.input], formulas.get(edge.id))
                for edge in by_activity.pop(node.key, [])
            ]
            activities.append(
                Activity(
                    name=node.name or node.code,
                    metadata=_metadata(node),
                    parameters=tuple(
                        self._parameter(p, f"{node.label} (group {p.group})", scope=node.label)
                        for p in by_code.pop(node.code, [])
                    ),
                    exchanges=tuple(rows),
                    source=node.label,
                )
            )
        for output, left in by_activity.items():
            self.notes.append(
                Note(
                    f"{len(left)} exchanges were not read: the node has type "
                    f"{own[output].type!r}, which is not a process type ("
                    f"{', '.join(sorted(PROCESS_TYPES))}), so it is read as a flow of nature",
                    own[output].label,
                )
            )
        for parameter in (p for left in by_code.values() for p in left):
            self.notes.append(
                Note(
                    f"activity parameter {parameter.name} of {self.database}/{parameter.code} "
                    "was not read: its activity is not a process of the database"
                )
            )
        project_rows = self.project.project_parameters()
        database_rows = self.project.database_parameters(self.database)
        book = BrightwayWorkbook(
            path=self.project.folder,
            database=self.database,
            project_parameters=tuple(self._parameter(p, "project parameter") for p in project_rows),
            database_parameters=tuple(
                self._parameter(p, f"database parameter of {self.database}") for p in database_rows
            ),
            activities=tuple(activities),
        )
        return ProjectDatabase(
            book=book,
            suppliers={
                name: self.project.backend(name)
                for name in sorted(
                    {e.input[0] for e in edges if e.type in _TECHNOSPHERE} - {self.database}
                )
            },
            notes=tuple(self.notes),
        )

    def _check_database(self) -> None:
        if self.database not in self.project.databases:
            raise InvalidDataError(
                [
                    Problem(
                        "no-database",
                        f"database {self.database}",
                        f"is not a database of project {self.project.name}. Its databases: "
                        f"{', '.join(sorted(self.project.databases)) or 'none'}.",
                    )
                ]
            )
        backend = self.project.backend(self.database)
        if backend != "sqlite":
            raise InvalidDataError(
                [
                    Problem(
                        "brightway-backend",
                        f"database {self.database}",
                        f"is stored as {backend!r}. headwater reads databases that Brightway "
                        "stores in SQLite (backend 'sqlite') only.",
                    )
                ]
            )

    def _check_types(self, nodes: list[Node]) -> None:
        products = [n for n in nodes if n.type in PRODUCT_TYPES]
        if products:
            shown = ", ".join(f"{n.label} ({n.type})" for n in products[:5])
            more = f" and {len(products) - 5} more" if len(products) > 5 else ""
            self.problems.append(
                Problem(
                    "brightway-node-type",
                    f"database {self.database}",
                    f"has nodes that are separate products or make several products: {shown}"
                    f"{more}. headwater reads databases where each activity makes its own "
                    "product (type process or processwithreferenceproduct).",
                )
            )

    def _suppliers(self, edges: list[Edge], own: Mapping[Key, Node]) -> dict[Key, Node]:
        """The node each exchange comes from. A key that leads nowhere is a problem."""
        wanted = {e.input for e in edges if e.input not in own}
        found = {**own, **self.project.find(wanted)}
        for edge in edges:
            if edge.output not in own:
                self.problems.append(
                    Problem(
                        "brightway-key",
                        edge.label,
                        f"belongs to {edge.output}, which is not a node of database "
                        f"{self.database}",
                    )
                )
            if edge.input in found:
                if edge.type == "production" and edge.input != edge.output:
                    self.problems.append(
                        Problem(
                            "brightway-production",
                            edge.label,
                            f"is a production exchange of {edge.input}, a node other than its "
                            "activity. headwater reads databases where each activity makes its "
                            "own product.",
                        )
                    )
                continue
            where = edge.input[0]
            why = (
                f"database {where} has no node with code {edge.input[1]!r}"
                if where in self.project.databases
                else f"project {self.project.name} has no database {where!r}"
            )
            self.problems.append(
                Problem(
                    "brightway-key",
                    edge.label,
                    f"comes from {edge.input}, which is in no database of the project: {why}. "
                    "Brightway cannot calculate it either: link the exchange again in "
                    "Brightway, or delete it.",
                )
            )
        return found

    def _row(self, edge: Edge, supplier: Node, formula: str | None) -> ExchangeRow:
        data = edge.data
        stated = data.get("formula")
        if formula is None and isinstance(stated, str) and stated.strip():
            self.notes.append(
                Note(
                    f"has the formula {stated!r}, which Brightway does not use: the exchange is "
                    "in no parameter group. The amount is kept.",
                    edge.label,
                )
            )
        categories = supplier.data.get("categories")
        return ExchangeRow(
            name=supplier.name or supplier.code,
            amount=data.get("amount"),
            unit=text_of(supplier.data.get("unit")) or text_of(data.get("unit")),
            location=supplier.location,
            type=edge.type,
            database=supplier.database,
            reference_product=supplier.product,
            categories=tuple(str(c) for c in cast(Sequence[object], categories))
            if isinstance(categories, list | tuple)
            else (),
            formula=formula,
            comment=text_of(data.get("comment")),
            uncertainty=fields_of(data),
            source=edge.label,
            key=supplier.label if supplier.database == self.database else None,
        )

    def _parameter(
        self, record: ParameterRecord, where: str, scope: str | None = None
    ) -> ParameterRow:
        return ParameterRow(
            name=record.name,
            amount=record.amount,
            formula=record.formula,
            unit=text_of(record.data.get("unit")),
            comment=text_of(record.data.get("comment")),
            source=f"{where}: {record.name}",
            scope=scope,
            uncertainty=fields_of(record.data),
        )


def _metadata(node: Node) -> dict[str, object]:
    return {
        "reference product": node.product,
        "location": node.location,
        "unit": node.data.get("unit"),
        "comment": node.data.get("comment"),
        "production amount": node.data.get("production amount"),
    }
