"""Regression tests for the defects found by the v2.1.1 code audit.

Each test names the finding ID used in ``docs/AUDIT_REPORT.md``. They run
against the real application and the disposable PostgreSQL database.
"""

from __future__ import annotations

import uuid

import psycopg2
import pytest

pytestmark = pytest.mark.integration

_UNIQUE = uuid.uuid4().hex[:8]


def _connect(pg_db):
    return psycopg2.connect(host=pg_db["host"], port=pg_db["port"], user=pg_db["user"],
                            password=pg_db["password"], dbname=pg_db["database"])


@pytest.fixture(scope="module")
def geo_context(pg_db):
    """One (source, side, content) context with a geolocated path."""
    conn = _connect(pg_db)
    ids = {}
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO sides (name, importance, date_creation)"
                        " VALUES (%s, 1.0, CURRENT_DATE) RETURNING id", (f"audit-side-{_UNIQUE}",))
            ids["side_id"] = cur.fetchone()[0]
            cur.execute("INSERT INTO sources (name, job, importance, country, date_creation)"
                        " VALUES (%s, 'test', 1.0, 'NL', CURRENT_DATE) RETURNING id",
                        (f"audit-source-{_UNIQUE}",))
            ids["source_id"] = cur.fetchone()[0]
            ids["hash"] = f"audit-hash-{_UNIQUE}"
            cur.execute("INSERT INTO hashs (hash) VALUES (%s) RETURNING id", (ids["hash"],))
            hash_id = cur.fetchone()[0]
            cur.execute("INSERT INTO hash_contexts (hash_id, source_id, side_id)"
                        " VALUES (%s, %s, %s) RETURNING id", (hash_id, ids["source_id"], ids["side_id"]))
            context_id = cur.fetchone()[0]
            cur.execute("""INSERT INTO paths (file_name, file_path, file_size, file_type, file_status,
                           file_date, date_creation, coordinates, context_id)
                           VALUES (%s, %s, 1, 'jpg', 'Read', CURRENT_DATE, CURRENT_DATE,
                                   '52.37,4.89', %s) RETURNING id""",
                        (f"geo-{_UNIQUE}.jpg", f"/audit/geo-{_UNIQUE}.jpg", context_id))
            ids["path_id"] = cur.fetchone()[0]
        conn.commit()
        yield ids
    finally:
        with conn.cursor() as cur:
            if "path_id" in ids:
                cur.execute("DELETE FROM paths WHERE id = %s", (ids["path_id"],))
        conn.commit()
        conn.close()


class TestGeolocationSortInjection:
    """AUDIT-SQLI-01: ``sort_dir`` reached ORDER BY verbatim."""

    URL = "/api/archives/geolocation"
    # Raises division-by-zero only when injected SQL is executed and the
    # condition is false; with the fix the value is ignored entirely.
    FALSE_ORACLE = "asc, (CASE WHEN UPPER(CURRENT_USER)='NOBODY' THEN 1 ELSE 1/(SELECT 0) END)"

    def test_injected_sql_is_not_executed(self, viewer_client, geo_context):
        resp = viewer_client.get(self.URL, query_string={"sort_dir": self.FALSE_ORACLE})
        assert resp.status_code == 200, resp.get_data(as_text=True)
        names = [row["file_name"] for row in resp.get_json()["data"]]
        assert f"geo-{_UNIQUE}.jpg" in names

    @pytest.mark.parametrize("value", ["asc", "ASC", "desc", "", "sideways"])
    def test_legitimate_and_unknown_directions_still_work(self, viewer_client, geo_context, value):
        resp = viewer_client.get(self.URL, query_string={"sort_dir": value})
        assert resp.status_code == 200
        assert resp.get_json()["success"] is True


