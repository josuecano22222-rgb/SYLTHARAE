"""``docs/reference/DATABASE_SCHEMA.md`` matches what the migrations build.

Regenerate after adding a migration::

    python3 tools/docs/generate_schema.py
    # or, without a configured database:
    INFORAXIS_WRITE_EVIDENCE=1 python3 -m pytest tests/integration/test_schema_reference.py
"""

import os
import sys
from pathlib import Path

import psycopg2
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.docs.generate_schema import TARGET, generate  # noqa: E402

pytestmark = pytest.mark.integration


@pytest.fixture
def fresh_schema_db(pg_db):
    """A database migrated from scratch, used only by this test.

    The shared session database is not suitable: other tests legitimately
    reshape it (``test_migration_0007`` downgrades and re-upgrades m0007, which
    moves the re-added columns to the end of ``paths``). The reference must
    describe what a *new* installation gets, independent of test order.
    """
    from database.bootstrap import bootstrap_database

    cfg = dict(pg_db, database=f"{pg_db['database']}_schema_ref")
    admin = dict(host=cfg["host"], port=cfg["port"], user=cfg["user"],
                 password=cfg["password"], dbname="postgres")

    def _drop():
        conn = psycopg2.connect(**admin)
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{cfg["database"]}"')
        conn.close()

    _drop()
    bootstrap_database(cfg)
    try:
        yield cfg
    finally:
        _drop()


def test_schema_reference_matches_the_migrated_database(fresh_schema_db):
    db = fresh_schema_db
    connection = psycopg2.connect(host=db["host"], port=db["port"], user=db["user"],
                                  password=db["password"], dbname=db["database"])
    try:
        generated = generate(connection)
    finally:
        connection.close()
    if os.environ.get("INFORAXIS_WRITE_EVIDENCE") == "1":
        TARGET.parent.mkdir(parents=True, exist_ok=True)
        TARGET.write_text(generated, encoding="utf-8")
        pytest.skip("schema reference regenerated")
    assert TARGET.exists(), "run python3 tools/docs/generate_schema.py"
    assert TARGET.read_text(encoding="utf-8") == generated, (
        "docs/reference/DATABASE_SCHEMA.md is stale; run python3 tools/docs/generate_schema.py")
