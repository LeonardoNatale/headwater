"""Formulas: Python-like arithmetic on parameter names, and nothing else.

`0.25 * (1 + loss)` is a formula, and so is `max(a, b) if a > 0 else 0`. It reads like the
formulas in Brightway's Excel files, so they copy over without translation.

A formula is parsed with Python's own parser, then every node is checked against a short list and
computed here, by `evaluate`. Python's evaluator is never called, so a formula cannot reach a
file, a module or an attribute. Every number is a float, so `10 ** 10 ** 10` overflows at once
instead of running for hours.
"""

import ast
import math
import operator
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import lru_cache

from headwater.model.errors import did_you_mean

MAX_LENGTH = 2000
"""The longest formula, in characters."""

MAX_DEPTH = 100
"""How deeply a formula may nest: parentheses, operators, calls."""

_EXAMPLE = "for example 0.25 * (1 + loss)"


class FormulaError(ValueError):
    """A formula that cannot be read or computed. The message says why, for a person."""


@dataclass(frozen=True)
class Function:
    """A function a formula may call, with how many arguments it takes."""

    fewest: int
    most: int | None
    call: Callable[..., float]


def _integer(x: float, what: str) -> int:
    if x != math.floor(x):
        raise FormulaError(f"{what} must be a whole number, got {x:g}")
    return int(x)


def _round(x: float, digits: float | None = None) -> float:
    if digits is None:
        return float(round(x))
    return float(round(x, _integer(digits, "the digits of round")))


def _abs(x: float) -> float:
    return abs(x)


def _ceil(x: float) -> float:
    return float(math.ceil(x))


def _floor(x: float) -> float:
    return float(math.floor(x))


def _max(*values: float) -> float:
    return max(values)


def _min(*values: float) -> float:
    return min(values)


FORMULA_FUNCTIONS: Mapping[str, Function] = {
    "abs": Function(1, 1, _abs),
    "ceil": Function(1, 1, _ceil),
    "exp": Function(1, 1, math.exp),
    "floor": Function(1, 1, _floor),
    "log": Function(1, 2, math.log),
    "log10": Function(1, 1, math.log10),
    "max": Function(2, None, _max),
    "min": Function(2, None, _min),
    "round": Function(1, 2, _round),
    "sqrt": Function(1, 1, math.sqrt),
}
"""The functions a formula may call, by name."""

FORMULA_CONSTANTS: Mapping[str, float] = {"pi": math.pi}
"""The constants a formula may use, by name."""

_BINARY: dict[type[ast.operator], Callable[[float, float], float | complex]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: lambda a, b: a / b,
    ast.FloorDiv: lambda a, b: a // b,
    ast.Mod: operator.mod,
    ast.Pow: lambda a, b: a**b,
}
_COMPARE: dict[type[ast.cmpop], Callable[[float, float], bool]] = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
}
_DESCRIPTIONS: dict[type[ast.AST], str] = {
    ast.Attribute: "attribute access (a.b)",
    ast.Subscript: "indexing (a[0])",
    ast.List: "a list",
    ast.Dict: "a dict",
    ast.Set: "a set",
    ast.Lambda: "lambda",
    ast.ListComp: "a comprehension",
    ast.SetComp: "a comprehension",
    ast.DictComp: "a comprehension",
    ast.GeneratorExp: "a comprehension",
    ast.JoinedStr: "an f-string",
    ast.NamedExpr: "':='",
    ast.Await: "'await'",
    ast.Yield: "'yield'",
    ast.YieldFrom: "'yield'",
    ast.Starred: "'*' before an argument",
    ast.Tuple: "a comma outside a function call (for a decimal number, write a dot: 0.25)",
    ast.Slice: "a slice",
}
_OPERATORS: dict[type[ast.AST], str] = {
    ast.MatMult: "'@'",
    ast.BitAnd: "'&'",
    ast.BitOr: "'|'",
    ast.LShift: "'<<'",
    ast.RShift: "'>>'",
    ast.Invert: "'~'",
    ast.Is: "'is'",
    ast.IsNot: "'is not'",
    ast.In: "'in'",
    ast.NotIn: "'not in'",
}


