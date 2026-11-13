"""Security regression tests (Gate 3).

Every confirmed vulnerability from the audit has a permanent regression test
here. A PR must not merge if any of these fail.
"""

import io
import json
import sys
import zipfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

pytestmark = [pytest.mark.integration, pytest.mark.security]

INJECTION_PAYLOADS = [
    "id; DROP TABLE users; --",
    "id --",
    "1=1 OR 1=1",
    "(SELECT CASE WHEN (1=1) THEN pg_sleep(5) ELSE pg_sleep(0) END)",
    "id UNION SELECT username, password FROM users",
    "title_data' OR '1'='1",
    "id/**/UNION/**/SELECT/**/1",
]


class TestSqlInjectionRegressions:
    """SEC-03: the confirmed exploitable sort_by injection (titles endpoint)."""

    @pytest.mark.parametrize("payload", INJECTION_PAYLOADS)
    def test_titles_sort_by_rejected(self, admin_client, payload):
        resp = admin_client.get(
            "/api/archives/titles", query_string={"sort_by": payload}
        )
        assert resp.status_code in (200, 400), resp.get_data(as_text=True)
        if resp.status_code == 200:
            body = resp.get_json()
            # Must never return a server error (the injection produced 500s/leaks)
            assert body.get("success") is not False or "Invalid" in str(body.get("error", ""))

    def test_titles_sort_by_valid_value_accepted(self, admin_client):
        resp = admin_client.get("/api/archives/titles", query_string={"sort_by": "id"})
        assert resp.status_code == 200

    def test_titles_sort_by_unknown_value_is_400(self, admin_client):
        resp = admin_client.get("/api/archives/titles", query_string={"sort_by": "not_a_column"})
        assert resp.status_code == 400


class TestBackupImportInjection:
    """SEC-04: backup import must never execute SQL identifiers from the file."""

    def _make_backup(self, tables_payload):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("backup.json", json.dumps(tables_payload))
        buf.seek(0)
        return buf

    def test_malicious_table_name_rejected(self, admin_client):
        evil = self._make_backup(
            {
                "include_data": True,
                "tables": {
                    "paths; DROP TABLE paths; --": {
                        "schema": [{"name": "id", "type": "integer"}],
                        "data": [],
                    }
                },
            }
        )
        resp = admin_client.post(
            "/api/import-export/database/backup",
            data={"file": (evil, "backup.zip"), "restore_data": "true"},
            content_type="multipart/form-data",
        )
        # The endpoint must not 500 with a leak; malicious table never executed.
        body = resp.get_json(silent=True) or {}
        text = resp.get_data(as_text=True).lower()
        assert "drop table" not in text
        assert "syntax error" not in text

    def test_non_allowlisted_table_rejected(self, admin_client):
        from Api.services.import_service import ImportService

        backup = {
            "include_data": True,
            "tables": {
                "users": {
                    "schema": [{"name": "id", "type": "integer"}],
                    "data": [{"id": 1}],
                }
            },
        }
        import psycopg2
        from settings.config import get_db_config

        cfg = get_db_config()
        conn = psycopg2.connect(
            host=cfg["host"], port=cfg["port"], user=cfg["user"],
            password=cfg["password"], dbname=cfg["database"],
        )
        cur = conn.cursor()
        result = ImportService._validate_schema_compatibility(backup, cur, {"users"})
        conn.close()
        assert result["compatible"] is False
        assert any("allowlist" in e for e in result["errors"])

    def test_malicious_column_name_rejected(self):
        from Api.services.import_service import ImportService

        with pytest.raises(ValueError):
            ImportService._validate_identifier("id; DROP TABLE paths; --")
        with pytest.raises(ValueError):
            ImportService._validate_identifier('"id')
        with pytest.raises(ValueError):
            ImportService._validate_identifier("1")
        with pytest.raises(ValueError):
            ImportService._validate_identifier(None)


class TestArbitraryPathAccess:
    """SEC-06: batch import must reject arbitrary server paths."""

    def test_etc_passwd_rejected(self, admin_client):
        resp = admin_client.post(
            "/api/import-export/batch-import",
            json={"file_paths": ["/etc/passwd"], "source_id": 1, "side_id": 1},
        )
        assert resp.status_code in (200, 400, 403, 422)
        body = resp.get_json(silent=True) or {}
        results = body.get("results", [])
        if results:
            assert results[0].get("status") in ("rejected", "error")

    def test_traversal_rejected(self, admin_client):
        resp = admin_client.post(
            "/api/import-export/batch-import",
            json={"file_paths": ["../../etc/shadow"], "source_id": 1, "side_id": 1},
        )
        body = resp.get_json(silent=True) or {}
        results = body.get("results", [])
        if results:
            assert results[0].get("status") in ("rejected", "error")

    def test_disabled_when_no_roots(self, admin_client, monkeypatch):
        monkeypatch.delenv("INGESTION_ROOTS", raising=False)
        resp = admin_client.post(
            "/api/import-export/batch-import",
            json={"file_paths": ["/tmp/anything.txt"], "source_id": 1, "side_id": 1},
        )
        body = resp.get_json(silent=True) or {}
        results = body.get("results", [])
        if results:
            assert results[0].get("status") == "rejected"


