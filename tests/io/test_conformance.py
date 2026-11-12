"""Every registered format reads the toy background and the toy method to the same numbers.

The reference is the toy background folder, `tests/fixtures/toy/background`, with the method
`tests/fixtures/toy/methods/toy-climate`: fabric 3.7086, electricity 0.5298, incineration 1.2 kg
CO2-eq per unit. Each format has the toy written in it under `tests/fixtures/formats/<format>/`:

- an inventory format: `toy/`, version 1.0, and `toy-climate-flows.csv`, the flow mapping from
  the toy's flow ids to its own, so toy-climate scores it;
- a method format: `toy-climate/`, the method named `toy climate v1`, written in the flow list
  of one of the inventory fixtures. A format that holds both in one package, as JSON-LD does,
  reads `toy/` twice.

Ids differ by format, so processes are matched by name and location, flows by name and kind,
and lines by process, role and flow. Then the suite compares processes, flows, A, B and the
scores. A format skips exactly the aspects it lists in `cannot_carry`.

Adding a format: its line in `FORMATS` and its fixtures here.
"""

import csv
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import openpyxl
import pytest

import headwater as hw
from headwater.io.formats import formats, inventory_from, method_from
from headwater.io.method import map_method, write_method
from headwater.io.source import (
    Aspect,
    FactorRow,
    Format,
    InventoryOptions,
    MethodOptions,
    MethodSource,
    SourceKind,
)
from headwater.link import FlowKey, read_flow_mapping
from headwater.model import FlowKind, Uncertainty
from headwater.query._internals import internals
from headwater.release import Release, ReleaseInfo, build
from headwater.study import read_method, read_process_folder

TESTS = Path(__file__).resolve().parents[1]
FORMATS_DIR = TESTS / "fixtures" / "formats"
TOY = TESTS / "fixtures" / "toy"
TOY_METHOD = TOY / "methods" / "toy-climate"

OPTIONS = InventoryOptions(version="1.0", workers=1)
FORMAT_OPTIONS = {
    "olca-jsonld": InventoryOptions(version="1.0", source="toy", licence="CC0-1.0"),
    "ecospold1": InventoryOptions(version="1.0", source="toy", licence="CC0-1.0", workers=1),
    "simapro-csv": InventoryOptions(version="1.0", source="toy", licence="CC0-1.0"),
}
"""For a format whose files do not name the source or its licence: the options that do."""
METHOD = MethodOptions(method="toy climate v1")

INVENTORY_FORMATS = formats(SourceKind.inventory)
METHOD_FORMATS = formats(SourceKind.method)


def _workbook(folder: Path, tmp_path: Path) -> Path:
    """The ecoinvent-lcia fixture is CSV sheets, so it can be reviewed: written to an .xlsx."""
    book = openpyxl.Workbook()
    book.remove(book.worksheets[0])
    for title, name in (("Read me", "read_me"), ("CFs", "cfs"), ("Indicators", "indicators")):
        sheet = book.create_sheet(title)
        with (folder / f"{name}.csv").open(encoding="utf-8", newline="") as file:
            for row in csv.reader(file):
                sheet.append([_cell(text) for text in row])
    path = tmp_path / "toy-climate.xlsx"
    book.save(path)
    return path


def _cell(text: str) -> str | int | float | None:
    """A number as a number, whole numbers as int cells, as Excel writes them."""
    if not text:
        return None
    for kind in (int, float):
        try:
            return kind(text)
        except ValueError:
            pass
    return text


def _olca_package(folder: Path, tmp_path: Path) -> Path:
    """A JSON-LD package holds the inventory and its methods together, so the toy and
    toy-climate are one package, `toy/`, read once for each. Zipped, as openLCA exports it."""
    path = tmp_path / "toy.zip"
    tmp_path.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        with zipfile.ZipFile(path, "w") as package:
            for file in sorted((folder.parent / "toy").rglob("*.json")):
                package.write(file, file.relative_to(folder.parent / "toy").as_posix())
    return path


