import pytest

from verified_rag.store import migrate
from verified_rag.store.migrate import Migration, MigrationError


def test_discover_finds_initial_schema_in_order() -> None:
    migrations = migrate.discover()
    assert migrations, "expected at least one migration"
    assert migrations[0].version == 1
    assert migrations[0].filename == "0001_initial_schema.sql"
    versions = [m.version for m in migrations]
    assert versions == sorted(versions)
    assert len(versions) == len(set(versions))


def test_checksum_changes_with_content() -> None:
    a = Migration(version=1, name="x", filename="0001_x.sql", sql="SELECT 1;")
    b = Migration(version=1, name="x", filename="0001_x.sql", sql="SELECT 2;")
    assert a.checksum != b.checksum
    assert a.checksum == Migration(1, "x", "0001_x.sql", "SELECT 1;").checksum


def test_edited_migration_is_rejected() -> None:
    m = Migration(version=1, name="x", filename="0001_x.sql", sql="SELECT 1;")
    with pytest.raises(MigrationError, match="changed after it was applied"):
        migrate._check_applied([m], {1: "not-the-checksum"})  # pyright: ignore[reportPrivateUsage]


def test_unknown_applied_migration_is_rejected() -> None:
    with pytest.raises(MigrationError, match="not in the repo"):
        migrate._check_applied([], {7: "abc"})  # pyright: ignore[reportPrivateUsage]
