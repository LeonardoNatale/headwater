"""`headwater calc <study> --json` and `--csv`: each product of the study with its amount and its
scores for that amount, as `calc` prints them for a database.

The toy: the study `tshirt-footprint` scores 1 T-shirt, 1.242646, and 1.237646 in its scenario
`cleaner-dye`, where the dye emits 3.5 kg CO2 per kg, not 4: 1.242646 - 0.01 × 0.5. The study
`tshirt-and-dye` scores 2 T-shirts, 2 × 1.242646 = 2.485292, and 0.5 kg of reactive dye,
0.5 × 5.0596 = 2.5298.
"""

import csv
import io
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

import headwater as hw
from headwater.query.study import study_input_hash
from tests.conftest import open_study_folder

from .conftest import copy_process

Invoke = Callable[..., tuple[int, str]]

TOY = Path(__file__).resolve().parent.parent / "fixtures" / "toy"
REL = 1e-9
SAME = 1e-12  # the command against the API it prints


def background() -> hw.Background:
    return hw.load(TOY / "background", method=TOY / "methods" / "toy-climate", namespace="toy")


def calc(invoke_stdout: Invoke, *args: str | Path) -> str:
    code, output = invoke_stdout("calc", *args)
    assert code == 0, output
    return output


def api_scores(folder: Path, scenario: str | None = None) -> list[float]:
    """What `Study.calc` gives for each product, its amount of it."""
    return [r.score for r in open_study_folder(folder).calc(background(), scenario=scenario)]


@pytest.mark.parametrize(("scenario", "expected"), [(None, 1.242646), ("cleaner-dye", 1.237646)])
def test_the_json_of_a_study_has_each_product_with_its_amount_and_scores(
    toy: Path,
    toy_args: list[str],
    invoke_stdout: Invoke,
    scenario: str | None,
    expected: float,
) -> None:
    folder = toy / "tshirt-dyed" / "tshirt-footprint"
    asked = ["--scenario", scenario] if scenario else []

    data = json.loads(calc(invoke_stdout, folder, *toy_args, *asked, "--json"))

    assert data["format"] == "headwater-calc/3"
    assert (data["path"], data["kind"], data["name"]) == (str(folder), "study", "tshirt-footprint")
    assert data["scenario"] == scenario
    [product] = data["products"]
    assert product["id"] == "tshirt:tshirt"
    assert (product["name"], product["process"], product["flow"]) == (
        "T-shirt",
        "T-shirt",
        "tshirt:tshirt",
    )
    assert (product["amount"], product["unit"], product["per"]) == (1, "unit", "1 unit")
    [result] = data["results"]
    assert result["product"] == {"id": "tshirt:tshirt", "name": "T-shirt", "process": "T-shirt"}
    assert result["score"] == pytest.approx(expected, rel=REL)
    [api] = api_scores(folder, scenario)
    assert result["score"] == pytest.approx(api, rel=SAME)
    study = open_study_folder(folder)
    chosen = next((s for s in study.folder.scenarios if s.name == scenario), None)
    assert product["input_hash"] == study_input_hash(
        study.folder, background(), "tshirt:tshirt", chosen
    )
    assert (
        product["input_hash"]
        == study.calc(background(), scenario=scenario)[0].provenance.input_hash
    )
    assert product["cancels"] == []
    assert 0 <= product["residual"] <= 1e-12
    provenance = data["provenance"]
    assert provenance["study"] == {
        "name": "tshirt-footprint",
        "saved": True,
        "commit": None,  # a copy of the toy outside git
        "changed": False,
    }
    assert provenance["uses"] == ["dyes", "tshirt"]
    assert provenance["residual"] == product["residual"]


def test_the_json_of_a_study_has_the_keys_of_a_database_and_adds_only(
    toy: Path, toy_args: list[str], invoke_stdout: Invoke
) -> None:
    folder = toy / "tshirt-dyed"
    study = json.loads(calc(invoke_stdout, folder / "tshirt-footprint", *toy_args, "--json"))
    database = json.loads(calc(invoke_stdout, folder / "tshirt", *toy_args, "--json"))

    assert set(study) == set(database)
    assert database["name"] == "tshirt"
    assert set(study["products"][0]) == set(database["products"][0]) | {"amount"}
    assert set(study["results"][0]) == set(database["results"][0])
    assert set(study["provenance"]) - set(database["provenance"]) == {"study"}
    assert set(database["provenance"]) - set(study["provenance"]) == {"database"}