def _only_csv(folder: Path, tmp_path: Path) -> Path:
    """A format read from one file keeps it alone in its fixture folder."""
    [path] = folder.glob("*.csv")
    return path


PREPARE: dict[str, Callable[[Path, Path], Path]] = {
    "ecoinvent-lcia": _workbook,
    "olca-jsonld": _olca_package,
    "simapro-csv": _only_csv,
}
"""For a fixture kept in a reviewable form: how to write it as the format's file."""


def _fixture(chosen: Format, kind: str, tmp_path: Path) -> Path:
    folder = FORMATS_DIR / chosen.name / kind
    prepare = PREPARE.get(chosen.name)
    return prepare(folder, tmp_path) if prepare is not None else folder


def _import(chosen: Format, tmp_path: Path) -> Release:
    path = _fixture(chosen, "toy", tmp_path)
    options = FORMAT_OPTIONS.get(chosen.name, OPTIONS)
    return inventory_from(path, options, format=chosen.name).freeze(into=tmp_path / "releases")


def _toy_climate(release: Release, chosen: Format, tmp_path: Path) -> Path:
    """toy-climate, which names its flows by the toy's ids, mapped onto a format's release by
    the fixture's reviewed flow mapping, as a method folder."""
    toy = read_method(TOY_METHOD)
    source = MethodSource(
        name="toy-climate",
        id=toy.id,
        version=toy.version,
        variant=None,
        indicators=toy.indicators,
        factors=tuple(FactorRow(f.indicator, FlowKey(id=f.flow_id), f.factor) for f in toy.factors),
        flow_list=toy.flow_list,
        origin="The toy method.",
        keyed_by="id",
    )
    flows = read_flow_mapping(FORMATS_DIR / chosen.name / "toy-climate-flows.csv")
    mapped = map_method(source, release, flows=flows)
    assert [r.mapped for r in mapped.indicators] == [2]
    assert mapped.warnings == ()
    return write_method(mapped, tmp_path / "toy-climate")


@pytest.fixture(scope="module")
def reference(tmp_path_factory: pytest.TempPathFactory) -> hw.Background:
    folder = read_process_folder(TOY / "background", namespace="toy")
    info = ReleaseInfo(
        name="toy-background", source="toy", version="1", licence="CC0-1.0", flow_list="toy"
    )
    release = build(
        info,
        processes=folder.processes,
        flows=folder.flows,
        into=tmp_path_factory.mktemp("reference"),
    )
    return hw.load(release.path, method=TOY_METHOD)


class Renamed:
    """The ids of a format's release, by the toy's ids: processes by name and location, flows
    by name and kind."""

    def __init__(self, toy: Release, other: Release) -> None:
        def processes(r: Release) -> dict[tuple[str, str], str]:
            return {(p.name, p.location): p.id for p in r.processes()}

        def flows(r: Release) -> dict[tuple[str, FlowKind], str]:
            return {(f.name, f.kind): f.id for f in r.flows()}

        theirs_p, theirs_f = processes(other), flows(other)
        assert set(processes(toy)) == set(theirs_p)
        assert set(flows(toy)) == set(theirs_f)
        self.process = {pid: theirs_p[key] for key, pid in processes(toy).items()}
        self.flow = {fid: theirs_f[key] for key, fid in flows(toy).items()}


def _uncertainty(release: Release) -> dict[tuple[str, str, str], Uncertainty | None]:
    """Each line's distribution, by process, role and flow."""
    return {
        (p.id, e.role.value, e.flow_id): e.uncertainty
        for p in release.processes()
        for e in p.exchanges
    }


def _dense(background: hw.Background, rows: list[str], columns: list[str], matrix: str) -> Any:
    block = internals(background).solved.block
    row_ids = block.ids if matrix == "A" else block.flows
    found = getattr(block, matrix).toarray()
    r = [row_ids.index(i) for i in rows]
    c = [block.ids.index(i) for i in columns]
    return found[np.ix_(r, c)]


