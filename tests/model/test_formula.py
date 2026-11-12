"""The formula evaluator: Python-like arithmetic on parameter names, and nothing else."""

import ast
import math
from pathlib import Path

import pytest

import headwater.model.formula as formula_module
from headwater.model import FORMULA_FUNCTIONS, Formula, FormulaError

REL = 1e-12


def value(text: str, **names: float) -> float:
    return Formula.parse(text).evaluate(names)


@pytest.mark.parametrize(
    ("text", "names", "expected"),
    [
        ("0.25", {}, 0.25),
        ("1e-3", {}, 0.001),
        ("fabric_kg", {"fabric_kg": 0.25}, 0.25),
        ("fabric_kg * (1 + loss)", {"fabric_kg": 0.25, "loss": 0.05}, 0.2625),
        ("2 ** 3 ** 2", {}, 512.0),
        ("-2 ** 2", {}, -4.0),
        ("7 // 2", {}, 3.0),
        ("7 % 4", {}, 3.0),
        ("-x + +y", {"x": 1.0, "y": 2.0}, 1.0),
        ("10 / 4", {}, 2.5),
        ("pi", {}, math.pi),
    ],
)
def test_arithmetic(text: str, names: dict[str, float], expected: float) -> None:
    assert value(text, **names) == pytest.approx(expected, rel=REL)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("abs(-2)", 2.0),
        ("sqrt(16)", 4.0),
        ("exp(0)", 1.0),
        ("log(1)", 0.0),
        ("log(8, 2)", 3.0),
        ("log10(1000)", 3.0),
        ("floor(2.7)", 2.0),
        ("ceil(2.1)", 3.0),
        ("round(2.567, 1)", 2.6),
        ("round(2.4)", 2.0),
        ("min(3, 1, 2)", 1.0),
        ("max(3, 1, 2)", 3.0),
        ("max(sqrt(4), 1) + 1", 3.0),
    ],
)
def test_functions(text: str, expected: float) -> None:
    assert value(text) == pytest.approx(expected, rel=REL)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("5 if 1 < 2 else 7", 5.0),
        ("5 if 2 < 1 else 7", 7.0),
        ("1 if 2 >= 2 else 0", 1.0),
        ("1 < 2 < 3", 1.0),
        ("1 < 3 < 2", 0.0),
        ("(1 < 2) + (2 < 3)", 2.0),
        ("1 == 1", 1.0),
        ("1 != 1", 0.0),
        ("0 or 3", 3.0),
        ("2 and 3", 3.0),
        ("0 and 3", 0.0),
        ("not 0", 1.0),
        ("not 5", 0.0),
    ],
)
def test_conditions_the_way_python_reads_them(text: str, expected: float) -> None:
    assert value(text) == pytest.approx(expected, abs=REL)


def test_names_are_listed_without_pi_or_functions() -> None:
    formula = Formula.parse("max(a, b) * pi + a + sqrt(c)")

    assert formula.names == ("a", "b", "c")


def test_a_formula_remembers_its_text() -> None:
    assert Formula.parse("  fabric_kg*2 ").text == "fabric_kg*2"


def test_the_same_text_parses_to_the_same_formula() -> None:
    assert Formula.parse("a + 1") == Formula.parse("a + 1")


def test_functions_are_the_documented_ones() -> None:
    assert sorted(FORMULA_FUNCTIONS) == [
        "abs",
        "ceil",
        "exp",
        "floor",
        "log",
        "log10",
        "max",
        "min",
        "round",
        "sqrt",
    ]


# --- what is refused -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "__import__('os').system('true')",
        "open('x')",
        "().__class__",
        "a.b",
        "a[0]",
        "[1, 2]",
        "{1: 2}",
        "'abc'",
        "f'{a}'",
        "lambda: 1",
        "[x for x in a]",
        "(a := 1)",
        "a, b",
        "True",
        "None",
        "1j",
        "a @ b",
        "a & b",
        "a << 1",
        "~a",
        "a is b",
        "a in b",
        "max(*a)",
        "max(a, key=b)",
        "await a",
        "yield a",
    ],
)
def test_anything_but_arithmetic_is_refused(text: str) -> None:
    with pytest.raises(FormulaError) as raised:
        Formula.parse(text)

    assert repr(text.strip()) in str(raised.value) or text.strip() in str(raised.value)