def test_a_study_of_two_products_gives_each_its_amount_in_per(
    toy: Path, toy_args: list[str], invoke_stdout: Invoke
) -> None:
    folder = toy / "tshirt-dyed" / "tshirt-and-dye"

    data = json.loads(calc(invoke_stdout, folder, *toy_args, "--json"))

    tshirt, dye = data["products"]
    assert (tshirt["id"], tshirt["amount"], tshirt["per"]) == ("tshirt:tshirt", 2, "2 unit")
    assert (dye["id"], dye["amount"], dye["per"]) == ("dyes:reactive-dye", 0.5, "0.5 kg")
    assert (dye["name"], dye["process"], dye["unit"]) == (
        "reactive dye",
        "reactive dye production",
        "kg",
    )
    scores = [r["score"] for r in data["results"]]
    assert scores == pytest.approx([2.485292, 2.5298], rel=REL)
    assert scores == pytest.approx(api_scores(folder), rel=SAME)
    assert tshirt["input_hash"] != dye["input_hash"]


def rows(output: str) -> list[dict[str, Any]]:
    return list(csv.DictReader(io.StringIO(output)))


@pytest.mark.parametrize(("scenario", "expected"), [(None, 1.242646), ("cleaner-dye", 1.237646)])
def test_the_csv_of_a_study_has_the_columns_of_a_database_and_its_amount_in_per(
    toy: Path,
    toy_args: list[str],
    invoke_stdout: Invoke,
    scenario: str | None,
    expected: float,
) -> None:
    folder = toy / "tshirt-dyed" / "tshirt-footprint"
    asked = ["--scenario", scenario] if scenario else []

    found = rows(calc(invoke_stdout, folder, *toy_args, *asked, "--csv"))
    columns = rows(calc(invoke_stdout, toy / "tshirt-dyed" / "tshirt", *toy_args, "--csv"))[0]

    assert list(found[0]) == list(columns)
    [row] = found
    assert (row["process_id"], row["process"], row["product"], row["per"]) == (
        "tshirt:tshirt",
        "T-shirt",
        "T-shirt",
        "1 unit",
    )
    assert (row["indicator_id"], row["score_unit"]) == ("climate-change", "kg CO2-eq")
    assert float(row["score"]) == pytest.approx(expected, rel=REL)
    [api] = api_scores(folder, scenario)
    assert float(row["score"]) == pytest.approx(api, rel=SAME)


def test_the_csv_of_a_study_of_two_products_scores_each_for_its_amount(
    toy: Path, toy_args: list[str], invoke_stdout: Invoke
) -> None:
    folder = toy / "tshirt-dyed" / "tshirt-and-dye"

    found = rows(calc(invoke_stdout, folder, *toy_args, "--csv"))

    assert [(r["process_id"], r["per"]) for r in found] == [
        ("tshirt:tshirt", "2 unit"),
        ("dyes:reactive-dye", "0.5 kg"),
    ]
    assert [float(r["score"]) for r in found] == pytest.approx(api_scores(folder), rel=SAME)


def test_a_study_still_refuses_product_options(
    toy: Path, toy_args: list[str], invoke: Invoke
) -> None:
    folder = toy / "tshirt-dyed" / "tshirt-footprint"

    for option in (["--product", "tshirt:tshirt"], ["--all-products"]):
        code, output = invoke("calc", folder, *toy_args, *option)
        assert code == 2, output
        assert "--product" in output


def test_json_of_a_study_with_a_monte_carlo_run_is_a_usage_error(
    toy: Path, toy_args: list[str], invoke: Invoke
) -> None:
    folder = toy / "tshirt-dyed" / "tshirt-footprint"

    code, output = invoke("calc", folder, *toy_args, "--json", "--iterations", "2")

    assert code == 2, output
    assert "--json" in output


def test_the_csv_works_out_no_input_hash_it_does_not_print(
    toy: Path, toy_args: list[str], invoke_stdout: Invoke, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The CSV has no column for input hashes: it asks for no more of them than the text does,
    which prints the first product's only. The JSON has one per product: the first's is its
    result's, and the second is worked out."""
    import headwater.query.database as database

    calls: list[str] = []
    real = database._input_hash  # pyright: ignore[reportPrivateUsage]

    def counted(*args: Any, **kwargs: Any) -> str:
        calls.append(args[2])
        return real(*args, **kwargs)

    monkeypatch.setattr(database, "_input_hash", counted)
    folder = toy / "tshirt-dyed" / "dyes"
    copy_process(folder, "reactive-dye", "dye-b")

    asked: dict[str, int] = {}
    for output in ("text", "--csv", "--json"):
        calls.clear()
        calc(invoke_stdout, folder, *toy_args, *([] if output == "text" else [output]))
        asked[output] = len(calls)

    assert asked["--csv"] == asked["text"] == 1
    assert asked["--json"] == 1 + 1
