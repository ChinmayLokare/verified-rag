import uuid
from collections.abc import Iterator

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from verified_rag.config import Settings


@pytest.fixture(scope="session")
def admin_url() -> str:
    """URL of the dev/CI Postgres. Integration tests are skipped when it is unreachable."""
    url = Settings().database_url
    try:
        with psycopg.connect(url, connect_timeout=3):
            pass
    except psycopg.OperationalError as exc:
        pytest.skip(f"Postgres not reachable ({exc.__class__.__name__}); run docker compose up")
    return url


@pytest.fixture
def fresh_db_url(admin_url: str) -> Iterator[str]:
    """A throwaway empty database, dropped after the test."""
    name = f"vrag_test_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(admin_url, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        yield make_conninfo(admin_url, dbname=name)
    finally:
        with psycopg.connect(admin_url, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
