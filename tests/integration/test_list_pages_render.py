"""The unified-table list pages must render (field defect, 2026-09-29).

The owner's Windows run hit ``GET /categories -> 500``:
``jinja2.exceptions.UndefinedError: 'record_table' is undefined`` - the page
calls the shared table macro without importing it. The same audit found the
same missing import in ``category_words.html``. No test rendered these pages,
so a missing Jinja import reached production.

Guard: the real routes rendered through the real app - every unified-table
list page answers 200 and contains the rendered table, not the error page.
(The static import guard is tests/unit/test_table_macro_imports.py.)
"""

import pytest

pytestmark = pytest.mark.integration

# template path -> route that renders it (the unified-table list pages).
# /search and /files/types render their table only when rows exist, so the
# route strings carry ``{token}`` - replaced with the seeded document's
# unique token by the parametrised test.
PAGES = [
    ("Category/categories_list.html", "/categories"),
    ("Category/category_words.html", None),          # route needs a category id; rendered below
    ("Keyword/keywords_list.html", "/keywords"),
    ("Side/sides_list.html", "/sides"),
    ("Sources/sources_list.html", "/sources"),
    ("Word/Word_list.html", "/words"),
    ("Search/search.html", "/search?q={token}"),
    ("file/files_list.html", "/files"),
    ("file/file_types.html", "/files/types"),
]


@pytest.fixture(scope="module")
def seeded_corpus(pg_db):
    """One real document, so the conditional-table pages have rows."""
    import datetime
    import uuid
    from _seed import connect, document, side, source, word_counts
    token = f"zqseed{uuid.uuid4().hex[:8]}"
    conn = connect(pg_db)
    with conn, conn.cursor() as cur:
        s, d = source(cur), side(cur)
        _, hash_id, _ = document(cur, source_id=s, side_id=d,
                                 text=f"{token} report body",
                                 file_type="pdf", file_date=datetime.date(2026, 1, 5))
        # The search page finds files through the word index, not raw text.
        word_counts(cur, hash_id, {token: 3})
    conn.close()
    return token


def test_the_categories_page_renders(admin_client):
    resp = admin_client.get("/categories")
    assert resp.status_code == 200, resp.get_data(as_text=True)[:400]
    body = resp.get_data(as_text=True)
    assert "<table" in body, "the categories table did not render"


def test_the_category_words_page_renders(admin_client, pg_db):
    from _seed import connect, side, source
    conn = connect(pg_db)
    with conn, conn.cursor() as cur:
        s = source(cur)
        d = side(cur)
        cur.execute(
            "INSERT INTO words (word) VALUES (%s)"
            " ON CONFLICT (word) DO UPDATE SET word = EXCLUDED.word RETURNING id",
            ("zrenderword",))
        word_id = cur.fetchone()[0]
        cur.execute("INSERT INTO categorys (word_id) VALUES (%s) RETURNING id",
                    (word_id,))
        category_id = cur.fetchone()[0]
    conn.close()
    resp = admin_client.get(f"/categories/{category_id}/words")
    assert resp.status_code == 200, resp.get_data(as_text=True)[:400]
    body = resp.get_data(as_text=True)
    assert "<table" in body, "the category words table did not render"


@pytest.mark.parametrize("route", [r for _, r in PAGES if r])
def test_every_list_page_route_renders_its_table(admin_client, seeded_corpus, route):
    resp = admin_client.get(route.format(token=seeded_corpus))
    assert resp.status_code == 200, (
        f"{route}: {resp.status_code} {resp.get_data(as_text=True)[:300]}")
    assert "<table" in resp.get_data(as_text=True), (
        f"{route} rendered without its table")


def test_the_file_library_carries_a_dedicated_name_column_search(admin_client,
                                                                 seeded_corpus):
    """The reader asked for one search field per column: the File Name
    column's filter is the text kind, bound to the view's `search`
    parameter and prefilled from it."""
    resp = admin_client.get("/files?search=rep")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert '{"kind":"text","param":"search"' in html.replace(" ", ""), (
        "the File Name column has no dedicated search field")
    assert 'value="rep"' in html, "the column search is not prefilled from the view"
