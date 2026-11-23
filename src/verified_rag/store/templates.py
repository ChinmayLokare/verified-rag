"""Fixed query templates (design.md section 8.7, ADR-1).

The agent never writes SQL. It picks a template and fills typed parameters (the Pydantic models
below); values are always bound, never formatted into the query. Every query runs through
``read_only``: a read-only transaction as the reader role, which can only SELECT the v_* views,
with a statement timeout and a row cap.

Facts default to ``is_latest_reported`` and whole-entity values (``dimensions IS NULL``).
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal, LiteralString

import psycopg
from psycopg.rows import class_row
from pydantic import BaseModel, ConfigDict, Field, PositiveInt, model_validator

from verified_rag.refs import calc_ref, fact_ref
from verified_rag.store.db import QueryLimits, read_only

FiscalPeriodName = Literal["FY", "Q1", "Q2", "Q3", "Q4", "H1", "9M"]
Year = Annotated[int, Field(ge=1990, le=2100)]
MAX_YEAR_SPAN = 30
MAX_COMPARE_ENTITIES = 10


# --- rows ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class FactRow:
    fact_id: int
    entity_id: int
    entity_name: str
    concept_id: int
    concept_name: str
    value: Decimal
    unit: str
    scale: int
    period_start: date | None
    period_end: date
    period_type: str
    fiscal_year: int | None
    fiscal_period: str | None
    dimensions: dict[str, Any] | None
    is_latest_reported: bool
    document_external_id: str
    doc_type: str
    filed_at: datetime | None
    source_uri: str
    source_tag: str | None

    @property
    def ref(self) -> str:
        return fact_ref(self.fact_id)

    @property
    def scaled_value(self) -> Decimal:
        return self.value.scaleb(self.scale)


@dataclass(frozen=True)
class CalculationRow:
    calc_id: int
    expression: str
    input_refs: dict[str, str]
    result: Decimal
    unit: str | None
    period_type: str | None

    @property
    def ref(self) -> str:
        return calc_ref(self.calc_id)


@dataclass(frozen=True)
class EntityRow:
    entity_id: int
    external_id: str
    name: str
    aliases: list[str]
    attributes: dict[str, Any]


@dataclass(frozen=True)
class ConceptAliasRow:
    concept_id: int
    concept_name: str
    description: str | None
    unit_kind: str
    alias: str
    source: str


@dataclass(frozen=True)
class QueryResult:
    rows: list[FactRow]
    truncated: bool  # more rows matched than the row cap allows


# --- parameters (what the agent fills in) ----------------------------------------------------


class _Params(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class LookupParams(_Params):
    """One figure: an entity's concept for one fiscal year and period."""

    template: Literal["lookup"] = "lookup"
    entity_id: PositiveInt
    concept_id: PositiveInt
    fiscal_year: Year
    fiscal_period: FiscalPeriodName = "FY"


class TimeSeriesParams(_Params):
    """An entity's concept across a range of fiscal years."""

    template: Literal["time_series"] = "time_series"
    entity_id: PositiveInt
    concept_id: PositiveInt
    from_year: Year
    to_year: Year
    fiscal_period: FiscalPeriodName = "FY"

    @model_validator(mode="after")
    def _range(self) -> "TimeSeriesParams":
        _check_range(self.from_year, self.to_year)
        return self


class CompareEntitiesParams(_Params):
    """One concept for several entities across a range of fiscal years."""

    template: Literal["compare_entities"] = "compare_entities"
    entity_ids: Annotated[list[PositiveInt], Field(min_length=2, max_length=MAX_COMPARE_ENTITIES)]
    concept_id: PositiveInt
    from_year: Year
    to_year: Year
    fiscal_period: FiscalPeriodName = "FY"

    @model_validator(mode="after")
    def _range(self) -> "CompareEntitiesParams":
        _check_range(self.from_year, self.to_year)
        if len(set(self.entity_ids)) != len(self.entity_ids):
            raise ValueError("entity_ids must be distinct")
        return self


