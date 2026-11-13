"""Advanced Search "Next" must return the next page, not an empty one.

The results screen pages forward with a relevance cursor (keyset pagination).
The keyset WHERE clause reuses the relevance expression, so its parameters must
be supplied twice; when they were not, the statement failed with IndexError,
the service returned an empty page, and every other "Next" click showed nothing.

These tests follow the same route the screen uses: page 1 returns a cursor,
page 2 is requested with that cursor, and the two pages must be disjoint and
full.
"""

from __future__ import annotations

import datetime
import uuid

from tests.integration._seed import connect, document, side, source

TERM = "cursorpagerterm"
DOCUMENTS = 60  # three full pages of 20


def _seed(pg_db):
    conn = connect(pg_db)
    with conn.cursor() as cur:
        src, sid = source(cur), side(cur)
        for index in range(DOCUMENTS):
            # Varying term frequency gives the relevance cursor real score values.
            text = (f"{TERM} " * (index % 4 + 1)) + f"cursor record {index}"
            document(cur, source_id=src, side_id=sid, text=text,
                     file_name=f"cursor-{index:03d}.txt", file_type="txt",
                     file_date=datetime.date.today())
    conn.commit()


def _search(client, **params):
    query = {"query": TERM, "per_page": 20, "use_advanced": "true",
             "use_fulltext": "true", "use_bm25": "false", "use_expansion": "true",
             "use_fuzzy": "true", "fast_mode": "true", "include_snippets": "false",
             "sort_by": "relevance", "sort_order": "desc"}
    query.update(params)
    resp = client.get("/api/search", query_string=query)
    assert resp.status_code == 200, resp.get_data(as_text=True)[:300]
    return resp.get_json()


def test_next_page_with_the_cursor_returns_a_full_disjoint_page(pg_db, admin_client):
    _seed(pg_db)
    first = _search(admin_client, page=1)
    cursor = first["pagination"]["cursor"]
    assert cursor, "page 1 must hand the screen a cursor for the next page"
    assert len(first["results"]) == 20

    second = _search(admin_client, page=2, cursor=cursor)
    assert len(second["results"]) == 20, (
        "the cursor page came back empty - the keyset query failed to bind")
    first_ids = {row["id"] for row in first["results"]}
    second_ids = {row["id"] for row in second["results"]}
    assert not first_ids & second_ids, "a row appeared on both pages"


def test_cursor_walk_covers_the_result_set(pg_db, admin_client):
    _seed(pg_db)
    seen = []
    cursor = None
    for page in range(1, 4):
        body = _search(admin_client, page=page, **({"cursor": cursor} if cursor else {}))
        assert len(body["results"]) == 20, f"page {page} must be full"
        seen.extend(row["id"] for row in body["results"])
        cursor = body["pagination"]["cursor"]
    assert len(seen) == len(set(seen)) == 60, "the walk must visit each match once"


# --- Ties (D7) -------------------------------------------------------------
# Every document below has the same text, so every relevance score is equal.
# The query orders ties by file_date DESC, then id ASC. The cursor used to carry
# only (score, id), so the next page compared ids alone: rows with a later date
# and a higher id were repeated, and rows with an earlier date and a lower id
# were skipped. Dates are assigned so that date order differs from id order.

TIE_TERM = "tiecursorwalkterm"
TIE_DOCUMENTS = 60


def _seed_ties(pg_db):
    """Seed TIE_DOCUMENTS tied rows under a term unique to this call.

    ``pg_db`` is session-scoped, so rows from earlier tests stay in the
    database. A fixed term made each tie test count rows another test had
    seeded (60 became 120) and passed only when it ran first.
    """
    term = f"{TIE_TERM}{uuid.uuid4().hex[:10]}"
    conn = connect(pg_db)
    base = datetime.date(2020, 1, 1)
    with conn.cursor() as cur:
        src, sid = source(cur), side(cur)
        for index in range(TIE_DOCUMENTS):
            document(cur, source_id=src, side_id=sid,
                     text=f"{term} identical body",
                     file_name=f"tie-{index:03d}.txt", file_type="txt",
                     file_date=base + datetime.timedelta(days=(index * 7) % 13))
    conn.commit()
    return term


def test_tied_scores_walk_in_the_query_order_without_repeats_or_gaps(pg_db, admin_client):
    tie = {"query": _seed_ties(pg_db)}

    reference = _search(admin_client, page=1, per_page=TIE_DOCUMENTS, **tie)
    expected = [row["id"] for row in reference["results"]]
    assert len(expected) == TIE_DOCUMENTS, "reference page must hold every tied row"
    dates = [row["file_date"] for row in reference["results"]]
    assert dates == sorted(dates, reverse=True), "reference is not date DESC"

    walked = []
    cursor = None
    for page in range(1, 4):
        params = dict(tie, page=page, per_page=20)
        if cursor:
            params["cursor"] = cursor
        body = _search(admin_client, **params)
        walked.extend(row["id"] for row in body["results"])
        cursor = body["pagination"]["cursor"]

    assert len(walked) == len(set(walked)) == TIE_DOCUMENTS, (
        f"walk returned {len(walked)} rows, {len(set(walked))} distinct")
    assert walked == expected, "cursor walk does not follow the query's tie order"



def test_the_walk_ends_with_a_short_page_and_no_further_cursor(pg_db, admin_client):
    """End of results: the walk terminates, and the last page is short."""
    tie = {"query": _seed_ties(pg_db)}
    cursor = None
    counts = []
    for page in range(1, 10):
        params = dict(tie, page=page, per_page=20)
        if cursor:
            params["cursor"] = cursor
        body = _search(admin_client, **params)
        counts.append(len(body["results"]))
        cursor = body["pagination"]["cursor"]
        if not body["results"]:
            break
        if not cursor:
            break
    assert sum(counts) == TIE_DOCUMENTS, f"walk returned {sum(counts)} rows"
    assert counts[-1] < 20 or cursor is None, f"walk did not end: {counts}"
    assert counts[-1] == TIE_DOCUMENTS - 20 * (len(counts) - 1), counts


def test_the_same_cursor_returns_the_same_page_on_repeat(pg_db, admin_client):
    """Stable ordering: a repeated request with one cursor yields identical rows."""
    tie = {"query": _seed_ties(pg_db)}
    first = _search(admin_client, page=1, per_page=20, **tie)
    cursor = first["pagination"]["cursor"]
    assert cursor, "page 1 must hand over a cursor"
    runs = [
        [row["id"] for row in _search(admin_client, page=2, per_page=20,
                                      cursor=cursor, **tie)["results"]]
        for _ in range(3)
    ]
    assert runs[0] and runs[0] == runs[1] == runs[2], "repeated cursor page changed"
