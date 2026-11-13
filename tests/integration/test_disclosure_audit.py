"""DATA_EXPORTED: every file that leaves the system is recorded (directive step 5).

The record must name the actor, the time, the criteria fingerprint, the
format, the scope, the row count and the SHA-256 of the bytes actually sent;
an export whose record cannot be written must be refused, not sent.
"""

import hashlib
import uuid

import pytest

from _seed import connect, document, side, source

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def token(pg_db, app):
    tag = f"zqexport{uuid.uuid4().hex[:8]}"
    conn = connect(pg_db)
    with conn, conn.cursor() as cur:
        s, d = source(cur), side(cur)
        document(cur, source_id=s, side_id=d, text=f"exportable {tag} one", file_type="txt")
        document(cur, source_id=s, side_id=d, text=f"exportable {tag} two", file_type="pdf")
    conn.close()
    return tag


def _csrf(client):
    with client.session_transaction() as sess:
        return sess.get("csrf_token") or sess.get("_csrf_token") or ""


def _audit_rows(pg_db, action="DATA_EXPORTED", since_id=0):
    conn = connect(pg_db)
    with conn.cursor() as cur:
        cur.execute("SELECT id, username, resource, detail, created_at FROM audit_log"
                    " WHERE action = %s AND id > %s ORDER BY id", (action, since_id))
        rows = cur.fetchall()
    conn.close()
    return rows


def _max_audit_id(pg_db):
    conn = connect(pg_db)
    with conn.cursor() as cur:
        cur.execute("SELECT COALESCE(max(id), 0) FROM audit_log")
        value = cur.fetchone()[0]
    conn.close()
    return value


def _export(client, payload):
    return client.post("/api/search/export", json=payload,
                       headers={"X-CSRFToken": _csrf(client)})


def test_search_export_writes_a_complete_disclosure_record(pg_db, admin_client, token):
    before = _max_audit_id(pg_db)
    payload = {"query": token, "export_scope": "filtered", "analyst_scope": "all",
               "format": "csv", "use_fuzzy": False, "use_expansion": False}
    resp = _export(admin_client, payload)
    assert resp.status_code == 200, resp.get_data(as_text=True)[:400]
    body = resp.get_data()
    assert "attachment" in resp.headers["Content-Disposition"]

    rows = _audit_rows(pg_db, since_id=before)
    assert len(rows) == 1, "exactly one disclosure record per export"
    audit_id, username, resource, detail, created_at = rows[0]
    assert str(audit_id) == resp.headers["X-Disclosure-Audit-Id"]
    assert username == "testadmin" and created_at is not None
    assert resource == "export:search_export:filtered"
    assert detail["kind"] == "search_export"
    assert detail["format"] == "csv" and detail["scope"] == "filtered"
    assert detail["row_count"] == 2 and detail["total"] == 2 and detail["truncated"] is False
    assert detail["actor_role"] == "admin"
    assert detail["artifact"]["sha256"] == hashlib.sha256(body).hexdigest()
    assert detail["artifact"]["bytes"] == len(body)
    assert len(detail["criteria_fingerprint"]) == 64
    assert len(detail["query_fingerprint"]) == 64
    assert detail["filename"] and detail["filename"].endswith(".csv")


def test_export_and_saved_search_share_one_fingerprint(pg_db, admin_client, token):
    """The register can answer "which exports disclosed what this search watches"."""
    before = _max_audit_id(pg_db)
    common = {"use_fuzzy": False, "use_expansion": False, "use_bm25": True}
    assert _export(admin_client, {"query": token, "export_scope": "filtered",
                                  "analyst_scope": "all", "format": "json",
                                  **common}).status_code == 200
    exported_fp = _audit_rows(pg_db, since_id=before)[0][3]["criteria_fingerprint"]
    saved = admin_client.post(
        "/api/search/saved", headers={"X-CSRFToken": _csrf(admin_client)},
        json={"name": "fp check", "query": token,
              "filters": {"scope": "all", "sort_by": "relevance", "sort_order": "desc",
                          "options": common}})
    assert saved.status_code == 201
    assert saved.get_json()["criteria_fingerprint"] == exported_fp


def test_export_is_refused_when_the_disclosure_cannot_be_recorded(
        pg_db, admin_client, token, monkeypatch):
    from core.security.service import AuthService

    def broken(self, *args, **kwargs):
        raise RuntimeError("audit store unavailable")

    monkeypatch.setattr(AuthService, "audit_strict", broken)
    before = _max_audit_id(pg_db)
    resp = _export(admin_client, {"query": token, "export_scope": "filtered",
                                  "analyst_scope": "all", "format": "csv"})
    assert resp.status_code == 503
    assert resp.get_json()["code"] == "disclosure_audit_failed"
    assert "Content-Disposition" not in resp.headers
    assert token not in resp.get_data(as_text=True)
    assert _audit_rows(pg_db, since_id=before) == []


def test_routes_without_enrichment_are_still_recorded(pg_db, admin_client, monkeypatch):
    """Coverage is central: an export route that never calls note_disclosure
    (simulated by disabling it for the analyst-categorisation CSV) is
    recorded all the same, with an explicit null count and a reason."""
    import core.security.disclosure as disclosure

    monkeypatch.setattr(disclosure, "note_disclosure", lambda **_: None)
    before = _max_audit_id(pg_db)
    resp = admin_client.get("/api/analyst/export")
    assert resp.status_code == 200, resp.get_data(as_text=True)[:300]
    assert "attachment" in resp.headers.get("Content-Disposition", "")
    rows = _audit_rows(pg_db, since_id=before)
    assert len(rows) == 1
    detail = rows[0][3]
    assert detail["endpoint"] and detail["kind"] == detail["endpoint"]
    assert detail["format"] == "csv" and detail["scope"] is None
    assert detail["row_count"] is None and detail["row_count_reason"]
    assert detail["artifact"]["sha256"] == hashlib.sha256(resp.get_data()).hexdigest()


