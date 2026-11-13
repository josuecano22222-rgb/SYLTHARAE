"""Saved searches in PostgreSQL (Phase 0 / directive step 4).

Covers the migration from ``data/saved_searches.json``, ownership isolation
(the former by-id routes let any authenticated user read, rename or delete
anyone's search), the canonical-criteria fingerprint, and backward
compatibility of the API shapes the pages use.
"""

import json
import uuid

import psycopg2
import pytest

from _seed import connect

pytestmark = pytest.mark.integration


@pytest.fixture()
def analysts(app, admin_credentials):
    """Two independent analyst clients (own cookie jars) plus their user ids."""
    from core.security.service import get_auth_service

    auth = get_auth_service()
    out = []
    for label in ("a", "b"):
        username = f"ss_analyst_{label}_{uuid.uuid4().hex[:6]}"
        password = "analyst-password-123"
        user = auth.create_user(username, password, role="analyst")
        c = app.test_client()
        assert c.post("/auth/login", json={"username": username,
                                           "password": password}).status_code == 200
        out.append((c, user.id if hasattr(user, "id") else auth.get_user_by_username(username).id))
    return out


def _csrf(client):
    with client.session_transaction() as sess:
        return sess.get("csrf_token") or sess.get("_csrf_token") or ""


def _post(client, url, payload, method="post"):
    return getattr(client, method)(url, json=payload, headers={"X-CSRFToken": _csrf(client)})


# --------------------------------------------------------------------------
# API: ownership and back-compat
# --------------------------------------------------------------------------


def test_save_list_get_update_delete_round_trip(analysts):
    (a, _), _ = analysts
    filters = {"scope": "all", "source_id": [1], "sort_by": "date", "sort_order": "asc",
               "options": {"case_sensitive": True}}
    resp = _post(a, "/api/search/saved", {"name": "Round trip", "query": "alpha",
                                          "filters": filters})
    assert resp.status_code == 201, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["success"] is True and len(body["criteria_fingerprint"]) == 64
    sid = body["search_id"]

    listed = a.get("/api/search/saved").get_json()
    mine = [s for s in listed["searches"] if s["id"] == sid]
    assert mine and mine[0]["name"] == "Round trip" and mine[0]["query"] == "alpha"
    assert mine[0]["filters"] == filters  # what the page replays, verbatim

    got = a.get(f"/api/search/saved/{sid}").get_json()["search"]
    assert got["criteria"]["sources"] == [1]
    assert got["criteria"]["analyst_scope"] == "all"
    assert got["last_used"] is not None

    upd = _post(a, f"/api/search/saved/{sid}", {"name": "Renamed"}, method="put")
    assert upd.status_code == 200
    assert upd.get_json()["criteria_fingerprint"] == body["criteria_fingerprint"]
    upd2 = _post(a, f"/api/search/saved/{sid}", {"query": "beta"}, method="put")
    assert upd2.get_json()["criteria_fingerprint"] != body["criteria_fingerprint"]

    assert _post(a, f"/api/search/saved/{sid}", {}, method="delete").status_code == 200
    assert a.get(f"/api/search/saved/{sid}").status_code == 404


def test_one_analyst_cannot_touch_anothers_search(analysts):
    (a, _), (b, _) = analysts
    sid = _post(a, "/api/search/saved", {"name": "Private", "query": "secret"}).get_json()["search_id"]

    assert b.get(f"/api/search/saved/{sid}").status_code == 404
    assert _post(b, f"/api/search/saved/{sid}", {"name": "pwned"}, method="put").status_code == 404
    assert _post(b, f"/api/search/saved/{sid}", {}, method="delete").status_code == 404
    assert all(s["id"] != sid for s in b.get("/api/search/saved").get_json()["searches"])
    # Untouched for the owner.
    assert a.get(f"/api/search/saved/{sid}").get_json()["search"]["name"] == "Private"


def test_unrepresentable_definition_is_refused(analysts):
    (a, _), _ = analysts
    resp = _post(a, "/api/search/saved", {"name": "Bad", "query": "x",
                                          "filters": {"date_from": "not-a-date"}})
    assert resp.status_code == 400
    assert resp.get_json()["code"] == "invalid_saved_search"
    assert _post(a, "/api/search/saved", {"name": "  ", "query": "x"}).status_code == 400


def test_viewer_cannot_save(client_factory):
    viewer = client_factory("viewer")
    assert _post(viewer, "/api/search/saved", {"name": "v", "query": "x"}).status_code == 403


# --------------------------------------------------------------------------
# Legacy JSON import
# --------------------------------------------------------------------------


@pytest.fixture()
def isolated_import(tmp_path, monkeypatch, app):
    from Api.services import saved_searches_repository as repo_mod
    from Api.services import search_history

    path = tmp_path / "saved_searches.json"
    monkeypatch.setattr(search_history, "SAVED_SEARCHES_FILE", path)
    repo_mod._reset_import_guard_for_tests()
    yield path, repo_mod
    repo_mod._reset_import_guard_for_tests()