class Formula:
    """A parsed formula. Make one with `Formula.parse`.

    `names` are the parameters it uses, sorted. `evaluate` computes it for given values.
    """

    __slots__ = ("_root", "names", "text")

    text: str
    names: tuple[str, ...]
    _root: ast.expr

    def __init__(self, text: str, names: tuple[str, ...], root: ast.expr) -> None:
        self.text = text
        self.names = names
        self._root = root

    @staticmethod
    def parse(text: str) -> "Formula":
        """Read a formula. Raises `FormulaError` naming the text and what is wrong with it."""
        return _parse(text.strip())

    def evaluate(self, values: Mapping[str, float]) -> float:
        """The value of the formula. Raises `FormulaError` for a name with no value, a division
        by zero, a number too large for a float, or a result that is not a finite real number."""
        try:
            return _Evaluator(self.text, values).visit(self._root)
        except FormulaError:
            raise
        except ZeroDivisionError:
            raise FormulaError(f"{self.text!r}: division by zero") from None
        except OverflowError:
            raise FormulaError(f"{self.text!r}: a number is too large for a float") from None
        except RecursionError:
            raise FormulaError(f"{self.text!r}: nested too deeply") from None

    def derivatives(self, values: Mapping[str, float]) -> dict[str, float]:
        """The partial derivative of the formula with respect to each name it uses, at `values`.

        Example: `fabric_kg * (1 + loss)` at 0.25 and 0.05 gives 1.05 for `fabric_kg` and 0.25
        for `loss`. The derivatives are exact, worked out on the formula's own terms, not by a
        finite difference. Where the formula has a kink, the derivative is that of the branch
        the evaluator takes: `max(a, b)` with a = b follows a, `abs(x)` at 0 follows x, and a
        comparison, `floor`, `ceil`, `round` and `//` are flat. Raises `FormulaError` as
        `evaluate` does, and where a derivative is not a finite number, such as `sqrt(x)` at 0.
        """
        try:
            _, gradient = _Differentiator(self.text, values).dual(self._root)
        except FormulaError:
            raise
        except ZeroDivisionError:
            raise FormulaError(f"{self.text!r}: division by zero") from None
        except OverflowError:
            raise FormulaError(f"{self.text!r}: a number is too large for a float") from None
        except RecursionError:
            raise FormulaError(f"{self.text!r}: nested too deeply") from None
        found = {name: gradient.get(name, 0.0) for name in self.names}
        if not all(math.isfinite(d) for d in found.values()):
            raise FormulaError(f"{self.text!r}: a derivative is not a finite number")
        return found

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Formula) and other.text == self.text

    def __hash__(self) -> int:
        return hash(self.text)

    def __repr__(self) -> str:
        return f"Formula({self.text!r})"


@lru_cache(maxsize=4096)
def _parse(text: str) -> Formula:
    if not text:
        raise FormulaError("formula is empty")
    if len(text) > MAX_LENGTH:
        raise FormulaError(
            f"formula {text[:30]!r}... is too long ({len(text)} characters, at most {MAX_LENGTH})"
        )
    try:
        tree = ast.parse(text, mode="eval")
    except (SyntaxError, ValueError, RecursionError, MemoryError) as exc:
        reason = getattr(exc, "msg", None) or str(exc) or type(exc).__name__
        raise FormulaError(
            f"cannot read {text!r} as a formula ({reason}). "
            f"Write arithmetic on parameter names, {_EXAMPLE}."
        ) from None
    names: set[str] = set()
    try:
        _Checker(text, names).visit(tree.body, 1)
    except RecursionError:
        raise FormulaError(f"formula {text[:30]!r}... is nested too deeply") from None
    return Formula(text, tuple(sorted(names)), tree.body)


