"""Security regressions for the unified operations API (spec section 36)."""
import pytest


class TestJobsAuthentication:
    def test_unauthenticated_cannot_create_jobs(self, app):
        """No session -> 401/302, and definitely no job."""
        client = app.test_client()
        for url, body in (
            ("/api/input/jobs", {"path": "/etc", "source": "s", "side": "d"}),
            ("/api/import/jobs", {"type": "domain_import"}),
            ("/api/input/uploads", None),
        ):
            if body is None:
                resp = client.post(url, data={})
            else:
                resp = client.post(url, json=body)
            assert resp.status_code in (302, 401), (url, resp.status_code)

    def test_unauthenticated_cannot_read_jobs(self, app):
        client = app.test_client()
        for url in ("/api/jobs", "/api/jobs/XYZ/events", "/api/jobs/XYZ/results"):
            resp = client.get(url)
            assert resp.status_code in (302, 401), (url, resp.status_code)


class TestJobsAuthorization:
    def test_viewer_cannot_start_or_cancel(self, app, viewer_client, admin_credentials,
                                           admin_client):
        """Viewer role is read-only server-side (not just UI-hidden)."""
        resp = viewer_client.post("/api/input/jobs", json={
            "path": "/etc/passwd", "source": "s", "side": "d",
        })
        assert resp.status_code == 403, (resp.status_code,
                                         resp.get_data(as_text=True)[:300])
        # Create a real job as admin, then viewer tries to cancel it. The job
        # is created through the manager: the API now refuses a domain import
        # of a missing file with 400 before any job row exists (validate
        # before persist), so it can no longer serve as a job factory.
        from services.jobs.manager import JobManager

        job = JobManager(synchronous=True).create_job(
            "domain_import", source="security-test",
            options={"data_file": "definitely-missing.xlsx"},
            created_by=admin_credentials[0])
        job_id = job["job_id"]
        cancel_resp = viewer_client.post(f"/api/jobs/{job_id}/cancel")
        assert cancel_resp.status_code == 403
        delete_resp = viewer_client.delete(f"/api/jobs/{job_id}")
        assert delete_resp.status_code == 403

    def test_backup_import_is_admin_only(self, app, client_factory, admin_credentials):
        """Analysts cannot start backup restores (destructive operation)."""
        analyst_client = client_factory("analyst")
        resp = analyst_client.post("/api/import/jobs", json={"type": "backup_import"})
        assert resp.status_code == 403

    def test_unknown_job_ids_404_not_500(self, app, admin_client):
        resp = admin_client.get("/api/jobs/NOPE123/events")
        assert resp.status_code == 404
        assert resp.get_json()["success"] is False


class TestJobsInputValidation:
    def test_malformed_job_payload_is_400(self, app, admin_client):
        for body in (
            {},  # nothing
            {"path": "/tmp"},  # missing source/side
            {"path": "/tmp", "source": "s", "side": "d", "file_paths": ["/x"]},  # both
            {"file_paths": "not-a-list", "source": "s", "side": "d"},
            {"path": "/tmp", "source": "s", "side": "d", "processing": "nope"},
        ):
            resp = admin_client.post("/api/input/jobs", json=body)
            assert resp.status_code == 400, (body, resp.status_code)
            err = resp.get_json()["error"]
            assert err["code"] == "VALIDATION_FAILED"
            assert "request_id" in err

    def test_path_outside_roots_rejected(self, app, admin_client, monkeypatch):
        """SEC-06: server paths need configured roots; fail closed."""
        monkeypatch.delenv("INGESTION_ROOTS", raising=False)
        from core.path_safety import configured_ingestion_roots

        if configured_ingestion_roots():
            pytest.skip("Roots configured outside the test")
        resp = admin_client.post("/api/input/jobs", json={
            "path": "/etc", "source": "s", "side": "d",
        })
        assert resp.status_code == 400
        # ...and no sensitive internals leak in the message
        msg = resp.get_json()["error"]["message"]
        assert "root" in msg.lower() or "not allowed" in msg.lower()

    def test_import_type_whitelist(self, app, admin_client):
        resp = admin_client.post("/api/import/jobs", json={"type": "DROP TABLE users"})
        assert resp.status_code == 400

    def test_error_envelope_no_stack_traces(self, app, admin_client):
        resp = admin_client.get("/api/jobs/%%2e%2e/events")
        assert b"Traceback" not in resp.data


class TestJobsCsrf:
    def test_csrf_enforced_when_enabled(self, app, admin_credentials):
        """A state-changing request without a CSRF token is rejected when the
        application has CSRF enabled (tests normally disable it globally)."""
        from core.security.rate_limit import limiter

        app.config["WTF_CSRF_ENABLED"] = True
        prev = limiter.enabled
        limiter.enabled = False
        try:
            client = app.test_client()
            tok = client.get("/api/csrf-token").get_json()["csrf_token"]
            resp = client.post("/auth/login", json={
                "username": admin_credentials[0], "password": admin_credentials[1],
            }, headers={"X-CSRFToken": tok})
            assert resp.status_code == 200, resp.get_data(as_text=True)
            resp = client.post("/api/input/jobs", json={
                "path": "/tmp", "source": "s", "side": "d",
            })
            assert resp.status_code == 400  # CSRFError
        finally:
            app.config["WTF_CSRF_ENABLED"] = False
            limiter.enabled = prev


class TestJobsConcurrency:
    def test_max_concurrent_jobs_configurable(self, app):
        from services.jobs.manager import JobsConfig
        import os

        old = os.environ.get("JOBS_MAX_CONCURRENT")
        try:
            os.environ["JOBS_MAX_CONCURRENT"] = "7"
            assert JobsConfig.max_concurrent_jobs() == 7
            os.environ["JOBS_MAX_CONCURRENT"] = "0"
            assert JobsConfig.max_concurrent_jobs() == 1  # floor
        finally:
            if old is None:
                os.environ.pop("JOBS_MAX_CONCURRENT", None)
            else:
                os.environ["JOBS_MAX_CONCURRENT"] = old