def test_legacy_import_is_lossless_idempotent_and_owner_aware(pg_db, isolated_import, analysts):
    path, repo_mod = isolated_import
    (_, owner_id) = analysts[0]
    base = 900_000 + (uuid.uuid4().int % 90_000)
    entries = [
        {"id": base + 1, "name": "Owned", "query": "alpha", "user_id": owner_id,
         "filters": {"scope": "categorized", "source_id": ["2"]},
         "created_at": "2025-01-02T03:04:05", "last_used": None},
        {"id": base + 2, "name": "Pre-auth", "query": "beta", "user_id": None, "filters": {}},
        {"id": base + 3, "name": "Ghost owner", "query": "c", "user_id": 987654321,
         "filters": {"mystery_key": 1}},
        {"id": base + 4, "name": "", "query": "no name"},
        {"id": "x", "name": "Bad id"},
        {"id": base + 5, "name": "Bad date", "filters": {"date_from": "31/12/2025"}},
        "not an object",
    ]
    path.write_text(json.dumps(entries), encoding="utf-8")
    original = path.read_bytes()

    repo = repo_mod.SavedSearchRepository()
    summary = repo.import_legacy_json(path)
    assert summary["status"] == "imported"
    assert summary["entries_found"] == 7
    assert summary["entries_imported"] == 3
    reasons = sorted(f["reason"] for f in summary["failures"])
    assert len(reasons) == 4 and any("no name" in r for r in reasons)
    assert any("YYYY-MM-DD" in r for r in reasons)
    assert path.read_bytes() == original, "the legacy file is kept as a backup"

    conn = connect(pg_db)
    with conn.cursor() as cur:
        cur.execute("SELECT legacy_id, owner_user_id, criteria->>'analyst_scope', import_notes,"
                    " created_at::date::text FROM saved_searches WHERE legacy_id BETWEEN %s AND %s"
                    " ORDER BY legacy_id", (base, base + 10))
        rows = cur.fetchall()
        cur.execute("SELECT entries_found, entries_imported, jsonb_array_length(failures)"
                    " FROM saved_search_imports WHERE file_sha256 = %s", (summary["file_sha256"],))
        ledger = cur.fetchone()
    conn.close()
    assert [r[0] for r in rows] == [base + 1, base + 2, base + 3]
    assert rows[0][1] == owner_id and rows[0][2] == "categorized" and rows[0][4] == "2025-01-02"
    assert rows[1][1] is None and rows[1][2] == "uncategorized"   # unowned; default scope
    assert rows[2][1] is None
    assert rows[2][3]["legacy_owner_unresolved"] == 987654321
    assert rows[2][3]["unmapped_filter_keys"] == ["mystery_key"]
    assert ledger == (7, 3, 4)

    # Same file again: nothing re-imported (deleted searches stay deleted).
    again = repo.import_legacy_json(path)
    assert again["status"] == "already_imported"
    # A changed file cannot duplicate already-imported entries.
    path.write_text(json.dumps(entries[:3] + [{"id": base + 6, "name": "New", "query": "n"}]))
    changed = repo.import_legacy_json(path)
    assert changed["entries_imported"] == 1 and changed["entries_already_present"] == 3


def test_invalid_json_file_is_recorded_not_dropped(pg_db, isolated_import):
    path, repo_mod = isolated_import
    path.write_text("{ this is not json " + uuid.uuid4().hex, encoding="utf-8")
    summary = repo_mod.SavedSearchRepository().import_legacy_json(path)
    assert summary["status"] == "imported" and summary["entries_imported"] == 0
    assert "not valid JSON" in summary["failures"][0]["reason"]
    assert summary["failures"][0]["raw"].startswith("{ this is not json")


def test_unowned_legacy_search_is_visible_to_admin_only(pg_db, isolated_import, analysts,
                                                        admin_client):
    path, repo_mod = isolated_import
    legacy_id = 800_000 + (uuid.uuid4().int % 90_000)
    path.write_text(json.dumps([{"id": legacy_id, "name": "Orphan", "query": "o",
                                 "user_id": None, "filters": {}}]))
    (a, _), _ = analysts
    # First request through the API triggers the one-time import.
    admin_list = admin_client.get("/api/search/saved").get_json()["searches"]
    orphan = [s for s in admin_list if s.get("legacy_id") == legacy_id]
    assert orphan and orphan[0]["unowned"] is True
    sid = orphan[0]["id"]
    assert admin_client.get(f"/api/search/saved/{sid}").status_code == 200
    assert a.get(f"/api/search/saved/{sid}").status_code == 404
    assert all(s["id"] != sid for s in a.get("/api/search/saved").get_json()["searches"])


# --------------------------------------------------------------------------
# Database constraints (m0016)
# --------------------------------------------------------------------------

GOOD_FP = "a" * 64


def _insert(cur, **overrides):
    values = {"name": "n", "query": "", "filters": "{}", "criteria": "{}",
              "criteria_fingerprint": GOOD_FP, "legacy_source": None, "legacy_id": None,
              "owner_user_id": None}
    values.update(overrides)
    cur.execute(
        "INSERT INTO saved_searches (owner_user_id, name, query, filters, criteria,"
        " criteria_fingerprint, criteria_schema_version, legacy_source, legacy_id) VALUES"
        " (%(owner_user_id)s, %(name)s, %(query)s, %(filters)s, %(criteria)s,"
        " %(criteria_fingerprint)s, 1, %(legacy_source)s, %(legacy_id)s) RETURNING id", values)
    return cur.fetchone()[0]


