"""openLCA JSON-LD: the toy package, the roles of its lines, uncertainty, allocation and methods.

Tests copy `tests/fixtures/formats/olca-jsonld/toy`, the toy background and toy-climate as an
openLCA package (schema 2), and add data sets to it. Its ids are made up: process N is
`a0000000-0000-4000-8000-00000000000N` (1 electricity, 2 cotton fabric, 3 incineration), flows
`b...` are products and wastes (1 electricity, 2 cotton fabric, 3 textile waste), `e...`
elementary (1 CO2, 2 CH4). Units are kg, and kWh with MJ.
"""

import json
import math
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

import headwater as hw
from headwater.io.formats import inventory_from, method_from
from headwater.io.method import write_method
from headwater.io.olca_jsonld import read, read_method
from headwater.io.source import InventoryOptions, MethodOptions
from headwater.model import Distribution, FlowKind, InvalidDataError, Uncertainty
from headwater.query._internals import internals

TOY = Path(__file__).resolve().parents[1] / "fixtures" / "formats" / "olca-jsonld" / "toy"
OPTIONS = InventoryOptions(version="1.0", source="toy", licence="CC0-1.0")


def uid(prefix: str, n: int) -> str:
    return f"{prefix}0000000-0000-4000-8000-{n:012d}"


ELECTRICITY, FABRIC, WASTE = uid("b", 1), uid("b", 2), uid("b", 3)
CO2, CH4 = uid("e", 1), uid("e", 2)
P_ELECTRICITY, P_FABRIC, P_INCINERATION = uid("a", 1), uid("a", 2), uid("a", 3)
GLO = uid("1", 1)
MASS, ENERGY = uid("c", 1), uid("c", 2)
UNITS = {"kg": (uid("5", 1), MASS), "kWh": (uid("5", 2), ENERGY), "MJ": (uid("5", 3), ENERGY)}


def pid(uuid: str) -> str:
    return f"toy:1.0:{uuid}"


def fid(uuid: str) -> str:
    return f"olca:flow:{uuid}"


class Package:
    """A copy of the toy package to add data sets to."""

    def __init__(self, root: Path) -> None:
        shutil.copytree(TOY, root)
        self.root = root

    def put(self, folder: str, data: dict[str, Any]) -> None:
        path = self.root / folder / f"{data['@id']}.json"
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")

    def edit(self, folder: str, uuid: str) -> "Edited":
        """A data set to change in a `with` block, written back at its end."""
        return Edited(self.root / folder / f"{uuid}.json")

    def flow(self, uuid: str, name: str, kind: str = "PRODUCT_FLOW", unit: str = "kWh") -> str:
        prop = UNITS[unit][1]
        self.put(
            "flows",
            {
                "@type": "Flow",
                "@id": uuid,
                "name": name,
                "flowType": kind,
                "flowProperties": [
                    {"isRefFlowProperty": True, "conversionFactor": 1.0, "flowProperty": _ref(prop)}
                ],
            },
        )
        return uuid

    def process(self, uuid: str, name: str, exchanges: list[dict[str, Any]], **extra: Any) -> str:
        self.put(
            "processes",
            {
                "@type": "Process",
                "@id": uuid,
                "name": name,
                "processType": "UNIT_PROCESS",
                "location": _ref(GLO),
                "exchanges": exchanges,
                **extra,
            },
        )
        return uuid

    def read(self, options: InventoryOptions = OPTIONS):  # noqa: ANN201
        return read(self.root, options)


class Edited:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))

    def __enter__(self) -> dict[str, Any]:
        return self.data

    def __exit__(self, *_: object) -> None:
        self.path.write_text(json.dumps(self.data), encoding="utf-8")


def _ref(uuid: str, **fields: Any) -> dict[str, Any]:
    return {"@id": uuid, **fields}


def exchange(
    n: int,
    flow: str,
    amount: float,
    unit: str = "kWh",
    *,
    is_input: bool = False,
    reference: bool = False,
    provider: str | None = None,
    **fields: Any,
) -> dict[str, Any]:
    unit_id, prop = UNITS[unit]
    found: dict[str, Any] = {
        "@type": "Exchange",
        "internalId": n,
        "amount": amount,
        "isInput": is_input,
        "isQuantitativeReference": reference,
        "flow": _ref(flow),
        "unit": _ref(unit_id, name=unit),
        "flowProperty": _ref(prop),
        **fields,
    }
    if provider is not None:
        found["defaultProvider"] = _ref(provider)
    return found


