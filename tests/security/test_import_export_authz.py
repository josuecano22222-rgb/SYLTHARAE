"""SEC-02 regression: the legacy /api/import-export surface is admin-gated.

Backup export contains the full evidence tables and backup restore replaces
their rows - both are administrator capabilities (docs/SECURITY.md). The
blueprint is also registered in AUTH_ADMIN_BLUEPRINTS, making every mutating
method on it admin-only.
"""
import io
import zipfile
import json

import pytest


class TestImportExportAuthz:
    def test_shared_table_export_requires_authentication(self, app, client):
        assert client.get("/api/export/words?format=csv").status_code == 401

    def test_viewer_can_export_the_same_safe_list_they_can_view(
            self, app, client_factory):
        response = client_factory("viewer").get("/api/export/words?format=csv")
        assert response.status_code == 200, response.get_data(as_text=True)
        assert "attachment" in response.headers.get("Content-Disposition", "")
        assert response.headers.get("X-Disclosure-Audit-Id")

    def test_anon_gets_401(self, app, client):
        assert client.get("/api/import-export/backup/export").status_code == 401
        assert client.post("/api/import-export/backup/import").status_code in (401, 403)

    def test_viewer_cannot_export_backup(self, app, client_factory):
        c = client_factory("viewer")
        assert c.get("/api/import-export/backup/export").status_code == 403

    def test_viewer_cannot_restore_backup(self, app, client_factory):
        c = client_factory("viewer")
        resp = c.post("/api/import-export/backup/import", data={
            "file": (io.BytesIO(b"not-a-zip"), "backup.zip"),
        }, content_type="multipart/form-data")
        assert resp.status_code == 403, resp.get_data(as_text=True)

    def test_analyst_cannot_restore_backup(self, app, client_factory):
        c = client_factory("analyst")
        resp = c.post("/api/import-export/backup/import", data={
            "file": (io.BytesIO(b"not-a-zip"), "backup.zip"),
        }, content_type="multipart/form-data")
        assert resp.status_code == 403, resp.get_data(as_text=True)

    def test_admin_can_export_and_validate_backup(self, app, client_factory):
        c = client_factory("admin")
        resp = c.get("/api/import-export/backup/export?include_data=true")
        assert resp.status_code == 200, resp.get_data(as_text=True)
        payload = resp.data
        z = zipfile.ZipFile(io.BytesIO(payload))
        assert "backup.json" in z.namelist()
        backup = json.loads(z.read("backup.json"))
        # System tables must never appear in backups (SEC-07: no credential
        # or session material; restore must not clobber system state).
        for banned in ("users", "sessions", "audit_log", "schema_migrations",
                       "jobs", "job_events"):
            assert banned not in backup["tables"], banned
        assert "words" in backup["tables"]

        # Validate-only restore of the exported backup succeeds for admin.
        resp = c.post("/api/import-export/backup/import", data={
            "file": (io.BytesIO(payload), "backup.zip"),
        }, content_type="multipart/form-data")
        assert resp.status_code == 200, resp.get_data(as_text=True)
        assert resp.get_json()["success"] is True


class TestSettingsExportIsConfiguration:
    """``/api/import-export/settings/export`` is the settings page's export.

    The settings surface is admin-read for everyone (``/api/settings/export``
    answers 403 to a viewer). The legacy import-export route returns the same
    three configuration blocks - and it answered 200 to a viewer and an
    analyst, so a non-admin could download system configuration that the
    settings blueprint refuses them.
    """

    def test_viewer_cannot_export_settings(self, app, client_factory):
        resp = client_factory("viewer").get("/api/import-export/settings/export")
        assert resp.status_code == 403, resp.status_code

    def test_analyst_cannot_export_settings(self, app, client_factory):
        resp = client_factory("analyst").get("/api/import-export/settings/export")
        assert resp.status_code == 403, resp.status_code

    def test_admin_can_export_settings(self, app, client_factory):
        resp = client_factory("admin").get("/api/import-export/settings/export")
        assert resp.status_code == 200, resp.get_data(as_text=True)[:200]
