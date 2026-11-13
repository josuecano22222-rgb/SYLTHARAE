"""What the tree query costs at 10 000 and 100 000 nodes.

``m0004`` removed the two-level cap, so "a tree" no longer means "a handful of
rows": the recursive CTE in ``services/categories/tree.py`` has to survive a
catalogue with a hundred thousand categories. This module measures it on a real
PostgreSQL database built by the migration bootstrap - no mocks, no EXPLAIN
guesswork - and asserts only loose ceilings, so the numbers below are recorded
measurements rather than a wish.

Why the fixture is not built through the HTTP API
================================================
The standing rule is "fixtures via the application's own APIs". A hundred
thousand categories through 100 000 POSTs would take hours and would measure the
rate limiter, not the query. The rows are written with the same two INSERTs the
API would perform (``words``, then ``categorys``), from a temporary table, so the
data is shaped exactly like API-created data; what the timing covers is the
read path, which is the thing under test.

The 100 000-node case is behind ``CATEGORY_SCALE_100K=1``: it needs roughly a
million ``words_categorys``-free rows and about a minute, which is too much for
the default integration run. The 10 000-node case always runs.

The second test is a measurement, not a promise: it records the per-page cost
and the cost of walking the whole tree, because a recursive CTE recomputed for
every keyset page is *not* free and the report must say so rather than imply
that 100 000 nodes paginate like a plain table.
"""
from __future__ import annotations

import itertools
import os
import time
import uuid

import pytest

from services.categories.tree import fetch_tree
from tests.integration import _seed

SEQ = itertools.count()
SCALE_100K = os.environ.get("CATEGORY_SCALE_100K") == "1"

#: A full page, which is the smallest number of round trips a client can use.
PAGE = 500


