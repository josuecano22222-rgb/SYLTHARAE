"""Bounded keyword search against the real schema.

The expected results come from a snapshot of the whole ``keywords`` table,
read with plain SQL, and ordered with the rules the old full-load code used
(see the comments on each oracle). The code under test is reached through
the real routes (``/api/keywords``) and the real function
(``get_keywords_with_usage``), not through the helper directly.
"""
import math
import uuid
from collections import namedtuple

import pytest

from core.serialization import pack_int_list, unpack_int_list
from core.table_paging import BATCH_HEADER, BATCH_VALUE
from _seed import SeedLedger, connect, document, remove_seeded, side, source

Row = namedtuple("Row", "id text usage cat cat_name")


# ----------------------------------------------------------------- seeding


def seed_keywords(pg_db, tag, count, *, words=24, categories=3, seed=1, max_usage=8):
    """``count`` keywords whose text is a few of ``words`` tag words. Words
    named ``...hit...`` match the search ``f"{tag}-hit"``; the rest do not.
    Usage (distinct paths) varies from 0 to ``max_usage``. Returns the
    ``SeedLedger`` the caller must pass to ``remove_seeded`` when done."""
    import random

    rng = random.Random(seed)
    ledger = SeedLedger()
    conn = connect(pg_db)
    try:
        with conn, conn.cursor() as cur:
            src, sd = source(cur), side(cur)
            ledger.sources.append(src)
            ledger.sides.append(sd)
            word_ids = {}
            for j in range(words):
                name = f"{tag}-{'hit' if j % 4 == 0 else 'miss'}{j:02d}"
                cur.execute("INSERT INTO words (word) VALUES (%s) ON CONFLICT (word)"
                            " DO UPDATE SET word = EXCLUDED.word RETURNING id", (name,))
                word_ids[j] = cur.fetchone()[0]
                ledger.words.append(word_ids[j])
            cat_ids = []
            for c in range(categories):
                cur.execute("INSERT INTO words (word) VALUES (%s) ON CONFLICT (word)"
                            " DO UPDATE SET word = EXCLUDED.word RETURNING id",
                            (f"{tag}-cat{c}",))
                wid = cur.fetchone()[0]
                ledger.words.append(wid)
                cur.execute("INSERT INTO categorys (word_id) VALUES (%s) ON CONFLICT (word_id)"
                            " DO UPDATE SET word_id = EXCLUDED.word_id RETURNING id", (wid,))
                cat_ids.append(cur.fetchone()[0])
                ledger.categories.append(cat_ids[-1])

            seen = set()
            made = 0
            while made < count:
                size = rng.randint(1, 3)
                combo = tuple(rng.randrange(words) for _ in range(size))
                if combo in seen:
                    continue
                seen.add(combo)
                made += 1
                text = " ".join(f"{tag}-{'hit' if j % 4 == 0 else 'miss'}{j:02d}" for j in combo)
                usage = rng.randint(0, max_usage)
                category = cat_ids[rng.randrange(categories)]
                cur.execute("INSERT INTO keywords (keyword, category_id) VALUES (%s, %s)"
                            " RETURNING id", (pack_int_list([word_ids[j] for j in combo]), category))
                kid = cur.fetchone()[0]
                ledger.keywords.append(kid)
                if usage == 0:
                    continue
                path_id, hash_id, context_id = document(cur, source_id=src, side_id=sd, text=text)
                ledger.paths.append(path_id)
                ledger.hashes.append(hash_id)
                ledger.contexts.append(context_id)
                cur.execute("INSERT INTO keywords_hashs (keyword_id, hash_id) VALUES (%s, %s)",
                            (kid, hash_id))
                for extra in range(usage - 1):
                    cur.execute(
                        "INSERT INTO paths (file_name, file_path, file_size, file_type,"
                        " file_status, file_date, date_creation, context_id, processing_status)"
                        " VALUES (%s, %s, 1, 'txt', 'Unread', CURRENT_DATE, CURRENT_DATE, %s,"
                        " 'processed') RETURNING id",
                        (f"x_{kid}_{extra}.txt", f"/seed/x_{kid}_{extra}.txt", context_id))
                    ledger.paths.append(cur.fetchone()[0])
    finally:
        conn.close()
    return ledger


