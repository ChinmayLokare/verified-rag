"""Agent tools: schemas the model sees, and a dispatcher that runs them (design.md 8.3).

The schemas are Pydantic models bound to the chat model. The dispatcher calls the plain,
framework-free modules (resolve, templates, calculate) and returns compact JSON. Every
fact or calculation reference a tool returns is recorded, so the verifier can reject
references the model never actually received.
"""

import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from uuid import UUID

import psycopg
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from verified_rag.store.db import QueryLimits
from verified_rag.store.templates import (
    CompareEntitiesParams,
    FactRow,
    LookupParams,
    TimeSeriesParams,
    run_template,
)
from verified_rag.tools.calculate import calculate
from verified_rag.tools.calculator import CalculationError
from verified_rag.tools.resolve import resolve_concept, resolve_entity, unambiguous
from verified_rag.verify.verifier import DraftAnswer

FiscalPeriod = str  # validated by the template params


_COMPANY = Field(description="Company name or ticker, e.g. 'Apple' or 'AAPL'")
_CONCEPT = Field(description="Figure name, e.g. 'revenue', 'net income', 'total assets'")
_PERIOD = Field(default="FY", description="FY (full year), Q1, Q2, Q3, Q4, H1 or 9M")


class LookupFact(BaseModel):
    """Get one reported figure: one company, one figure, one fiscal year."""

    model_config = ConfigDict(title="lookup_fact")
    company: str = _COMPANY
    concept: str = _CONCEPT
    fiscal_year: int
    fiscal_period: FiscalPeriod = _PERIOD


class FactTimeSeries(BaseModel):
    """Get one company's figure for every fiscal year from from_year to to_year."""

    model_config = ConfigDict(title="fact_time_series")
    company: str = _COMPANY
    concept: str = _CONCEPT
    from_year: int
    to_year: int
    fiscal_period: FiscalPeriod = _PERIOD


class CompareEntities(BaseModel):
    """Get the same figure for several companies across a range of fiscal years."""

    model_config = ConfigDict(title="compare_entities")
    companies: list[str] = Field(description="Two or more company names or tickers")
    concept: str = _CONCEPT
    from_year: int
    to_year: int
    fiscal_period: FiscalPeriod = _PERIOD


class Calculate(BaseModel):
    """Do arithmetic on figures by reference. Never type numbers from tool results here.

    Example: expression "(a - b) / b * 100", inputs {"a": "f_12", "b": "f_9"} gives percent
    change. Allowed: + - * / ** and parentheses. Returns a calc_ reference.
    """

    model_config = ConfigDict(title="calculate")
    expression: str
    inputs: dict[str, str] = Field(description='Name to reference, e.g. {"a": "f_12"}')


class SubmitAnswer(DraftAnswer):
    """Submit the final answer. Every claim needs evidence refs (f_ or calc_) from tool results.

    Every number in answer_text must also appear in a claim. Write numbers as the tools gave
    them, or rounded (e.g. "$391.0 billion", "31.5%").
    """

    model_config = ConfigDict(title="submit_answer", extra="forbid")


# Lookups take names and resolve them internally (ADR-10): fewer steps, no IDs to guess.
TOOL_SCHEMAS: list[type[BaseModel]] = [
    LookupFact,
    FactTimeSeries,
    CompareEntities,
    Calculate,
    SubmitAnswer,
]
SUBMIT = "submit_answer"


class UnresolvedName(Exception):
    """A company or figure name that doesn't resolve to exactly one match."""


@dataclass
class ToolOutcome:
    content: str
    refs: list[str] = field(default_factory=list[str])
    error: bool = False


def _display(value: Decimal, unit: str) -> str:
    """A readable rendering that the verifier will accept, e.g. '391.04 billion USD'."""
    magnitude = abs(value)
    for size, word in (
        (Decimal("1e12"), "trillion"),
        (Decimal("1e9"), "billion"),
        (Decimal("1e6"), "million"),
    ):
        if magnitude >= size and unit != "pure":
            return f"{(value / size).quantize(Decimal('0.01'))} {word} {unit}"
    return f"{value.quantize(Decimal('0.0001')).normalize():f} {unit}"