def test_a_caret_is_not_a_power() -> None:
    with pytest.raises(FormulaError, match=r"'\*\*'"):
        Formula.parse("a ^ 2")


def test_an_unknown_function_is_named_and_the_known_ones_listed() -> None:
    with pytest.raises(FormulaError) as raised:
        Formula.parse("sqroot(4)")

    message = str(raised.value)
    assert "'sqroot'" in message
    assert "Did you mean 'sqrt'?" in message
    assert "log10" in message


def test_a_function_name_alone_is_not_a_parameter() -> None:
    with pytest.raises(FormulaError, match="'sqrt' is a function"):
        Formula.parse("sqrt + 1")


@pytest.mark.parametrize(
    "text", ["abs()", "abs(1, 2)", "sqrt(1, 2)", "max(1)", "log(1, 2, 3)", "round(1, 2, 3)"]
)
def test_a_function_called_with_the_wrong_number_of_arguments(text: str) -> None:
    with pytest.raises(FormulaError, match="argument"):
        Formula.parse(text)


@pytest.mark.parametrize("text", ["", "   ", "a +", "(1 + 2", "1 +* 2", "a b", "1 2"])
def test_a_syntax_error_names_the_text(text: str) -> None:
    with pytest.raises(FormulaError) as raised:
        Formula.parse(text)

    assert "formula" in str(raised.value)


def test_a_comma_outside_a_call_hints_at_the_decimal_dot() -> None:
    with pytest.raises(FormulaError, match="decimal"):
        Formula.parse("0,25")


def test_a_formula_that_is_too_long_is_refused() -> None:
    with pytest.raises(FormulaError, match="long"):
        Formula.parse("1 + " * 1000 + "1")


@pytest.mark.parametrize("depth", [400, 5000])
def test_deep_nesting_is_an_error_not_a_crash(depth: int) -> None:
    with pytest.raises(FormulaError):
        Formula.parse("(" * depth + "1" + ")" * depth)


def test_a_long_chain_is_an_error_not_a_crash() -> None:
    with pytest.raises(FormulaError):
        value("+".join(["1"] * 900))


def test_a_null_byte_is_an_error() -> None:
    with pytest.raises(FormulaError):
        Formula.parse("1 +\x002")


# --- what goes wrong while computing ---------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        ("1 / 0", "division by zero"),
        ("1 // 0", "division by zero"),
        ("1 % 0", "division by zero"),
        ("0 ** -1", "division by zero"),
        ("sqrt(-1)", "sqrt"),
        ("log(0)", "log"),
        ("10 ** 400", "too large"),
        ("10 ** 10 ** 10", "too large"),
        ("exp(1000)", "too large"),
        ("(-8) ** (1 / 3)", "real number"),
        ("1e308 * 10", "finite"),
    ],
)
def test_arithmetic_that_fails_is_a_named_error(text: str, fragment: str) -> None:
    with pytest.raises(FormulaError, match=fragment):
        value(text)


def test_an_unknown_name_is_named_with_the_known_ones() -> None:
    with pytest.raises(FormulaError) as raised:
        Formula.parse("fabric_kgg * 2").evaluate({"fabric_kg": 0.25})

    assert "'fabric_kgg'" in str(raised.value)
    assert "Did you mean 'fabric_kg'?" in str(raised.value)


def test_a_non_finite_input_is_refused() -> None:
    with pytest.raises(FormulaError, match="finite"):
        value("a + 1", a=float("inf"))


def test_the_module_never_calls_python_eval() -> None:
    tree = ast.parse(Path(formula_module.__file__).read_text())
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }

    assert not called & {"eval", "exec", "compile", "__import__"}
