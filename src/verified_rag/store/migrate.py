"""Plain-SQL schema migrations (see docs/adr/0008-plain-sql-migrations.md).

Migrations are files named ``NNNN_description.sql`` in the ``migrations`` package directory.
Each one is applied once, in version order, inside its own transaction, and recorded in
``schema_migrations`` with a checksum. Editing an applied migration is an error: write a new one.
"""

import hashlib
import re
from dataclasses import dataclass
from importlib import resources
from typing import LiteralString, cast

import psycopg

_FILENAME = re.compile(r"^(?P<version>\d{4})_(?P<name>[a-z0-9_]+)\.sql$")

# Arbitrary constant key so concurrent runners serialize instead of racing.
_LOCK_KEY = 7_201_105_001

_BOOKKEEPING = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     integer PRIMARY KEY,
    name        text        NOT NULL,
    checksum    text        NOT NULL,
    applied_at  timestamptz NOT NULL DEFAULT now()
)
"""


class MigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    filename: str
    sql: str

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.sql.encode("utf-8")).hexdigest()


def discover(package: str = "verified_rag.store.migrations") -> list[Migration]:
    """Load migration files in version order, rejecting bad names and duplicate versions."""
    found: list[Migration] = []
    for entry in resources.files(package).iterdir():
        if not entry.name.endswith(".sql"):
            continue
        match = _FILENAME.match(entry.name)
        if match is None:
            raise MigrationError(f"bad migration filename: {entry.name}")
        found.append(
            Migration(
                version=int(match["version"]),
                name=match["name"],
                filename=entry.name,
                sql=entry.read_text(encoding="utf-8"),
            )
        )
    found.sort(key=lambda m: m.version)
    versions = [m.version for m in found]
    if len(versions) != len(set(versions)):
        raise MigrationError(f"duplicate migration versions: {versions}")
    return found


def _applied(conn: psycopg.Connection) -> dict[int, str]:
    rows = conn.execute("SELECT version, checksum FROM schema_migrations").fetchall()
    return {int(v): str(c) for v, c in rows}


def _check_applied(migrations: list[Migration], applied: dict[int, str]) -> None:
    known = {m.version: m for m in migrations}
    for version, checksum in applied.items():
        m = known.get(version)
        if m is None:
            raise MigrationError(f"database has migration {version} that is not in the repo")
        if m.checksum != checksum:
            raise MigrationError(f"{m.filename} changed after it was applied; add a new migration")


def apply_pending(database_url: str, migrations: list[Migration] | None = None) -> list[Migration]:
    """Apply every migration not yet recorded. Returns the ones applied by this call."""
    migrations = discover() if migrations is None else migrations
    done: list[Migration] = []
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (_LOCK_KEY,))
        try:
            conn.execute(_BOOKKEEPING)
            applied = _applied(conn)
            _check_applied(migrations, applied)
            for m in migrations:
                if m.version in applied:
                    continue
                with conn.transaction():
                    # Migration files are trusted, reviewed repo content, not user input.
                    conn.execute(cast(LiteralString, m.sql))
                    conn.execute(
                        "INSERT INTO schema_migrations (version, name, checksum)"
                        " VALUES (%s, %s, %s)",
                        (m.version, m.name, m.checksum),
                    )
                done.append(m)
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (_LOCK_KEY,))
    return done


def status(database_url: str) -> list[str]:
    migrations = discover()
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute(_BOOKKEEPING)
        applied = _applied(conn)
    _check_applied(migrations, applied)
    return [f"{'applied' if m.version in applied else 'pending'}  {m.filename}" for m in migrations]
