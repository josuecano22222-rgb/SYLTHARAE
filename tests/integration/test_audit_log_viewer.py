"""Audit log viewer: core/security/audit_query.py and Api/routes/audit.py.

Against the real audit_log (m0003) in PostgreSQL. Each test writes entries
under its own action name so tests sharing the session database never see
each other's rows.
"""
from __future__ import annotations

import datetime
import uuid

import psycopg2.extras
import pytest

from _seed import connect


def _write(pg_db, action, n, *, username="aud_user", user_id=None, resource=None,
           detail=None, created_at=None):
    conn = connect(pg_db)
    ids = []
    with conn, conn.cursor() as cur:
        for i in range(n):
            cur.execute(
                "INSERT INTO audit_log (user_id, username, action, resource, detail, ip_address,"
                " created_at) VALUES (%s, %s, %s, %s, %s, '10.0.0.1', COALESCE(%s, NOW()))"
                " RETURNING id",
                (user_id, username, action,
                 resource(i) if callable(resource) else resource,
                 psycopg2.extras.Json(detail) if detail is not None else None,
                 created_at(i) if callable(created_at) else created_at))
            ids.append(cur.fetchone()[0])
    conn.close()
    return ids


def _action():
    return f"test.audit.{uuid.uuid4().hex[:10]}"


# ---------------------------------------------------------------- service


def test_keyset_pages_are_complete_ordered_and_state_more(pg_db):
    from core.security import audit_query as aq

    action = _action()
    ids = _write(pg_db, action, 7)
    conn = connect(pg_db)
    seen, before, pages = [], None, 0
    while True:
        page = aq.list_entries(conn, aq.parse_filters(
            {"action": action, "limit": "3", **({"before_id": str(before)} if before else {})}))
        pages += 1
        seen += [e["id"] for e in page["items"]]
        assert page["total"] is None and "not counted" in page["total_reason"]
        if not page["has_more"]:
            assert page["next_before_id"] is None
            break
        assert page["next_before_id"] == page["items"][-1]["id"]
        before = page["next_before_id"]
    conn.close()
    assert pages == 3
    assert seen == sorted(ids, reverse=True)        # every row once, newest first


def test_exactly_limit_rows_is_not_reported_as_more(pg_db):
    from core.security import audit_query as aq

    action = _action()
    _write(pg_db, action, 3)
    conn = connect(pg_db)
    page = aq.list_entries(conn, aq.parse_filters({"action": action, "limit": "3"}))
    conn.close()
    assert len(page["items"]) == 3 and page["has_more"] is False


def test_filters_user_resource_prefix_literal_and_window(pg_db):
    from core.security import audit_query as aq

    action = _action()
    t0 = datetime.datetime(2030, 1, 1, tzinfo=datetime.timezone.utc)
    _write(pg_db, action, 4, username="aud_alice", resource=lambda i: f"report_run:{i}",
           created_at=lambda i: t0 + datetime.timedelta(hours=i))
    _write(pg_db, action, 1, username="aud_bob", resource="report%run:9")
    _write(pg_db, action, 1, username="aud_bob", resource="reportXrun:9")
    conn = connect(pg_db)

    def ids(**f):
        return [e["resource"] for e in aq.list_entries(
            conn, aq.parse_filters(dict(f, action=action)))["items"]]

    assert len(ids(username="aud_alice")) == 4
    assert ids(username="aud_bob") and all(r.startswith("report") for r in ids(username="aud_bob"))
    # '%' and '_' in the prefix are literal, not wildcards
    assert ids(resource="report%") == ["report%run:9"]
    assert ids(resource="report_run:") == ["report_run:3", "report_run:2", "report_run:1", "report_run:0"]
    # since inclusive, until exclusive
    assert ids(since="2030-01-01T01:00:00Z", until="2030-01-01T03:00:00Z") == ["report_run:2", "report_run:1"]
    # a naive timestamp is read as UTC
    assert ids(since="2030-01-01T03:00:00") == ["report_run:3"]
    conn.close()


@pytest.mark.parametrize("args,needle", [
    ({"limit": "0"}, "limit"), ({"limit": "201"}, "at most 200"), ({"limit": "x"}, "limit"),
    ({"before_id": "-1"}, "before_id"), ({"user_id": "1.5"}, "user_id"),
    ({"since": "yesterday"}, "since"), ({"order": "asc"}, "unknown filter"),
    ({"since": "2030-01-02", "until": "2030-01-01"}, "earlier"),
    ({"username": "u" * 65}, "username"),
])
def test_invalid_filters_are_refused(args, needle):
    from core.security import audit_query as aq

    with pytest.raises(aq.AuditQueryError) as exc:
        aq.parse_filters(args)
    assert exc.value.status == 400 and needle in exc.value.message