def snapshot(pg_db):
    """Every keyword with its text, usage and category, read straight from SQL."""
    conn = connect(pg_db)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, word FROM words")
            words = dict(cur.fetchall())
            cur.execute("SELECT cat.id, w.word FROM categorys cat"
                        " LEFT JOIN words w ON cat.word_id = w.id")
            cats = dict(cur.fetchall())
            cur.execute("SELECT k.id, k.keyword, k.category_id FROM keywords k")
            kws = cur.fetchall()
            cur.execute("SELECT kp.keyword_id, COUNT(DISTINCT p.id) FROM keywords_hashs kp"
                        " JOIN hash_contexts hc ON hc.hash_id = kp.hash_id"
                        " JOIN paths p ON p.context_id = hc.id GROUP BY kp.keyword_id")
            usage = dict(cur.fetchall())
    finally:
        conn.close()
    rows = []
    for kid, blob, cat in kws:
        try:
            ids = unpack_int_list(bytes(blob))
        except Exception:
            ids = []
        text = " ".join(words[w] for w in ids if w in words)
        rows.append(Row(kid, text, usage.get(kid, 0), cat, cats.get(cat)))
    return rows


# ------------------------------------------------------------------ oracles


def api_expected(rows, q, sort, order, status="", category=""):
    """The API's old order. Old code pre-sorted by id ascending, then did a
    stable sort on the column (so ties stay id ascending in both directions).
    Rows with no text never match a search. A sort it did not know was not
    applied at all, so the order stays id ascending."""
    desc = order == "desc"
    out = sorted(rows, key=lambda r: r.id)
    if status == "active":
        out = [r for r in out if r.usage > 0]
    elif status == "unused":
        out = [r for r in out if r.usage == 0]
    if category:
        out = [r for r in out if r.cat == int(category)]
    if q:
        out = [r for r in out if r.text and q in r.text.lower()]
    keys = {
        "usage_count": lambda r: r.usage,
        "text": lambda r: r.text.lower(),
        "id": lambda r: r.id,
        "status": lambda r: (r.usage > 0, r.usage),
    }
    if sort in keys:
        out = sorted(out, key=keys[sort], reverse=desc)
    return [r.id for r in out]


def html_expected(rows, q, sort, order):
    """The HTML page's old order: one sort on a tuple key, so the id tie-break
    follows the direction. The sort-by-text listing (no search) includes rows
    with no text."""
    desc = order != "asc"
    out = list(rows)
    if q:
        out = [r for r in out if r.text and q in r.text.lower()]
    keys = {
        "text": lambda r: (r.text.lower(), r.id),
        "id": lambda r: (r.id,),
        "status": lambda r: (r.usage > 0, r.usage, r.id),
        "usage_count": lambda r: (r.usage, r.id),
    }
    out = sorted(out, key=keys.get(sort, keys["usage_count"]), reverse=desc)
    return [r.id for r in out]


# ---------------------------------------------------------------- helpers


def api(client, **params):
    resp = client.get("/api/keywords", query_string=params)
    assert resp.status_code == 200, resp.get_data(as_text=True)[:400]
    return resp.get_json()


def api_all_pages(client, *, per_page, **params):
    first = api(client, page=1, per_page=per_page, **params)
    ids, pages = [], []
    for page in range(1, first["total_pages"] + 1):
        body = first if page == 1 else api(client, page=page, per_page=per_page, **params)
        assert body["total"] == first["total"]
        assert body["page"] == page
        pages.append(len(body["keywords"]))
        ids.extend(k["id"] for k in body["keywords"])
    return first["total"], first["total_pages"], pages, ids


def html_all_pages(pg_db, *, per_page, q, sort, order):
    """Every page through the HTML function: (total, ids)."""
    from Api.utils.utils import get_keywords_with_usage

    _, total = get_keywords_with_usage(search_term=q, page=1, per_page=per_page,
                                       sort_by=sort, sort_order=order)
    ids = []
    for page in range(1, max(1, math.ceil(total / per_page)) + 1):
        rows, t = get_keywords_with_usage(search_term=q, page=page, per_page=per_page,
                                          sort_by=sort, sort_order=order)
        assert t == total
        ids.extend(r[0] for r in rows)
    return total, ids


