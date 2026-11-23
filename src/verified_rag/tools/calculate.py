"""The calculate tool: arithmetic over fact and calculation references (design.md section 8.3).

1. Parse the references and load their values through the read-only views.
2. Apply each fact's scale.
3. Evaluate with the pure calculator (unit and period checks, Decimal math, no eval).
4. Store the result in ``calculations`` with its input references and return its ``calc_id``.

The model passes references, never numbers, so it can't transpose digits or substitute a
remembered figure.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from verified_rag.refs import Ref, calc_ref, parse_ref
from verified_rag.store.db import QueryLimits, returned_id
from verified_rag.store.templates import calculations_by_id, facts_by_id
from verified_rag.tools.calculator import CalculationError, Operand, evaluate


@dataclass(frozen=True)
class CalcResult:
    calc_id: int
    value: Decimal
    unit: str
    period_type: str | None
    expression: str
    inputs: dict[str, str]

    @property
    def ref(self) -> str:
        return calc_ref(self.calc_id)


def load_operands(
    conn: psycopg.Connection, inputs: Mapping[str, str], limits: QueryLimits
) -> dict[str, Operand]:
    if not inputs:
        raise CalculationError("at least one input reference is required")
    parsed: dict[str, Ref] = {}
    for name, text in inputs.items():
        if not name.isidentifier() or name.startswith("_"):
            raise CalculationError(f"invalid input name {name!r}")
        try:
            parsed[name] = parse_ref(text)
        except ValueError as exc:
            raise CalculationError(str(exc)) from exc

    fact_ids = [r.id for r in parsed.values() if r.kind == "fact"]
    calc_ids = [r.id for r in parsed.values() if r.kind == "calc"]
    facts = facts_by_id(conn, fact_ids, limits) if fact_ids else {}
    calcs = calculations_by_id(conn, calc_ids, limits) if calc_ids else {}

    operands: dict[str, Operand] = {}
    for name, ref in parsed.items():
        if ref.kind == "fact":
            fact = facts.get(ref.id)
            if fact is None:
                raise CalculationError(f"no such fact: {ref}")
            operands[name] = Operand(str(ref), fact.scaled_value, fact.unit, fact.period_type)
        else:
            calc = calcs.get(ref.id)
            if calc is None:
                raise CalculationError(f"no such calculation: {ref}")
            operands[name] = Operand(str(ref), calc.result, calc.unit or "pure", calc.period_type)
    return operands


def calculate(
    conn: psycopg.Connection,
    expression: str,
    inputs: Mapping[str, str],
    limits: QueryLimits,
    request_id: UUID | None = None,
) -> CalcResult:
    operands = load_operands(conn, inputs, limits)
    result = evaluate(expression, operands)
    refs = {name: op.ref for name, op in operands.items()}
    with conn.transaction():
        row = conn.execute(
            "INSERT INTO calculations (request_id, expression, input_refs, result, unit,"
            " period_type) VALUES (%s, %s, %s, %s, %s, %s) RETURNING calc_id",
            (request_id, expression, Jsonb(refs), result.value, result.unit, result.period_type),
        ).fetchone()
    return CalcResult(
        returned_id(row), result.value, result.unit, result.period_type, expression, refs
    )
