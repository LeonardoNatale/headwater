"""The toy background as release records, with one exchange that carries every optional field.

Line `toy:fabric-production e02` (3 kWh of electricity) gets uncertainty, a pedigree, two
properties and the link status `linked`. Line `e05` (dye) is cut off, so it has no supplier.
Both have a resolved issue. The fabric process gets a time period, 2024.
"""

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import pyarrow as pa
import pytest
from numpy.typing import NDArray

from headwater.model import (
    DEFAULT_UNITS,
    CutOffCriterion,
    Exchange,
    Flow,
    FlowKind,
    FlowProperty,
    FlowPropertyName,
    Issue,
    IssueStatus,
    Method,
    Process,
    Units,
)
from headwater.release import Release, ReleaseInfo, build, build_tables
from headwater.release.schema import (
    exchanges_table,
    flow_properties_table,
    flows_table,
    issues_table,
    processes_table,
)
from headwater.study import read_method, read_process_folder

TOY_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "toy"

INFO = ReleaseInfo(
    name="toy-background",
    source="toy",
    version="1",
    system_model="cutoff",
    licence="CC0-1.0",
    flow_list="toy",
)

RESOLVED_AT = datetime(2026, 10, 11, 9, 30, tzinfo=UTC)

TEST_LINE = {
    "uncertainty": {"distribution": "lognormal", "loc": 1.0986122886681098, "scale": 0.0953},
    "pedigree": {
        "reliability": 2,
        "completeness": 3,
        "temporal_correlation": 1,
        "geographical_correlation": 2,
        "further_technological_correlation": 4,
    },
    "properties": [
        {"name": "price", "value": 0.11, "unit": "EUR2005"},
        {"name": "heating value, net", "value": 3.6, "unit": "MJ"},
    ],
    "link_status": "linked",
}

DYE = Flow(id="toy:dye", name="indigo dye, C₁₆H₁₀N₂O₂", kind=FlowKind.product, unit="kg")

ETHER = Flow(id="toy:äther", name="Äther, unused", kind=FlowKind.product, unit="kg")
"""Not used by any line. Its id and name are not ASCII, so the hash covers UTF-8 byte order
and byte lengths."""

TOY_SCORES = {
    "toy:electricity-production": 0.5298,
    "toy:fabric-production": 3.7086,
    "toy:incineration-textile-waste": 1.2,
}
"""Hand-checked scores of the toy background for toy-climate, from docs/calculation.md."""


@dataclass(frozen=True)
class Records:
    processes: tuple[Process, ...]
    flows: tuple[Flow, ...]
    flow_properties: tuple[FlowProperty, ...]
    issues: tuple[Issue, ...]
    methods: tuple[Method, ...]
    units: Units = field(default=DEFAULT_UNITS)

    def build(self, into: Path, info: ReleaseInfo = INFO) -> Release:
        return build(
            info,
            processes=self.processes,
            flows=self.flows,
            flow_properties=self.flow_properties,
            units=self.units,
            issues=self.issues,
            methods=self.methods,
            into=into,
        )

    def with_line(self, process_id: str, line_id: str, **changes: object) -> "Records":
        """The same records, with one exchange changed. Validators run on the new exchange."""

        def change(e: Exchange) -> Exchange:
            if (e.process_id, e.line_id) != (process_id, line_id):
                return e
            return Exchange.model_validate({**e.model_dump(), **changes})

        processes = tuple(
            _with_exchanges(p, [change(e) for e in p.exchanges]) for p in self.processes
        )
        return replace(self, processes=processes)

    def with_new_line(self, process_id: str, line_id: str, **fields: object) -> "Records":
        """The same records with one more line in a process."""

        def add(p: Process) -> Process:
            if p.id != process_id:
                return p
            line = Exchange.model_validate({"process_id": process_id, "line_id": line_id, **fields})
            return _with_exchanges(p, [*p.exchanges, line])

        return replace(self, processes=tuple(add(p) for p in self.processes))

    def with_properties(self, *properties: FlowProperty) -> "Records":
        return replace(self, flow_properties=(*self.flow_properties, *properties))

    def tables(self) -> dict[str, pa.Table]:
        """The records as the Arrow tables `build_tables` takes, with each line's flow name."""
        return {
            "processes": processes_table(self.processes),
            "exchanges": exchanges_table(self.processes, flow_names=True),
            "flows": flows_table(self.flows),
            "flow_properties": flow_properties_table(self.flow_properties),
            "issues": issues_table(self.issues),
        }

    def build_tables(self, into: Path, info: ReleaseInfo = INFO, **tables: pa.Table) -> Release:
        """Freeze through the table path, with some tables replaced."""
        t = {**self.tables(), **tables}
        return build_tables(
            info,
            processes=t["processes"],
            exchanges=t["exchanges"],
            flows=t["flows"],
            flow_properties=t["flow_properties"],
            issues=t["issues"],
            units=self.units,
            methods=self.methods,
            into=into,
        )

    def reversed(self) -> "Records":
        """The same records in the opposite order, down to the lines of each process."""
        return replace(
            self,
            processes=tuple(
                _with_exchanges(p, list(reversed(p.exchanges))) for p in reversed(self.processes)
            ),
            flows=tuple(reversed(self.flows)),
            flow_properties=tuple(reversed(self.flow_properties)),
            issues=tuple(reversed(self.issues)),
        )