@pytest.fixture(scope="module")
def tag():
    return "kwb" + uuid.uuid4().hex[:8]


@pytest.fixture(scope="module")
def corpus(pg_db, tag):
    """A corpus large enough for several pages and for usage ties."""
    ledger = seed_keywords(pg_db, tag, 150, seed=3)
    yield tag
    remove_seeded(pg_db, ledger)


# ---------------------------------------------------------- ordering & totals


@pytest.mark.parametrize("sort", ["usage_count", "text", "id", "status", "date_creation"])
@pytest.mark.parametrize("order", ["asc", "desc"])
def test_api_search_order_and_totals_match_the_old_order(admin_client, pg_db, corpus, sort, order):
    rows = snapshot(pg_db)
    q = f"{corpus}-hit"
    total, total_pages, sizes, ids = api_all_pages(
        admin_client, per_page=7, q=q, sort=sort, order=order)
    expected = api_expected(rows, q.lower(), sort, order)
    assert total == len(expected) > 0
    assert total_pages == math.ceil(total / 7)
    assert sizes[:-1] == [7] * (total_pages - 1)
    assert sizes[-1] == total - 7 * (total_pages - 1)
    assert ids == expected
    assert len(ids) == len(set(ids)), "no keyword appears twice across pages"


@pytest.mark.parametrize("sort", ["usage_count", "text", "id", "status"])
@pytest.mark.parametrize("order", ["asc", "desc"])
def test_html_search_order_and_totals_match_the_old_order(pg_db, corpus, sort, order):
    rows = snapshot(pg_db)
    q = f"{corpus}-hit"
    total, ids = html_all_pages(pg_db, per_page=9, q=q, sort=sort, order=order)
    expected = html_expected(rows, q.lower(), sort, order)
    assert total == len(expected) > 0
    assert ids == expected


@pytest.mark.parametrize("order", ["asc", "desc"])
def test_html_text_listing_without_search_includes_rows_with_no_text(pg_db, corpus, order):
    rows = snapshot(pg_db)
    total, ids = html_all_pages(pg_db, per_page=50, q=None, sort="text", order=order)
    expected = html_expected(rows, None, "text", order)
    assert total == len(rows)
    assert ids == expected


# -------------------------------------------------------- combined filters


@pytest.mark.parametrize("status", ["active", "unused"])
@pytest.mark.parametrize("sort", ["usage_count", "text", "status"])
def test_search_with_status_and_category_filters(admin_client, pg_db, corpus, status, sort):
    rows = snapshot(pg_db)
    q = f"{corpus}-hit"
    cat = next(r.cat for r in rows if r.cat_name and r.cat_name.startswith(corpus))
    for category in ("", str(cat)):
        total, _, _, ids = api_all_pages(admin_client, per_page=5, q=q, sort=sort,
                                         order="desc", status=status, category=category)
        expected = api_expected(rows, q.lower(), sort, "desc", status=status, category=category)
        assert total == len(expected)
        assert ids == expected, (status, category)


def test_category_names_and_row_fields_are_unchanged(admin_client, pg_db, corpus):
    rows = snapshot(pg_db)
    by_id = {r.id: r for r in rows}
    body = api(admin_client, q=f"{corpus}-hit", sort="id", order="asc", per_page=200)
    assert body["keywords"], "the corpus should match"
    for k in body["keywords"]:
        assert set(k) == {"id", "text", "importance", "usage_count", "is_duplicate",
                          "category_id", "category_name", "date_creation", "status"}
        ref = by_id[k["id"]]
        assert k["text"] == ref.text
        assert k["usage_count"] == ref.usage
        assert k["category_id"] == ref.cat
        assert k["category_name"] == ref.cat_name
        assert k["status"] == ("active" if ref.usage > 0 else "unused")


# -------------------------------------------------------------- empty results