class _Checker:
    """Walks a parsed formula once and refuses everything that is not arithmetic."""

    def __init__(self, text: str, names: set[str]) -> None:
        self.text = text
        self.names = names

    def refuse(self, what: str) -> FormulaError:
        return FormulaError(
            f"{self.text!r} uses {what}, which a formula cannot contain. A formula holds "
            "numbers, parameter names, + - * / // % **, comparisons, 'x if c else y', "
            f"parentheses and the functions {', '.join(FORMULA_FUNCTIONS)}."
        )

    def visit(self, node: ast.expr, depth: int) -> None:
        if depth > MAX_DEPTH:
            raise FormulaError(f"formula {self.text[:30]!r}... is nested too deeply")
        deeper = depth + 1
        match node:
            case ast.Constant():
                self._constant(node)
            case ast.Name():
                self._name(node)
            case ast.UnaryOp():
                if not isinstance(node.op, ast.UAdd | ast.USub | ast.Not):
                    raise self.refuse(_OPERATORS.get(type(node.op), type(node.op).__name__))
                self.visit(node.operand, deeper)
            case ast.BinOp():
                if isinstance(node.op, ast.BitXor):
                    raise FormulaError(
                        f"{self.text!r} uses '^', which is not a power here (in Python it is "
                        "'exclusive or'). Write '**' for powers."
                    )
                if type(node.op) not in _BINARY:
                    raise self.refuse(_OPERATORS.get(type(node.op), type(node.op).__name__))
                self.visit(node.left, deeper)
                self.visit(node.right, deeper)
            case ast.BoolOp():
                for value in node.values:
                    self.visit(value, deeper)
            case ast.Compare():
                for op in node.ops:
                    if type(op) not in _COMPARE:
                        raise self.refuse(_OPERATORS.get(type(op), type(op).__name__))
                for operand in (node.left, *node.comparators):
                    self.visit(operand, deeper)
            case ast.IfExp():
                for part in (node.test, node.body, node.orelse):
                    self.visit(part, deeper)
            case ast.Call():
                self._call(node, deeper)
            case _:
                raise self.refuse(_DESCRIPTIONS.get(type(node), type(node).__name__))

    def _constant(self, node: ast.Constant) -> None:
        value: object = node.value  # pyright: ignore[reportAny]
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise self.refuse(_describe_constant(value))
        try:
            too_large = not math.isfinite(float(value))
        except OverflowError:
            too_large = True
        if too_large:
            raise FormulaError(f"{self.text!r} holds a number too large for a float")

    def _name(self, node: ast.Name) -> None:
        if node.id in FORMULA_FUNCTIONS:
            raise FormulaError(
                f"{self.text!r}: {node.id!r} is a function, so it needs arguments: {node.id}(...)"
            )
        if node.id not in FORMULA_CONSTANTS:
            self.names.add(node.id)

    def _call(self, node: ast.Call, depth: int) -> None:
        if not isinstance(node.func, ast.Name):
            raise self.refuse("a call that is not a plain function name")
        name = node.func.id
        function = FORMULA_FUNCTIONS.get(name)
        if function is None:
            raise FormulaError(
                f"{self.text!r} calls an unknown function {name!r}."
                f"{did_you_mean(name, FORMULA_FUNCTIONS)} "
                f"Functions: {', '.join(FORMULA_FUNCTIONS)}."
            )
        if node.keywords:
            raise self.refuse("a keyword argument")
        count = len(node.args)
        if count < function.fewest or (function.most is not None and count > function.most):
            wanted = (
                f"at least {function.fewest}"
                if function.most is None
                else str(function.fewest)
                if function.fewest == function.most
                else f"{function.fewest} or {function.most}"
            )
            raise FormulaError(
                f"{self.text!r}: {name} takes {wanted} argument(s), but is given {count}"
            )
        for arg in node.args:
            if isinstance(arg, ast.Starred):
                raise self.refuse(_DESCRIPTIONS[ast.Starred])
            self.visit(arg, depth)


def _describe_constant(value: object) -> str:
    if isinstance(value, str | bytes):
        return "text in quotes"
    if isinstance(value, complex):
        return "an imaginary number"
    if value is Ellipsis:
        return "'...'"
    return str(value)


