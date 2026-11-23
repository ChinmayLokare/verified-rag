"""End to end on the facts path: ingest the fixture company, then query, resolve, calculate."""

from decimal import Decimal

import psycopg
import pytest
from pydantic import ValidationError

from verified_rag.cli import main
from verified_rag.ingest.pipeline import ingest
from verified_rag.packs.interface import EntityConfig, Pack, PackContext
from verified_rag.store.db import QueryLimits, connect, read_only
from verified_rag.store.templates import (
    CompareEntitiesParams,
    FactSourceParams,
    LookupParams,
    TimeSeriesParams,
    run_template,
)
from verified_rag.tools.calculate import calculate
from verified_rag.tools.calculator import CalculationError
from verified_rag.tools.resolve import resolve_concept, resolve_entity, unambiguous

pytestmark = pytest.mark.integration

LIMITS = QueryLimits(timeout_ms=3000, max_rows=50)
EXMP = [EntityConfig(ref="EXMP", aliases=("Example",))]


@pytest.fixture
def loaded(
    migrated_db_url: str, fixture_pack: tuple[Pack, PackContext]
) -> tuple[str, int, dict[str, int]]:
    """Ingested database URL, the entity id, and concept ids by name."""
    pack, ctx = fixture_pack
    with connect(migrated_db_url) as conn:
        report = ingest(conn, pack, ctx, EXMP)
        assert report.ok, report
        entity_id = int(conn.execute("SELECT entity_id FROM entities").fetchone()[0])  # type: ignore[index]
        concepts = {
            str(n): int(i) for i, n in conn.execute("SELECT concept_id, name FROM concepts")
        }
    return migrated_db_url, entity_id, concepts


def test_ingest_report_and_idempotence(
    migrated_db_url: str, fixture_pack: tuple[Pack, PackContext]
) -> None:
    pack, ctx = fixture_pack
    with connect(migrated_db_url) as conn:
        first = ingest(conn, pack, ctx, EXMP).results[0]
        assert first.ok, first.error
        assert first.documents_written == 4
        assert first.report is not None
        assert len(first.report.conflicts) == 1

        second = ingest(conn, pack, ctx, EXMP).results[0]
        assert second.documents_written == 0
        assert second.documents_unchanged == 4

        latest = conn.execute("SELECT count(*) FROM facts WHERE is_latest_reported").fetchone()
        distinct_periods = conn.execute(
            "SELECT count(DISTINCT (concept_id, period_start, period_end)) FROM facts"
        ).fetchone()
        assert latest == distinct_periods


def test_lookup_returns_the_restated_value(loaded: tuple[str, int, dict[str, int]]) -> None:
    url, entity, concepts = loaded
    with connect(url) as conn:
        result = run_template(
            conn,
            LookupParams(entity_id=entity, concept_id=concepts["revenue"], fiscal_year=2023),
            LIMITS,
        )
    [row] = result.rows
    assert row.value == Decimal(1010)  # restated in the FY2024 10-K, not the original 1000
    assert row.document_external_id == "0000000001-24-000020"
    assert row.ref == f"f_{row.fact_id}"


def test_time_series_and_quarters(loaded: tuple[str, int, dict[str, int]]) -> None:
    url, entity, concepts = loaded
    with connect(url) as conn:
        series = run_template(
            conn,
            TimeSeriesParams(
                entity_id=entity, concept_id=concepts["revenue"], from_year=2020, to_year=2024
            ),
            LIMITS,
        )
        q1 = run_template(
            conn,
            LookupParams(
                entity_id=entity,
                concept_id=concepts["revenue"],
                fiscal_year=2024,
                fiscal_period="Q1",
            ),
            LIMITS,
        )
        gross = run_template(
            conn,
            LookupParams(entity_id=entity, concept_id=concepts["gross_profit"], fiscal_year=2024),
            LIMITS,
        )
    assert [(r.fiscal_year, r.value) for r in series.rows] == [
        (2022, Decimal(900)),
        (2023, Decimal(1010)),
        (2024, Decimal(1200)),
    ]
    assert [r.value for r in q1.rows] == [Decimal(300)]
    assert gross.rows == []  # conflicting source values were dropped, not guessed


def test_row_cap_reports_truncation(loaded: tuple[str, int, dict[str, int]]) -> None:
    url, entity, concepts = loaded
    with connect(url) as conn:
        result = run_template(
            conn,
            TimeSeriesParams(
                entity_id=entity, concept_id=concepts["revenue"], from_year=2020, to_year=2024
            ),
            QueryLimits(max_rows=2),
        )
    assert len(result.rows) == 2
    assert result.truncated


def test_fact_source(loaded: tuple[str, int, dict[str, int]]) -> None:
    url, entity, concepts = loaded
    with connect(url) as conn:
        [row] = run_template(
            conn,
            LookupParams(entity_id=entity, concept_id=concepts["eps_diluted"], fiscal_year=2024),
            LIMITS,
        ).rows
        [source] = run_template(conn, FactSourceParams(fact_id=row.fact_id), LIMITS).rows
    assert source.value == Decimal("1.23")
    assert source.unit == "USD/shares"
    assert source.source_tag == "us-gaap:EarningsPerShareDiluted"
    assert source.source_uri.endswith("exmp-20240928.htm")


