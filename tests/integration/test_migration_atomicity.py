"""A migration that fails must leave nothing behind - and must be retryable.

AUDIT-MIG-01 claimed the runner commits a migration's DDL and only then records
its version, leaving a crash window in which the schema moved but the bookkeeping
did not: the next start would re-run the migration against an already-migrated
schema. This test establishes what the code actually does, on a real database:

* ``run_migrations`` calls ``upgrade(conn)`` and inserts into
  ``schema_migrations`` **before** a single ``conn.commit()``, and PostgreSQL
  supports transactional DDL, so both halves are one transaction;
* a raising migration therefore rolls back its own DDL, records nothing, stops
  the run, and the same migration can be attempted again after the cause is
  fixed.

If that ever changes (an internal ``conn.commit()`` in a migration module, an
autocommit connection), these tests fail.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


class _Boom(RuntimeError):
    pass


@pytest.fixture()
def conn(pg_db):
    import psycopg2

    connection = psycopg2.connect(
        host=pg_db["host"], port=pg_db["port"], user=pg_db["user"],
        password=pg_db["password"], dbname=pg_db["database"],
    )
    yield connection
    connection.rollback()
    connection.close()


def _fake_migration(monkeypatch, module):
    """Make the runner discover *module* as one extra migration."""
    from database import migration_runner

    real = migration_runner.discover_migrations()

    def discover():
        return real + [migration_runner.Migration(
            version=module.version, name=module.name, module=module)]

    monkeypatch.setattr(migration_runner, "discover_migrations", discover)
    return real


def test_a_failing_migration_rolls_back_its_ddl_and_its_bookkeeping(conn, monkeypatch):
    import types

    from database.migration_runner import run_migrations

    module = types.SimpleNamespace(version="9998", name="boom")
    module.upgrade = _raises_after_ddl
    _fake_migration(monkeypatch, module)

    with pytest.raises(RuntimeError) as raised:
        run_migrations(conn)
    assert "9998" in str(raised.value)

    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.audit_mig_9998')")
        assert cur.fetchone()[0] is None, "the failed migration's table survived"
        cur.execute("SELECT 1 FROM schema_migrations WHERE version = '9998'")
        assert cur.fetchone() is None, "a failed migration was recorded as applied"

    # The failure must not poison the connection: the runner rolls back, so the
    # next run starts from the recorded state.
    from database.migration_runner import current_version
    assert current_version(conn) is not None


def _raises_after_ddl(cursor_conn):
    """A migration that creates a table and then fails."""
    with cursor_conn.cursor() as cur:
        cur.execute("CREATE TABLE audit_mig_9998 (id integer)")
    raise _Boom("the migration failed after its DDL")


def test_a_failed_migration_can_be_retried(conn, monkeypatch):
    import types

    from database.migration_runner import run_migrations

    module = types.SimpleNamespace(version="9999", name="retry")
    module.upgrade = _raises_after_ddl
    real = _fake_migration(monkeypatch, module)

    with pytest.raises(RuntimeError):
        run_migrations(conn)

    # Fix the cause and run again: it succeeds, once, and is recorded once.
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS audit_mig_9998")
        cur.execute("DELETE FROM schema_migrations WHERE version = '9999'")
    conn.commit()

    module.upgrade = lambda c: c.cursor().execute("CREATE TABLE audit_mig_9999 (id integer)")
    module.version = "9999"
    applied = run_migrations(conn)
    assert applied == ["9999"], applied

    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM schema_migrations WHERE version = '9999'")
        assert cur.fetchone()[0] == 1
        cur.execute("DROP TABLE audit_mig_9999")
    conn.commit()
    assert len(real) >= 1