class _Evaluator:
    """Computes a checked formula with floats, the way Python reads it."""

    def __init__(self, text: str, values: Mapping[str, float]) -> None:
        self.text = text
        self.values = values

    def finite(self, x: float | complex) -> float:
        if isinstance(x, complex):
            raise FormulaError(f"{self.text!r}: a result is not a real number")
        if not math.isfinite(x):
            raise FormulaError(f"{self.text!r}: a result is not a finite number")
        return x

    def visit(self, node: ast.expr) -> float:
        match node:
            case ast.Constant():
                return float(node.value)  # pyright: ignore[reportAny, reportArgumentType]
            case ast.Name():
                return self._name(node.id)
            case ast.UnaryOp():
                operand = self.visit(node.operand)
                if isinstance(node.op, ast.Not):
                    return 0.0 if operand else 1.0
                return self.finite(-operand if isinstance(node.op, ast.USub) else operand)
            case ast.BinOp():
                result = _BINARY[type(node.op)](self.visit(node.left), self.visit(node.right))
                return self.finite(result)
            case ast.BoolOp():
                return self._bool(node)
            case ast.Compare():
                return self._compare(node)
            case ast.IfExp():
                return self.visit(node.body if self.visit(node.test) else node.orelse)
            case ast.Call():
                return self._call(node)
            case _:  # pragma: no cover - the checker refused everything else
                raise FormulaError(f"{self.text!r}: cannot compute {type(node).__name__}")

    def _name(self, name: str) -> float:
        if name in FORMULA_CONSTANTS:
            return FORMULA_CONSTANTS[name]
        if name not in self.values:
            raise FormulaError(
                f"{self.text!r}: unknown parameter {name!r}.{did_you_mean(name, self.values)}"
            )
        value = self.values[name]
        if not math.isfinite(value):
            raise FormulaError(f"{self.text!r}: parameter {name!r} is not a finite number")
        return value

    def _bool(self, node: ast.BoolOp) -> float:
        result = 0.0
        for value in node.values:
            result = self.visit(value)
            if bool(result) == isinstance(node.op, ast.Or):
                break
        return result

    def _compare(self, node: ast.Compare) -> float:
        left = self.visit(node.left)
        for op, comparator in zip(node.ops, node.comparators, strict=True):
            right = self.visit(comparator)
            if not _COMPARE[type(op)](left, right):
                return 0.0
            left = right
        return 1.0

    def _call(self, node: ast.Call) -> float:
        assert isinstance(node.func, ast.Name)
        name = node.func.id
        args = [self.visit(arg) for arg in node.args]
        try:
            return self.finite(FORMULA_FUNCTIONS[name].call(*args))
        except FormulaError:
            raise
        except ValueError:
            shown = ", ".join(f"{a:g}" for a in args)
            raise FormulaError(
                f"{self.text!r}: {name}({shown}) is outside the domain of {name}"
            ) from None


type Gradient = dict[str, float]
"""The partial derivative of a part of a formula with respect to each name in it."""


def _combined(a: Gradient, ca: float, b: Gradient, cb: float) -> Gradient:
    """ca × a + cb × b, keeping every name of both."""
    return {k: ca * a.get(k, 0.0) + cb * b.get(k, 0.0) for k in a.keys() | b.keys()}


def _moves(gradient: Gradient) -> bool:
    return any(d != 0.0 for d in gradient.values())