def test_template_params_are_validated() -> None:
    with pytest.raises(ValidationError):
        LookupParams(entity_id=1, concept_id=1, fiscal_year=1800)
    with pytest.raises(ValidationError):
        TimeSeriesParams(entity_id=1, concept_id=1, from_year=2024, to_year=2020)
    with pytest.raises(ValidationError):
        CompareEntitiesParams(entity_ids=[1], concept_id=1, from_year=2020, to_year=2024)
    with pytest.raises(ValidationError):
        LookupParams.model_validate(
            {"entity_id": "1; DROP TABLE facts", "concept_id": 1, "fiscal_year": 2024}
        )


def test_read_only_path_cannot_write_or_see_tables(loaded: tuple[str, int, dict[str, int]]) -> None:
    url, _, _ = loaded
    with connect(url) as conn:
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction), read_only(conn, LIMITS):
            conn.execute(
                "INSERT INTO calculations (expression, input_refs, result) VALUES ('x', '{}', 1)"
            )
        with pytest.raises(psycopg.errors.InsufficientPrivilege), read_only(conn, LIMITS):
            conn.execute("SELECT * FROM facts")  # base tables are not visible, only views
        with (
            pytest.raises(psycopg.errors.QueryCanceled),
            read_only(conn, QueryLimits(timeout_ms=100)),
        ):
            conn.execute("SELECT pg_sleep(1)")


def test_resolve(loaded: tuple[str, int, dict[str, int]]) -> None:
    url, entity, concepts = loaded
    with connect(url) as conn:
        by_ticker = resolve_entity(conn, "finance", "exmp", LIMITS)
        by_name = resolve_entity(conn, "finance", "Example Corp.", LIMITS)
        fuzzy = resolve_entity(conn, "finance", "Exampel", LIMITS)
        sales = resolve_concept(conn, "finance", "Net Sales", LIMITS)
        none = resolve_concept(conn, "finance", "number of employees", LIMITS)
    assert unambiguous(by_ticker) is not None
    assert unambiguous(by_ticker).entity_id == entity  # type: ignore[union-attr]
    assert by_name[0].score == 1.0
    assert fuzzy
    assert fuzzy[0].score < 1.0
    assert sales[0].concept_id == concepts["revenue"]
    assert sales[0].score == 1.0
    assert unambiguous(none) is None


def test_calculate_chains_and_stores(loaded: tuple[str, int, dict[str, int]]) -> None:
    url, entity, concepts = loaded
    with connect(url) as conn:
        rows = run_template(
            conn,
            TimeSeriesParams(
                entity_id=entity, concept_id=concepts["revenue"], from_year=2023, to_year=2024
            ),
            LIMITS,
        ).rows
        [op] = run_template(
            conn,
            LookupParams(
                entity_id=entity, concept_id=concepts["operating_income"], fiscal_year=2024
            ),
            LIMITS,
        ).rows
        growth = calculate(conn, "(a - b) / b * 100", {"a": rows[1].ref, "b": rows[0].ref}, LIMITS)
        margin = calculate(conn, "op / rev", {"op": op.ref, "rev": rows[1].ref}, LIMITS)
        chained = calculate(conn, "m * 100", {"m": margin.ref}, LIMITS)
        stored = conn.execute(
            "SELECT input_refs FROM calculations WHERE calc_id = %s", (growth.calc_id,)
        ).fetchone()

        with pytest.raises(CalculationError, match="no such fact"):
            calculate(conn, "a", {"a": "f_999999"}, LIMITS)
        with pytest.raises(CalculationError, match="not a fact"):
            calculate(conn, "a", {"a": "1200"}, LIMITS)

    assert growth.value.quantize(Decimal("0.01")) == Decimal("18.81")  # (1200 - 1010) / 1010
    assert growth.unit == "pure"
    assert chained.value == Decimal("27.5")  # 330 / 1200 * 100
    assert stored is not None
    assert stored[0] == {"a": rows[1].ref, "b": rows[0].ref}


def test_cli_answers_and_refuses(
    loaded: tuple[str, int, dict[str, int]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    url, _, _ = loaded
    monkeypatch.setenv("DATABASE_URL", url)
    from verified_rag.config import get_settings

    get_settings.cache_clear()
    try:
        assert main(["lookup", "EXMP", "net sales", "--year", "2024"]) == 0
        out = capsys.readouterr().out
        assert "1,200 USD" in out
        assert "10-K 0000000001-24-000020" in out

        assert main(["lookup", "EXMP", "employees", "--year", "2024"]) == 2
        assert main(["lookup", "EXMP", "gross profit", "--year", "2024"]) == 2  # no clean value
    finally:
        get_settings.cache_clear()
