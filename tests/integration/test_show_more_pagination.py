"""Integration tests: "Show More" progressive loading across paginating interfaces.

Verifies that all list interfaces utilizing pagination render both the table-level
progressive loading control (data-ut-load-more) and the unified pagination Show More
button (unified-pagination-show-more-btn) when multiple pages exist, allowing the client
to dynamically append batches in-place without full-page reloads.
"""

from __future__ import annotations

import datetime
import uuid
import pytest

from _seed import connect, document, side, source

pytestmark = pytest.mark.integration


def test_show_more_on_categories_list(admin_client, pg_db):
    """Categories list with total_pages > 1 renders Show More controls."""
    tag = uuid.uuid4().hex[:8]
    conn = connect(pg_db)
    with conn, conn.cursor() as cur:
        for i in range(12):
            cur.execute(
                "INSERT INTO words (word) VALUES (%s) RETURNING id",
                (f"catw_{tag}_{i}",)
            )
            wid = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO categorys (word_id) VALUES (%s)",
                (wid,)
            )
    conn.close()

    resp = admin_client.get("/categories?per_page=5")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert "data-ut-load-more" in html, "categoriesTable lacks data-ut-load-more progressive loading control"
    assert "unified-pagination-show-more-btn" not in html, "two Load More controls on one list"
    assert html.count("data-ut-load-more") == 1
    assert 'data-table-id="categoriesTable"' in html
    assert 'data-page="1"' in html
    assert "Show More" in html or "Load More" in html


def test_show_more_on_keywords_list(admin_client, pg_db):
    """Keywords list with total_pages > 1 renders Show More controls."""
    from core.serialization import pack_int_list

    tag = uuid.uuid4().hex[:8]
    conn = connect(pg_db)
    with conn, conn.cursor() as cur:
        cur.execute("INSERT INTO words (word) VALUES (%s) RETURNING id", (f"kwc_{tag}",))
        cwid = cur.fetchone()[0]
        cur.execute("INSERT INTO categorys (word_id) VALUES (%s) RETURNING id", (cwid,))
        cid = cur.fetchone()[0]

        for i in range(12):
            cur.execute("INSERT INTO words (word) VALUES (%s) RETURNING id", (f"kww_{tag}_{i}",))
            wid = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO keywords (keyword, category_id) VALUES (%s, %s)",
                (pack_int_list([wid]), cid)
            )
    conn.close()

    resp = admin_client.get("/keywords?per_page=5")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert "data-ut-load-more" in html
    assert "unified-pagination-show-more-btn" not in html, "two Load More controls on one list"
    assert html.count("data-ut-load-more") == 1
    assert 'data-table-id="keywordsTable"' in html
    assert 'data-page="1"' in html


def test_show_more_on_sides_list(admin_client, pg_db):
    """Sides list with total_pages > 1 renders Show More controls."""
    tag = uuid.uuid4().hex[:8]
    conn = connect(pg_db)
    with conn, conn.cursor() as cur:
        for i in range(8):
            cur.execute(
                "INSERT INTO sides (name, importance, date_creation) VALUES (%s, %s, CURRENT_DATE)",
                (f"Side_{tag}_{i}", 0.5)
            )
    conn.close()

    resp = admin_client.get("/sides?per_page=4")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert "data-ut-load-more" in html
    assert "unified-pagination-show-more-btn" not in html, "two Load More controls on one list"
    assert html.count("data-ut-load-more") == 1
    assert 'data-table-id="sidesTable"' in html
    assert 'data-page="1"' in html


def test_show_more_on_sources_list(admin_client, pg_db):
    """Sources list with total_pages > 1 renders Show More controls."""
    tag = uuid.uuid4().hex[:8]
    conn = connect(pg_db)
    with conn, conn.cursor() as cur:
        for i in range(8):
            cur.execute(
                "INSERT INTO sources (name, job, importance, country, date_creation) VALUES (%s, 't', %s, 'NL', CURRENT_DATE)",
                (f"Source_{tag}_{i}", 0.5)
            )
    conn.close()

    resp = admin_client.get("/sources?per_page=4")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert "data-ut-load-more" in html
    assert "unified-pagination-show-more-btn" not in html, "two Load More controls on one list"
    assert html.count("data-ut-load-more") == 1
    assert 'data-table-id="sourcesTable"' in html
    assert 'data-page="1"' in html


def test_show_more_on_words_list(admin_client, pg_db):
    """Words list with total_pages > 1 renders Show More controls."""
    tag = uuid.uuid4().hex[:8]
    conn = connect(pg_db)
    with conn, conn.cursor() as cur:
        for i in range(12):
            cur.execute(
                "INSERT INTO words (word) VALUES (%s) ON CONFLICT (word) DO NOTHING",
                (f"testword_{tag}_{i}",)
            )
    conn.close()

    resp = admin_client.get("/words?per_page=5")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert "data-ut-load-more" in html
    assert "unified-pagination-show-more-btn" not in html, "two Load More controls on one list"
    assert html.count("data-ut-load-more") == 1
    assert 'data-table-id="wordsTable"' in html
    assert 'data-page="1"' in html


def test_show_more_on_category_words(admin_client, pg_db):
    """Category words with total_pages > 1 renders Show More controls."""
    tag = uuid.uuid4().hex[:8]
    conn = connect(pg_db)
    with conn, conn.cursor() as cur:
        cur.execute("INSERT INTO words (word) VALUES (%s) RETURNING id", (f"cname_{tag}",))
        cwid = cur.fetchone()[0]
        cur.execute("INSERT INTO categorys (word_id) VALUES (%s) RETURNING id", (cwid,))
        cid = cur.fetchone()[0]

        for i in range(10):
            cur.execute("INSERT INTO words (word) VALUES (%s) RETURNING id", (f"cw_{tag}_{i}",))
            wid = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO words_categorys (word_id, category_id) VALUES (%s, %s)",
                (wid, cid)
            )
    conn.close()

    resp = admin_client.get(f"/categories/{cid}/words?per_page=5")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert "data-ut-load-more" in html
    assert "unified-pagination-show-more-btn" not in html, "two Load More controls on one list"
    assert html.count("data-ut-load-more") == 1
    assert 'data-table-id="categoryWordsTable"' in html
    assert 'data-page="1"' in html


def test_show_more_on_email_words(admin_client, pg_db):
    """Email words list with total_pages > 1 renders Show More button."""
    tag = uuid.uuid4().hex[:8]
    conn = connect(pg_db)
    with conn, conn.cursor() as cur:
        for i in range(10):
            cur.execute(
                "INSERT INTO words (word) VALUES (%s) ON CONFLICT (word) DO NOTHING",
                (f"user_{tag}_{i}@example.com",)
            )
    conn.close()

    resp = admin_client.get("/email-words?per_page=4")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert "unified-pagination-show-more-btn" not in html, "two Load More controls on one list"
    assert html.count("data-ut-load-more") == 1
    assert 'data-table-id="emailWordsTable"' in html
    assert 'data-page="1"' in html