class _Differentiator(_Evaluator):
    """Computes a checked formula and its derivatives together, one node at a time."""

    def flat(self, *gradients: Gradient) -> Gradient:
        """A part that does not move with any of its names, such as a comparison."""
        return {k: 0.0 for g in gradients for k in g}

    def not_differentiable(self, what: str) -> FormulaError:
        return FormulaError(f"{self.text!r}: the derivative of {what} is not a finite number")

    def dual(self, node: ast.expr) -> tuple[float, Gradient]:
        match node:
            case ast.Constant():
                return self.visit(node), {}
            case ast.Name():
                value = self._name(node.id)
                return value, {} if node.id in FORMULA_CONSTANTS else {node.id: 1.0}
            case ast.UnaryOp():
                value, gradient = self.dual(node.operand)
                if isinstance(node.op, ast.Not):
                    return (0.0 if value else 1.0), self.flat(gradient)
                if isinstance(node.op, ast.USub):
                    return self.finite(-value), {k: -d for k, d in gradient.items()}
                return value, gradient
            case ast.BinOp():
                return self._binary_dual(node)
            case ast.BoolOp():
                found: tuple[float, Gradient] = (0.0, {})
                for part in node.values:
                    found = self.dual(part)
                    if bool(found[0]) == isinstance(node.op, ast.Or):
                        break
                return found
            case ast.Compare():
                parts = [self.dual(n)[1] for n in (node.left, *node.comparators)]
                return self.visit(node), self.flat(*parts)
            case ast.IfExp():
                return self.dual(node.body if self.visit(node.test) else node.orelse)
            case ast.Call():
                return self._call_dual(node)
            case _:  # pragma: no cover - the checker refused everything else
                raise FormulaError(f"{self.text!r}: cannot compute {type(node).__name__}")

    def _binary_dual(self, node: ast.BinOp) -> tuple[float, Gradient]:
        a, da = self.dual(node.left)
        b, db = self.dual(node.right)
        value = self.finite(_BINARY[type(node.op)](a, b))
        match node.op:
            case ast.Add():
                return value, _combined(da, 1.0, db, 1.0)
            case ast.Sub():
                return value, _combined(da, 1.0, db, -1.0)
            case ast.Mult():
                return value, _combined(da, b, db, a)
            case ast.Div():
                return value, _combined(da, 1.0 / b, db, -value / b)
            case ast.FloorDiv():
                return value, self.flat(da, db)
            case ast.Mod():  # a % b is a - b × floor(a / b)
                return value, _combined(da, 1.0, db, -math.floor(a / b))
            case _:  # a ** b
                return value, _combined(
                    da,
                    self._power_base(a, b) if _moves(da) else 0.0,
                    db,
                    self._power_exponent(a, b, value) if _moves(db) else 0.0,
                )

    def _power_base(self, a: float, b: float) -> float:
        """d(a ** b) / da."""
        if b == 0.0:
            return 0.0
        if a == 0.0 and b < 1.0:
            raise self.not_differentiable(f"x ** {b:g} at x = 0")
        return self.finite(b * a ** (b - 1.0))

    def _power_exponent(self, a: float, b: float, value: float) -> float:
        """d(a ** b) / db."""
        if a > 0.0:
            return self.finite(value * math.log(a))
        if a == 0.0 and b > 0.0:
            return 0.0
        raise self.not_differentiable(f"{a:g} ** x, a power of a base that is not positive")

    def _call_dual(self, node: ast.Call) -> tuple[float, Gradient]:
        assert isinstance(node.func, ast.Name)
        name = node.func.id
        parts = [self.dual(arg) for arg in node.args]
        args = [v for v, _ in parts]
        value = self._call(node)
        first, d = parts[0]
        match name:
            case "abs":
                return value, _combined(d, 1.0 if first >= 0 else -1.0, {}, 0.0)
            case "exp":
                return value, _combined(d, value, {}, 0.0)
            case "sqrt":
                if first == 0.0 and _moves(d):
                    raise self.not_differentiable("sqrt at 0")
                return value, _combined(d, 0.5 / value if value else 0.0, {}, 0.0)
            case "log10":
                return value, _combined(d, 1.0 / (first * math.log(10.0)), {}, 0.0)
            case "log" if len(parts) == 1:
                return value, _combined(d, 1.0 / first, {}, 0.0)
            case "log":  # log(x, base) is ln x / ln base
                base, db = parts[1]
                ln_base = math.log(base)
                return value, _combined(d, 1.0 / (first * ln_base), db, -value / (base * ln_base))
            case "max" | "min":
                chosen = args.index(value)  # max and min return the first of equal values
                others = [g for i, (_, g) in enumerate(parts) if i != chosen]
                return value, {**self.flat(*others), **parts[chosen][1]}
            case _:  # ceil, floor, round: flat between their steps
                return value, self.flat(*(g for _, g in parts))