def test_an_empty_search_returns_no_rows_and_one_page(admin_client, corpus):
    body = api(admin_client, q=f"{corpus}-no-such-text", page=1, per_page=10)
    assert body["keywords"] == []
    assert body["total"] == 0
    assert body["page"] == 1
    assert body["total_pages"] == 1


def test_an_empty_html_search(pg_db, corpus):
    total, ids = html_all_pages(pg_db, per_page=10, q=f"{corpus}-no-such-text",
                                sort="usage_count", order="desc")
    assert total == 0 and ids == []


# ------------------------------------------------------- invalid & past-end pages


@pytest.mark.parametrize("page_value", ["0", "-3", "abc", "1.5", ""])
def test_invalid_pages_fall_back_to_the_first_page(admin_client, pg_db, corpus, page_value):
    rows = snapshot(pg_db)
    q = f"{corpus}-hit"
    body = api(admin_client, q=q, sort="id", order="asc", per_page=10, page=page_value)
    expected = api_expected(rows, q.lower(), "id", "asc")
    assert body["page"] == 1
    assert [k["id"] for k in body["keywords"]] == expected[:10]


def test_a_page_past_the_end_clamps_to_the_last_page(admin_client, pg_db, corpus):
    rows = snapshot(pg_db)
    q = f"{corpus}-hit"
    expected = api_expected(rows, q.lower(), "id", "asc")
    last = math.ceil(len(expected) / 10)
    body = api(admin_client, q=q, sort="id", order="asc", per_page=10, page=10**6)
    assert body["page"] == last
    assert [k["id"] for k in body["keywords"]] == expected[(last - 1) * 10:]


def test_a_page_past_the_end_as_a_show_more_batch_returns_no_rows(admin_client, pg_db, corpus):
    q = f"{corpus}-hit"
    resp = admin_client.get("/api/keywords", query_string={
        "q": q, "sort": "id", "order": "asc", "per_page": 10, "page": 10**6},
        headers={BATCH_HEADER: BATCH_VALUE})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["keywords"] == []
    assert body["total"] > 0


def test_html_page_past_the_end_is_an_empty_page(pg_db, corpus):
    from Api.utils.utils import get_keywords_with_usage

    _, total = get_keywords_with_usage(search_term=f"{corpus}-hit", page=1, per_page=10,
                                       sort_by="id", sort_order="asc")
    rows, t = get_keywords_with_usage(search_term=f"{corpus}-hit", page=10**6, per_page=10,
                                      sort_by="id", sort_order="asc")
    assert rows == [] and t == total


# ------------------------------------------------------ page-boundary traversal


@pytest.mark.parametrize("per_page", [1, 2, 49, 50, 51])
def test_traversal_at_page_boundaries(admin_client, pg_db, corpus, per_page):
    rows = snapshot(pg_db)
    q = f"{corpus}-hit"
    expected = api_expected(rows, q.lower(), "usage_count", "desc")
    total, total_pages, sizes, ids = api_all_pages(
        admin_client, per_page=per_page, q=q, sort="usage_count", order="desc")
    assert total == len(expected)
    assert ids == expected
    assert sum(sizes) == total
    assert all(s == per_page for s in sizes[:-1])


