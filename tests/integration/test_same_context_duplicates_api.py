"""The same-source, same-side cleanup as the real application serves it.

Preview is read-only. Removal is for analysts and admins only, needs the CSRF
token, and removes exactly the repeats the preview reported.
"""

from __future__ import annotations

import uuid

import pytest

from tests.integration._seed import connect, document, side, source

pytestmark = pytest.mark.integration


def _csrf(client):
    with client.session_transaction() as sess:
        return sess.get("csrf_token") or sess.get("_csrf_token") or ""


def _login_new(app, role):
    from core.security.service import get_auth_service

    username, password = f"scd_{role}_{uuid.uuid4().hex[:6]}", "scd-password-123"
    get_auth_service().create_user(username, password, role=role)
    client = app.test_client()
    assert client.post("/auth/login", json={"username": username,
                                            "password": password}).status_code == 200
    return client


def _seed(pg_db):
    conn = connect(pg_db)
    with conn.cursor() as cur:
        a = source(cur)
        s1 = side(cur)
        body = f"repeated body {uuid.uuid4().hex}"
        p1, h, _ = document(cur, source_id=a, side_id=s1, text=body, file_name="r.txt")
        p2, _, _ = document(cur, source_id=a, side_id=s1, text=body,
                            file_name="r.txt", hash_id=h)
    conn.commit()
    conn.close()
    return p1, p2


def test_preview_reports_the_repeat_and_changes_nothing(app, pg_db):
    p1, p2 = _seed(pg_db)
    analyst = _login_new(app, "analyst")
    body = analyst.get("/api/files/same-context-duplicates").get_json()
    assert body["success"] is True
    assert body["to_remove"] >= 1
    kept_ids = {row["kept_id"] for row in body["sample"]}
    removed = {i for row in body["sample"] for i in row["removed_ids"]}
    assert p1 in kept_ids
    assert p2 in removed

    conn = connect(pg_db)
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM paths WHERE id = %s", (p2,))
        assert cur.fetchone()[0] == 1, "the preview must not delete anything"
    conn.close()


def test_a_viewer_cannot_remove_repeats(app, pg_db):
    p1, p2 = _seed(pg_db)
    viewer = _login_new(app, "viewer")
    response = viewer.post("/api/files/same-context-duplicates/remove",
                           headers={"X-CSRFToken": _csrf(viewer)})
    assert response.status_code == 403

    conn = connect(pg_db)
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM paths WHERE id = %s", (p2,))
        assert cur.fetchone()[0] == 1
    conn.close()


def test_an_analyst_removal_removes_the_reported_repeats(app, pg_db):
    p1, p2 = _seed(pg_db)
    analyst = _login_new(app, "analyst")
    preview = analyst.get("/api/files/same-context-duplicates").get_json()
    expected = preview["to_remove"]

    response = analyst.post("/api/files/same-context-duplicates/remove",
                            headers={"X-CSRFToken": _csrf(analyst)})
    body = response.get_json()
    assert response.status_code == 200 and body["success"] is True
    assert body["removed"] == expected

    conn = connect(pg_db)
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM paths WHERE id IN (%s, %s) ORDER BY id", (p1, p2))
        assert [row[0] for row in cur.fetchall()] == [p1], "the earliest copy is kept"
    conn.close()


def test_the_files_page_offers_the_cleanup_with_its_confirmation(app):
    analyst = _login_new(app, "analyst")
    response = analyst.get("/files")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'id="removeSameContextDuplicatesBtn"' in html
    assert 'data-on-click="removeSameContextDuplicates()"' in html
    assert 'data-confirm-dialog' in html and 'id="sameContextDuplicatesDialog"' in html