@pytest.fixture
def package(tmp_path: Path) -> Package:
    return Package(tmp_path / "package")


def _lines(source: Any, process: str) -> dict[str, dict[str, Any]]:
    return {r["line_id"]: r for r in source.lines.to_pylist() if r["process_id"] == pid(process)}


def _codes(raised: pytest.ExceptionInfo[InvalidDataError]) -> list[str]:
    return [p.code for p in raised.value.problems]


# --- the toy ---------------------------------------------------------------------------------


def test_the_toy_reads_with_the_packages_ids() -> None:
    source = read(TOY, OPTIONS)

    info = source.info
    assert (info.name, info.source, info.version, info.licence, info.flow_list) == (
        "toy-1.0",
        "toy",
        "1.0",
        "CC0-1.0",
        "olca",
    )
    processes = {r["id"]: r for r in source.processes.to_pylist()}
    assert processes[pid(P_FABRIC)] == {
        "id": pid(P_FABRIC),
        "name": "cotton fabric production",
        "location": "GLO",
        "time_start": None,
        "time_end": None,
        "kind": "ordinary_transforming_activity",
        "source_ref": f"processes/{P_FABRIC}.json",
    }
    fabric = _lines(source, P_FABRIC)
    assert {k: (r["role"], r["flow_id"], r["amount"], r["unit"]) for k, r in fabric.items()} == {
        "1": ("reference", fid(FABRIC), 1.0, "kg"),
        "2": ("input", fid(ELECTRICITY), 3.0, "kWh"),
        "3": ("emission", fid(CO2), 2.0, "kg"),
        "4": ("emission", fid(CH4), 0.004, "kg"),
    }
    assert fabric["2"]["supplier_id"] == pid(P_ELECTRICITY)
    assert fabric["2"]["flow_name"] == "electricity"
    assert _lines(source, P_INCINERATION)["1"]["role"] == "reference"


def test_flows_units_and_places_come_from_the_package() -> None:
    source = read(TOY, OPTIONS)

    flows = {f.id: f for f in source.flows}
    co2 = flows[fid(CO2)]
    assert (co2.name, co2.kind, co2.unit, co2.cas) == (
        "Carbon dioxide, fossil",
        FlowKind.elementary,
        "kg",
        "124-38-9",
    )
    assert co2.compartment == "Elementary flows/Emission to air/unspecified"
    assert (flows[fid(WASTE)].kind, flows[fid(ELECTRICITY)].unit) == (FlowKind.waste, "kWh")
    assert source.units.convert(3.6, "MJ", "kWh") == pytest.approx(1.0)
    assert source.geographies.to_pylist() == [{"code": "GLO", "name": "Global"}]


@pytest.mark.parametrize("missing", ["version", "source", "licence"])
def test_version_source_and_licence_are_required(missing: str) -> None:
    options = InventoryOptions(**{**vars(OPTIONS), missing: None})

    with pytest.raises(InvalidDataError) as raised:
        read(TOY, options)

    [problem] = raised.value.problems
    assert (problem.code, problem.item) == ("missing-option", f"--{missing}")


def test_a_zip_reads_as_the_folder_does(
    tmp_path: Path, zip_package: Callable[[Path, Path], Path]
) -> None:
    path = zip_package(TOY, tmp_path / "toy.zolca")

    zipped, folder = read(path, OPTIONS), read(TOY, OPTIONS)

    assert zipped.lines.equals(folder.lines)
    assert zipped.processes.equals(folder.processes)
    assert zipped.flows == folder.flows


def test_an_openlca_database_backup_is_refused_with_what_to_do(
    tmp_path: Path, database_backup: Callable[[Path], Path]
) -> None:
    path = database_backup(tmp_path / "BAFU.zolca")

    with pytest.raises(InvalidDataError) as raised:
        read(path, OPTIONS)

    [problem] = raised.value.problems
    assert problem.code == "database-backup"
    assert "export it as JSON-LD" in problem.message


@pytest.mark.parametrize("schema", ['{"version": 1}', None])
def test_a_package_of_another_schema_is_refused(package: Package, schema: str | None) -> None:
    marker = package.root / "olca-schema.json"
    if schema is None:
        marker.unlink()
    else:
        marker.write_text(schema, encoding="utf-8")

    with pytest.raises(InvalidDataError) as raised:
        package.read()

    assert _codes(raised) == ["unsupported-schema"]


# --- roles -----------------------------------------------------------------------------------