def test_a_total_that_is_an_exact_multiple_has_a_full_last_page(admin_client, pg_db, corpus):
    rows = snapshot(pg_db)
    q = f"{corpus}-hit"
    n = len(api_expected(rows, q.lower(), "id", "asc"))
    per_page = next(p for p in range(1, n + 1) if n % p == 0 and n // p <= 60)
    total, total_pages, sizes, ids = api_all_pages(admin_client, per_page=per_page,
                                                   q=q, sort="id", order="asc")
    assert total == n and sizes[-1] == per_page


# ------------------------------------------------------------- large set


@pytest.fixture(scope="module")
def big_corpus(pg_db):
    big = "kwbig" + uuid.uuid4().hex[:6]
    ledger = seed_keywords(pg_db, big, 2500, words=40, categories=4, seed=9, max_usage=6)
    yield big
    remove_seeded(pg_db, ledger)


def test_large_set_spans_several_chunks_and_pages(admin_client, pg_db, big_corpus):
    """2,500 keywords: the stream reads three chunks of 1,000, and the matches
    cross the chunk boundaries. Every page at 200 a page (the largest size the
    table offers) is compared with the oracle."""
    rows = snapshot(pg_db)
    q = f"{big_corpus}-hit"
    expected = api_expected(rows, q.lower(), "usage_count", "desc")
    assert len(expected) > 1000  # matches spread over all three chunks
    total, total_pages, sizes, ids = api_all_pages(
        admin_client, per_page=200, q=q, sort="usage_count", order="desc")
    assert total == len(expected)
    assert ids == expected
    assert len(set(ids)) == len(ids)


def test_large_set_html_total_and_order(pg_db, big_corpus):
    rows = snapshot(pg_db)
    q = f"{big_corpus}-hit"
    total, ids = html_all_pages(pg_db, per_page=200, q=q, sort="text", order="asc")
    expected = html_expected(rows, q.lower(), "text", "asc")
    assert total == len(expected) > 1000
    assert ids == expected


# ------------------------------------------------- changes between two requests


def test_matches_removed_between_requests_clamp_the_page(admin_client, pg_db):
    gone = "kwdel" + uuid.uuid4().hex[:6]
    ledger = seed_keywords(pg_db, gone, 60, words=12, categories=2, seed=21, max_usage=3)
    try:
        _matches_removed(admin_client, pg_db, gone)
    finally:
        remove_seeded(pg_db, ledger)


def _matches_removed(admin_client, pg_db, gone):
    q = f"{gone}-hit"
    first = api(admin_client, q=q, sort="id", order="asc", per_page=10)
    last_page = first["total_pages"]
    assert last_page >= 2
    rows = snapshot(pg_db)
    doomed = [r.id for r in rows if r.text and q in r.text.lower()]
    keep_ids = set(doomed[: len(doomed) // 2])
    conn = connect(pg_db)
    try:
        with conn, conn.cursor() as cur:
            drop = [k for k in doomed if k not in keep_ids]
            cur.execute("DELETE FROM keywords_hashs WHERE keyword_id = ANY(%s)", (drop,))
            cur.execute("DELETE FROM keywords WHERE id = ANY(%s)", (drop,))
    finally:
        conn.close()
    body = api(admin_client, q=q, sort="id", order="asc", per_page=10, page=last_page)
    assert body["total"] == len(keep_ids)
    assert body["page"] == body["total_pages"] == math.ceil(len(keep_ids) / 10)
    assert [k["id"] for k in body["keywords"]] == sorted(keep_ids)[(body["page"] - 1) * 10:]


# ------------------------------------------------------------ errors surface


@pytest.fixture()
def broken_keyword_queries(monkeypatch):
    """Make the keyword-stream query fail, and nothing else."""
    import Api.routes.keywords as routes
    import Api.utils.utils as utils

    real_utils, real_routes = utils.execute_query, routes.execute_query

    def wrap(real):
        def run(sql, params=(), fetch="all", *a, **kw):
            if "FROM keywords k" in sql and "k.id >" in sql:
                raise RuntimeError("simulated database failure")
            return real(sql, params, fetch, *a, **kw)
        return run

    monkeypatch.setattr(utils, "execute_query", wrap(real_utils))
    monkeypatch.setattr(routes, "execute_query", wrap(real_routes))
    yield


def test_a_database_error_in_html_search_is_raised_not_empty(broken_keyword_queries):
    from Api.utils.utils import get_keywords_with_usage

    with pytest.raises(RuntimeError, match="simulated"):
        get_keywords_with_usage(search_term="anything", page=1, per_page=10,
                                sort_by="usage_count", sort_order="desc")


def test_a_database_error_in_html_text_listing_is_raised(broken_keyword_queries):
    from Api.utils.utils import get_keywords_with_usage

    with pytest.raises(RuntimeError, match="simulated"):
        get_keywords_with_usage(search_term=None, page=1, per_page=10,
                                sort_by="text", sort_order="asc")


def test_a_database_error_in_api_search_is_an_error_response(admin_client, broken_keyword_queries):
    resp = admin_client.get("/api/keywords", query_string={"q": "anything", "page": 1})
    assert resp.status_code == 500
    assert resp.get_json()["success"] is False
