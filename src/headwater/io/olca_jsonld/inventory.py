"""Read the processes of an openLCA JSON-LD package into an inventory source.

Each exchange of a process is a line. Its role comes from its flow's type and its direction,
never from the sign of its amount:

| Exchange                               | Role                                                  |
|----------------------------------------|-------------------------------------------------------|
| product, output                        | `reference`: a product the process makes              |
| product, input                         | `input` from its default provider; `own_use` when the |
|                                        | provider is the process itself                        |
| product, avoided (stored as an input)  | `avoided_product`, from its default provider          |
| waste, input                           | `reference`: the waste the process treats             |
| waste, output                          | `waste_to_treatment`, to its default provider         |
| elementary, output or input            | `emission` or `resource`                              |

An avoided waste has no role, and is refused. Amounts are kept as written. A process that makes
several products keeps all of them, with the allocation factors of its default allocation
method: splitting it is the shared allocation stage.

Ids: a process is `<source>:<version>:<uuid>`, for example `bafu:2026:<uuid>`, a line its
exchange's `internalId`, and a flow `olca:flow:<uuid>` (see `flows`).
"""

import math
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

import pyarrow as pa
from pydantic import ValidationError

from headwater.io.olca_jsonld.flows import (
    FLOW_LIST,
    KINDS,
    Flows,
    flow_id,
    geographies,
    in_other_property,
    number,
    read_flows,
    read_locations,
    unit_name,
)
from headwater.io.olca_jsonld.package import DataSet, Package, field, obj, objs, ref_id, text
from headwater.io.source import ALLOCATION, LINES, PROCESSES_READ, InventoryOptions, InventorySource
from headwater.model import (
    FlowKind,
    InvalidDataError,
    Problem,
    ProcessKind,
    Role,
    Severity,
    Uncertainty,
)
from headwater.model.process import ROLES_WITH_SUPPLIER
from headwater.release import ReleaseInfo

ALLOCATION_METHODS = {
    "PHYSICAL_ALLOCATION": "physical",
    "ECONOMIC_ALLOCATION": "economic",
    "CAUSAL_ALLOCATION": "causal",
}
"""The allocation methods whose factors split a process, by their openLCA name."""

NOT_KEPT = {
    "dqEntry": "a data quality entry",
    "costValue": "a cost",
    "location": "a location of its own",
}
"""Fields of an exchange that a release has no place for yet, with what they are. The import
warns how many exchanges have each."""

_REQUIRED = {
    "version": "which version of its source it is: give it, for example --version 2026. It goes "
    "into every process id.",
    "source": "its source's name: give it, for example --source bafu. It goes into every "
    "process id.",
    "licence": "its licence: give it, for example --licence 'BAFU terms of use'.",
}


def read(path: Path, options: InventoryOptions) -> InventorySource:
    """Read the package at `path`, a folder, a .zip or a .zolca, of `options.source` and
    `options.version`. Links nothing and splits nothing: that is
    `headwater.io.inventory.link_inventory`."""
    missing = [name for name in _REQUIRED if not getattr(options, name)]
    if missing:
        raise InvalidDataError(
            [
                Problem(
                    "missing-option",
                    f"--{name}",
                    f"a JSON-LD package does not say {_REQUIRED[name]}",
                )
                for name in missing
            ]
        )
    assert options.source and options.version and options.licence
    info = _info(options.source, options.version, options.licence)
    with Package(path) as package:
        flows = read_flows(package)
        locations = read_locations(package)
        reader = _Reader(package, f"{options.source}:{options.version}", flows, locations)
        reader.read()
    if reader.problems:
        raise InvalidDataError(reader.problems)
    return InventorySource(
        info=info,
        processes=pa.Table.from_pylist(reader.processes, schema=PROCESSES_READ.schema),
        lines=pa.Table.from_pylist(reader.lines, schema=LINES.schema),
        flows=flows.flows,
        flow_properties=(),
        units=flows.units,
        geographies=geographies(locations),
        allocation=pa.Table.from_pylist(reader.allocation, schema=ALLOCATION.schema),
        supplier_hint=_missing_file,
        warnings=tuple(reader.warnings),
    )


def _info(source: str, version: str, licence: str) -> ReleaseInfo:
    try:
        return ReleaseInfo(
            name=f"{source}-{version}",
            source=source,
            version=version,
            licence=licence,
            flow_list=FLOW_LIST,
        )
    except ValidationError:
        raise InvalidDataError(
            [
                Problem(
                    "bad-option",
                    f"--source {source} --version {version}",
                    "name the release: use letters, digits, '.', '_' and '-' only",
                )
            ]
        ) from None


def _missing_file(supplier: str) -> str:
    return f"There is no file processes/{supplier.rsplit(':', 1)[1]}.json."


