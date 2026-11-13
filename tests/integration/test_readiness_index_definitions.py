"""``verify_readiness.py`` must judge indexes by what they enforce, not by name.

The check compared ``pg_indexes.indexname`` with a hard-coded set of names. A
database that enforces the same columns under a PostgreSQL-derived name
therefore failed a *readiness* check while the invariant was intact - a red
result caused by a label. The check now reads the definitions from
``pg_index`` (table, key columns in order, uniqueness) and matches on those.
"""

from __future__ import annotations

import pathlib
import sys

import psycopg2
import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import verify_readiness as readiness  # noqa: E402

pytestmark = pytest.mark.integration


def connect(cfg):
    return psycopg2.connect(host=cfg["host"], port=cfg["port"], user=cfg["user"],
                            password=cfg["password"], dbname=cfg["database"])


def test_the_required_indexes_are_stated_as_definitions():
    assert readiness.REQUIRED_INDEX_DEFINITIONS, "no definitions declared"
    for label, (table, columns) in readiness.REQUIRED_INDEX_DEFINITIONS.items():
        assert isinstance(table, str) and table, label
        assert isinstance(columns, tuple) and columns, label


def test_every_declared_definition_exists_in_the_migrated_database(pg_db):
    conn = connect(pg_db)
    try:
        with conn.cursor() as cur:
            definitions = readiness._index_definitions(cur)
    finally:
        conn.close()
    plain = {(table, cols) for table, cols, unique in definitions if not unique}
    missing = [(table, cols) for table, cols in readiness.REQUIRED_INDEX_DEFINITIONS.values()
               if (table, cols) not in plain]
    assert missing == [], missing


def test_a_renamed_index_still_satisfies_the_definition(pg_db):
    """The name is a label; the definition is what the check relies on."""
    conn = connect(pg_db)
    try:
        with conn.cursor() as cur:
            cur.execute("BEGIN")
            cur.execute("ALTER INDEX idx_paths_file_name RENAME TO idx_paths_file_name_r4")
            try:
                # what the old check looked at: names only
                cur.execute("SELECT indexname FROM pg_indexes"
                            " WHERE schemaname = 'public' AND indexname = %s",
                            ("idx_paths_file_name",))
                name_lookup = cur.fetchone()
                # what the check looks at now: the definition
                definitions = readiness._index_definitions(cur)
            finally:
                cur.execute("ROLLBACK")
    finally:
        conn.close()

    assert name_lookup is None, "the rename did not take effect inside the transaction"
    assert ("paths", ("file_name",)) in {(t, c) for t, c, _u in definitions}