class TestSetupDiagnosticsAfterInstall:
    """AUDIT-SETUP-01: the wizard's probes stayed public after installation."""

    BODY = {"host": "127.0.0.1", "port": 1, "user": "x", "database": "x", "password": "x"}

    def test_anonymous_is_refused(self, app):
        anon = app.test_client()
        assert anon.post("/api/setup/test-database", json=self.BODY).status_code == 401
        assert anon.get("/api/setup/system-check").status_code == 401

    def test_viewer_is_refused(self, viewer_client):
        assert viewer_client.post("/api/setup/test-database", json=self.BODY).status_code == 403
        assert viewer_client.get("/api/setup/system-check").status_code == 403

    def test_admin_keeps_the_diagnostics(self, admin_client):
        resp = admin_client.get("/api/setup/system-check")
        assert resp.status_code == 200
        assert "checks" in resp.get_json()
        resp = admin_client.post("/api/setup/test-database", json=self.BODY)
        assert resp.status_code == 400  # port 1 refuses: validated, attempted, reported
        assert resp.get_json()["ok"] is False

    def test_installation_state_probe_stays_public(self, app):
        resp = app.test_client().get("/api/setup/check")
        assert resp.status_code == 200
        assert resp.get_json()["initialized"] is True


class TestPathsBlueprintErrors:
    """AUDIT-PY-01: the error branch referenced an undefined ``logger``."""

    def test_invalid_body_is_a_400_not_a_500(self, admin_client):
        resp = admin_client.post("/api/paths", data="not json", content_type="application/json")
        assert resp.status_code == 400, resp.get_data(as_text=True)


class TestHstsHeader:
    """AUDIT-HSTS-01: HSTS was keyed on a config value that is never set."""

    def test_sent_on_tls_requests_in_production(self, admin_client, monkeypatch):
        monkeypatch.setenv("FLASK_ENV", "production")
        resp = admin_client.get("/health", base_url="https://localhost")
        assert "max-age=" in resp.headers.get("Strict-Transport-Security", "")

    def test_not_sent_over_plain_http(self, admin_client, monkeypatch):
        monkeypatch.setenv("FLASK_ENV", "production")
        resp = admin_client.get("/health")
        assert "Strict-Transport-Security" not in resp.headers

    def test_not_sent_in_development(self, admin_client, monkeypatch):
        monkeypatch.setenv("FLASK_ENV", "development")
        resp = admin_client.get("/health", base_url="https://localhost")
        assert "Strict-Transport-Security" not in resp.headers


class TestSideScopedHashExists:
    """AUDIT-DB-01: ``hash_exists(..., side_id)`` raised AttributeError."""

    def test_side_scoped_lookup(self, pg_db, geo_context):
        from database.services.contents_db_service import ContentDBService

        service = ContentDBService()
        h, src, side = geo_context["hash"], geo_context["source_id"], geo_context["side_id"]
        assert service.hash_exists(h, src, side) is True
        assert service.hash_exists(h, src, side + 100000) is False
        assert service.hash_exists(h, src) is True
        assert service.hash_exists(f"missing-{_UNIQUE}", src, side) is False


class TestTemporaryPasswordIsForcedToChange:
    """AUDIT-AUTH-01: ``must_change_password`` was a banner, not a gate."""

    def _client(self, app, suffix):
        from core.security.service import get_auth_service

        auth = get_auth_service()
        username, password = f"temp_{suffix}_{_UNIQUE}", "temporary-password-123"
        if auth.get_user_by_username(username) is None:
            auth.create_user(username, password, role="analyst", must_change_password=True)
        client = app.test_client()
        assert client.post("/auth/login", json={"username": username,
                                                "password": password}).status_code == 200
        return client

    def test_apis_and_pages_are_closed_until_the_password_changes(self, app):
        client = self._client(app, "closed")
        resp = client.get("/api/dashboard/stats")
        assert resp.status_code == 403
        assert resp.get_json()["code"] == "password_change_required"
        assert client.post("/api/paths", json={}).status_code == 403
        page = client.get("/search")
        assert page.status_code == 302 and page.headers["Location"].endswith("/")
        assert client.get("/auth/me").status_code == 200
        assert client.get("/").status_code == 200  # the shell with the banner

    def test_changing_the_password_opens_the_account(self, app):
        client = self._client(app, "opened")
        resp = client.post("/auth/change-password", json={
            "current_password": "temporary-password-123",
            "new_password": "a-brand-new-password-456"})
        assert resp.status_code == 200, resp.get_data(as_text=True)
        assert client.get("/api/dashboard/stats").status_code != 403
        assert client.get("/search").status_code == 200

    def test_accounts_without_the_flag_are_unaffected(self, viewer_client):
        assert viewer_client.get("/search").status_code == 200
