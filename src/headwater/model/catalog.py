from collections.abc import Iterable, Mapping

from headwater.model.errors import InvalidDataError, Problem, did_you_mean
from headwater.model.flows import CONVERTING_PROPERTIES, Flow, FlowKind, FlowProperty
from headwater.model.process import Exchange, Process
from headwater.model.units import DEFAULT_UNITS, Units


class Catalog:
    """Flows, processes, flow properties and units, looked up by id.

    Reads nothing from disk and keeps no matrix indices. It answers one question that
    needs all of them: how much of the flow's own unit is this exchange?
    """

    def __init__(
        self,
        flows: Iterable[Flow],
        processes: Iterable[Process] = (),
        properties: Iterable[FlowProperty] = (),
        units: Units = DEFAULT_UNITS,
    ) -> None:
        problems: list[Problem] = []
        self._flows: dict[str, Flow] = {}
        for flow in flows:
            known = self._flows.setdefault(flow.id, flow)
            if known is not flow and known != flow:
                problems.append(
                    Problem(
                        "duplicate-flow", f"flow {flow.id}", "is defined twice, in different ways"
                    )
                )
        self._processes: dict[str, Process] = {}
        for process in processes:
            known = self._processes.setdefault(process.id, process)
            if known is not process and known != process:
                problems.append(
                    Problem(
                        "duplicate-process",
                        f"process {process.id}",
                        "is defined twice, in different ways",
                    )
                )
        self._properties: dict[str, dict[str, FlowProperty]] = {}
        for prop in properties:
            by_name = self._properties.setdefault(prop.flow_id, {})
            known = by_name.setdefault(prop.property.value, prop)
            if known is not prop and known != prop:
                problems.append(
                    Problem(
                        "duplicate-property",
                        f"flow {prop.flow_id} property {prop.property.value}",
                        "is given twice, with different values",
                    )
                )
        if problems:
            raise InvalidDataError(problems)
        self.units = units

    @property
    def flows(self) -> Mapping[str, Flow]:
        return self._flows

    @property
    def processes(self) -> Mapping[str, Process]:
        return self._processes

    def flow(self, flow_id: str) -> Flow | None:
        return self._flows.get(flow_id)

    def process(self, process_id: str) -> Process | None:
        return self._processes.get(process_id)

    def properties_of(self, flow_id: str) -> tuple[FlowProperty, ...]:
        return tuple(self._properties.get(flow_id, {}).values())

    def is_treatment(self, process: Process) -> bool:
        """True when the process makes a waste flow, that is, it treats waste.

        A treatment is not a kind of process: it is derived from the reference flow.
        """
        reference = process.reference_exchange
        flow = self._flows.get(reference.flow_id)
        if flow is None:
            raise _error(
                "unknown-flow",
                reference.label,
                f"flow {reference.flow_id!r} is not defined."
                f"{did_you_mean(reference.flow_id, self._flows)}",
            )
        return flow.kind is FlowKind.waste

    def convert(self, exchange: Exchange) -> float:
        """The amount of `exchange` in the reference unit of its flow.

        Within one quantity, the unit table does it (250 g of a kg flow is 0.25). Across
        quantities, a property of the flow does it (natural gas: 72 MJ is 2 m3 at 36 MJ per m3).
        A missing property is an error, never a factor of 1.
        """
        flow = self._flows.get(exchange.flow_id)
        if flow is None:
            raise _error(
                "unknown-flow",
                exchange.label,
                f"flow {exchange.flow_id!r} is not defined."
                f"{did_you_mean(exchange.flow_id, self._flows)}",
            )
        flow_unit = self.units.get(flow.unit)
        if flow_unit is None:
            raise InvalidDataError([self.units.unknown(flow.unit, f"flow {flow.id}")])
        unit = self.units.get(exchange.unit)
        if unit is None:
            raise InvalidDataError(
                [self.units.unknown(exchange.unit, exchange.label, f" (flow {flow.id})")]
            )
        if unit.quantity == flow_unit.quantity:
            return self.units.convert(exchange.amount, unit.name, flow_unit.name)
        return self._convert_with_property(exchange, flow, unit.quantity, flow_unit.quantity)

    def _convert_with_property(
        self, exchange: Exchange, flow: Flow, quantity: str, flow_quantity: str
    ) -> float:
        usable: list[FlowProperty] = []
        for prop in self.properties_of(flow.id):
            prop_unit = self.units.get(prop.unit)
            if (
                prop.property in CONVERTING_PROPERTIES
                and prop_unit is not None
                and prop_unit.quantity == quantity
            ):
                usable.append(prop)
        if not usable:
            raise _error(
                "unit-quantity-mismatch",
                exchange.label,
                f"flow {flow.id} is counted in {flow.unit!r} ({flow_quantity}), but this line "
                f"uses {exchange.unit!r} ({quantity}), and the flow has no property that "
                f"converts {quantity} to {flow_quantity}. Add one, or use a unit of "
                f"{flow_quantity}.",
            )
        if len(usable) > 1:
            names = ", ".join(p.property.value for p in usable)
            raise _error(
                "ambiguous-conversion",
                exchange.label,
                f"flow {flow.id} has several properties that convert {quantity} to "
                f"{flow_quantity} ({names}), so the unit {exchange.unit!r} is ambiguous",
            )
        prop = usable[0]
        in_property_unit = self.units.convert(exchange.amount, exchange.unit, prop.unit)
        return in_property_unit / prop.value


def _error(code: str, item: str, message: str) -> InvalidDataError:
    return InvalidDataError([Problem(code, item, message)])