class BreakdownParams(_Params):
    """A concept split by dimension (segment, region) for one period."""

    template: Literal["breakdown"] = "breakdown"
    entity_id: PositiveInt
    concept_id: PositiveInt
    fiscal_year: Year
    fiscal_period: FiscalPeriodName = "FY"


class FactSourceParams(_Params):
    """Where one fact came from."""

    template: Literal["fact_source"] = "fact_source"
    fact_id: PositiveInt


TemplateParams = Annotated[
    LookupParams | TimeSeriesParams | CompareEntitiesParams | BreakdownParams | FactSourceParams,
    Field(discriminator="template"),
]


def _check_range(from_year: int, to_year: int) -> None:
    if from_year > to_year:
        raise ValueError("from_year must not be after to_year")
    if to_year - from_year > MAX_YEAR_SPAN:
        raise ValueError(f"year range longer than {MAX_YEAR_SPAN} years")


# --- SQL -------------------------------------------------------------------------------------

_FACT_COLUMNS: LiteralString = (
    "SELECT fact_id, entity_id, entity_name, concept_id, concept_name, value, unit, scale,"
    " period_start, period_end, period_type, fiscal_year, fiscal_period, dimensions,"
    " is_latest_reported, document_external_id, doc_type, filed_at, source_uri, source_tag"
    " FROM v_facts"
)

_LOOKUP: LiteralString = (
    _FACT_COLUMNS + " WHERE entity_id = %(entity_id)s AND concept_id = %(concept_id)s"
    " AND fiscal_year = %(fiscal_year)s AND fiscal_period = %(fiscal_period)s"
    " AND dimensions IS NULL AND is_latest_reported"
    " ORDER BY period_end, period_start NULLS FIRST LIMIT %(limit)s"
)

_TIME_SERIES: LiteralString = (
    _FACT_COLUMNS + " WHERE entity_id = %(entity_id)s AND concept_id = %(concept_id)s"
    " AND fiscal_year BETWEEN %(from_year)s AND %(to_year)s"
    " AND fiscal_period = %(fiscal_period)s"
    " AND dimensions IS NULL AND is_latest_reported"
    " ORDER BY fiscal_year, period_end LIMIT %(limit)s"
)

_COMPARE: LiteralString = (
    _FACT_COLUMNS + " WHERE entity_id = ANY(%(entity_ids)s) AND concept_id = %(concept_id)s"
    " AND fiscal_year BETWEEN %(from_year)s AND %(to_year)s"
    " AND fiscal_period = %(fiscal_period)s"
    " AND dimensions IS NULL AND is_latest_reported"
    " ORDER BY entity_name, fiscal_year, period_end LIMIT %(limit)s"
)

_BREAKDOWN: LiteralString = (
    _FACT_COLUMNS + " WHERE entity_id = %(entity_id)s AND concept_id = %(concept_id)s"
    " AND fiscal_year = %(fiscal_year)s AND fiscal_period = %(fiscal_period)s"
    " AND dimensions IS NOT NULL AND is_latest_reported"
    " ORDER BY dimensions::text, period_end LIMIT %(limit)s"
)

_FACT_SOURCE: LiteralString = _FACT_COLUMNS + " WHERE fact_id = %(fact_id)s LIMIT %(limit)s"

_FACTS_BY_ID: LiteralString = (
    _FACT_COLUMNS + " WHERE fact_id = ANY(%(ids)s) ORDER BY fact_id LIMIT %(limit)s"
)

_CALCS_BY_ID: LiteralString = (
    "SELECT calc_id, expression, input_refs, result, unit, period_type FROM v_calculations"
    " WHERE calc_id = ANY(%(ids)s) ORDER BY calc_id LIMIT %(limit)s"
)

_ENTITIES: LiteralString = (
    "SELECT entity_id, external_id, name, aliases, attributes FROM v_entities"
    " WHERE pack_id = %(pack_id)s ORDER BY entity_id LIMIT %(limit)s"
)

