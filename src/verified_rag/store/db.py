"""Database connections and the read-only access path used by query templates and tools."""

from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import psycopg

# Created by migration 0002. Can only SELECT from the v_* views.
READER_ROLE = "vrag_reader"


@dataclass(frozen=True)
class QueryLimits:
    timeout_ms: int = 3000
    max_rows: int = 500


CONNECT_TIMEOUT_S = 5  # fail fast with a clear error instead of hanging when the DB is down


def connect(database_url: str) -> psycopg.Connection:
    """Autocommit connection: every unit of work opens its own explicit transaction."""
    return psycopg.connect(database_url, autocommit=True, connect_timeout=CONNECT_TIMEOUT_S)


def returned_id(row: tuple[Any, ...] | None) -> int:
    """The id from an ``INSERT ... RETURNING`` row."""
    if row is None:
        raise RuntimeError("INSERT ... RETURNING returned no row")
    return int(row[0])


@contextmanager
def read_only(conn: psycopg.Connection, limits: QueryLimits) -> Generator[psycopg.Connection]:
    """Run the block in a read-only transaction, as the reader role, with a statement timeout.

    The connection must be in autocommit mode so this opens a real transaction, not a savepoint.
    """
    if not conn.autocommit:
        raise RuntimeError("read_only() needs an autocommit connection")
    with conn.transaction():
        conn.execute("SET TRANSACTION READ ONLY")
        conn.execute("SET LOCAL ROLE vrag_reader")
        conn.execute("SELECT set_config('statement_timeout', %s, true)", (str(limits.timeout_ms),))
        yield conn
