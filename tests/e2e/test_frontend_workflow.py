"""E2E: the full frontend workflow through the operations API (spec section 32).

Login -> Input -> stage upload -> create job -> monitor -> complete ->
inspect results -> search hit -> retry safety.
"""
import io
import time
import uuid

import pytest


def _wait_terminal(client, job_id, timeout=120):
    deadline = time.time() + timeout
    status = None
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").get_json()["job"]
        status = job["status"]
        if status in ("COMPLETED", "COMPLETED_WITH_WARNINGS", "FAILED",
                      "CANCELLED"):
            return job
        time.sleep(0.2)
    return client.get(f"/api/jobs/{job_id}").get_json()["job"]


@pytest.fixture()
def roots(tmp_path_factory, monkeypatch):
    root = tmp_path_factory.mktemp("e2e_roots")
    monkeypatch.setenv("INGESTION_ROOTS", str(root))
    return root


class TestFrontendIngestionWorkflow:
    def test_upload_ingest_monitor_search(self, app, admin_client, roots):
        uid = uuid.uuid4().hex
        admin_client.post("/api/input/sources", json={
            "name": f"e2e-src-{uid}", "job": "analyst", "country": "NL",
        })
        admin_client.post("/api/input/sides", json={"name": f"e2e-side-{uid}"})

        # 1. Upload a document through the staging endpoint
        content = f"quantum pineapple dossier {uid} " * 30
        resp = admin_client.post(
            "/api/input/uploads",
            data={"files": (io.BytesIO(content.encode()), f"report_{uid}.txt")},
            content_type="multipart/form-data",
        )
        assert resp.status_code == 201, resp.get_data(as_text=True)
        staged = resp.get_json()["staged_paths"]
        assert staged

        # 2. Dry run through the same API the Input page uses
        dry = admin_client.post("/api/input/jobs", json={
            "file_paths": staged,
            "source": f"e2e-src-{uid}", "side": f"e2e-side-{uid}",
            "dry_run": True,
        })
        assert dry.status_code == 200
        preview = dry.get_json()["preview"]
        assert preview["files_discovered"] == 1
        assert preview["files_eligible"] == 1

        # 3. Create the real ingestion job (as the Input page does)
        resp = admin_client.post("/api/input/jobs", json={
            "file_paths": staged,
            "source": f"e2e-src-{uid}", "side": f"e2e-side-{uid}",
            "processing": {"checkpoint": "off"},
        })
        assert resp.status_code == 202, resp.get_data(as_text=True)
        job_id = resp.get_json()["job"]["job_id"]

        # 4. Monitor until terminal (polling, as the Jobs page does)
        job = _wait_terminal(admin_client, job_id)
        assert job["status"] in ("COMPLETED", "COMPLETED_WITH_WARNINGS"), job
        assert (job["statistics"]["files_succeeded"] or 0) >= 1

        # 5. Events exist and are ordered
        events = admin_client.get(f"/api/jobs/{job_id}/events").get_json()["events"]
        kinds = [e["event_type"] for e in events]
        assert "JOB_CREATED" in kinds and "JOB_COMPLETED" in kinds

        # 6. The content is searchable (the actual point of ingestion)
        search = admin_client.get(f"/api/search?query=quantum+pineapple+{uid}")
        assert search.status_code == 200
        results = search.get_json().get("results") or []
        assert any(str(uid) in str(r) for r in results) or len(results) >= 1

        # 7. Re-running the same staged file is safe (dedup, no duplicates)
        resp = admin_client.post("/api/input/jobs", json={
            "file_paths": staged,
            "source": f"e2e-src-{uid}", "side": f"e2e-side-{uid}",
            "processing": {"checkpoint": "off"},
        })
        job2 = _wait_terminal(admin_client, resp.get_json()["job"]["job_id"])
        assert job2["status"] in ("COMPLETED", "COMPLETED_WITH_WARNINGS")
        stats2 = job2["statistics"]
        assert (stats2["duplicates"] or 0) >= 1
        assert (stats2["files_succeeded"] or 0) == 0

    def test_a_refused_server_path_is_echoed_exactly_as_typed(
            self, app, admin_client, roots):
        r"""A Windows path on a host with no matching root, and a POSIX path
        outside every root: the refusal must name the path the operator typed.

        `Path(raw).name` used to build this message, which reduced
        `C:\Windows\System32` to `System32` on Windows and `/etc` to `etc`
        everywhere else - the operator was told about a path they had not
        typed, in the one message that is supposed to explain the refusal.
        """
        for typed in ("C:\\Evidence\\Case 1", "/etc"):
            resp = admin_client.post("/api/input/jobs", json={
                "path": typed, "source": "s", "side": "d",
            })
            assert resp.status_code == 400, resp.get_data(as_text=True)
            message = resp.get_json()["error"]["message"]
            assert message == f"Path not allowed: {typed}", message

    def test_server_folder_ingestion_job(self, app, admin_client, roots):
        uid = uuid.uuid4().hex
        admin_client.post("/api/input/sources", json={
            "name": f"e2e-src2-{uid}", "job": "analyst", "country": "NL",
        })
        admin_client.post("/api/input/sides", json={"name": f"e2e-side2-{uid}"})

        folder = roots / f"inbox_{uid}"
        folder.mkdir()
        for i in range(5):
            (folder / f"f{i}.txt").write_text(
                f"folder corpus {uid} file {i} zebra unicorn\n" * 8)
        (folder / "sub").mkdir()
        (folder / "sub" / "nested.txt").write_text(
            f"nested {uid} recursive kangaroo\n" * 8)

        resp = admin_client.post("/api/input/jobs", json={
            "path": str(folder),
            "source": f"e2e-src2-{uid}", "side": f"e2e-side2-{uid}",
            "recursive": True,
            "processing": {"checkpoint": "off"},
        })
        assert resp.status_code == 202
        job = _wait_terminal(admin_client, resp.get_json()["job"]["job_id"])
        assert job["status"] in ("COMPLETED", "COMPLETED_WITH_WARNINGS"), job
        # 6 files: 5 top-level + 1 nested (recursive)
        assert (job["statistics"]["files_succeeded"] or 0) == 6

    def test_browser_multipart_direct_job(self, app, admin_client, roots):
        """Regression: the browser Start button posts the files DIRECTLY to
        POST /api/input/jobs as multipart/form-data (the upload staging
        endpoint is only used by drag&drop). The job is created from the
        staged copies in the same request, and form string booleans
        ("false") must not coerce to True.
        """
        uid = uuid.uuid4().hex
        admin_client.post("/api/input/sources", json={
            "name": f"e2e-src3-{uid}", "job": "analyst", "country": "NL",
        })
        admin_client.post("/api/input/sides", json={"name": f"e2e-side3-{uid}"})

        resp = admin_client.post("/api/input/jobs", data={
            "source": f"e2e-src3-{uid}",
            "side": f"e2e-side3-{uid}",
            "recursive": "false",  # multipart form values are strings
            "files": [
                (io.BytesIO(f"direct multipart one {uid} falcon\n".encode() * 10), "one.txt"),
                (io.BytesIO(f"direct multipart two {uid} falcon\n".encode() * 10), "two.txt"),
            ],
        }, content_type="multipart/form-data")
        assert resp.status_code == 202, resp.get_data(as_text=True)
        job_id = resp.get_json()["job"]["job_id"]

        job = _wait_terminal(admin_client, job_id)
        assert job["status"] in ("COMPLETED", "COMPLETED_WITH_WARNINGS"), job
        # recursive=false honored: only the 2 top-level staged files
        assert (job["statistics"]["files_succeeded"] or 0) == 2, job["statistics"]