def _bulk_tree(pg_db, *, roots, fanouts):
    """Build a tree with ``roots`` roots and one fanout per level.

    Returns ``(count, tag)``; ``tag`` names the rows so ``_drop_tree`` can remove
    them again. The fixture database is shared by the whole integration session,
    and eleven thousand extra categories would slow - and could change the
    totals asserted by - the category list tests that run after this module.

    Every level is one temporary table upload plus two INSERT ... SELECT
    statements, so the fixture costs seconds rather than hours and never
    depends on the order of RETURNING rows.
    """
    tag = uuid.uuid4().hex[:8]
    conn = _seed.connect(pg_db)
    created = 0
    try:
        with conn.cursor() as cur:
            cur.execute("CREATE TEMP TABLE scale_nodes (name text, parent_name text)")
            parents = [None]
            for level, fanout in enumerate(fanouts):
                rows = []
                for index in range(len(parents) * (roots if level == 0 else fanout)):
                    parent = parents[0 if level == 0 else index // fanout]
                    name = f"scale-{tag}-{level}-{index}"
                    rows.append((name, parent))
                parents = [row[0] for row in rows]
                cur.execute("TRUNCATE scale_nodes")
                cur.executemany("INSERT INTO scale_nodes (name, parent_name) VALUES (%s, %s)",
                                rows)
                cur.execute("INSERT INTO words (word) SELECT name FROM scale_nodes")
                cur.execute(
                    "INSERT INTO categorys (word_id, parent_id)"
                    " SELECT w.id, p.id FROM scale_nodes s"
                    "   JOIN words w ON w.word = s.name"
                    "   LEFT JOIN words pw ON pw.word = s.parent_name"
                    "   LEFT JOIN categorys p ON p.word_id = pw.id")
                created += len(rows)
        conn.commit()
    finally:
        conn.close()
    return created, tag


def _drop_tree(pg_db, tag):
    """Remove the nodes this module created (children first, via NULL parents)."""
    conn = _seed.connect(pg_db)
    try:
        with conn.cursor() as cur:
            pattern = f"scale-{tag}-%"
            cur.execute(
                "CREATE TEMP TABLE doomed AS SELECT c.id FROM categorys c"
                " JOIN words w ON w.id = c.word_id WHERE w.word LIKE %s", (pattern,))
            cur.execute("UPDATE categorys SET parent_id = NULL"
                        " WHERE id IN (SELECT id FROM doomed)")
            cur.execute("DELETE FROM categorys WHERE id IN (SELECT id FROM doomed)")
            cur.execute("DELETE FROM words WHERE word LIKE %s", (pattern,))
        conn.commit()
    finally:
        conn.close()


def _timed(fn):
    started = time.perf_counter()
    result = fn()
    return result, (time.perf_counter() - started) * 1000.0


def _first_page(pg_db, *, search=None, with_total=True):
    conn = _seed.connect(pg_db)
    try:
        with conn.cursor() as cur:
            return _timed(lambda: fetch_tree(
                cur, search=search, limit=PAGE, with_total=with_total))
    finally:
        conn.close()


def test_ten_thousand_nodes_answer_a_page_in_reasonable_time(pg_db):
    """The default case: 10 roots x 10 x 10 x 10 = 11 110 nodes."""
    total, tag = _bulk_tree(pg_db, roots=10, fanouts=(10, 10, 10, 10))
    assert total == 11_110
    try:
        _measure_ten_thousand(pg_db, total)
    finally:
        _drop_tree(pg_db, tag)


def _measure_ten_thousand(pg_db, total):
    rows, ms = _first_page(pg_db)
    assert len(rows["rows"]) == PAGE, "a page should be full"
    assert rows["total"] is not None and rows["total"] >= total
    assert rows["next_cursor"], "11k nodes cannot fit on one page"

    (_, ms_no_total) = _first_page(pg_db, with_total=False)
    print(f"\n10k nodes: first page (limit {PAGE}, with total) {ms:.0f} ms; "
          f"same page without the count {ms_no_total:.0f} ms")
    assert ms < 10_000, f"first page took {ms:.0f} ms at 11k nodes"


def test_ten_thousand_nodes_search_answers_in_reasonable_time(pg_db):
    _count, tag = _bulk_tree(pg_db, roots=5, fanouts=(10, 10, 10))
    try:
        _measure_search(pg_db)
    finally:
        _drop_tree(pg_db, tag)


def _measure_search(pg_db):
    (payload, ms) = _first_page(pg_db, search="scale-", with_total=False)
    assert payload["rows"], "the search returned nothing"
    assert any(row["is_match"] for row in payload["rows"])
    assert all("is_match" in row for row in payload["rows"])
    print(f"\n10k nodes: search page (ancestors included) {ms:.0f} ms")
    assert ms < 10_000


def test_walking_every_page_is_measured(pg_db):
    """A full walk is O(pages x tree): every keyset page rebuilds the CTE.

    That is the honest cost of deriving the tree without a persisted path
    column, and it is recorded here so nobody discovers it in production.
    """
    total, tag = _bulk_tree(pg_db, roots=4, fanouts=(10, 10, 10))
    assert total == 444
    try:
        _measure_walk(pg_db)
    finally:
        _drop_tree(pg_db, tag)


def _measure_walk(pg_db):
    total = 444
    conn = _seed.connect(pg_db)
    try:
        with conn.cursor() as cur:
            pages, cursor, seen, started = 0, None, 0, time.perf_counter()
            while True:
                payload = fetch_tree(cur, limit=200, cursor=cursor, with_total=False)
                pages += 1
                seen += len(payload["rows"])
                cursor = payload["next_cursor"]
                if not cursor:
                    break
                # The session database also holds the trees the other tests
                # in this module built, so the cap is generous.
                assert pages < 2_000, "pagination did not terminate"
            elapsed = (time.perf_counter() - started) * 1000.0
    finally:
        conn.close()

    assert seen >= total
    print(f"\n{total} nodes: full walk in {pages} pages, {elapsed:.0f} ms "
          f"({elapsed / pages:.0f} ms per page)")
    assert elapsed < 60_000


@pytest.mark.skipif(not SCALE_100K,
                    reason="set CATEGORY_SCALE_100K=1 to build the 100k-node fixture")
def test_one_hundred_thousand_nodes(pg_db):
    """10 + 100 + 1 000 + 100 000 = 101 110 nodes, and the same page measured."""
    total, tag = _bulk_tree(pg_db, roots=10, fanouts=(10, 10, 10, 100))
    assert total == 101_110
    try:
        _measure_hundred_thousand(pg_db)
    finally:
        _drop_tree(pg_db, tag)


def _measure_hundred_thousand(pg_db):

    (payload, ms) = _first_page(pg_db)
    assert len(payload["rows"]) == PAGE
    assert payload["total"] >= 100_000

    (_, ms_no_total) = _first_page(pg_db, with_total=False)
    (_, ms_search) = _first_page(pg_db, search="-3-1", with_total=False)

    conn = _seed.connect(pg_db)
    try:
        with conn.cursor() as cur:
            cur.execute("EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT) " + """
                WITH RECURSIVE tree AS (
                    SELECT c.id, c.parent_id, 1 AS depth
                      FROM categorys c WHERE c.parent_id IS NULL
                    UNION ALL
                    SELECT c.id, c.parent_id, p.depth + 1
                      FROM categorys c JOIN tree p ON c.parent_id = p.id)
                SELECT count(*) FROM tree
            """)
            plan = "\n".join(row[0] for row in cur.fetchall())
    finally:
        conn.close()

    exec_ms = None
    for line in plan.splitlines():
        if line.strip().startswith("Execution Time:"):
            exec_ms = float(line.split(":")[1].strip().split(" ")[0])
    print(f"\n100k nodes: first page {ms:.0f} ms, without count {ms_no_total:.0f} ms, "
          f"search {ms_search:.0f} ms; recursive walk alone (EXPLAIN ANALYZE) {exec_ms} ms")
    print(plan)
    assert ms < 60_000, f"first page took {ms:.0f} ms at 100k nodes"