def test_every_kind_of_line_gets_its_role(package: Package) -> None:
    heat = package.flow(uid("b", 4), "heat")
    mill = package.process(
        uid("a", 4),
        "mill",
        [
            exchange(1, FABRIC, 1.0, "kg", reference=True),
            exchange(2, ELECTRICITY, 2.0, is_input=True, provider=P_ELECTRICITY),
            exchange(3, FABRIC, 0.1, "kg", is_input=True, provider=uid("a", 4)),
            exchange(4, WASTE, 0.2, "kg", provider=P_INCINERATION),
            exchange(5, heat, 0.5, is_input=True, isAvoidedProduct=True, provider=P_ELECTRICITY),
            exchange(6, CO2, 0.3, "kg"),
            exchange(7, CO2, 0.1, "kg", is_input=True),
            exchange(8, ELECTRICITY, 1.0, is_input=True),
        ],
    )

    lines = _lines(package.read(), mill)

    assert {k: (r["role"], r["supplier_id"]) for k, r in lines.items()} == {
        "1": ("reference", None),
        "2": ("input", pid(P_ELECTRICITY)),
        "3": ("own_use", pid(mill)),
        "4": ("waste_to_treatment", pid(P_INCINERATION)),
        "5": ("avoided_product", pid(P_ELECTRICITY)),
        "6": ("emission", None),
        "7": ("resource", None),
        "8": ("input", None),
    }


def test_an_avoided_waste_has_no_role(package: Package) -> None:
    package.process(
        uid("a", 4),
        "mill",
        [
            exchange(1, FABRIC, 1.0, "kg", reference=True),
            exchange(2, WASTE, 0.2, "kg", isAvoidedProduct=True),
        ],
    )

    with pytest.raises(InvalidDataError) as raised:
        package.read()

    [problem] = raised.value.problems
    assert (problem.code, problem.item) == ("unsupported-line", f"{pid(uid('a', 4))} line 2")


def test_a_line_in_another_flow_property_than_its_flows_is_refused(package: Package) -> None:
    line = exchange(1, FABRIC, 1.0, "kg", reference=True)
    line["flowProperty"] = _ref(ENERGY)
    package.process(uid("a", 4), "mill", [line])

    with pytest.raises(InvalidDataError) as raised:
        package.read()

    assert _codes(raised) == ["other-flow-property"]


# --- uncertainty -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        (
            {"distributionType": "LOG_NORMAL_DISTRIBUTION", "geomMean": 2.0, "geomSd": 1.5},
            Uncertainty(
                distribution=Distribution.lognormal, loc=math.log(2.0), scale=math.log(1.5)
            ),
        ),
        (
            {"distributionType": "NORMAL_DISTRIBUTION", "mean": 2.0, "sd": 0.1},
            Uncertainty(distribution=Distribution.normal, loc=2.0, scale=0.1),
        ),
        (
            {"distributionType": "UNIFORM_DISTRIBUTION", "minimum": 1.0, "maximum": 3.0},
            Uncertainty(distribution=Distribution.uniform, minimum=1.0, maximum=3.0),
        ),
        (
            {"distributionType": "TRIANGLE_DISTRIBUTION", "minimum": 1, "mode": 2, "maximum": 3},
            Uncertainty(distribution=Distribution.triangular, loc=2.0, minimum=1.0, maximum=3.0),
        ),
    ],
    ids=["lognormal", "normal", "uniform", "triangle"],
)
def test_each_distribution_maps_to_its_own(
    package: Package, given: dict[str, Any], expected: Uncertainty
) -> None:
    lines = [
        exchange(1, FABRIC, 1.0, "kg", reference=True),
        exchange(2, CO2, 2.0, "kg", uncertainty=given),
    ]
    package.process(uid("a", 4), "mill", lines)

    found = _lines(package.read(), uid("a", 4))["2"]["uncertainty"]

    assert Uncertainty.model_validate(found) == expected


def test_a_distribution_with_no_spread_is_a_fixed_amount_with_a_warning(package: Package) -> None:
    flat = {"distributionType": "LOG_NORMAL_DISTRIBUTION", "geomMean": 2.0, "geomSd": 1.0}
    lines = [
        exchange(1, FABRIC, 1.0, "kg", reference=True),
        exchange(2, CO2, 2.0, "kg", uncertainty=flat),
    ]
    package.process(uid("a", 4), "mill", lines)

    source = package.read()

    assert _lines(source, uid("a", 4))["2"]["uncertainty"] is None
    assert [w.code for w in source.warnings] == ["zero-variance"]