def test_timeout_is_reported_not_empty(pg_db, monkeypatch):
    from core.security import audit_query as aq

    monkeypatch.setattr(aq, "STATEMENT_TIMEOUT_MS", 20)
    conn = connect(pg_db)
    with pytest.raises(aq.AuditQueryError) as exc:
        aq._read(conn, "SELECT pg_sleep(1)", ())
    assert exc.value.code == "QUERY_TIMEOUT" and exc.value.status == 503
    # the connection is usable afterwards
    assert aq._read(conn, "SELECT 1 AS one", ()) == [{"one": 1}]
    conn.close()


def test_reads_are_read_only(pg_db):
    from core.security import audit_query as aq

    conn = connect(pg_db)
    with pytest.raises(psycopg2.errors.ReadOnlySqlTransaction):
        aq._read(conn, "DELETE FROM audit_log WHERE id < 0 RETURNING id", ())
    conn.close()


def test_actions_lists_distinct_names(pg_db):
    from core.security import audit_query as aq

    action = _action()
    _write(pg_db, action, 3)
    conn = connect(pg_db)
    body = aq.actions(conn)
    conn.close()
    assert body["items"].count(action) == 1 and body["capped"] is False
    assert body["items"] == sorted(body["items"])


# -------------------------------------------------------------------- API


def test_api_admin_reads_entries_and_detail(pg_db, admin_client):
    action = _action()
    [entry_id] = _write(pg_db, action, 1, resource="export:report_artifact:report_run:1",
                        detail={"format": "csv", "sha256": "ab" * 32, "row_count": 3})
    resp = admin_client.get(f"/api/audit?action={action}")
    assert resp.status_code == 200
    body = resp.get_json()
    assert [e["id"] for e in body["items"]] == [entry_id]
    assert body["items"][0]["detail"]["sha256"] == "ab" * 32
    one = admin_client.get(f"/api/audit/{entry_id}").get_json()["entry"]
    assert one["resource"] == "export:report_artifact:report_run:1"
    assert admin_client.get("/api/audit/999999999").status_code == 404
    assert action in admin_client.get("/api/audit/actions").get_json()["items"]
    bad = admin_client.get("/api/audit?limit=500")
    assert bad.status_code == 400 and bad.get_json()["error"]["code"] == "VALIDATION_FAILED"


def test_api_sees_real_audited_actions(pg_db, admin_client, admin_credentials):
    """The viewer reads what the application writes: the admin's own sign-in."""
    username = admin_credentials[0]
    body = admin_client.get(f"/api/audit?action=login.success&username={username}").get_json()
    assert body["items"], "the sign-in of admin_client is in the log"
    newest = body["items"][0]
    assert newest["action"] == "login.success" and newest["username"] == username
    assert newest["user_id"] is not None and newest["created_at"]


@pytest.mark.parametrize("role", ["analyst", "viewer"])
def test_api_and_page_refuse_non_admins(client_factory, role):
    c = client_factory(role)
    for url in ("/api/audit", "/api/audit/actions", "/api/audit/1"):
        assert c.get(url).status_code == 403, url
    assert c.get("/admin/audit").status_code in (302, 403)


def test_api_refuses_anonymous(app):
    c = app.test_client()
    assert c.get("/api/audit").status_code in (401, 302)


def test_page_renders_for_admin(admin_client):
    resp = admin_client.get("/admin/audit")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'id="auditPage"' in html and "js/pages/audit-page.js" in html
    assert 'id="audit-page-labels"' in html


def test_actions_loose_scan_equals_distinct(pg_db):
    """The menu's recursive index walk returns exactly SELECT DISTINCT's list."""
    from core.security import audit_query as aq

    _write(pg_db, _action(), 2)
    conn = connect(pg_db)
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT action FROM audit_log ORDER BY action")
        expected = [r[0] for r in cur.fetchall()]
    conn.rollback()
    assert aq.actions(conn)["items"] == expected
    conn.close()


def test_actions_cap_is_stated(pg_db, monkeypatch):
    from core.security import audit_query as aq

    _write(pg_db, _action(), 1)
    _write(pg_db, _action(), 1)
    monkeypatch.setattr(aq, "MAX_ACTIONS", 1)
    conn = connect(pg_db)
    body = aq.actions(conn)
    conn.close()
    assert body == {"items": body["items"], "capped": True, "cap": 1} and len(body["items"]) == 1
