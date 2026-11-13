"""Live-DB check: Q07 flat OR returns stable result shape (run when DB available).

The corpus is seeded here, through the same tables ingestion writes
(``tests/integration/_seed.py``), so the page-size assertion is a property of
the query and not of whatever happens to be in the developer's database.
"""

from __future__ import annotations

import pytest

from tools.perf.bootstrap_runtime import bootstrap_runtime, db_connection_probe

#: More rows than one page, so the limit has something to cut.
CORPUS_ROWS = 30
PAGE_SIZE = 25

#: Every row carries every term of the query below, so the flat-OR branch
#: cannot come up short for want of matches.
OR_QUERY = "report OR invoice OR contract OR memo"


@pytest.fixture(scope="module", autouse=True)
def _runtime():
    bootstrap_runtime()


@pytest.fixture(scope="module")
def _corpus(pg_db):
    from _seed import connect, document, side, source

    conn = connect(pg_db)
    try:
        with conn.cursor() as cur:
            source_id = source(cur, name="q07_or_corpus_source")
            side_id = side(cur, name="q07_or_corpus_side")
            for index in range(CORPUS_ROWS):
                document(
                    cur,
                    source_id=source_id,
                    side_id=side_id,
                    text=(f"report invoice contract memo item {index} "
                          "quarterly figures for the review cycle"),
                    file_name=f"q07_or_corpus_{index}.txt",
                )
        conn.commit()
    finally:
        conn.close()
    return CORPUS_ROWS


@pytest.mark.integration
def test_q07_or_returns_expected_hit_count(_corpus):
    probe = db_connection_probe()
    if not probe.get("connected"):
        pytest.skip(probe.get("error") or "database unavailable")

    from Api.services.search_service import SearchService

    results, _total = SearchService.advanced_search(
        OR_QUERY,
        limit=PAGE_SIZE,
        use_bm25=False,
        use_expansion=False,
        analyst_scope="all",
        fast_mode=True,
        include_snippets=False,
        count_mode="skip",
    )
    assert SearchService._last_literal_compile == "flat_or"
    assert len(results) == PAGE_SIZE
    ids = [int(r["id"]) for r in results]
    assert len(ids) == len(set(ids))
