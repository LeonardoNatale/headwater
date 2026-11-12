"""What an upgrade changes in a study, by id: the same moves are written to the files, and made to
the records for a diff across the release change."""

from collections.abc import Mapping
from dataclasses import dataclass, replace

from headwater.model import Exchange, Override, ParameterSet, Pin
from headwater.study import DatabaseFolder


@dataclass(frozen=True)
class Moves:
    """Example, toy-1 to toy-2: the pin becomes `toy-2@bd46d2b98059`, the supplier
    `toy:1:fabric-a` becomes `toy:2:fabric-b`, the flow `toy:dye` is named `reactive dye`, and
    line `e02` of `toy:1:market-elec` is line `a2` of its successor."""

    pin: Pin
    suppliers: Mapping[str, str]
    """Each old process with a successor, by id."""
    products: Mapping[str, tuple[str, str]]
    """The old processes whose successor makes another product: the old product and the new."""
    names: Mapping[str, str]
    """The name in the new release of each flow the studies name."""
    lines: Mapping[tuple[str, str], str]
    """The line of the successor for each line of an old process an override or a scenario
    changes, by old process and line id."""

    def line(
        self, supplier: str | None, flow: str, flow_name: str | None
    ) -> tuple[str | None, str, str | None]:
        """The supplier, flow and flow name of a line once moved. A line buys the product its new
        supplier makes, and a flow name that is written takes the new release's name."""
        if supplier is not None and supplier in self.suppliers:
            products = self.products.get(supplier)
            if products is not None and flow == products[0]:
                flow = products[1]
            supplier = self.suppliers[supplier]
        if flow_name is not None and flow in self.names:
            flow_name = self.names[flow]
        return supplier, flow, flow_name

    def process(self, process_id: str) -> str:
        return self.suppliers.get(process_id, process_id)

    def line_id(self, process_id: str, line_id: str) -> str:
        return self.lines.get((process_id, line_id), line_id)


def moved(folder: DatabaseFolder, moves: Moves) -> DatabaseFolder:
    """`folder` and the studies it uses, with every id of the old release moved. Nothing else
    changes: amounts, formulas, comments and the study's own ids stay as they are."""
    parameters = folder.parameters
    if any(p.scope in moves.suppliers for p in parameters):
        parameters = ParameterSet(
            p.model_copy(update={"scope": moves.process(p.scope)}) if p.scope else p
            for p in parameters
        )
    return replace(
        folder,
        database=folder.database.model_copy(update={"background": moves.pin}),
        processes=tuple(
            p.model_copy(update={"exchanges": tuple(_line(e, moves) for e in p.exchanges)})
            for p in folder.processes
        ),
        overrides=tuple(_override(o, moves) for o in folder.overrides),
        parameters=parameters,
        no_factor=tuple(
            r.model_copy(update={"flow_name": moves.names.get(r.flow_id, r.flow_name)})
            if r.flow_name is not None
            else r
            for r in folder.no_factor
        ),
        override_sources=tuple(
            (_label(label, moves), source) for label, source in folder.override_sources
        ),
        used=tuple(moved(u, moves) for u in folder.used),
    )


def _line(e: Exchange, moves: Moves, process_id: str | None = None) -> Exchange:
    supplier, flow, name = moves.line(e.supplier_id, e.flow_id, e.flow_name)
    update: dict[str, object] = {"supplier_id": supplier, "flow_id": flow, "flow_name": name}
    if process_id is not None:
        update |= {"process_id": process_id, "line_id": moves.line_id(e.process_id, e.line_id)}
    if all(getattr(e, k) == v for k, v in update.items()):
        return e
    return e.model_copy(update=update)


def _override(o: Override, moves: Moves) -> Override:
    process_id = moves.process(o.process_id)
    return o.model_copy(
        update={
            "process_id": process_id,
            "lines": tuple(_line(e, moves, process_id) for e in o.lines),
        }
    )


def _label(label: str, moves: Moves) -> str:
    """`toy:1:elec line e02` as `toy:2:elec line a2`."""
    process_id, _, line_id = label.partition(" line ")
    return f"{moves.process(process_id)} line {moves.line_id(process_id, line_id)}"