def _fact(row: FactRow) -> dict[str, Any]:
    period = f"{row.period_start}..{row.period_end}" if row.period_start else f"at {row.period_end}"
    filed = row.filed_at.date().isoformat() if row.filed_at else None
    return {
        "ref": row.ref,
        "entity": row.entity_name,
        "concept": row.concept_name,
        "fiscal_year": row.fiscal_year,
        "fiscal_period": row.fiscal_period,
        "value": str(row.scaled_value),
        "display": _display(row.scaled_value, row.unit),
        "unit": row.unit,
        "period": period,
        "source": f"{row.doc_type} {row.document_external_id} filed {filed}",
    }


def _json(data: Any) -> str:
    return json.dumps(data, separators=(",", ":"), default=str)


class AgentTools:
    """Runs tool calls for one request against the read-only data path."""

    def __init__(
        self,
        conn: psycopg.Connection,
        pack_id: str,
        limits: QueryLimits,
        request_id: UUID | None = None,
    ) -> None:
        self.conn = conn
        self.pack_id = pack_id
        self.limits = limits
        self.request_id = request_id

    @staticmethod
    def _error(message: str) -> ToolOutcome:
        return ToolOutcome(_json({"error": message}), error=True)

    def _entity(self, name: str) -> int:
        candidates = resolve_entity(self.conn, self.pack_id, name, self.limits)
        choice = unambiguous(candidates)
        if choice is None:
            if not candidates:
                raise UnresolvedName(f"no company matches {name!r}")
            names = ", ".join(c.name for c in candidates)
            raise UnresolvedName(
                f"company {name!r} is ambiguous ({names}); use a more specific name"
            )
        return choice.entity_id

    def _concept(self, phrase: str) -> int:
        candidates = resolve_concept(self.conn, self.pack_id, phrase, self.limits)
        choice = unambiguous(candidates)
        if choice is None:
            if not candidates:
                raise UnresolvedName(f"no figure matches {phrase!r}")
            names = ", ".join(c.name.replace("_", " ") for c in candidates)
            raise UnresolvedName(
                f"figure {phrase!r} is ambiguous ({names}); use one of these names"
            )
        return choice.concept_id

    def run(self, name: str, args: dict[str, Any]) -> ToolOutcome:
        try:
            return self._run(name, args)
        except (ValidationError, ValueError, CalculationError, UnresolvedName) as exc:
            return ToolOutcome(_json({"error": str(exc)[:500]}), error=True)
        except psycopg.Error as exc:
            return ToolOutcome(
                _json({"error": f"query failed: {exc.__class__.__name__}"}), error=True
            )

    def _run(self, name: str, args: dict[str, Any]) -> ToolOutcome:
        if name == "lookup_fact":
            a = LookupFact.model_validate(args)
            entity, concept = self._entity(a.company), self._concept(a.concept)
            params = LookupParams(
                entity_id=entity,
                concept_id=concept,
                fiscal_year=a.fiscal_year,
                fiscal_period=a.fiscal_period,  # pyright: ignore[reportArgumentType]
            )
        elif name == "fact_time_series":
            a = FactTimeSeries.model_validate(args)
            entity, concept = self._entity(a.company), self._concept(a.concept)
            params = TimeSeriesParams(
                entity_id=entity,
                concept_id=concept,
                from_year=a.from_year,
                to_year=a.to_year,
                fiscal_period=a.fiscal_period,  # pyright: ignore[reportArgumentType]
            )
        elif name == "compare_entities":
            a = CompareEntities.model_validate(args)
            params = CompareEntitiesParams(
                entity_ids=[self._entity(c) for c in a.companies],
                concept_id=self._concept(a.concept),
                from_year=a.from_year,
                to_year=a.to_year,
                fiscal_period=a.fiscal_period,  # pyright: ignore[reportArgumentType]
            )
        elif name == "calculate":
            a = Calculate.model_validate(args)
            result = calculate(self.conn, a.expression, a.inputs, self.limits, self.request_id)
            body = {
                "ref": result.ref,
                "value": str(result.value),
                "display": _display(result.value, result.unit),
                "unit": result.unit,
                "expression": result.expression,
                "inputs": result.inputs,
            }
            return ToolOutcome(_json(body), refs=[result.ref])
        else:
            return ToolOutcome(_json({"error": f"unknown tool {name!r}"}), error=True)

        result = run_template(self.conn, params, self.limits)
        facts = [_fact(r) for r in result.rows]
        body: dict[str, Any] = {"facts": facts}
        if not facts:
            body["note"] = "no matching facts; do not guess a value"
        if result.truncated:
            body["note"] = "more rows exist than shown"
        return ToolOutcome(_json(body), refs=[r.ref for r in result.rows])
