"""m0004 downgrade -> upgrade on a populated database.

The one authorised migration of round 11 replaces the *body* of
``categorys_hierarchy_check()``. That cannot be tested by "the new rule
works": a migration that also moved data, dropped an index or left the
trigger disabled would pass that test and still be wrong.

So this module checks the three things that make the swap reversible:

* **downgrade puts the old rule back** - a depth-3 insert is refused with
  the 2-level message again;
* **upgrade puts the new rule back** - a depth-3 insert is accepted, a
  self-parent and a move under one's own descendant are refused;
* **neither direction touches a row** - a fingerprint over every table
  (count + an order-independent checksum of the row images) is identical
  before, between and after, and ``schema_migrations`` still records the
  whole chain.

Run against a database of its own, populated with a real five-level tree,
because "no rows changed" is only a claim about a database that has rows.
"""

from __future__ import annotations

import hashlib
import json

import psycopg2
import pytest

pytestmark = pytest.mark.integration


def _connect(pg_db):
    return psycopg2.connect(
        host=pg_db["host"], port=pg_db["port"], user=pg_db["user"],
        password=pg_db["password"], dbname=pg_db["database"],
    )


@pytest.fixture(scope="module")
def populated_db(pg_db):
    """An empty database, migrated, then filled with a five-level tree."""
    from database.bootstrap import bootstrap_database
    from database.migration_runner import current_version

    name = f"{pg_db['database']}_m0004_roundtrip"
    admin = psycopg2.connect(
        host=pg_db["host"], port=pg_db["port"], user=pg_db["user"],
        password=pg_db["password"], dbname="postgres",
    )
    admin.autocommit = True
    try:
        with admin.cursor() as cur:
            cur.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity"
                        " WHERE datname = %s AND pid <> pg_backend_pid()", (name,))
            cur.execute(f'DROP DATABASE IF EXISTS "{name}"')
            cur.execute(f'CREATE DATABASE "{name}"')
    finally:
        admin.close()

    cfg = {k: pg_db[k] for k in ("host", "port", "user", "password")}
    cfg["database"] = name
    bootstrap_database(cfg)

    conn = _connect(cfg)
    try:
        with conn.cursor() as cur:
            # The migration under test is m0004; a fresh database is at the
            # head of the chain, which is m0005 since the term_names migration
            # was authorised. Bumping this string is the expected maintenance.
            assert current_version(conn) == "0005"
            parent = None
            for level in range(1, 6):
                cur.execute("INSERT INTO words (word) VALUES (%s) RETURNING id",
                            (f"m0004-level-{level}",))
                word_id = cur.fetchone()[0]
                cur.execute(
                    "INSERT INTO categorys (word_id, parent_id) VALUES (%s, %s) RETURNING id",
                    (word_id, parent))
                parent = cur.fetchone()[0]
            cur.execute("SELECT count(*) FROM categorys")
            assert cur.fetchone()[0] == 5
        conn.commit()
        yield cfg, conn
    finally:
        conn.close()


def _fingerprint(conn):
    """{table: (rows, checksum)} for every table in ``public``."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT c.relname FROM pg_class c
              JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
               AND NOT c.relispartition
             ORDER BY 1
        """)
        tables = [row[0] for row in cur.fetchall()]
    out = {}
    for table in tables:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*), coalesce("
                "sum(('x' || substr(md5(t::text), 1, 15))::bit(60)::bigint::numeric), 0)"
                f' FROM "{table}" t')  # nosec B608 - table names come from pg_class
            rows, acc = cur.fetchone()
            out[table] = [rows, f"{int(acc):040x}"]
    return out


def _digest(fingerprint):
    return hashlib.sha256(json.dumps(fingerprint, sort_keys=True).encode()).hexdigest()


def _scalar(conn, sql, params=()):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()[0]


