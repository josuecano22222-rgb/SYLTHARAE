"""Advanced Search with "hide duplicates" and relevance sorting must return results.

Regression: the candidate query built for ``hide_duplicates=true`` ranked the
duplicate groups with ``ROW_NUMBER() OVER (PARTITION BY ... ORDER BY
relevance_score ...)``. ``relevance_score`` is an output alias of the same
SELECT, which PostgreSQL does not allow inside a window ORDER BY, so every such
search raised ``UndefinedColumn``. The service logged the error and returned an
empty page.

These tests use the same route the screen uses and check the rows, not just the
status code, because the failure path returns HTTP 200 with no results.
"""

from __future__ import annotations

import datetime

from tests.integration._seed import connect, document, side, source, word_counts

TERM = "dupranktermzz"


def _seed(pg_db):
    conn = connect(pg_db)
    with conn.cursor() as cur:
        src_a, src_b = source(cur), source(cur)
        side_a, side_b = side(cur), side(cur)
        day = datetime.date(2026, 1, 1)
        # Group 1: one content in two contexts (a real duplicate pair).
        # The name carries the term, so the copy in the first context scores 2.0.
        _, shared_hash, _ = document(
            cur, source_id=src_a, side_id=side_a, text=f"{TERM} shared body",
            file_name=f"{TERM}-original.txt", file_date=day)
        # Second occurrence of the same content, different context, name without the term.
        document(cur, source_id=src_b, side_id=side_b, text=f"{TERM} shared body",
                 file_name="copy-of-shared.txt", file_date=day, hash_id=shared_hash)
        # A unique document whose content mentions the term (scores 1.0).
        _, unique_hash, _ = document(
            cur, source_id=src_a, side_id=side_a, text=f"{TERM} unique body",
            file_name="unique.txt", file_date=day)
        word_counts(cur, unique_hash, {TERM: 2})
    conn.commit()


def _search(client, **params):
    query = {"query": TERM, "per_page": 20, "use_advanced": "true",
             "use_fulltext": "true", "use_bm25": "false", "use_expansion": "true",
             "use_fuzzy": "true", "fast_mode": "true", "include_snippets": "false",
             "sort_by": "relevance", "sort_order": "desc", "hide_duplicates": "true"}
    query.update(params)
    resp = client.get("/api/search", query_string=query)
    assert resp.status_code == 200, resp.get_data(as_text=True)[:300]
    return resp.get_json()


def test_hide_duplicates_with_relevance_sort_returns_the_ranked_rows(pg_db, admin_client):
    _seed(pg_db)
    body = _search(admin_client, page=1)
    names = [row["file_name"] for row in body["results"]]
    # Two distinct contents survive: the duplicate pair collapses to one row.
    assert len(names) == 2, f"expected 2 rows after collapsing duplicates, got {names}"
    # The kept copy of the duplicate pair is the higher-scoring one (name hit, 2.0).
    assert names[0] == f"{TERM}-original.txt", names
    assert names[1] == "unique.txt", names



def test_a_failed_advanced_search_leaves_no_aborted_pooled_connection(
        pg_db, admin_client, monkeypatch, caplog):
    """A search that fails in SQL must not leave a connection aborted in the pool.

    The live log showed the pool's health check failing with "current transaction
    is aborted" right after a search error. The count and candidate queries ran
    after their connection had been released, so the failing statement aborted a
    pooled connection. Force a SQL error in that statement, then check the next
    pooled checkout is clean: no health-check failure and no aborted transaction.
    """
    import logging
    import psycopg2.extensions as ext
    from Api.services import search_service
    from Api.utils import get_connection

    _seed(pg_db)
    monkeypatch.setattr(
        search_service, "sql_relevance_score_expr",
        lambda terms, **kw: ("COALESCE((p.no_such_column)::float, 0)", []))
    caplog.clear()
    body = _search(admin_client, page=1)
    assert body["results"] == [], "the forced SQL error should yield no rows"

    with caplog.at_level(logging.WARNING, logger="database.database.database"):
        with get_connection() as conn:
            assert conn.info.transaction_status != ext.TRANSACTION_STATUS_INERROR
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                assert cur.fetchone()[0] == 1
    health = [r.getMessage() for r in caplog.records if "health check" in r.getMessage()]
    assert not health, f"the pool handed out an aborted connection: {health}"