class _Reader:
    """Reads every process of a package into rows, collecting every problem."""

    def __init__(
        self,
        package: Package,
        prefix: str,
        flows: Flows,
        locations: dict[str, tuple[str, str]],
    ) -> None:
        self.package = package
        self.prefix = prefix
        self.flows = flows
        self.locations = locations
        self.processes: list[dict[str, Any]] = []
        self.lines: list[dict[str, Any]] = []
        self.allocation: list[dict[str, Any]] = []
        self.problems: list[Problem] = []
        self.warnings: list[Problem] = []
        self.not_kept: Counter[str] = Counter()

    def read(self) -> None:
        for uuid, data in self.package.all("processes"):
            self._process(uuid, data)
        for name, count in sorted(self.not_kept.items()):
            has = "exchange has" if count == 1 else "exchanges have"
            message = f"{count:,} {has} {NOT_KEPT[name]} ({name}), which the release does not keep"
            self._warn("not-kept", f"package {self.package.path.name}", message, None)

    def _process_id(self, uuid: str) -> str:
        return f"{self.prefix}:{uuid}"

    def _fail(self, code: str, item: str, message: str, where: str) -> None:
        self.problems.append(Problem(code, item, message, location=where))

    def _warn(self, code: str, item: str, message: str, where: str | None) -> None:
        self.warnings.append(
            Problem(code, item, message, severity=Severity.warning, location=where)
        )

    def _process(self, uuid: str, data: DataSet) -> None:
        process_id = self._process_id(uuid)
        where = f"processes/{uuid}.json"
        place = ref_id(data, "location")
        location = self.locations.get(place or "")
        if location is None:
            missing = (
                f"the location {place}, which is not in the package" if place else "no location"
            )
            self._fail("bad-location", f"process {process_id}", f"has {missing}", where)
        name = text(data, "name")
        if name is None:
            self._fail("bad-process", f"process {process_id}", "has no name", where)
        document = obj(data, "processDocumentation")
        self.processes.append(
            {
                "id": process_id,
                "name": name,
                "location": location[0] if location else None,
                "time_start": self._date(text(document, "validFrom"), process_id, where),
                "time_end": self._date(text(document, "validUntil"), process_id, where),
                "kind": ProcessKind.ordinary_transforming_activity.value,
                "source_ref": where,
            }
        )
        products: dict[str, list[str]] = {}
        others: set[str] = set()
        for exchange in objs(data, "exchanges"):
            line = self._line(uuid, process_id, exchange, where)
            if line is None:
                continue
            self.lines.append(line)
            self.not_kept.update(k for k in NOT_KEPT if field(exchange, k) not in (None, ""))
            if line["role"] == Role.reference.value:
                products.setdefault(ref_id(exchange, "flow") or "", []).append(line["line_id"])
            else:
                others.add(line["line_id"])
        count = sum(len(lines) for lines in products.values())
        if count > 1:
            self._allocate(process_id, data, products, others, where, count)

    def _date(self, value: str | None, process_id: str, where: str) -> date | None:
        if value is None:
            return None
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            message = f"has the date {value!r}, which is not an ISO date"
            self._fail("bad-date", f"process {process_id}", message, where)
            return None

    def _line(
        self, process: str, process_id: str, exchange: DataSet, where: str
    ) -> dict[str, Any] | None:
        internal = field(exchange, "internalId")
        label = f"{process_id} line {internal}"
        flow = obj(exchange, "flow")
        uuid = text(flow, "@id") or ""
        kind = self.flows.kinds.get(uuid) or KINDS.get(text(flow, "flowType") or "")
        amount = field(exchange, "amount")
        unit = unit_name(obj(exchange, "unit"), self.flows.unit_names)
        is_input = field(exchange, "isInput") is True
        avoided = field(exchange, "isAvoidedProduct") is True
        checks = (
            ("an internalId", isinstance(internal, int) and not isinstance(internal, bool)),
            ("a flow of known type", kind is not None),
            ("a unit", unit is not None),
            ("a number as amount", number(amount)),
        )
        missing = [name for name, ok in checks if not ok]
        if missing or kind is None or unit is None:
            self._fail("bad-exchange", label, f"needs {', '.join(missing)}", where)
            return None
        role = _role(kind, is_input, avoided)
        if role is None:
            self._fail(
                "unsupported-line",
                label,
                f"is an avoided {kind.value} flow stored as an {'input' if is_input else 'output'}"
                ", which has no role: only an avoided product, stored as an input, has one",
                where,
            )
            return None
        reference = self.flows.reference_property.get(uuid)
        other = in_other_property(uuid, ref_id(exchange, "flowProperty"), reference)
        if other is not None:
            self._fail("other-flow-property", label, other, where)
            return None
        supplier = None
        provider = ref_id(exchange, "defaultProvider")
        if role in ROLES_WITH_SUPPLIER and provider:
            supplier = self._process_id(provider)
            if role is Role.input and provider == process:
                role = Role.own_use
        return {
            "process_id": process_id,
            "line_id": str(internal),
            "role": role.value,
            "flow_id": flow_id(uuid),
            "flow_name": text(flow, "name"),
            "amount": float(amount),
            "unit": unit,
            "supplier_id": supplier,
            "uncertainty": self._uncertainty(obj(exchange, "uncertainty"), label, where),
            "properties": [],
            "comment": text(exchange, "description"),
        }

    def _uncertainty(self, given: DataSet, label: str, where: str) -> dict[str, Any] | None:
        """The distribution as headwater writes it. Lognormal: `loc` is ln(geomMean) and `scale`
        ln(geomSd). A distribution with no spread is kept as a fixed amount, with a warning."""
        kind = text(given, "distributionType")
        if kind is None:
            return None
        fields: dict[str, Any]
        match kind:
            case "LOG_NORMAL_DISTRIBUTION":
                mean, sd = field(given, "geomMean"), field(given, "geomSd")
                spread = sd != 1
                fields = {
                    "distribution": "lognormal",
                    "loc": math.log(mean) if number(mean) and mean > 0 else None,
                    "scale": math.log(sd) if number(sd) and sd > 0 else None,
                }
            case "NORMAL_DISTRIBUTION":
                spread = field(given, "sd") != 0
                fields = {
                    "distribution": "normal",
                    "loc": field(given, "mean"),
                    "scale": field(given, "sd"),
                }
            case "UNIFORM_DISTRIBUTION":
                spread = True
                fields = {
                    "distribution": "uniform",
                    "minimum": field(given, "minimum"),
                    "maximum": field(given, "maximum"),
                }
            case "TRIANGLE_DISTRIBUTION":
                spread = True
                fields = {
                    "distribution": "triangular",
                    "loc": field(given, "mode"),
                    "minimum": field(given, "minimum"),
                    "maximum": field(given, "maximum"),
                }
            case _:
                self._fail("bad-uncertainty", label, f"has the distribution {kind!r}", where)
                return None
        if not spread:
            message = (
                f"has a {fields['distribution']} distribution with no spread: kept as a fixed "
                "amount"
            )
            self._warn("zero-variance", label, message, where)
            return None
        try:
            return Uncertainty.model_validate(fields).model_dump(mode="json")
        except ValidationError as error:
            message = "; ".join(e["msg"].removeprefix("Value error, ") for e in error.errors())
            self._fail("bad-uncertainty", label, f"{kind}: {message}", where)
            return None

    def _allocate(
        self,
        process_id: str,
        data: DataSet,
        products: dict[str, list[str]],
        others: set[str],
        where: str,
        count: int,
    ) -> None:
        """The factors of the process's default allocation method, one row per product, or per
        product and line for causal factors. Factors of other methods are not read."""
        method = text(data, "defaultAllocationMethod")
        name = ALLOCATION_METHODS.get(method or "")
        message = f"makes {count} products, and its default allocation method is {name or 'none'}"
        self._warn("allocation-method", f"process {process_id}", message, where)
        if name is None:
            return
        for factor in objs(data, "allocationFactors"):
            if text(factor, "allocationType") != method:
                continue
            product = ref_id(factor, "product") or ""
            lines = products.get(product, [])
            value = field(factor, "value")
            line = field(obj(factor, "exchange"), "internalId") if name == "causal" else None
            item = f"allocation of {process_id} product {product}"
            if not lines:
                message = "names a flow the process does not make"
            elif len(lines) > 1:
                message = f"names a flow the process makes on {len(lines)} lines"
            elif not number(value):
                message = f"has the value {value!r}, not a number"
            elif line is not None and str(line) not in others:
                message = (
                    f"is for exchange {line!r}, which is not a line of the process but a product"
                )
            else:
                message = None
            if message is not None:
                self._fail("bad-allocation", item, message, where)
                continue
            self.allocation.append(
                {
                    "process_id": process_id,
                    "product": lines[0],
                    "line_id": str(line) if line is not None else None,
                    "factor": float(value),
                }
            )


def _role(kind: FlowKind, is_input: bool, avoided: bool) -> Role | None:
    """The role of an exchange, or None for one that has none. openLCA stores an avoided product
    as an input, though it shows it as an output."""
    match kind:
        case FlowKind.elementary:
            return None if avoided else Role.resource if is_input else Role.emission
        case FlowKind.product:
            if avoided:
                return Role.avoided_product if is_input else None
            return Role.input if is_input else Role.reference
        case FlowKind.waste:
            if avoided:
                return None
            return Role.reference if is_input else Role.waste_to_treatment
