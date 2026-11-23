from decimal import Decimal

import pytest

from verified_rag.tools.calculator import (
    CalculationError,
    Operand,
    evaluate,
    format_unit,
    parse_unit,
)

REV_23 = Operand("f_1", Decimal("383285000000"), "USD", "duration")
REV_24 = Operand("f_2", Decimal("391035000000"), "USD", "duration")
OPINC_24 = Operand("f_3", Decimal("123216000000"), "USD", "duration")
ASSETS = Operand("f_4", Decimal("364980000000"), "USD", "instant")
SHARES = Operand("f_5", Decimal("15000000000"), "shares", "duration")
EUR = Operand("f_6", Decimal("10"), "EUR", "duration")


def test_growth_percent_is_exact_decimal() -> None:
    r = evaluate("(a - b) / b * 100", {"a": REV_24, "b": REV_23})
    assert r.unit == "pure"
    assert r.period_type == "duration"
    assert r.value.quantize(Decimal("0.0001")) == Decimal("2.0220")


def test_margin() -> None:
    r = evaluate("op / rev", {"op": OPINC_24, "rev": REV_24})
    assert r.unit == "pure"
    assert round(r.value * 100, 2) == Decimal("31.51")


def test_decimal_literals_are_not_floats() -> None:
    r = evaluate("x * 0.1 + x * 0.2", {"x": Operand("f_9", Decimal(1), "pure")})
    assert r.value == Decimal("0.3")


def test_per_share_unit() -> None:
    r = evaluate("rev / s", {"rev": REV_24, "s": SHARES})
    assert r.unit == "USD/shares"


def test_compound_growth_with_fractional_power() -> None:
    r = evaluate("(b / a) ** (1 / 2) - 1", {"a": REV_23, "b": REV_24})
    assert r.unit == "pure"
    assert Decimal("0.0100") < r.value < Decimal("0.0101")


def test_ratio_of_instant_and_duration_is_allowed() -> None:
    r = evaluate("rev / assets", {"rev": REV_24, "assets": ASSETS})
    assert r.unit == "pure"
    assert r.period_type is None


@pytest.mark.parametrize(
    ("expr", "operands", "message"),
    [
        ("a + b", {"a": REV_24, "b": ASSETS}, "instant"),
        ("a + b", {"a": REV_24, "b": EUR}, "unit mismatch"),
        ("a - 1", {"a": REV_24}, "unit mismatch"),
        ("a / (b - b)", {"a": REV_24, "b": REV_23}, "division by zero"),
        ("a + c", {"a": REV_24}, "unknown name"),
        ("a", {"a": REV_24, "b": REV_23}, "not used"),
        ("2 + 2", {}, "no inputs"),
        ("a ** b", {"a": REV_24, "b": REV_23}, "literals"),
        ("a ** 0.5", {"a": REV_24}, "dimensionless"),
        ("a % 2", {"a": REV_24}, "operator not allowed"),
        ("__import__('os')", {}, "not allowed"),
        ("a.real", {"a": REV_24}, "not allowed"),
        ("[a][0]", {"a": REV_24}, "not allowed"),
        ("a if a else a", {"a": REV_24}, "not allowed"),
        ("a +", {"a": REV_24}, "can't parse"),
        ("a + " * 200 + "a", {"a": REV_24}, "longer than"),
    ],
)
def test_rejections(expr: str, operands: dict[str, Operand], message: str) -> None:
    with pytest.raises(CalculationError, match=message):
        evaluate(expr, operands)


@pytest.mark.parametrize("unit", ["USD", "USD/shares", "pure", "shares", "USD*shares/EUR"])
def test_unit_round_trip(unit: str) -> None:
    assert format_unit(parse_unit(unit)) == unit