def test_ordinary_responses_are_not_disclosures(pg_db, admin_client):
    before = _max_audit_id(pg_db)
    assert admin_client.get("/api/search/saved").status_code == 200
    assert admin_client.get("/api/search/export/columns").status_code == 200
    assert _audit_rows(pg_db, since_id=before) == []


def test_refused_exports_are_not_recorded_as_disclosures(pg_db, admin_client, token):
    before = _max_audit_id(pg_db)
    resp = _export(admin_client, {"query": token, "export_scope": "bogus"})
    assert resp.status_code == 400
    assert _audit_rows(pg_db, since_id=before) == []


# --------------------------------------------------------------------------
# Every export route: one record, with scope, format and count (step 5)
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def export_data(pg_db, app):
    from core.serialization import pack_int_list

    tag = uuid.uuid4().hex[:8]
    conn = connect(pg_db)
    with conn, conn.cursor() as cur:
        s, d = source(cur), side(cur)
        path_id, hash_id, _ = document(cur, source_id=s, side_id=d,
                                       text=f"routes {tag} write alice@example.org here",
                                       file_type="txt")
        cur.execute("INSERT INTO words (word) VALUES (%s), (%s) RETURNING id",
                    (f"kwcat{tag}", f"kw{tag}"))
        cat_word, kw_word = [r[0] for r in cur.fetchall()]
        cur.execute("INSERT INTO categorys (word_id) VALUES (%s) RETURNING id", (cat_word,))
        category_id = cur.fetchone()[0]
        cur.execute("INSERT INTO keywords (keyword, category_id) VALUES (%s, %s) RETURNING id",
                    (pack_int_list([kw_word]), category_id))
        keyword_id = cur.fetchone()[0]
    conn.close()
    return {"source_id": s, "path_id": path_id, "keyword_id": keyword_id}


def _exports(data):
    pid, sid = data["path_id"], data["source_id"]
    return [
        # (method, url, json, expected kind, expected scope, expected row_count)
        ("get", "/api/analyst/export", None, "analyst_categorizations", "all", None),
        ("get", f"/api/sources/{sid}/export", None, "source_record", f"source:{sid}", 1),
        ("get", f"/api/keywords/export?ids={data['keyword_id']}", None,
         "keywords_export", "selection", 1),
        ("post", "/files/export", {"file_ids": [pid]}, "bulk_text_export", "selection", 1),
        ("post", "/api/files/names/export", {"scope": "selected", "file_ids": [pid]},
         "file_names_export", "selected", 1),
        ("post", "/api/files/excerpt/export", {"file_id": pid, "text": "routes", "format": "txt"},
         "excerpt_selection", f"file:{pid}", 1),
        ("post", "/api/files/extract-contacts/export", {"file_ids": [pid]},
         "contacts_export", "selection", None),
        ("post", "/api/files/first-pages/export", {"file_ids": [pid]},
         "first_pages_export", "selection", 1),
        ("get", f"/api/files/{pid}/export", None, "file_text_export", f"file:{pid}", 1),
        ("get", "/api/export/words?format=csv&columns=word&filename=named-table.csv", None,
         "list_export", "interface:words", None),
        ("get", "/api/import-export/backup/export", None, "database_backup",
         "tables:evidence", "unknown"),
        ("get", "/api/import-export/settings/export", None, "settings_export",
         "settings:search,display,system", 1),
        ("get", "/api/settings/export", None, "settings_export", "settings:all", 1),
    ]


def test_every_export_route_writes_a_complete_record(pg_db, admin_client, export_data):
    problems = []
    for method, url, body, kind, scope, count in _exports(export_data):
        before = _max_audit_id(pg_db)
        kwargs = {"headers": {"X-CSRFToken": _csrf(admin_client)}}
        if body is not None:
            kwargs["json"] = body
        resp = getattr(admin_client, method)(url, **kwargs)
        if resp.status_code != 200:
            problems.append((url, "status", resp.status_code, resp.get_data(as_text=True)[:200]))
            continue
        rows = _audit_rows(pg_db, since_id=before)
        if len(rows) != 1:
            problems.append((url, "records", len(rows)))
            continue
        detail = rows[0][3]
        if detail["kind"] != kind and not detail["kind"].startswith(kind.split("_")[0] + "_"):
            problems.append((url, "kind", detail["kind"]))
        if detail.get("scope") != scope:
            problems.append((url, "scope", detail.get("scope")))
        if kind == "list_export":
            if detail.get("filename") != "named-table.csv":
                problems.append((url, "filename", detail.get("filename")))
            if detail.get("columns") != ["word"]:
                problems.append((url, "columns", detail.get("columns")))
        if not detail.get("format") or detail["format"] == "unknown":
            problems.append((url, "format", detail.get("format")))
        if count == "unknown":
            if detail.get("row_count") is not None or not detail.get("row_count_reason"):
                problems.append((url, "unknown count must be null with a reason", detail))
        elif count is None:
            if not isinstance(detail.get("row_count"), int):
                problems.append((url, "row_count", detail.get("row_count")))
        elif detail.get("row_count") != count:
            problems.append((url, "row_count", detail.get("row_count")))
        if detail["artifact"].get("sha256") != hashlib.sha256(resp.get_data()).hexdigest():
            problems.append((url, "sha256"))
        if rows[0][1] != "testadmin":
            problems.append((url, "actor", rows[0][1]))
    assert not problems, problems
