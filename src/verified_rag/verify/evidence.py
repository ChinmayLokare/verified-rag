"""Load cited evidence for the verifier, recomputing calculations from their inputs.

Reads only through the read-only templates. No LangChain or LangGraph imports.
"""

from collections.abc import Iterable
from decimal import Decimal

import psycopg

from verified_rag.refs import RefError, parse_ref
from verified_rag.store.db import QueryLimits
from verified_rag.store.templates import (
    CalculationRow,
    FactRow,
    calculations_by_id,
    entities_by_id,
    facts_by_id,
)
from verified_rag.tools.calculator import CalculationError, Operand, evaluate
from verified_rag.verify.verifier import Evidence

MAX_DEPTH = 5  # calculations built on calculations


class EvidenceError(RuntimeError):
    pass


def load_evidence(
    conn: psycopg.Connection, refs: Iterable[str], limits: QueryLimits
) -> dict[str, Evidence]:
    """Evidence for every valid reference in ``refs``. Unknown references are left out.

    A calculation whose stored result doesn't match a fresh recomputation raises
    ``EvidenceError``: the verifier must not run on tampered or stale results.
    """
    facts: dict[int, FactRow] = {}
    calcs: dict[int, CalculationRow] = {}
    pending = {r.strip() for r in refs}
    for _ in range(MAX_DEPTH + 1):
        fact_ids, calc_ids = set[int](), set[int]()
        for text in pending:
            try:
                ref = parse_ref(text)
            except RefError:
                continue
            (fact_ids if ref.kind == "fact" else calc_ids).add(ref.id)
        fact_ids -= facts.keys()
        calc_ids -= calcs.keys()
        if not fact_ids and not calc_ids:
            break
        if fact_ids:
            facts.update(facts_by_id(conn, sorted(fact_ids), limits))
        new_calcs = calculations_by_id(conn, sorted(calc_ids), limits) if calc_ids else {}
        calcs.update(new_calcs)
        pending = {ref for c in new_calcs.values() for ref in c.input_refs.values()}
    else:
        raise EvidenceError("calculations nested too deeply")

    entity_ids = sorted({f.entity_id for f in facts.values()})
    entities = entities_by_id(conn, entity_ids, limits) if entity_ids else {}

    def names(fact: FactRow) -> frozenset[str]:
        row = entities.get(fact.entity_id)
        raw = [fact.entity_name, *(row.aliases if row else [])]
        return frozenset(n.lower() for n in raw if n)

    def years(fact: FactRow) -> frozenset[int]:
        found = {fact.period_end.year}
        if fact.fiscal_year is not None:
            found.add(fact.fiscal_year)
        return frozenset(found)

    out: dict[str, Evidence] = {}
    for fact in facts.values():
        out[fact.ref] = Evidence(
            ref=fact.ref,
            value=fact.scaled_value,
            unit=fact.unit,
            years=years(fact),
            entity_names=names(fact),
            description=f"{fact.entity_name} {fact.concept_name} FY{fact.fiscal_year}"
            f" {fact.fiscal_period or ''}".strip(),
        )

    def build_calc(calc: CalculationRow, depth: int) -> Evidence:
        if calc.ref in out:
            return out[calc.ref]
        if depth > MAX_DEPTH:
            raise EvidenceError("calculations nested too deeply")
        operands: dict[str, Operand] = {}
        inputs: list[Evidence] = []
        for name, ref_text in calc.input_refs.items():
            ref = parse_ref(ref_text)
            if ref.kind == "fact":
                if ref_text not in out:
                    raise EvidenceError(f"{calc.ref} uses missing {ref_text}")
                ev = out[ref_text]
                fact = facts[ref.id]
                period = fact.period_type
            else:
                inner = calcs.get(ref.id)
                if inner is None:
                    raise EvidenceError(f"{calc.ref} uses missing {ref_text}")
                ev = build_calc(inner, depth + 1)
                period = inner.period_type
            inputs.append(ev)
            operands[name] = Operand(ref_text, ev.value, ev.unit, period)
        try:
            fresh = evaluate(calc.expression, operands)
        except CalculationError as exc:
            raise EvidenceError(f"{calc.ref} no longer evaluates: {exc}") from exc
        if fresh.value != Decimal(calc.result) or fresh.unit != (calc.unit or "pure"):
            raise EvidenceError(f"{calc.ref} stored result differs from recomputation")
        ev = Evidence(
            ref=calc.ref,
            value=fresh.value,
            unit=fresh.unit,
            years=frozenset[int]().union(*(i.years for i in inputs)),
            entity_names=frozenset[str]().union(*(i.entity_names for i in inputs)),
            description=f"{calc.expression} with "
            + ", ".join(f"{k}={v}" for k, v in calc.input_refs.items()),
        )
        out[calc.ref] = ev
        return ev

    for calc in calcs.values():
        build_calc(calc, 0)
    return out