def _constraint_definitions(conn, table):
    """Every CHECK/FOREIGN KEY on ``table``, name -> normalised definition.

    The frozen baseline (database/migrations/m0001_baseline.py) writes its own
    constraint names; nothing in the application reads them, so a test that
    pins a name pins a decision the schema never made. These tests pin the
    *rule* instead: the definition the constraint carries.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint"
            " WHERE conrelid = %s::regclass AND contype IN ('c', 'f')", (table,))
        return {name: " ".join(definition.split()) for name, definition in cur.fetchall()}


@pytest.mark.parametrize("overrides, rule", [
    ({"name": "   "}, "btrim"),
    ({"criteria_fingerprint": "Z" * 64}, "criteria_fingerprint ~"),
    ({"legacy_source": "x"}, "(legacy_source IS NULL) = (legacy_id IS NULL)"),
    ({"owner_user_id": 2_000_000_000}, "FOREIGN KEY (owner_user_id)"),
])
def test_constraints_reject_invalid_rows(pg_db, overrides, rule):
    conn = connect(pg_db)
    try:
        definitions = _constraint_definitions(conn, "saved_searches")
        with conn.cursor() as cur:
            with pytest.raises(psycopg2.Error) as info:
                _insert(cur, **overrides)
        violated = info.value.diag.constraint_name
        matching = {name for name, definition in definitions.items() if rule in definition}
        assert matching, f"no constraint on saved_searches enforces {rule!r}"
        assert violated in matching, (
            f"the row was rejected by {violated!r}, whose definition is"
            f" {definitions.get(violated)!r}; the rule asked for {rule!r}")
    finally:
        conn.rollback()
        conn.close()


def test_legacy_uniqueness_on_conflict_and_rollback(pg_db):
    legacy_id = 700_000 + (uuid.uuid4().int % 90_000)
    conn = connect(pg_db)
    try:
        with conn.cursor() as cur:
            _insert(cur, legacy_source="t", legacy_id=legacy_id)
            with pytest.raises(psycopg2.errors.UniqueViolation):
                _insert(cur, legacy_source="t", legacy_id=legacy_id)
        conn.rollback()
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM saved_searches WHERE legacy_id = %s", (legacy_id,))
            assert cur.fetchone()[0] == 0, "rollback left a row behind"
            _insert(cur, legacy_source="t", legacy_id=legacy_id)
            cur.execute(
                "INSERT INTO saved_searches (name, query, filters, criteria, criteria_fingerprint,"
                " criteria_schema_version, legacy_source, legacy_id)"
                " VALUES ('dup', '', '{}', '{}', %s, 1, 't', %s)"
                " ON CONFLICT (legacy_source, legacy_id) DO NOTHING", (GOOD_FP, legacy_id))
            assert cur.rowcount == 0
            # NULL legacy pairs never collide (many natively-created searches).
            _insert(cur)
            _insert(cur)
        conn.rollback()
    finally:
        conn.close()


def test_deleting_a_user_removes_their_saved_searches(pg_db):
    conn = connect(pg_db)
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO users (username, password_hash, role) VALUES (%s, 'x', 'analyst')"
                        " RETURNING id", (f"gone_{uuid.uuid4().hex[:8]}",))
            uid = cur.fetchone()[0]
            sid = _insert(cur, owner_user_id=uid)
            cur.execute("DELETE FROM users WHERE id = %s", (uid,))
            cur.execute("SELECT count(*) FROM saved_searches WHERE id = %s", (sid,))
            assert cur.fetchone()[0] == 0
    finally:
        conn.rollback()
        conn.close()


def test_saved_searches_page_runs_the_full_definition_and_only_shows_own(analysts):
    """The page's Run link restores query + filters (run_url), from PostgreSQL,
    and never lists another user's search."""
    import html as _html

    (a, _), (b, _) = analysts
    tag = uuid.uuid4().hex[:8]
    filters = {"scope": "all", "source_id": [7], "file_type": "pdf"}
    assert _post(a, "/api/search/saved", {"name": f"mine{tag}", "query": f"q{tag}",
                                          "filters": filters}).status_code == 201
    assert _post(b, "/api/search/saved", {"name": f"theirs{tag}", "query": "x"}).status_code == 201

    page = a.get("/search/saved")
    assert page.status_code == 200
    body = _html.unescape(page.get_data(as_text=True))
    assert f"mine{tag}" in body and f"theirs{tag}" not in body
    runs = [line for line in body.splitlines() if "/search/advanced?" in line and f"q{tag}" in line]
    assert runs, "Run link missing"
    # parameter names of the Advanced Search page (SEARCH_URL_PARAMS)
    assert "src=7" in runs[0] and "ft=pdf" in runs[0] and "scope=all" in runs[0], runs[0]
