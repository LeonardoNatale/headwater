"""Named parameters: values and formulas, scoped to a study or to one process."""

import pytest

from headwater.model import FormulaError, InvalidDataError, Parameter, ParameterSet, Problem

REL = 1e-12
PROCESS = "tshirt:tshirt"


def value(name: str, amount: float, **extra: object) -> Parameter:
    return Parameter.model_validate({"name": name, "value": amount, **extra})


def formula(name: str, text: str, **extra: object) -> Parameter:
    return Parameter.model_validate({"name": name, "formula": text, **extra})


def problems_of(*parameters: Parameter) -> tuple[Problem, ...]:
    with pytest.raises(InvalidDataError) as raised:
        ParameterSet(parameters)
    return raised.value.problems


# --- one parameter ---------------------------------------------------------------------------


def test_a_parameter_has_a_name_a_value_a_unit_and_a_comment() -> None:
    parameter = value("fabric_kg", 0.25, unit="kg", comment="From the bill of materials")

    assert (parameter.name, parameter.value, parameter.unit) == ("fabric_kg", 0.25, "kg")
    assert parameter.formula is None
    assert parameter.scope is None
    assert parameter.id == "fabric_kg"


def test_a_process_parameter_has_the_process_in_its_id() -> None:
    assert value("cut_ratio", 0.1, scope=PROCESS).id == "tshirt:tshirt/cut_ratio"


@pytest.mark.parametrize("extra", [{"value": 1, "formula": "2"}, {}])
def test_a_parameter_has_a_value_or_a_formula_not_both_and_not_none(
    extra: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="value or a formula"):
        Parameter.model_validate({"name": "x", **extra})


@pytest.mark.parametrize("name", ["fabric kg", "1a", "a-b", "", "größe", "a.b"])
def test_a_name_is_an_identifier(name: str) -> None:
    with pytest.raises(ValueError, match="name"):
        value(name, 1)


@pytest.mark.parametrize(
    "name", ["sqrt", "pi", "max", "if", "lambda", "True", "None", "nan", "inf", "Infinity"]
)
def test_a_name_may_not_be_a_function_a_constant_or_a_keyword(name: str) -> None:
    with pytest.raises(ValueError, match="reserved|keyword|function|constant|number"):
        value(name, 1)


def test_a_yes_is_not_a_number() -> None:
    with pytest.raises(ValueError, match="yes/no"):
        value("x", True)


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_a_value_is_finite(bad: float) -> None:
    with pytest.raises(ValueError, match="finite"):
        value("x", bad)


def test_a_formula_is_checked_when_the_parameter_is_made() -> None:
    with pytest.raises(ValueError, match="formula"):
        formula("x", "a +")


# --- a set of parameters ---------------------------------------------------------------------


def test_values_are_worked_out_whatever_the_order_of_definition() -> None:
    parameters = ParameterSet(
        [
            formula("gross", "fabric_kg * (1 + loss)"),
            value("loss", 0.05),
            value("fabric_kg", 0.25),
        ]
    )

    assert parameters.values["gross"] == pytest.approx(0.2625, rel=REL)
    assert [p.name for p in parameters.parameters].index("fabric_kg") < [
        p.name for p in parameters.parameters
    ].index("gross")
    assert len(parameters) == 3
    assert "loss" in parameters
    assert "unknown" not in parameters


def test_a_chain_of_formulas() -> None:
    parameters = ParameterSet([formula("c", "b * 2"), formula("b", "a + 1"), value("a", 1)])

    assert (parameters.values["a"], parameters.values["b"], parameters.values["c"]) == (1, 2, 4)


def test_an_empty_set_is_fine() -> None:
    assert len(ParameterSet()) == 0
    assert dict(ParameterSet().values) == {}


def test_a_loop_is_refused_with_the_names_in_it() -> None:
    (problem,) = problems_of(formula("a", "b"), formula("b", "c"), formula("c", "a + 1"))

    assert problem.code == "parameter-cycle"
    assert (
        "a → b → c → a" in problem.message
        or "b → c → a → b" in problem.message
        or ("c → a → b → c" in problem.message)
    )


def test_a_parameter_that_uses_itself_is_a_loop() -> None:
    (problem,) = problems_of(formula("x", "x + 1"))

    assert problem.code == "parameter-cycle"
    assert "x → x" in problem.message


def test_an_unknown_name_is_named_with_a_suggestion() -> None:
    (problem,) = problems_of(value("fabric_kg", 0.25), formula("gross", "fabric_kgg * 2"))

    assert problem.code == "unknown-parameter"
    assert problem.item == "parameter gross"
    assert "'fabric_kgg'" in problem.message
    assert "Did you mean 'fabric_kg'?" in problem.message


def test_a_parameter_defined_twice_is_refused() -> None:
    (problem,) = problems_of(value("x", 1), value("x", 2))

    assert problem.code == "duplicate-parameter"
    assert problem.item == "parameter x"


