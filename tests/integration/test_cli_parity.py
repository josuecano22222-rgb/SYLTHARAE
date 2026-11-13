"""Feature-parity gate (spec section 31).

Every capability of the former CLI entry points must be reachable through
the shared service layer, and the CLI must be a thin adapter over it.

Capability matrix (CLI capability -> service destination):

| CLI capability              | run_cli.py / apps.cli.main   | Service destination                       | API endpoint                    |
|-----------------------------|------------------------------|-------------------------------------------|---------------------------------|
| file/folder path input      | --path                       | IngestionRequest.path                     | POST /api/input/jobs            |
| source selection/creation   | --source / interactive step 2| services.sources.create_source_svc        | POST /api/input/sources         |
| side selection/creation     | --side / interactive step 3  | services.sources.create_side_svc          | POST /api/input/sides           |
| worker count                | --workers                    | IngestionOptions.max_workers              | processing.max_workers          |
| checkpoint/resume           | --checkpoint + interactive 4 | IngestionOptions.checkpoint               | processing.checkpoint           |
| single file processing      | main_read_file_threaded      | IngestionService.run (engine)             | POST /api/input/jobs            |
| folder processing           | main_read_folder_threaded    | IngestionService.run (engine)             | POST /api/input/jobs            |
| recursion                   | engine behavior (always)     | IngestionRequest.recursive                | recursive field                 |
| JSON output                 | --json / --format            | IngestionResult.to_dict                   | GET /api/jobs/{id}              |
| quiet progress suppression  | --quiet                      | (adapter-only: output formatting)         | n/a (progress via API)          |
| exit codes 0/1/2/3          | cli_main                     | (adapter-only: exit codes)                | HTTP status codes               |
| batch report                | --report-job / --report-html | services.reporting.ingestion_batch        | GET /operations/input/reports/<id> |
| domain data import          | run_import.py --data-file    | DomainImportService.run                   | POST /api/import/jobs           |
| progress display            | terminal prints              | JobManager progress events                | GET /api/jobs/{id}/events + SSE |
| action recording            | Hdg_Err_Ex_Log (CLI-only)    | audit/correlation ids (superset)          | correlation ids in logs         |
"""
import io
import sys
from contextlib import redirect_stdout
from unittest import mock

import pytest


class TestCliCapabilitiesExistAsServices:
    """Every CLI argument maps to a service-layer field (no silent loss)."""

    def test_ingestion_request_covers_cli_args(self):
        from services.ingesting.options import IngestionOptions, IngestionRequest
        import dataclasses

        option_fields = {f.name for f in dataclasses.fields(IngestionOptions)}
        request_fields = {f.name for f in dataclasses.fields(IngestionRequest)}
        # --workers            -> max_workers
        assert "max_workers" in option_fields
        # --checkpoint         -> checkpoint
        assert "checkpoint" in option_fields
        # --path               -> path
        assert "path" in request_fields
        # --source / --side    -> source / side
        assert {"source", "side"} <= request_fields
        # recursion            -> recursive
        assert "recursive" in request_fields

    def test_domain_import_request_covers_cli_args(self):
        from services.importing.domain_import_service import DomainImportRequest
        import dataclasses

        fields = {f.name for f in dataclasses.fields(DomainImportRequest)}
        assert "data_file" in fields  # --data-file


class TestCliIsThinAdapter:
    """cli_main must delegate to IngestionService (no duplicated engine)."""

    def test_cli_main_uses_ingestion_service(self, tmp_path, monkeypatch):
        monkeypatch.setenv("INGESTION_ROOTS", str(tmp_path))
        target = tmp_path / "sample.txt"
        target.write_text("parity sample")

        captured = {}

        class FakeResult:
            success = True
            cancelled = False
            paused = False
            errors = []
            warnings = []
            stats = {"files_total": 1, "files_stored": 1, "files_failed": 0,
                     "files_duplicates": 0}

        class FakeService:
            def validate(self, request):
                captured["request"] = request
                return request

            def run(self, request, progress_cb=None, **kw):
                captured["ran"] = True
                return FakeResult()

        import services.ingesting.service as svc_mod

        with mock.patch.object(svc_mod, "IngestionService", FakeService):
            from apps.cli.main import cli_main

            buf = io.StringIO()
            with redirect_stdout(buf):
                code = cli_main([
                    "--path", str(target),
                    "--source", "parity-src",
                    "--side", "parity-side",
                    "--workers", "3",
                    "--json", "--quiet",
                ])

        assert code == 0
        assert captured["ran"] is True
        req = captured["request"]
        assert req.source == "parity-src"
        assert req.side == "parity-side"
        assert req.options.max_workers == 3
        assert "--workers" and req.options.max_workers == 3

    def test_cli_exit_codes_contract(self, tmp_path, monkeypatch, capsys):
        """Exit codes: 1 invalid usage, 3 failure (documented contract)."""
        monkeypatch.setenv("INGESTION_ROOTS", str(tmp_path))
        from apps.cli.main import cli_main

        # nonexistent path -> validation error -> exit 1
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli_main([
                "--path", str(tmp_path / "nope.txt"),
                "--source", "s", "--side", "d", "--json",
            ])
        assert code == 1
        assert '"success": false' in buf.getvalue().lower()

    def test_run_import_uses_domain_service(self, capsys):
        """run_import.py main() is an adapter over DomainImportService."""
        import run_import

        captured = {}

        class FakeResult:
            success = True
            stats = {"words": 5}

        class FakeSvc:
            def validate(self, request):
                captured["request"] = request
                return request

            def run(self, request, progress_cb=None):
                captured["ran"] = True
                return FakeResult()

        with mock.patch.object(
            "services.importing.domain_import_service" and sys.modules[
                "services.importing.domain_import_service"
            ], "DomainImportService", FakeSvc,
        ):
            with mock.patch.object(sys, "argv", ["run_import.py"]):
                with pytest.raises(SystemExit) as exc:
                    run_import.main()
        assert exc.value.code == 0
        assert captured["ran"] is True


class TestFrontendCapabilities:
    """Frontend/API capabilities previously CLI-only or web-only (superset)."""

    def test_sources_and_sides_creatable_via_api(self, app, admin_client):
        import uuid

        uid = uuid.uuid4().hex
        r = admin_client.post("/api/input/sources", json={
            "name": f"parity-src-{uid}", "job": "tester", "country": "NL",
            "importance": 0.7,
        })
        assert r.status_code == 201, r.get_data(as_text=True)
        r = admin_client.post("/api/input/sides", json={
            "name": f"parity-side-{uid}", "importance": 0.6,
        })
        assert r.status_code == 201

    def test_dry_run_capability(self, app, admin_client, tmp_path, monkeypatch):
        monkeypatch.setenv("INGESTION_ROOTS", str(tmp_path))
        (tmp_path / "one.txt").write_text("x")
        r = admin_client.post("/api/input/jobs", json={
            "path": str(tmp_path / "one.txt"),
            "source": "s", "side": "d", "dry_run": True,
        })
        assert r.status_code == 200
        assert r.get_json()["preview"]["files_discovered"] == 1

    def test_job_listing_and_history(self, app, admin_client):
        r = admin_client.get("/api/jobs")
        assert r.status_code == 200
        assert r.get_json()["success"] is True