def _attempt(conn, statements):
    """Run statements in a transaction that is always rolled back.

    Returns ``(accepted, message)`` - the message is the evidence when the
    database refused.
    """
    conn.rollback()
    try:
        with conn.cursor() as cur:
            for sql, params in statements:
                cur.execute(sql, params)
        conn.rollback()
        return True, None
    except Exception as exc:  # noqa: BLE001 - the refusal message is the point
        conn.rollback()
        return False, str(exc).strip().splitlines()[0]


def _body(conn):
    return _scalar(conn, """
        SELECT pg_get_functiondef(p.oid)
          FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname = 'public' AND p.proname = 'categorys_hierarchy_check'
    """)


def _new_category_under(conn, parent_id, word):
    return [
        ("INSERT INTO words (word) VALUES (%s) RETURNING id", (word,)),
        ("INSERT INTO categorys (word_id, parent_id)"
         " SELECT id, %s FROM words WHERE word = %s", (parent_id, word)),
    ]


def test_downgrade_restores_the_old_rule_and_changes_no_rows(populated_db):
    from database.migrations import m0004_category_hierarchy_depth as m0004

    cfg, conn = populated_db
    before = _fingerprint(conn)
    depth3 = _scalar(conn, """
        WITH RECURSIVE walk AS (
            SELECT id, 1 AS depth FROM categorys WHERE parent_id IS NULL
            UNION ALL
            SELECT c.id, w.depth + 1 FROM categorys c JOIN walk w ON c.parent_id = w.id
             WHERE w.depth < 3
        ) SELECT id FROM walk WHERE depth = 3
    """)

    m0004.downgrade(conn)
    conn.commit()
    try:
        body = _body(conn)
        assert "A subcategory''s parent must be a main category" in body or \
               "parent must be a main category" in body, body

        accepted, message = _attempt(conn, _new_category_under(conn, depth3, "m0004-down"))
        assert not accepted, "the old 2-level rule did not come back"
        assert "must be a main category" in message, message

        assert _digest(_fingerprint(conn)) == _digest(before)
    finally:
        m0004.upgrade(conn)
        conn.commit()


def test_upgrade_restores_the_cycle_guard_and_changes_no_rows(populated_db):
    from database.migrations import m0004_category_hierarchy_depth as m0004

    cfg, conn = populated_db
    before = _fingerprint(conn)

    # the fixture left the new rule in place; re-apply it so this test does
    # not depend on the order pytest runs these two functions in
    m0004.upgrade(conn)
    conn.commit()

    depth3 = _scalar(conn, """
        WITH RECURSIVE walk AS (
            SELECT id, 1 AS depth FROM categorys WHERE parent_id IS NULL
            UNION ALL
            SELECT c.id, w.depth + 1 FROM categorys c JOIN walk w ON c.parent_id = w.id
             WHERE w.depth < 3
        ) SELECT id FROM walk WHERE depth = 3
    """)

    accepted, message = _attempt(conn, _new_category_under(conn, depth3, "m0004-up"))
    assert accepted, message

    accepted, message = _attempt(
        conn, [("UPDATE categorys SET parent_id = id WHERE id = %s", (depth3,))])
    assert not accepted and "own parent" in message, message

    root = _scalar(conn, """
        WITH RECURSIVE up AS (
            SELECT id, parent_id FROM categorys WHERE id = %s
            UNION ALL
            SELECT c.id, c.parent_id FROM categorys c JOIN up u ON c.id = u.parent_id
        ) SELECT id FROM up WHERE parent_id IS NULL
    """, (depth3,))
    accepted, message = _attempt(
        conn, [("UPDATE categorys SET parent_id = %s WHERE id = %s", (depth3, root))])
    assert not accepted and "own descendant" in message, message

    assert _digest(_fingerprint(conn)) == _digest(before)

    versions = [row[0] for row in _scalar_all(
        conn, "SELECT version FROM schema_migrations ORDER BY version")]
    assert versions == ["0001", "0002", "0003", "0004", "0005"], versions


def _scalar_all(conn, sql, params=()):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()
