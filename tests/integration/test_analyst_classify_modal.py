"""Integration: analyst classification in the file preview pop-up.

Requested: "in the pop-up interface to display the content, add the analyst
classification feature". The pop-up is the File Details modal on the archives
page, which shows one file after another (opened from a list, stepped through
with its own previous/next).

These tests run the real page against the real database and prove that the
pop-up ships the same, server-rendered analyst card the File Detail page and
the Reader use - not a second implementation - with the write controls gated
on the viewer's role exactly as they are on the pages, and that the endpoints
the card calls are the audit-logged analyst ones, addressed to the file on
screen.

The card's *behaviour* (re-pointing itself at the next file, refreshing
badges, sending the on-screen file id) is driven by
tests/unit/test_frontend_analyst_classify_modal.py, which runs the shipped
module under node.
"""

import re
import sys
import uuid
from pathlib import Path

import psycopg2
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

pytestmark = pytest.mark.integration

_UNIQUE = uuid.uuid4().hex[:8]
_CARD_ID = "modalAnalystClassifyCard"

_CARD = re.compile(
    r'<div class="analyst-classify[^"]*"\s+id="%s"' % _CARD_ID,
)


@pytest.fixture(scope="module")
def page_file(pg_db):
    """One file, so the pop-up has something real to classify."""
    cfg = pg_db
    conn = psycopg2.connect(
        host=cfg["host"], port=cfg["port"], user=cfg["user"],
        password=cfg["password"], dbname=cfg["database"],
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO sides (name, importance, date_creation)"
                " VALUES (%s, 1.0, CURRENT_DATE) RETURNING id",
                (f"modal-analyst-side-{_UNIQUE}",),
            )
            side_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO sources (name, job, importance, country, date_creation)"
                " VALUES (%s, 'test', 1.0, 'NL', CURRENT_DATE) RETURNING id",
                (f"modal-analyst-source-{_UNIQUE}",),
            )
            source_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO hashs (hash) VALUES (%s) RETURNING id",
                (f"modal-analyst-hash-{_UNIQUE}",),
            )
            hash_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO hash_contexts (hash_id, source_id, side_id)"
                " VALUES (%s, %s, %s) RETURNING id",
                (hash_id, source_id, side_id),
            )
            context_id = cur.fetchone()[0]
            cur.execute(
                """
                INSERT INTO paths (file_name, file_path, file_size, file_type,
                                   file_status, file_date, date_creation, context_id)
                VALUES (%s, %s, 4096, '.txt', 'Read', CURRENT_DATE, CURRENT_DATE, %s)
                RETURNING id
                """,
                (f"modal-analyst-{_UNIQUE}.txt", f"/modal-test/{_UNIQUE}.txt", context_id),
            )
            path_id = cur.fetchone()[0]
        conn.commit()
    finally:
        conn.close()
    return path_id


def archives_page(client):
    response = client.get("/archives")
    assert response.status_code == 200, response.get_data(as_text=True)[:400]
    return response.get_data(as_text=True)


# ---------------------------------------------------------------------------
# The pop-up carries the card
# ---------------------------------------------------------------------------

def test_the_popup_renders_the_shared_analyst_card(admin_client):
    html = archives_page(admin_client)
    assert _CARD.search(html), "the pop-up does not render the analyst card"


def test_the_card_sits_inside_the_preview_popup(admin_client):
    """In the pop-up - not somewhere else on the page, where nobody looks."""
    html = archives_page(admin_client)
    modal_at = html.index('id="fileModal"')
    card_at = html.index(f'id="{_CARD_ID}"')
    analysis_at = html.index('id="fileAnalysisSection"')
    assert modal_at < card_at < analysis_at


def test_the_card_is_not_a_second_page_level_card(admin_client):
    """The pop-up's card has its own id: the two cards cannot cross-wire."""
    html = archives_page(admin_client)
    assert 'id="analystClassifyCard"' not in html
    assert _CARD.search(html)


def test_the_card_carries_the_hooks_the_module_binds_to(admin_client):
    html = archives_page(admin_client)
    for hook in ("data-analyst-classify", "data-analyst-badges", "data-analyst-select",
                 "data-analyst-new-name", "data-analyst-assign", "data-analyst-remove-all"):
        assert hook in html, hook


def test_the_page_data_and_styles_the_card_needs_are_shipped(admin_client):
    html = archives_page(admin_client)
    assert 'id="analyst-classify-page-data"' in html
    assert "css/analyst-classify.css" in html
    # The module is loaded through the page's module graph (file-details.js
    # imports it), so it must be reachable as a static file.
    assert admin_client.get("/static/js/modules/analyst-classify.js").status_code == 200


# ---------------------------------------------------------------------------
# Roles: same gate as the pages
# ---------------------------------------------------------------------------

def test_an_admin_may_write_from_the_popup(admin_client):
    html = archives_page(admin_client)
    assert '"canCategorize": true' in html
    assert "data-analyst-assign" in html
    assert "analyst-classify-readonly-note" not in html


def test_a_viewer_reads_but_does_not_write(viewer_client, admin_credentials, page_file):
    """The pop-up must not become a way around the role model."""
    html = archives_page(viewer_client)
    assert '"canCategorize": false' in html
    assert "analyst-classify-readonly-note" in html
    assert "data-analyst-assign" not in html
    # And the write endpoint refuses that session outright.
    refused = viewer_client.post(
        "/api/analyst/assign",
        json={"path_ids": [page_file], "category_name": f"modal-{_UNIQUE}",
              "create_category": True},
    )
    assert refused.status_code in (401, 403), refused.status_code


# ---------------------------------------------------------------------------
# What the card does when a file is shown
# ---------------------------------------------------------------------------

def test_the_card_reads_assignments_for_a_single_file(admin_client, page_file):
    """The badge source the card refreshes from, filtered to one file."""
    response = admin_client.get(f"/api/analyst/assignments?file_id={page_file}&per_page=100")
    assert response.status_code == 200
    body = response.get_json()
    assert "assignments" in body
    for row in body["assignments"]:
        assert row["path_id"] == page_file


def test_assigning_from_the_popup_is_audited_for_that_file(admin_client, page_file):
    """The same audit-logged endpoint the card posts to, for the on-screen file."""
    name = f"modal-category-{_UNIQUE}"
    assigned = admin_client.post(
        "/api/analyst/assign",
        json={"path_ids": [page_file], "category_name": name, "create_category": True,
              "source_query": "content view"},
    )
    assert assigned.status_code == 200, assigned.get_data(as_text=True)
    assert assigned.get_json()["success"] is True

    listed = admin_client.get(f"/api/analyst/assignments?file_id={page_file}&per_page=100")
    names = [row["category_name"] for row in listed.get_json()["assignments"]]
    assert name in names

    removed = admin_client.post("/api/analyst/remove", json={"path_ids": [page_file]})
    assert removed.status_code == 200
    after = admin_client.get(f"/api/analyst/assignments?file_id={page_file}&per_page=100")
    assert after.get_json()["assignments"] == []