@pytest.mark.parametrize("chosen", INVENTORY_FORMATS, ids=lambda f: f.name)
def test_the_toy_imports_to_the_same_processes_flows_matrices_and_scores(
    chosen: Format, reference: hw.Background, tmp_path: Path
) -> None:
    release = _import(chosen, tmp_path)
    other = hw.load(release.path, method=_toy_climate(release, chosen, tmp_path))
    toy = reference.release
    assert toy is not None
    ids = Renamed(toy, release)
    skip = chosen.cannot_carry

    toy_processes = {p.id: p for p in toy.processes()}
    their_processes = {p.id: p for p in release.processes()}
    for toy_id, their_id in ids.process.items():
        if Aspect.process_kind not in skip:
            assert toy_processes[toy_id].kind == their_processes[their_id].kind, toy_id
    toy_flows, their_flows = {f.id: f for f in toy.flows()}, {f.id: f for f in release.flows()}
    for toy_id, their_id in ids.flow.items():
        assert toy_flows[toy_id].unit == their_flows[their_id].unit, toy_id
        if Aspect.cas not in skip:
            assert toy_flows[toy_id].cas == their_flows[their_id].cas, toy_id
    if Aspect.uncertainty not in skip:
        theirs = _uncertainty(release)
        for (process, role, flow), expected in _uncertainty(toy).items():
            found = theirs[ids.process[process], role, ids.flow[flow]]
            assert found == expected, (process, role, flow)

    processes = sorted(ids.process)
    renamed = [ids.process[p] for p in processes]
    elementary = sorted(f for f in ids.flow if toy_flows[f].kind is FlowKind.elementary)
    if Aspect.supplier_links not in skip:
        np.testing.assert_allclose(
            _dense(other, renamed, renamed, "A"), _dense(reference, processes, processes, "A")
        )
        np.testing.assert_allclose(
            _dense(other, [ids.flow[f] for f in elementary], renamed, "B"),
            _dense(reference, elementary, processes, "B"),
        )
    [indicator] = [i.id for i in reference.method.indicators]
    toy_x, their_x = (
        internals(reference).solved.x[indicator].values,
        internals(other).solved.x[indicator].values,
    )
    for toy_id, their_id in ids.process.items():
        expected = toy_x[internals(reference).index[toy_id]]
        assert their_x[internals(other).index[their_id]] == pytest.approx(expected, rel=1e-9), (
            toy_id
        )


@pytest.mark.parametrize("chosen", METHOD_FORMATS, ids=lambda f: f.name)
def test_the_toy_method_maps_to_the_same_factors(chosen: Format, tmp_path: Path) -> None:
    path = _fixture(chosen, "toy-climate", tmp_path)
    assert chosen.read_method is not None
    flow_list = chosen.read_method(path, METHOD).flow_list
    releases = [_import(f, tmp_path / f.name) for f in INVENTORY_FORMATS]
    onto = [r for r in releases if r.info.flow_list == flow_list]
    assert onto, f"no inventory fixture has the flow list {flow_list}"
    names = {f.id: (f.name, f.kind) for f in onto[0].flows()}
    toy = read_method(TOY_METHOD)
    toy_flows = {
        f.id: (f.name, f.kind)
        for f in read_process_folder(TOY / "background", namespace="toy").flows
    }

    method = method_from(path, onto[0], METHOD, format=chosen.name).method

    naming = {"name", "category_indicator"}  # only ecoinvent's workbook gives the second
    assert [i.model_dump(exclude=naming) for i in method.indicators] == [
        i.model_dump(exclude=naming) for i in toy.indicators
    ]
    assert sorted((f.indicator, names[f.flow_id], f.factor) for f in method.factors) == sorted(
        (f.indicator, toy_flows[f.flow_id], f.factor) for f in toy.factors
    )


def test_every_format_has_its_toy_fixtures(tmp_path: Path) -> None:
    for chosen in INVENTORY_FORMATS:
        assert chosen.detect(_fixture(chosen, "toy", tmp_path)), chosen.name
        assert (FORMATS_DIR / chosen.name / "toy-climate-flows.csv").is_file(), chosen.name
    for chosen in METHOD_FORMATS:
        assert chosen.detect(_fixture(chosen, "toy-climate", tmp_path)), chosen.name