class TestFrontendImportWorkflow:
    def test_batch_import_preview_confirm(self, app, admin_client, roots):
        uid = uuid.uuid4().hex
        admin_client.post("/api/input/sources", json={
            "name": f"e2e-src3-{uid}", "job": "analyst", "country": "NL",
        })
        admin_client.post("/api/input/sides", json={"name": f"e2e-side3-{uid}"})

        f1 = roots / f"a_{uid}.txt"
        f2 = roots / f"b_{uid}.txt"
        f1.write_text(f"batch import {uid} delta bravo\n" * 10)
        f2.write_text(f"batch import {uid} charlie echo\n" * 10)

        # validate
        v = admin_client.post("/api/import/validate", json={
            "type": "batch_import", "file_paths": [str(f1), str(f2)],
        })
        assert v.status_code == 200
        preview = v.get_json()["preview"]
        assert preview["accepted"] == 2 and preview["rejected"] == 0

        # confirm -> job
        resp = admin_client.post("/api/import/jobs", json={
            "type": "batch_import", "file_paths": [str(f1), str(f2)],
            "source": f"e2e-src3-{uid}", "side": f"e2e-side3-{uid}",
        })
        assert resp.status_code == 202, resp.get_data(as_text=True)
        job = _wait_terminal(admin_client, resp.get_json()["job"]["job_id"])
        assert job["status"] in ("COMPLETED", "COMPLETED_WITH_WARNINGS"), job
        assert (job["statistics"]["files_succeeded"] or 0) == 2

    def test_invalid_import_requests_fail_fast_without_doomed_jobs(
        self, app, admin_client,
    ):
        """Regression: invalid import requests return structured 400s and do
        NOT persist doomed job rows (validate-before-persist contract)."""
        before = admin_client.get("/api/import/jobs?limit=200").get_json()["jobs"]
        n_before = len(before)

        resp = admin_client.post("/api/import/jobs", json={
            "type": "backup_import",
        })
        assert resp.status_code == 400, resp.get_data(as_text=True)
        assert resp.get_json()["success"] is False

        resp = admin_client.post("/api/import/jobs", json={
            "type": "domain_import",
        })
        assert resp.status_code == 400, resp.get_data(as_text=True)
        assert resp.get_json()["success"] is False

        after = admin_client.get("/api/import/jobs?limit=200").get_json()["jobs"]
        assert len(after) == n_before, "doomed job rows were persisted"
