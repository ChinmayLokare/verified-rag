"""Pure arithmetic over referenced values (design.md section 8.3, "Calculator").

No database, no framework imports. ``evaluate`` takes an expression over named operands and
returns a Decimal result with a derived unit. The expression is parsed with ``ast`` and only a
whitelist of nodes is allowed; nothing is ever passed to ``eval``.

Units are products of base units with integer exponents, written like ``USD``, ``shares``,
``USD/shares``. ``pure`` means dimensionless. Rules:
- ``+`` and ``-`` need identical units and, when both are known, the same period type
  (an instant balance can't be added to a duration flow).
- ``*`` and ``/`` combine units; numeric literals are dimensionless.
- ``**`` needs a literal exponent; a non-integer exponent needs a dimensionless base.
"""

import ast
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, DivisionByZero, InvalidOperation, localcontext

MAX_EXPRESSION_LENGTH = 300
MAX_OPERANDS = 20
MAX_NODES = 100
PRECISION = 34

PURE = "pure"


class CalculationError(ValueError):
    pass


@dataclass(frozen=True)
class Operand:
    """A value loaded from a fact or calculation. ``value`` is already scaled."""

    ref: str
    value: Decimal
    unit: str
    period_type: str | None = None


@dataclass(frozen=True)
class Evaluated:
    value: Decimal
    unit: str
    period_type: str | None


# --- units -----------------------------------------------------------------------------------

Dims = Counter[str]


def parse_unit(unit: str) -> Dims:
    text = unit.strip()
    dims: Dims = Counter()
    if not text or text == PURE:
        return dims
    numerator, _, denominator = text.partition("/")
    for part in numerator.split("*"):
        if part and part != PURE:
            dims[part.strip()] += 1
    for part in denominator.split("*") if denominator else []:
        if part and part != PURE:
            dims[part.strip()] -= 1
    return _clean(dims)


def format_unit(dims: Dims) -> str:
    num = [f"{u}^{e}" if e > 1 else u for u, e in sorted(dims.items()) if e > 0]
    den = [f"{u}^{-e}" if e < -1 else u for u, e in sorted(dims.items()) if e < 0]
    if not num and not den:
        return PURE
    return ("*".join(num) or "1") + ("/" + "*".join(den) if den else "")


def _clean(dims: Dims) -> Dims:
    return Counter({u: e for u, e in dims.items() if e != 0})


# --- evaluation ------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Value:
    value: Decimal
    dims: Dims
    period_type: str | None
    literal: bool = False


def _combine_period(a: _Value, b: _Value, additive: bool) -> str | None:
    if a.literal:
        return b.period_type
    if b.literal:
        return a.period_type
    if a.period_type and b.period_type and a.period_type != b.period_type:
        if additive:
            raise CalculationError(
                f"can't add or subtract a {a.period_type} value and a {b.period_type} value"
            )
        return None
    return a.period_type if a.period_type == b.period_type else None


class _Evaluator:
    def __init__(self, source: str, operands: Mapping[str, Operand]) -> None:
        self.source = source
        self.operands = operands
        self.used: set[str] = set()

    def eval(self, node: ast.AST) -> _Value:
        match node:
            case ast.Expression(body=body):
                return self.eval(body)
            case ast.Name(id=name):
                operand = self.operands.get(name)
                if operand is None:
                    raise CalculationError(f"unknown name {name!r}; every name needs an input")
                self.used.add(name)
                return _Value(operand.value, parse_unit(operand.unit), operand.period_type)
            case ast.Constant(value=value) if isinstance(value, int | float) and not isinstance(
                value, bool
            ):
                text = ast.get_source_segment(self.source, node) or repr(value)
                return _Value(Decimal(text), Counter(), None, literal=True)
            case ast.UnaryOp(op=ast.USub(), operand=inner):
                v = self.eval(inner)
                return _Value(-v.value, v.dims, v.period_type, v.literal)
            case ast.UnaryOp(op=ast.UAdd(), operand=inner):
                return self.eval(inner)
            case ast.BinOp(left=left, op=op, right=right):
                return self._binop(self.eval(left), op, self.eval(right), node)
            case _:
                raise CalculationError(f"not allowed in an expression: {type(node).__name__}")

    def _binop(self, a: _Value, op: ast.operator, b: _Value, node: ast.BinOp) -> _Value:
        literal = a.literal and b.literal
        match op:
            case ast.Add() | ast.Sub():
                # Literals are dimensionless, so "x - 1" only works when x is dimensionless too.
                if a.dims != b.dims:
                    raise CalculationError(
                        f"unit mismatch: {format_unit(a.dims)} and {format_unit(b.dims)}"
                    )
                period = _combine_period(a, b, additive=True)
                value = a.value + b.value if isinstance(op, ast.Add) else a.value - b.value
                return _Value(value, a.dims, period, literal)
            case ast.Mult():
                period = _combine_period(a, b, additive=False)
                return _Value(a.value * b.value, _clean(a.dims + b.dims), period, literal)
            case ast.Div():
                if b.value == 0:
                    raise CalculationError("division by zero")
                dims: Dims = Counter(a.dims)
                dims.subtract(b.dims)
                period = _combine_period(a, b, additive=False)
                return _Value(a.value / b.value, _clean(dims), period, literal)
            case ast.Pow():
                if not b.literal:
                    raise CalculationError("exponents must be numeric literals")
                if b.value == b.value.to_integral_value():
                    exp = int(b.value)
                    dims: Dims = Counter({u: e * exp for u, e in a.dims.items()})
                elif a.dims:
                    raise CalculationError("a fractional exponent needs a dimensionless base")
                else:
                    dims = Dims()
                if a.value < 0 and b.value != b.value.to_integral_value():
                    raise CalculationError("fractional power of a negative number")
                return _Value(a.value**b.value, _clean(dims), a.period_type, literal)
            case _:
                segment = ast.get_source_segment(self.source, node) or "?"
                raise CalculationError(f"operator not allowed: {segment}")


def evaluate(expression: str, operands: Mapping[str, Operand]) -> Evaluated:
    """Evaluate ``expression`` (names refer to ``operands``) with Decimal arithmetic."""
    if len(expression) > MAX_EXPRESSION_LENGTH:
        raise CalculationError(f"expression longer than {MAX_EXPRESSION_LENGTH} characters")
    if len(operands) > MAX_OPERANDS:
        raise CalculationError(f"more than {MAX_OPERANDS} inputs")
    try:
        tree = ast.parse(expression.strip(), mode="eval")
    except SyntaxError as exc:
        raise CalculationError(f"can't parse expression: {exc.msg}") from exc
    if sum(1 for _ in ast.walk(tree)) > MAX_NODES:
        raise CalculationError("expression too complex")

    evaluator = _Evaluator(expression.strip(), operands)
    with localcontext() as ctx:
        ctx.prec = PRECISION
        ctx.traps[DivisionByZero] = True
        ctx.traps[InvalidOperation] = True
        try:
            result = evaluator.eval(tree)
        except (DivisionByZero, InvalidOperation, OverflowError) as exc:
            raise CalculationError(f"arithmetic error: {exc.__class__.__name__}") from exc

    unused = set(operands) - evaluator.used
    if unused:
        raise CalculationError(f"inputs not used in the expression: {sorted(unused)}")
    if result.literal:
        raise CalculationError("expression uses no inputs; constants alone are not evidence")
    return Evaluated(result.value, format_unit(result.dims), result.period_type)
