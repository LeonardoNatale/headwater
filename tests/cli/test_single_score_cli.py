"""`--set` on the study commands, and `headwater method import-set`, which writes a set folder
beside its method from a CSV a person writes."""

import json
from collections.abc import Callable
from pathlib import Path

import pytest

import headwater as hw
from tests.conftest import open_database

Invoke = Callable[..., tuple[int, str]]
Git = Callable[..., str]
Commit = Callable[[str], str]

TOY = Path(__file__).resolve().parent.parent / "fixtures" / "toy"
METHOD = TOY / "methods" / "toy-impacts"
SET = TOY / "methods" / "toy-nw"
SINGLE = "toy-nw:single-score"


@pytest.fixture
def args() -> list[str]:
    return [
        "--release",
        str(TOY / "background"),
        "--method",
        str(METHOD),
        "--namespace",
        "toy",
        "--set",
        str(SET),
    ]


def edit(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    assert old in text, f"{old!r} not found in {path}"
    path.write_text(text.replace(old, new, 1))


def on_impacts(folder: Path) -> Path:
    """The study in `folder`, on toy-impacts instead of toy-climate."""
    edit(folder / "database.yaml", "methods: [toy-climate]", "methods: [toy-impacts]")
    return folder


@pytest.fixture
def tshirt(toy: Path) -> Path:
    return on_impacts(toy / "tshirt" / "tshirt")


def test_calc_prints_the_sets_scores_and_names_the_set(
    tshirt: Path, args: list[str], invoke: Invoke
) -> None:
    code, output = invoke("calc", tshirt, *args)

    assert code == 0, output
    assert "Climate change:" in output
    assert "Climate change, normalised (toy-nw):" in output
    assert "Single score (toy-nw):" in output
    assert "0.161523 Pt" in output
    assert "0.119205\n" in output  # a normalised score has no unit
    nw = hw.load(TOY / "background", method=METHOD, namespace="toy", sets=SET)
    p = open_database(tshirt).calc(nw, "tshirt:tshirt", indicator=SINGLE).provenance
    assert p.nw_set is not None
    assert f"set: toy-nw 1, hash {p.nw_set.hash}, for method toy-impacts" in output
    assert "left out of the set: climate-change-fossil" in output


def test_calc_of_one_set_indicator(tshirt: Path, args: list[str], invoke: Invoke) -> None:
    code, output = invoke("calc", tshirt, *args, "--indicator", SINGLE)

    assert code == 0, output
    assert "Single score (toy-nw): 0.161523 Pt" in output
    assert "Climate change:" not in output


def test_without_a_set_calc_prints_as_before(tshirt: Path, invoke: Invoke) -> None:
    code, output = invoke(
        "calc",
        tshirt,
        "--release",
        str(TOY / "background"),
        "--method",
        str(METHOD),
        "--namespace",
        "toy",
    )

    assert code == 0, output
    assert "Single score" not in output
    assert "set:" not in output


def test_explain_where_and_levers_take_the_single_score(
    tshirt: Path, args: list[str], invoke: Invoke
) -> None:
    for command in ("contributions", "where", "sensitivity"):
        code, output = invoke(command, tshirt, *args, "--indicator", SINGLE)
        assert code == 0, (command, output)
        assert "Indicator: Single score (toy-nw) (Pt)" in output, command

    _, explained = invoke("contributions", tshirt, *args, "--indicator", SINGLE)
    assert "1 unit of T-shirt: 0.16152 (100.0%), direct 0.00000" in explained
    _, normalised = invoke(
        "contributions", tshirt, *args, "--indicator", "toy-nw:normalised:ozone-formation"
    )
    assert "Indicator: Photochemical ozone formation, normalised (toy-nw)\n" in normalised


def test_a_one_indicator_method_with_a_set_still_explains_its_indicator_by_default(
    toy: Path, tmp_path: Path, invoke: Invoke
) -> None:
    """A set's indicator is chosen by name, as in the Python API: toy-climate's climate change
    stays the one `contributions` takes when none is named."""
    rows = tmp_path / "rows.csv"
    rows.write_text("indicator,normalisation,weight\nclimate-change,10,1\n")
    method = TOY / "methods" / "toy-climate"
    code, output = invoke(
        "method",
        "import-set",
        rows,
        "--method",
        method,
        "--id",
        "cc",
        "--version",
        "1",
        "--unit",
        "Pt",
        "--source",
        "made up",
        "--out",
        tmp_path / "cc",
    )
    assert code == 0, output

    code, output = invoke(
        "contributions",
        toy / "tshirt" / "tshirt",
        "--release",
        TOY / "background",
        "--method",
        method,
        "--namespace",
        "toy",
        "--set",
        tmp_path / "cc",
    )

    assert code == 0, output
    assert "Indicator: Climate change (kg CO2-eq)" in output
    assert "1 unit of T-shirt: 1.19205 (100.0%)" in output


def test_an_unknown_set_indicator_suggests_the_close_one(
    tshirt: Path, args: list[str], invoke: Invoke
) -> None:
    code, output = invoke("calc", tshirt, *args, "--indicator", "toy-nw:single")

    assert code == 2
    assert SINGLE in output


def test_a_set_of_another_method_stops_with_its_problem(toy: Path, invoke: Invoke) -> None:
    code, output = invoke(
        "calc",
        toy / "tshirt" / "tshirt",
        "--release",
        str(TOY / "background"),
        "--method",
        str(TOY / "methods" / "toy-climate"),
        "--namespace",
        "toy",
        "--set",
        str(SET),
    )

    assert code == 1
    assert "set toy-nw: is for method toy-impacts, which is not loaded" in output
    assert "Traceback" not in output


def test_diff_compares_the_single_score(
    repo: Path, commit: Commit, git: Git, args: list[str], invoke: Invoke
) -> None:
    on_impacts(repo / "tshirt" / "tshirt")
    commit("on toy-impacts")
    git("switch", "--quiet", "--create", "lighter")
    edit(
        repo / "tshirt/tshirt/exchanges.csv",
        "e02,input,toy:cotton-fabric,0.25",
        "e02,input,toy:cotton-fabric,0.22",
    )
    commit("lighter T-shirt")

    code, output = invoke("diff", "main", "tshirt/tshirt", *args, "--json")

    assert code == 0, output
    results = {r["indicator"]["id"]: r for r in json.loads(output)["results"]}
    single = results[SINGLE]
    assert single["indicator"]["unit"] == "Pt"
    # 0.03 kg less fabric at 0.502516 Pt per kg
    assert single["change"] == pytest.approx(-0.03 * 0.502516, rel=1e-9)
    assert single["drivers"][0]["contribution"] == pytest.approx(-0.03 * 0.502516, rel=1e-9)

    code, text = invoke("diff", "main", "tshirt/tshirt", *args)
    assert code == 0, text
    normalised = next(line for line in text.splitlines() if "normalised (toy-nw):" in line)
    assert " ," not in normalised  # a normalised score has no unit
    assert "Climate change, normalised (toy-nw):" in text.split("Effect of each line")[1]


# --- headwater method import-set ----------------------------------------------------------------


def test_import_set_writes_the_set_beside_its_method(tmp_path: Path, invoke: Invoke) -> None:
    methods = tmp_path / "methods"
    methods.mkdir()
    method = methods / "toy-impacts"
    method.mkdir()
    for name in ("method.yaml", "factors.csv"):
        (method / name).write_bytes((METHOD / name).read_bytes())
    method_files = {p.name: p.read_bytes() for p in method.iterdir()}

    code, output = invoke(
        "method",
        "import-set",
        SET / "factors.csv",
        "--method",
        method,
        "--id",
        "toy-nw",
        "--version",
        "1",
        "--unit",
        "Pt",
        "--source",
        "Made up for the toy, so every number can be checked by hand",
    )

    assert code == 0, output
    written = methods / "toy-nw"
    assert sorted(p.name for p in written.iterdir()) == ["factors.csv", "set.yaml"]
    assert (written / "set.yaml").read_text() == (SET / "set.yaml").read_text()
    assert (written / "factors.csv").read_text() == (SET / "factors.csv").read_text()
    assert {p.name: p.read_bytes() for p in method.iterdir()} == method_files
    assert f"Wrote {written}" in output
    assert "left out: climate-change-fossil" in output


def test_import_set_refuses_a_row_of_an_unknown_indicator(tmp_path: Path, invoke: Invoke) -> None:
    rows = tmp_path / "rows.csv"
    rows.write_text("indicator,normalisation,weight\nclimate-change,10,0.6\nwater-use,100,0.4\n")

    code, output = invoke(
        "method",
        "import-set",
        rows,
        "--method",
        METHOD,
        "--id",
        "toy-nw",
        "--version",
        "1",
        "--unit",
        "Pt",
        "--source",
        "made up",
        "--out",
        tmp_path / "out",
    )

    assert code == 1
    assert "water-use" in output
    assert not (tmp_path / "out").exists()