# --- allocation ------------------------------------------------------------------------------


def _chp(package: Package, method: str, *, causal: bool = False) -> str:
    """A CHP plant: 1 kWh of electricity (line 1) and 3 kWh of heat (line 2) from burning gas
    that emits 2 kg of CO2 (line 3), using 0.05 kWh of its own electricity (line 4). Physical
    factors 0.6 and 0.4, economic 0.5 and 0.5, and causal 0.7 and 0.3 for the CO2 and 0.6 and
    0.4 for the own use."""
    heat = package.flow(uid("b", 4), "heat")
    chp = uid("a", 4)

    def factor(kind: str, product: str, value: float, line: int | None = None) -> dict[str, Any]:
        row: dict[str, Any] = {"allocationType": kind, "product": _ref(product), "value": value}
        if line is not None:
            row["exchange"] = {"internalId": line}
        return row

    factors = [
        factor("PHYSICAL_ALLOCATION", ELECTRICITY, 0.6),
        factor("PHYSICAL_ALLOCATION", heat, 0.4),
        factor("ECONOMIC_ALLOCATION", ELECTRICITY, 0.5),
        factor("ECONOMIC_ALLOCATION", heat, 0.5),
    ]
    if causal:
        factors += [
            factor("CAUSAL_ALLOCATION", ELECTRICITY, 0.7, 3),
            factor("CAUSAL_ALLOCATION", heat, 0.3, 3),
            factor("CAUSAL_ALLOCATION", ELECTRICITY, 0.6, 4),
            factor("CAUSAL_ALLOCATION", heat, 0.4, 4),
        ]
    package.process(
        chp,
        "heat and power co-generation",
        [
            exchange(1, ELECTRICITY, 1.0, reference=True),
            exchange(2, heat, 3.0),
            exchange(3, CO2, 2.0, "kg"),
            exchange(4, ELECTRICITY, 0.05, is_input=True, provider=chp),
        ],
        defaultAllocationMethod=method,
        allocationFactors=factors,
    )
    return chp


def test_allocation_uses_the_factors_of_the_default_method(package: Package) -> None:
    chp = _chp(package, "PHYSICAL_ALLOCATION")

    source = package.read()

    assert source.allocation.to_pylist() == [
        {"process_id": pid(chp), "product": "1", "line_id": None, "factor": 0.6},
        {"process_id": pid(chp), "product": "2", "line_id": None, "factor": 0.4},
    ]
    [warning] = source.warnings
    assert (warning.code, warning.item) == ("allocation-method", f"process {pid(chp)}")
    assert "physical" in warning.message


def test_causal_factors_hold_for_their_line_and_split_the_process(package: Package) -> None:
    chp = _chp(package, "CAUSAL_ALLOCATION", causal=True)

    rows = package.read().allocation.to_pylist()
    split = inventory_from(package.root, OPTIONS)

    assert rows == [
        {"process_id": pid(chp), "product": "1", "line_id": "3", "factor": 0.7},
        {"process_id": pid(chp), "product": "2", "line_id": "3", "factor": 0.3},
        {"process_id": pid(chp), "product": "1", "line_id": "4", "factor": 0.6},
        {"process_id": pid(chp), "product": "2", "line_id": "4", "factor": 0.4},
    ]
    assert split.problems == ()
    amounts = {
        (r["process_id"], r["line_id"]): r["amount"] for r in split.exchange_table.to_pylist()
    }
    assert amounts[f"{pid(chp)}:1", "3"] == pytest.approx(1.4)
    assert amounts[f"{pid(chp)}:2", "4"] == pytest.approx(0.02)


def test_a_process_with_no_allocation_method_gives_no_factors(package: Package) -> None:
    _chp(package, "NO_ALLOCATION")

    assert package.read().allocation.num_rows == 0


def test_own_use_in_a_split_chp_gives_hand_checked_scores(package: Package, tmp_path: Path) -> None:
    """The electricity split keeps 0.6 x 0.05 = 0.03 kWh as its own use, so it scores
    1.2 / 0.97 kg CO2 per kWh. The heat split buys 0.4 x 0.05 = 0.02 kWh from it, so it scores
    (0.8 + 0.02 x 1.2 / 0.97) / 3 per kWh. Together 0.95 kWh of electricity and 3 kWh of heat
    carry the 2 kg."""
    chp = _chp(package, "PHYSICAL_ALLOCATION")
    release = inventory_from(package.root, OPTIONS).freeze(into=tmp_path / "releases")
    method = method_from(package.root, release, MethodOptions(method="toy climate v1"))
    background = hw.load(release.path, method=write_method(method, tmp_path / "toy-climate"))

    scores = internals(background).solved.x["climate-change"].values
    electricity = scores[internals(background).index[f"{pid(chp)}:1"]]
    heat = scores[internals(background).index[f"{pid(chp)}:2"]]

    assert electricity == pytest.approx(1.2 / 0.97, rel=1e-12)
    assert electricity == pytest.approx(1.2371134020618557, rel=1e-12)
    assert heat == pytest.approx(0.27491408934707906, rel=1e-12)
    assert 0.95 * electricity + 3 * heat == pytest.approx(2.0, rel=1e-12)


