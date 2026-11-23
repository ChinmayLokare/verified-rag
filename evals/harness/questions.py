"""Build a smoke question set from the facts table (M2). Deterministic for a given seed.

Expected values come from the database, never from memory. This set measures the agent and
verifier (did it find and state the right stored figure?), not the data itself; FinanceBench
covers that in M4.
"""

import random
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

import psycopg

from verified_rag.store.db import QueryLimits
from verified_rag.store.templates import LookupParams, run_template
from verified_rag.tools.calculator import Operand, evaluate

Kind = Literal["lookup", "growth", "refuse"]


@dataclass(frozen=True)
class Question:
    id: str
    kind: Kind
    text: str
    expected: Decimal | None  # None: the right outcome is a refusal
    unit: str | None


# (concept name, phrase used in the question)
LOOKUP_CONCEPTS = [
    ("revenue", "revenue"),
    ("net_income", "net income"),
    ("operating_income", "operating income"),
    ("total_assets", "total assets"),
    ("eps_diluted", "diluted earnings per share"),
]
YEARS = [2022, 2023, 2024]

REFUSALS = [
    "Should I buy {name} stock right now?",
    "What will {name}'s revenue be next year?",
    "What was {name}'s revenue in fiscal 1985?",
    "What is {name}'s current stock price?",
]


def _entities(conn: psycopg.Connection, pack_id: str) -> list[tuple[int, str]]:
    rows = conn.execute(
        "SELECT entity_id, name, aliases FROM entities WHERE pack_id = %s ORDER BY entity_id",
        (pack_id,),
    ).fetchall()
    out: list[tuple[int, str]] = []
    for entity_id, name, aliases in rows:
        # Prefer a friendly alias ("Apple") over the legal name ("Apple Inc.").
        friendly = [a for a in aliases if not a.isupper() or len(a) > 5]
        out.append((int(entity_id), str(friendly[0] if friendly else name)))
    return out


def _concept_ids(conn: psycopg.Connection, pack_id: str) -> dict[str, int]:
    rows = conn.execute("SELECT name, concept_id FROM concepts WHERE pack_id = %s", (pack_id,))
    return {str(n): int(i) for n, i in rows}


def _value(
    conn: psycopg.Connection, entity: int, concept: int, year: int, limits: QueryLimits
) -> tuple[Decimal, str, str] | None:
    rows = run_template(
        conn, LookupParams(entity_id=entity, concept_id=concept, fiscal_year=year), limits
    ).rows
    if len(rows) != 1:
        return None  # missing or ambiguous: not a fair question
    return rows[0].scaled_value, rows[0].unit, rows[0].period_type


def build(
    conn: psycopg.Connection,
    pack_id: str,
    limits: QueryLimits,
    *,
    n_lookup: int = 12,
    n_growth: int = 4,
    n_refuse: int = 4,
    seed: int = 7,
) -> list[Question]:
    rng = random.Random(seed)  # noqa: S311 - sampling questions, not security
    entities = _entities(conn, pack_id)
    concepts = _concept_ids(conn, pack_id)
    questions: list[Question] = []

    combos = [(e, c, y) for e in entities for c in LOOKUP_CONCEPTS for y in YEARS]
    rng.shuffle(combos)
    for (entity_id, name), (concept, phrase), year in combos:
        if sum(q.kind == "lookup" for q in questions) >= n_lookup:
            break
        found = _value(conn, entity_id, concepts[concept], year, limits)
        if found is None:
            continue
        value, unit, _ = found
        questions.append(
            Question(
                f"lookup-{len(questions)}",
                "lookup",
                f"What was {name}'s {phrase} in fiscal {year}?",
                value,
                unit,
            )
        )

    growth_pool = [(e, y) for e in entities for y in YEARS[1:]]
    rng.shuffle(growth_pool)
    for (entity_id, name), year in growth_pool:
        if sum(q.kind == "growth" for q in questions) >= n_growth:
            break
        new = _value(conn, entity_id, concepts["revenue"], year, limits)
        old = _value(conn, entity_id, concepts["revenue"], year - 1, limits)
        if new is None or old is None or old[0] == 0:
            continue
        result = evaluate(
            "(a - b) / b * 100",
            {"a": Operand("a", new[0], new[1], new[2]), "b": Operand("b", old[0], old[1], old[2])},
        )
        questions.append(
            Question(
                f"growth-{len(questions)}",
                "growth",
                f"By what percent did {name}'s revenue change"
                f" from fiscal {year - 1} to fiscal {year}?",
                result.value,
                "pure",
            )
        )

    names = [n for _, n in entities]
    rng.shuffle(names)
    for i, template in enumerate(REFUSALS[:n_refuse]):
        name = names[i % len(names)]
        questions.append(Question(f"refuse-{i}", "refuse", template.format(name=name), None, None))
    return questions
