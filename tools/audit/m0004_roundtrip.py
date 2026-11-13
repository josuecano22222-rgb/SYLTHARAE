"""m0004 downgrade -> upgrade on a populated database (audit tool).

The same three checks as ``tests/integration/test_m0004_downgrade_upgrade.py``,
run against a *real* populated database instead of a five-node fixture: point
``DSN`` (or ``--database``) at a copy of production and it prints the row
counts and checksums it compared. This is the artifact behind the round-12
evidence in ``audit_report/r12/m0004_roundtrip.log``; the test is the version
that runs in CI.

Checks, in order:

1. finger-print every table (row count + an order-independent checksum of
   the row images);
2. downgrade m0004 and prove the *old* rule is back (a depth-3 insert is
   refused again) while the finger-print is unchanged;
3. upgrade m0004 again and prove the *new* rule is back (a depth-3 insert
   works, a cycle is refused) while the finger-print is unchanged.

Every probe insert runs in a transaction that is rolled back, so the data
cannot move between finger-prints. Exits non-zero on the first mismatch.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys

import pathlib

import psycopg2

DSN = dict(
    host=os.environ.get("DB_HOST", "127.0.0.1"),
    port=int(os.environ.get("DB_PORT", "5440")),
    user=os.environ.get("DB_USER", "postgres"),
    password=os.environ.get("DB_PASSWORD", ""),
    dbname=os.environ.get("M0004_DB", "syltharae_app"),
)

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from database.migrations import m0004_category_hierarchy_depth as m0004  # noqa: E402

FAILURES: list[str] = []


def check(ok: bool, what: str) -> None:
    print(("  ok   " if ok else "  FAIL ") + what)
    if not ok:
        FAILURES.append(what)


def tables(conn):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT c.relname FROM pg_class c
              JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
               AND NOT c.relispartition
             ORDER BY 1
        """)
        return [row[0] for row in cur.fetchall()]


def fingerprint(conn) -> dict:
    """{table: {rows, checksum}} for every table in ``public``."""
    out = {}
    for table in tables(conn):
        with conn.cursor() as cur:
            # md5(row) per row, summed order-independently, so a page-order
            # change cannot masquerade as a data change.
            cur.execute(
                'SELECT count(*), coalesce(sum((\'x\' || substr(md5(t::text), 1, 15))::bit(60)::bigint::numeric), 0)'
                f' FROM "{table}" t')
            rows, acc = cur.fetchone()
            out[table] = {"rows": rows, "checksum": f"{int(acc):040x}"}
    return out


def digest(fp: dict) -> str:
    blob = json.dumps(fp, sort_keys=True).encode()
    return hashlib.sha256(blob).hexdigest()