_ENTITIES_BY_ID: LiteralString = (
    "SELECT entity_id, external_id, name, aliases, attributes FROM v_entities"
    " WHERE entity_id = ANY(%(ids)s) ORDER BY entity_id LIMIT %(limit)s"
)

_CONCEPT_ALIASES: LiteralString = (
    "SELECT a.concept_id, a.concept_name, c.description, c.unit_kind, a.alias, a.source"
    " FROM v_concept_aliases a JOIN v_concepts c ON c.concept_id = a.concept_id"
    " WHERE a.pack_id = %(pack_id)s AND a.source IN ('curated', 'tag')"
    " UNION ALL"
    " SELECT concept_id, name, description, unit_kind, name, 'name' FROM v_concepts"
    " WHERE pack_id = %(pack_id)s LIMIT %(limit)s"
)

_SQL: dict[str, LiteralString] = {
    "lookup": _LOOKUP,
    "time_series": _TIME_SERIES,
    "compare_entities": _COMPARE,
    "breakdown": _BREAKDOWN,
    "fact_source": _FACT_SOURCE,
}


# --- execution -------------------------------------------------------------------------------


def run_template(
    conn: psycopg.Connection, params: TemplateParams, limits: QueryLimits
) -> QueryResult:
    """Run one fact template. Returns at most ``limits.max_rows`` rows."""
    bound = params.model_dump(exclude={"template"})
    return _facts(conn, _SQL[params.template], bound, limits)


def _facts(
    conn: psycopg.Connection, sql: LiteralString, bound: dict[str, Any], limits: QueryLimits
) -> QueryResult:
    with read_only(conn, limits), conn.cursor(row_factory=class_row(FactRow)) as cur:
        rows = cur.execute(sql, {**bound, "limit": limits.max_rows + 1}).fetchall()
    return QueryResult(rows[: limits.max_rows], truncated=len(rows) > limits.max_rows)


def facts_by_id(
    conn: psycopg.Connection, fact_ids: list[int], limits: QueryLimits
) -> dict[int, FactRow]:
    result = _facts(conn, _FACTS_BY_ID, {"ids": fact_ids}, limits)
    return {r.fact_id: r for r in result.rows}


def calculations_by_id(
    conn: psycopg.Connection, calc_ids: list[int], limits: QueryLimits
) -> dict[int, CalculationRow]:
    with read_only(conn, limits), conn.cursor(row_factory=class_row(CalculationRow)) as cur:
        rows = cur.execute(_CALCS_BY_ID, {"ids": calc_ids, "limit": limits.max_rows}).fetchall()
    return {r.calc_id: r for r in rows}


def list_entities(conn: psycopg.Connection, pack_id: str, limits: QueryLimits) -> list[EntityRow]:
    with read_only(conn, limits), conn.cursor(row_factory=class_row(EntityRow)) as cur:
        return cur.execute(_ENTITIES, {"pack_id": pack_id, "limit": limits.max_rows}).fetchall()


def entities_by_id(
    conn: psycopg.Connection, entity_ids: list[int], limits: QueryLimits
) -> dict[int, EntityRow]:
    with read_only(conn, limits), conn.cursor(row_factory=class_row(EntityRow)) as cur:
        rows = cur.execute(
            _ENTITIES_BY_ID, {"ids": entity_ids, "limit": limits.max_rows}
        ).fetchall()
    return {r.entity_id: r for r in rows}


def list_concept_aliases(
    conn: psycopg.Connection, pack_id: str, limits: QueryLimits
) -> list[ConceptAliasRow]:
    # Vocabulary lists are larger than answer row sets; allow more but still cap them.
    cap = limits.max_rows * 20
    with read_only(conn, limits), conn.cursor(row_factory=class_row(ConceptAliasRow)) as cur:
        return cur.execute(_CONCEPT_ALIASES, {"pack_id": pack_id, "limit": cap}).fetchall()