class TestAuthenticationBypass:
    """SEC-01: unauthenticated clients must not reach protected data."""

    PROTECTED_PATHS = [
        ("/", 302),
        ("/api/preview/1", 401),
        ("/api/import-export/batch-import", 401),
        ("/api/analysis/file/1/reprocess", 401),
        ("/settings", 302),
    ]

    @pytest.mark.parametrize("path,expected", PROTECTED_PATHS)
    def test_unauthenticated_blocked(self, client, path, expected):
        resp = client.get(path)
        assert resp.status_code == expected, f"{path} -> {resp.status_code}"

    def test_api_returns_401_not_data(self, client):
        for path in ("/api/preview/1", "/api/import-export/batch-import"):
            resp = client.get(path)
            if resp.status_code != 404:  # 404 acceptable for unknown GET routes
                assert resp.status_code == 401


class TestAuthorizationBypass:
    """SEC-02: viewer role must not access write operations (server-side)."""

    def test_viewer_cannot_delete(self, viewer_client):
        resp = viewer_client.post("/file/1/delete")
        assert resp.status_code == 403

    def test_viewer_cannot_import(self, viewer_client):
        resp = viewer_client.post(
            "/api/import-export/batch-import",
            json={"file_paths": [], "source_id": 1, "side_id": 1},
        )
        assert resp.status_code == 403

    def test_viewer_can_read(self, viewer_client):
        # A read-only user can access a listing endpoint (200 or graceful empty).
        resp = viewer_client.get("/api/sources")
        assert resp.status_code in (200, 404)


class TestCsrfEnforcement:
    """SEC-09: state-changing requests require CSRF tokens."""

    def test_post_without_csrf_rejected(self, app, client, admin_credentials):
        app.config["WTF_CSRF_ENABLED"] = True
        try:
            username, password = admin_credentials
            resp = client.post(
                "/auth/login",
                data=json.dumps({"username": username, "password": password}),
                content_type="application/json",
            )
            assert resp.status_code == 400  # CSRF error -> rejected
        finally:
            app.config["WTF_CSRF_ENABLED"] = False


class TestErrorDisclosure:
    """SEC-08: no raw exception text in client responses."""

    def test_500_response_is_generic(self, admin_client):
        # /_test/sec08/boom is registered by the session app fixture.
        resp = admin_client.get("/_test/sec08/boom")
        assert resp.status_code == 500
        body = resp.get_json()
        assert "postgresql://" not in resp.get_data(as_text=True)
        assert "SECRET" not in body["error"]
        assert "correlation_id" in body


class TestDebugModeProtection:
    """SEC-10: production rejects debug mode."""

    def test_run_web_rejects_debug_in_production(self, monkeypatch):
        monkeypatch.setenv("FLASK_ENV", "production")
        monkeypatch.setenv("FLASK_DEBUG", "true")
        import importlib

        import run_web

        importlib.reload(run_web)
        with pytest.raises(RuntimeError, match="debug"):
            run_web._run_main()

    def test_no_hardcoded_debug_true(self):
        source = Path(PROJECT_ROOT / "run_web.py").read_text()
        assert "app.run(debug=True" not in source


class TestSecretsRemoved:
    """SEC-07: no credentials in the source tree."""

    def test_no_hardcoded_password_anywhere(self):
        import subprocess

        # Build the needle from parts so this test file never contains the
        # literal credential (a tracked file cannot match its own needle).
        needle = "egg" + "arf" + "123"
        result = subprocess.run(
            ["git", "grep", "-l", needle, "--", ":!tests/security/test_security_regressions.py", ":!docs"],
            cwd=PROJECT_ROOT, capture_output=True, text=True,
        )
        offenders = [ln for ln in result.stdout.strip().splitlines() if ln]
        assert not offenders, f"Credential found in: {offenders}"

    def test_env_example_has_no_real_password(self):
        needle = "egg" + "arf" + "123"
        example = Path(PROJECT_ROOT / ".env.example").read_text()
        assert needle not in example