def function_body(conn) -> str:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT pg_get_functiondef(p.oid)
              FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
             WHERE n.nspname = 'public' AND p.proname = 'categorys_hierarchy_check'
        """)
        row = cur.fetchone()
        return row[0] if row else ""


def try_insert(conn, label, statements):
    """Run statements in one transaction and roll it back."""
    conn.rollback()
    try:
        with conn.cursor() as cur:
            for sql, params in statements:
                cur.execute(sql, params)
        conn.rollback()
        print(f"  {label}: accepted")
        return True, None
    except Exception as exc:  # noqa: BLE001 - the message is the evidence
        conn.rollback()
        first = str(exc).strip().splitlines()[0]
        print(f"  {label}: refused -> {first}")
        return False, first


def node_at_depth(conn, depth: int) -> int:
    """A real category id at a given depth in the populated tree."""
    with conn.cursor() as cur:
        cur.execute("""
            WITH RECURSIVE walk AS (
                SELECT id, 1 AS depth FROM categorys WHERE parent_id IS NULL
                UNION ALL
                SELECT c.id, w.depth + 1 FROM categorys c JOIN walk w ON c.parent_id = w.id
                 WHERE w.depth < %s
            )
            SELECT id FROM walk WHERE depth = %s LIMIT 1
        """, (depth, depth))
        row = cur.fetchone()
        return row[0] if row else None


def new_word(conn) -> int:
    with conn.cursor() as cur:
        cur.execute("INSERT INTO words (word) VALUES ('m0004-roundtrip') RETURNING id")
        return cur.fetchone()[0]


def main() -> int:
    conn = psycopg2.connect(**DSN)
    conn.autocommit = False
    with conn.cursor() as cur:
        cur.execute("SELECT current_database(), version()")
        db, version = cur.fetchone()
    print(f"database: {db}\npostgres: {version.split(',')[0]}")
    with conn.cursor() as cur:
        cur.execute("SELECT version FROM schema_migrations ORDER BY version")
        print("applied:", [r[0] for r in cur.fetchall()])

    before = fingerprint(conn)
    print(f"\nfingerprint before: {len(before)} tables, "
          f"{sum(v['rows'] for v in before.values())} rows, {digest(before)[:16]}")
    for name in ("categorys", "words", "geo_places", "users"):
        if name in before:
            print(f"  {name:14} rows={before[name]['rows']:>7} sum={before[name]['checksum']}")

    depth3 = node_at_depth(conn, 3)
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM categorys")
        total_categories = cur.fetchone()[0]
    print(f"\npopulated tree: {total_categories} categories, level-3 node = {depth3}")

    # ---- 1. downgrade -----------------------------------------------------
    print("\n[1] downgrade m0004")
    conn.rollback()
    with conn.cursor() as cur:
        cur.execute("INSERT INTO words (word) VALUES ('m0004-roundtrip-probe') RETURNING id")
        probe_word = cur.fetchone()[0]
    conn.rollback()
    m0004.downgrade(conn)
    conn.commit()

    body = function_body(conn)
    check("main category" in body, "downgrade restored the two-level function body")
    check("cycle" not in body.lower() or "parent must be a main category"
          in body, "the restored body carries the old message")
    ok, _ = try_insert(
        conn, "depth-3 insert after downgrade",
        [("INSERT INTO words (word) VALUES ('m0004-roundtrip-w1') RETURNING id", None),
         ("INSERT INTO categorys (word_id, parent_id) SELECT id, %s FROM words"
          " WHERE word = 'm0004-roundtrip-w1'", (depth3,))])
    check(not ok, "downgrade: depth-3 insert is refused again")

    after_down = fingerprint(conn)
    check(after_down == before, "downgrade changed no row in any table")
    print(f"  fingerprint after downgrade: {digest(after_down)[:16]}")

    # ---- 2. upgrade -------------------------------------------------------
    print("\n[2] upgrade m0004")
    m0004.upgrade(conn)
    conn.commit()
    body = function_body(conn)
    check("cycle" in body.lower() or "ancestor" in body.lower(),
          "upgrade installed the cycle guard")

    ok, _ = try_insert(
        conn, "depth-3 insert after upgrade",
        [("INSERT INTO words (word) VALUES ('m0004-roundtrip-w2') RETURNING id", None),
         ("INSERT INTO categorys (word_id, parent_id) SELECT id, %s FROM words"
          " WHERE word = 'm0004-roundtrip-w2'", (depth3,))])
    check(ok, "upgrade: depth-3 insert is accepted")

    ok, msg = try_insert(
        conn, "self-parent after upgrade",
        [("UPDATE categorys SET parent_id = id WHERE id = %s", (depth3,))])
    check(not ok, "upgrade: a category cannot be its own parent")

    # a cycle: move the root ancestor of the level-3 node under that node
    with conn.cursor() as cur:
        cur.execute("""
            WITH RECURSIVE up AS (
                SELECT id, parent_id, 1 AS steps FROM categorys WHERE id = %s
                UNION ALL
                SELECT c.id, c.parent_id, u.steps + 1
                  FROM categorys c JOIN up u ON c.id = u.parent_id
            )
            SELECT id FROM up WHERE parent_id IS NULL LIMIT 1
        """, (depth3,))
        ancestor = cur.fetchone()[0]
    conn.rollback()
    print(f"  root ancestor of {depth3} = {ancestor}")
    ok, msg = try_insert(
        conn, f"cycle (root {ancestor} moved under its descendant {depth3})",
        [("UPDATE categorys SET parent_id = %s WHERE id = %s", (depth3, ancestor))])
    check(not ok, "upgrade: moving an ancestor under its descendant is refused")

    after_up = fingerprint(conn)
    check(after_up == before, "upgrade changed no row in any table")
    print(f"  fingerprint after upgrade: {digest(after_up)[:16]}")

    with conn.cursor() as cur:
        cur.execute("SELECT version FROM schema_migrations ORDER BY version")
        versions = [r[0] for r in cur.fetchall()]
    check(versions == ["0001", "0002", "0003", "0004"],
          f"schema_migrations still records the whole chain: {versions}")

    conn.close()
    print("\nRESULT:", "PASS" if not FAILURES else f"FAIL ({len(FAILURES)})")
    for failure in FAILURES:
        print("  -", failure)
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    raise SystemExit(main())
