"""Lines of the toy background as a reader gives them, before linking.

The fabric process (`p:fabric`) buys 3 kWh of electricity and emits CO2; electricity
(`p:electricity`) makes kWh and emits CO2. A test gives each buying line its supplier key.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pytest

from headwater.io.source import LINES
from headwater.release import Release, ReleaseInfo, build
from headwater.study import read_process_folder

TOY = Path(__file__).resolve().parents[1] / "fixtures" / "toy" / "background"

WHEN = datetime(2026, 10, 24, tzinfo=UTC)


class Lines:
    """Makes `LINES` tables, and reads columns back."""

    when = WHEN

    @staticmethod
    def line(
        process_id: str, line_id: str, role: str, flow_id: str, **fields: Any
    ) -> dict[str, Any]:
        return {
            "process_id": process_id,
            "line_id": line_id,
            "role": role,
            "flow_id": flow_id,
            "amount": 1.0,
            "unit": "kg",
            "properties": [],
            **fields,
        }

    @staticmethod
    def table(*rows: dict[str, Any]) -> pa.Table:
        return pa.Table.from_pylist(list(rows), schema=LINES.schema)

    @staticmethod
    def columns(table: pa.Table, *names: str) -> list[tuple[Any, ...]]:
        """The rows of `table`, as tuples of these columns."""
        return list(zip(*(table.column(n).to_pylist() for n in names), strict=True))


@pytest.fixture(scope="session")
def lx() -> type[Lines]:
    return Lines


@pytest.fixture
def csv_file(tmp_path: Path) -> Callable[[str], Path]:
    def write(text: str) -> Path:
        path = tmp_path / "mapping.csv"
        path.write_text(text, encoding="utf-8")
        return path

    return write


@pytest.fixture
def toy_release(tmp_path: Path) -> Release:
    """The toy background folder, frozen."""
    folder = read_process_folder(TOY, namespace="toy")
    info = ReleaseInfo(
        name="toy-background", source="toy", version="1", licence="CC0-1.0", flow_list="toy"
    )
    return build(info, processes=folder.processes, flows=folder.flows, into=tmp_path)
