"""BM25 ranked search must return the requested window, not an empty page.

The advanced search ranks a candidate pool in Python (BM25) and then slices the
requested window out of it. The pool was sized from ``limit`` alone, so any
window that began past the pool - the export's "Current page only" on page 4
at 20 rows, for example - came back empty while the total still reported every
match. These tests walk every window of a result set through the BM25 path and
check that together they return each matching document exactly once.
"""

from __future__ import annotations

import datetime

from tests.integration._seed import connect, document, side, source

TERM = "windowterm"
DOCUMENTS = 90  # larger than the 60-row pool a 20-row window gets


def _seed(pg_db):
    conn = connect(pg_db)
    with conn.cursor() as cur:
        src, sid = source(cur), side(cur)
        for index in range(DOCUMENTS):
            document(cur, source_id=src, side_id=sid,
                     text=f"{TERM} corpus record {index}",
                     file_name=f"window-{index:03d}.txt", file_type="txt",
                     file_date=datetime.date.today())
    conn.commit()


def test_every_bm25_window_returns_its_rows_and_the_set_is_complete(pg_db):
    from Api.services.search_service import SearchService

    _seed(pg_db)
    seen = []
    for offset in range(0, DOCUMENTS, 20):
        rows, total = SearchService.advanced_search(
            query=TERM, limit=20, offset=offset, use_bm25=True,
            use_expansion=True, use_fuzzy=False, analyst_scope="all",
            sort_by="relevance", sort_order="desc")
        assert total == DOCUMENTS, f"total must count every match (offset {offset})"
        expected = min(20, DOCUMENTS - offset)
        assert len(rows) == expected, (
            f"offset {offset}: expected {expected} rows from the BM25 path, got {len(rows)}")
        seen.extend(row["id"] for row in rows)

    assert len(seen) == DOCUMENTS, "the windows must cover the whole result set"
    assert len(set(seen)) == DOCUMENTS, "no document may appear in two windows"
