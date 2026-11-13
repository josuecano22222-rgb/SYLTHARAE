"""verify_readiness.py expects the schema the current migrations produce.

verify_readiness's database checks used to require table and index names from
before the m0011 content_identity rename (words_paths -> words_hashs,
keywords_paths -> keywords_hashs, path_id-keyed indexes -> hash_id/context_id),
so on a *correctly migrated* database the readiness report failed two critical
checks with names that no longer exist. These tests pin the required sets to a
database migrated from scratch, so a future migration that renames a table or
index fails here unless verify_readiness.py is updated in the same commit.
"""

import sys
from pathlib import Path

import psycopg2
import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import verify_readiness  # noqa: E402

pytestmark = pytest.mark.integration


@pytest.fixture
def fresh_schema_db(pg_db):
    """A database migrated from scratch, used only by these tests.

    The shared session database is not suitable: other tests legitimately
    reshape it (``test_migration_0007`` downgrades and re-upgrades m0007).
    Readiness must describe what a *new* installation gets, independent of
    test order.
    """
    from database.bootstrap import bootstrap_database

    cfg = dict(pg_db, database=f"{pg_db['database']}_readiness")
    admin = dict(host=cfg["host"], port=cfg["port"], user=cfg["user"],
                 password=cfg["password"], dbname="postgres")

    def _drop():
        conn = psycopg2.connect(**admin)
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{cfg["database"]}"')
        conn.close()

    _drop()
    conn = psycopg2.connect(**admin)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(f'CREATE DATABASE "{cfg["database"]}"')
    conn.close()
    bootstrap_database(cfg)
    try:
        yield cfg
    finally:
        _drop()


def _actual_tables(cfg):
    conn = psycopg2.connect(**cfg)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT table_name FROM information_schema.tables"
                        " WHERE table_schema = 'public'")
            return {r[0] for r in cur.fetchall()}
    finally:
        conn.close()


def _actual_indexes(cfg):
    conn = psycopg2.connect(**cfg)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'")
            return {r[0] for r in cur.fetchall()}
    finally:
        conn.close()


def test_required_tables_exist_on_a_fresh_migration(fresh_schema_db):
    missing = verify_readiness.REQUIRED_TABLES - _actual_tables(fresh_schema_db)
    assert not missing, (
        "verify_readiness.REQUIRED_TABLES names tables the current migrations "
        f"do not create: {', '.join(sorted(missing))}"
    )


def test_required_indexes_exist_on_a_fresh_migration(fresh_schema_db):
    missing = verify_readiness.REQUIRED_INDEXES - _actual_indexes(fresh_schema_db)
    assert not missing, (
        "verify_readiness.REQUIRED_INDEXES names indexes the current migrations "
        f"do not create: {', '.join(sorted(missing))}"
    )