def test_every_problem_is_listed_at_once() -> None:
    found = problems_of(
        value("x", 1),
        value("x", 2),
        formula("a", "nope + 1"),
        formula("b", "c"),
        formula("c", "b"),
    )

    assert sorted(p.code for p in found) == [
        "duplicate-parameter",
        "parameter-cycle",
        "unknown-parameter",
    ]


def test_a_formula_that_cannot_be_computed_names_the_parameter() -> None:
    (problem,) = problems_of(value("zero", 0), formula("ratio", "1 / zero"))

    assert problem.code == "bad-formula"
    assert problem.item == "parameter ratio"
    assert "division by zero" in problem.message


def test_a_parameter_that_depends_on_a_broken_one_adds_no_noise() -> None:
    found = problems_of(
        value("zero", 0), formula("ratio", "1 / zero"), formula("twice", "ratio * 2")
    )

    assert [p.item for p in found] == ["parameter ratio"]


# --- scope -----------------------------------------------------------------------------------


def test_a_process_parameter_is_seen_only_from_its_own_process() -> None:
    parameters = ParameterSet([value("fabric_kg", 0.25), value("cut_ratio", 0.1, scope=PROCESS)])

    assert parameters.visible(None) == {"fabric_kg": "fabric_kg"}
    assert parameters.visible(PROCESS) == {
        "fabric_kg": "fabric_kg",
        "cut_ratio": "tshirt:tshirt/cut_ratio",
    }
    assert parameters.visible("toy:fabric-production") == {"fabric_kg": "fabric_kg"}


def test_a_process_formula_can_use_the_study_parameters_and_its_own() -> None:
    parameters = ParameterSet(
        [
            value("fabric_kg", 0.25),
            value("cut_ratio", 0.1, scope=PROCESS),
            formula("used", "fabric_kg * (1 - cut_ratio)", scope=PROCESS),
        ]
    )

    assert parameters.values["tshirt:tshirt/used"] == pytest.approx(0.225, rel=REL)


def test_a_study_formula_cannot_use_a_process_parameter() -> None:
    (problem,) = problems_of(value("cut_ratio", 0.1, scope=PROCESS), formula("x", "cut_ratio * 2"))

    assert problem.code == "unknown-parameter"
    assert "tshirt:tshirt" in problem.message  # says where the name does exist


def test_a_process_parameter_may_not_hide_a_study_parameter() -> None:
    (problem,) = problems_of(value("loss", 0.05), value("loss", 0.1, scope=PROCESS))

    assert problem.code == "parameter-shadow"
    assert problem.item == "parameter tshirt:tshirt/loss"


def test_two_processes_may_each_have_a_parameter_of_the_same_name() -> None:
    parameters = ParameterSet(
        [value("recovery", 0.9, scope=PROCESS), value("recovery", 0.8, scope="tshirt:dyeing")]
    )

    assert parameters.values["tshirt:tshirt/recovery"] == 0.9
    assert parameters.values["tshirt:dyeing/recovery"] == 0.8


def test_evaluate_a_formula_in_a_scope() -> None:
    parameters = ParameterSet([value("fabric_kg", 0.25), value("cut_ratio", 0.1, scope=PROCESS)])

    assert parameters.evaluate("fabric_kg * 2") == pytest.approx(0.5, rel=REL)
    assert parameters.evaluate("fabric_kg * cut_ratio", PROCESS) == pytest.approx(0.025, rel=REL)
    with pytest.raises(FormulaError, match="'cut_ratio'"):
        parameters.evaluate("cut_ratio", None)


# --- changes ---------------------------------------------------------------------------------


def test_changing_a_value_works_out_everything_that_depends_on_it() -> None:
    base = ParameterSet([value("a", 1, unit="kg"), formula("b", "a * 3")])

    changed = base.with_changes({"a": 2})

    assert (changed.values["a"], changed.values["b"]) == (2, 6)
    assert (base.values["a"], base.values["b"]) == (1, 3)  # the original is untouched
    first = next(p for p in changed.parameters if p.name == "a")
    assert first.unit == "kg"  # what the change did not mention stays


def test_changing_a_parameter_to_a_formula() -> None:
    base = ParameterSet([value("a", 1), value("b", 5)])

    changed = base.with_changes({"a": "b * 2"})

    assert changed.values["a"] == 10


def test_a_text_that_is_a_number_is_a_value() -> None:
    changed = ParameterSet([value("a", 1)]).with_changes({"a": "0.30"})

    assert changed.values["a"] == 0.3
    assert next(iter(changed)).formula is None


def test_changing_an_unknown_parameter_is_refused_with_a_suggestion() -> None:
    with pytest.raises(InvalidDataError) as raised:
        ParameterSet([value("fabric_kg", 0.25)]).with_changes({"fabric_kgg": 0.3})

    (problem,) = raised.value.problems
    assert problem.code == "unknown-parameter"
    assert "Did you mean 'fabric_kg'?" in problem.message


def test_a_change_that_makes_a_loop_is_refused() -> None:
    base = ParameterSet([value("a", 1), formula("b", "a + 1")])

    with pytest.raises(InvalidDataError) as raised:
        base.with_changes({"a": "b"})

    assert [p.code for p in raised.value.problems] == ["parameter-cycle"]
