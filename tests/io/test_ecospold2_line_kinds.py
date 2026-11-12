"""bench/ecospold2_line_kinds.py counts the kinds of product lines of the APOS fixture."""

import importlib.util
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


def _script() -> Any:
    spec = importlib.util.spec_from_file_location(
        "ecospold2_line_kinds", ROOT / "bench" / "ecospold2_line_kinds.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_line_of_the_apos_fixture_is_counted_with_its_role(fx) -> None:
    found, examples = _script().survey(fx.apos, workers=1)

    kinds = {(k["side"], k["class"], k["sign"]): k for k in found["kinds"]}
    assert (found["system_model"], found["files"], found["rejected_lines"]) == ("apos", 8, 0)
    assert examples == {}
    sent = "waste_to_treatment, amount negated"
    treats = "reference, amount negated"
    assert {key: (k["lines"], k["role"]) for key, k in kinds.items()} == {
        ("input", "Recyclable", "negative"): (1, sent),
        ("input", "Waste", "negative"): (1, sent),
        ("input", "allocatable product", "negative"): (1, sent),
        ("input", "allocatable product", "positive"): (6, "input"),
        ("reference", "Recyclable", "negative"): (1, treats),
        ("reference", "Waste", "negative"): (1, treats),
        ("reference", "allocatable product", "negative"): (1, treats),
        ("reference", "allocatable product", "positive"): (5, "reference"),
    }
    bought = kinds[("input", "allocatable product", "positive")]
    assert (bought["with_supplier"], bought["supplier_is_itself"]) == (6, 1)
    assert bought["supplier_reference"] == {"allocatable product, positive": 6}
    assert kinds[("input", "Recyclable", "negative")]["supplier_reference"] == {
        "Recyclable, negative": 1
    }
    assert found["elementary"] == {"output 4, input None": 7, "output None, input 4": 1}


def test_a_line_the_table_rejects_is_counted_with_an_example(
    fx, source_copy: Any, tmp_path: Path
) -> None:
    widget = f"datasets/{fx.file_name(1, 1)}"
    folder = source_copy(
        edit={widget: [('amount="2.0"', 'amount="-2.0"')]}, remove=(fx.empty_file,)
    )

    found, examples = _script().survey(folder, workers=1)

    assert found["rejected_lines"] == 1
    assert examples == {("input", "allocatable product", "negative"): [fx.file_name(1, 1)]}
