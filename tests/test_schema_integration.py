"""Applies the migrations to a fresh database and checks the schema's key invariants."""

from typing import Any

import psycopg
import pytest

from verified_rag.store import migrate

pytestmark = pytest.mark.integration

EXPECTED_TABLES = {
    "entities",
    "documents",
    "concepts",
    "concept_aliases",
    "facts",
    "chunks",
    "calculations",
    "requests",
    "schema_migrations",
}


def _returned_id(cur: psycopg.Cursor[tuple[Any, ...]]) -> int:
    row = cur.fetchone()
    assert row is not None
    return int(row[0])


def _seed(conn: psycopg.Connection) -> tuple[int, int, int]:
    entity_id = _returned_id(
        conn.execute(
            "INSERT INTO entities (pack_id, external_id, name)"
            " VALUES ('test', 'E1', 'Example Co') RETURNING entity_id"
        )
    )
    document_id = _returned_id(
        conn.execute(
            "INSERT INTO documents (pack_id, entity_id, external_id, doc_type, source_uri,"
            " content_hash, parser_version)"
            " VALUES ('test', %s, 'D1', 'report', 'file://x', 'h', 'v1') RETURNING document_id",
            (entity_id,),
        )
    )
    concept_id = _returned_id(
        conn.execute(
            "INSERT INTO concepts (pack_id, name, unit_kind)"
            " VALUES ('test', 'revenue', 'monetary') RETURNING concept_id"
        )
    )
    return entity_id, document_id, concept_id


_INSERT_FACT = (
    "INSERT INTO facts (pack_id, entity_id, document_id, concept_id, value, unit,"
    " period_start, period_end, period_type, is_latest_reported)"
    " VALUES ('test', %s, %s, %s, %s, 'USD', %s, %s, %s, %s)"
)


def test_migrations_apply_and_are_idempotent(fresh_db_url: str) -> None:
    applied = migrate.apply_pending(fresh_db_url)
    assert [m.version for m in applied] == [m.version for m in migrate.discover()]
    assert migrate.apply_pending(fresh_db_url) == []

    with psycopg.connect(fresh_db_url) as conn:
        rows = conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
        ).fetchall()
        assert {r[0] for r in rows} >= EXPECTED_TABLES
        ext = conn.execute("SELECT 1 FROM pg_extension WHERE extname = 'vector'").fetchone()
        assert ext is not None


def test_only_one_latest_reported_fact_per_period(fresh_db_url: str) -> None:
    migrate.apply_pending(fresh_db_url)
    with psycopg.connect(fresh_db_url) as conn:
        e, d, c = _seed(conn)
        period = ("2024-01-01", "2024-12-31", "duration")
        conn.execute(_INSERT_FACT, (e, d, c, 100, *period, True))
        # A restated, older value for the same period is fine if it is not marked latest.
        conn.execute(_INSERT_FACT, (e, d, c, 99, *period, False))
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(_INSERT_FACT, (e, d, c, 101, *period, True))


def test_period_shape_is_enforced(fresh_db_url: str) -> None:
    migrate.apply_pending(fresh_db_url)
    with psycopg.connect(fresh_db_url) as conn:
        e, d, c = _seed(conn)
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(_INSERT_FACT, (e, d, c, 1, "2024-01-01", "2024-12-31", "instant", False))


def test_chunk_tsv_is_generated(fresh_db_url: str) -> None:
    migrate.apply_pending(fresh_db_url)
    with psycopg.connect(fresh_db_url) as conn:
        _, d, _ = _seed(conn)
        conn.execute(
            "INSERT INTO chunks (pack_id, document_id, text, context_header)"
            " VALUES ('test', %s, 'Revenue grew because services expanded', 'Example Co 2024')",
            (d,),
        )
        hit = conn.execute(
            "SELECT count(*) FROM chunks WHERE tsv @@ plainto_tsquery('english', 'services')"
        ).fetchone()
        assert hit is not None
        assert hit[0] == 1