def _with_exchanges(process: Process, exchanges: list[Exchange]) -> Process:
    return Process.model_validate(
        {**process.model_dump(exclude={"exchanges"}), "exchanges": exchanges}
    )


def _enrich(process: Process) -> Process:
    if process.id != "toy:fabric-production":
        return process
    exchanges = [
        Exchange.model_validate({**e.model_dump(), **TEST_LINE}) if e.line_id == "e02" else e
        for e in process.exchanges
    ]
    dye = Exchange.model_validate(
        {
            "process_id": process.id,
            "line_id": "e05",
            "role": "input",
            "flow_id": DYE.id,
            "amount": 0.002,
            "unit": "kg",
            "link_status": "cut_off",
            "comment": "no dye dataset yet",
        }
    )
    dated = Process.model_validate(
        {**process.model_dump(), "time_start": date(2024, 1, 1), "time_end": date(2024, 12, 31)}
    )
    return _with_exchanges(dated, [*exchanges, dye])


def toy_records() -> Records:
    folder = read_process_folder(TOY_DIR / "background", namespace="toy")
    method = read_method(TOY_DIR / "methods" / "toy-climate")
    issues = (
        Issue(
            process_id="toy:fabric-production",
            line_id="e02",
            flow_id="toy:electricity",
            status=IssueStatus.linked,
            supplier_id="toy:electricity-production",
            resolved_by="toy importer",
            resolved_at=RESOLVED_AT,
        ),
        Issue(
            process_id="toy:fabric-production",
            line_id="e05",
            flow_id=DYE.id,
            status=IssueStatus.cut_off,
            reason="no dye dataset; below 1% of the mass",
            criterion=CutOffCriterion.mass,
            share=0.002,
            cumulative_share=0.002,
            resolved_by="toy importer",
            resolved_at=RESOLVED_AT,
        ),
    )
    properties = (
        FlowProperty(
            flow_id="toy:cotton-fabric", property=FlowPropertyName.dry_mass, value=0.92, unit="kg"
        ),
        FlowProperty(
            flow_id="toy:cotton-fabric",
            property=FlowPropertyName.carbon_content_biogenic,
            value=0.4,
            unit="kg",
        ),
    )
    return Records(
        processes=tuple(_enrich(p) for p in folder.processes),
        flows=(*folder.flows, DYE, ETHER),
        flow_properties=properties,
        issues=issues,
        methods=(method,),
    )


@pytest.fixture
def info() -> ReleaseInfo:
    return INFO


@pytest.fixture
def records() -> Records:
    return toy_records()


@pytest.fixture
def fresh_records() -> Callable[[], Records]:
    """Reads the toy folder again on every call, so two builds share no objects."""
    return toy_records


@pytest.fixture
def toy_release(records: Records, tmp_path: Path) -> Release:
    return records.build(tmp_path / "releases")


@pytest.fixture
def toy_method() -> Method:
    return read_method(TOY_DIR / "methods" / "toy-climate")


@pytest.fixture
def toy_scores() -> tuple[list[str], dict[str, NDArray[np.float64]]]:
    """Process ids, and the score of each for every indicator of toy-climate."""
    ids = sorted(TOY_SCORES)
    return ids, {"climate-change": np.array([TOY_SCORES[i] for i in ids])}


def _snapshot(path: Path) -> dict[str, bytes]:
    return {p.relative_to(path).as_posix(): p.read_bytes() for p in path.rglob("*") if p.is_file()}


@pytest.fixture
def snapshot() -> Callable[[Path], dict[str, bytes]]:
    """Every file under a folder, with its bytes. Equal snapshots mean nothing changed."""
    return _snapshot