# --- methods ---------------------------------------------------------------------------------


def test_a_method_of_the_package_reads_with_factors_by_flow_id() -> None:
    method = read_method(TOY, MethodOptions(method="toy climate v1"))

    assert (method.name, method.id, method.version, method.flow_list) == (
        "toy climate v1",
        "toy-climate-1",
        "1",
        "olca",
    )
    assert [(i.id, i.name, i.unit) for i in method.indicators] == [
        ("climate-change", "Climate change", "kg CO2-eq")
    ]
    assert [(f.indicator, f.flow.id, f.factor, f.unit) for f in method.factors] == [
        ("climate-change", fid(CO2), 1.0, "kg"),
        ("climate-change", fid(CH4), 29.8, "kg"),
    ]


def test_the_only_method_of_a_package_needs_no_name() -> None:
    assert read_method(TOY, MethodOptions()).name == "toy climate v1"


def test_a_method_the_package_does_not_hold_is_refused_naming_those_it_does() -> None:
    with pytest.raises(InvalidDataError) as raised:
        read_method(TOY, MethodOptions(method="EF 3.1"))

    [problem] = raised.value.problems
    assert problem.code == "unknown-method"
    assert "toy climate v1" in problem.message


def test_categories_can_be_chosen_by_name(package: Package) -> None:
    package.put(
        "lcia_categories",
        {"@type": "ImpactCategory", "@id": uid("7", 3), "name": "Ozone", "refUnit": "kg CFC-11"},
    )
    with package.edit("lcia_methods", uid("7", 1)) as method:
        method["impactCategories"].append(_ref(uid("7", 3), name="Ozone"))

    both = read_method(package.root, MethodOptions())
    one = read_method(package.root, MethodOptions(categories=("Climate change",)))

    assert [i.id for i in both.indicators] == ["climate-change", "ozone"]
    assert ([i.id for i in one.indicators], one.left_out) == (["climate-change"], ("Ozone",))


def test_exchange_data_the_release_does_not_keep_is_counted_in_a_warning(
    package: Package,
) -> None:
    lines = [
        exchange(1, FABRIC, 1.0, "kg", reference=True, costValue=2.5),
        exchange(2, CO2, 2.0, "kg", dqEntry="(1;2;3;4;5)"),
        exchange(3, CH4, 0.1, "kg", dqEntry="(1;2;3;4;5)"),
    ]
    package.process(uid("a", 4), "mill", lines)

    warnings = [w for w in package.read().warnings if w.code == "not-kept"]

    assert [w.message.split(" (")[0] for w in warnings] == [
        "1 exchange has a cost",
        "2 exchanges have a data quality entry",
    ]


def test_a_causal_factor_for_a_product_line_is_refused(package: Package) -> None:
    chp = _chp(package, "CAUSAL_ALLOCATION", causal=True)
    with package.edit("processes", chp) as process:
        process["allocationFactors"][-1]["exchange"] = {"internalId": 1}

    with pytest.raises(InvalidDataError) as raised:
        package.read()

    [problem] = raised.value.problems
    assert (problem.code, problem.location) == ("bad-allocation", f"processes/{chp}.json")


def test_a_process_without_a_name_is_refused(package: Package) -> None:
    package.process(uid("a", 4), "", [exchange(1, FABRIC, 1.0, "kg", reference=True)])

    with pytest.raises(InvalidDataError) as raised:
        package.read()

    assert _codes(raised) == ["bad-process"]


def test_a_factor_for_a_location_not_in_the_package_is_refused(package: Package) -> None:
    with package.edit("lcia_categories", uid("7", 2)) as category:
        category["impactFactors"][0]["location"] = _ref(uid("1", 9))

    with pytest.raises(InvalidDataError) as raised:
        read_method(package.root, MethodOptions())

    assert _codes(raised) == ["bad-location"]
