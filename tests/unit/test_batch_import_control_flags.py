"""batch_import jobs must be cancellable and pausable like ingestions.

``_run_batch_import`` used to call ``IngestionService().run()`` with only
``progress_cb``. With ``cancel_cb`` and ``pause_cb`` left as None the service's
control sentinel had nothing to poll, and with ``engine_cb`` unset no engine
was registered for a direct signal - so a running batch import could not be
cancelled or paused at all.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import pytest


class _StubRepo:
    """Minimal job repository: one in-memory job row."""

    def __init__(self, job: Dict[str, Any]):
        self.job = dict(job)
        self.updates = []

    def get(self, job_id):
        return dict(self.job) if self.job.get("job_id") == job_id else None

    def update_fields(self, job_id, fields):
        self.updates.append(fields)
        self.job.update(fields)

    def add_event(self, *a, **k):
        return None


class _StubResult:
    stats: Dict[str, Any] = {}
    cancelled = False
    paused = False


@pytest.fixture(autouse=True)
def allow_tmp_ingestion(monkeypatch, tmp_path):
    """Server-path ingestion is allowlisted (SEC-06); allow the temp corpus."""
    monkeypatch.setenv("INGESTION_ROOTS", str(tmp_path))


@pytest.fixture
def capture_run(monkeypatch):
    """Intercept IngestionService.run and record the callbacks it receives."""
    captured = {}

    def fake_run(self, request, progress_cb=None, cancel_cb=None, pause_cb=None,
                 engine_cb=None, issue_registry=None):
        captured.update({
            "cancel_cb": cancel_cb,
            "pause_cb": pause_cb,
            "engine_cb": engine_cb,
        })
        # Exercise the callbacks exactly as the real service would.
        if engine_cb is not None:
            engine_cb("READER")
        return _StubResult()

    import services.ingesting.service as svc_mod

    monkeypatch.setattr(svc_mod.IngestionService, "run", fake_run)
    return captured


def _manager_with(repo):
    from services.jobs.manager import JobManager

    return JobManager(repository=repo, synchronous=True)


def test_batch_import_wires_cancel_and_pause(capture_run, tmp_path):
    """Regression: these three callbacks were all None for batch_import."""
    from services.jobs.manager import JobManager

    job = {
        "job_id": "ABC123",
        "job_type": "batch_import",
        "options": {
            "file_paths": [str(tmp_path / "a.txt")],
            "source": "SRC", "side": "SIDE", "processing": {},
        },
        "created_by": "tester",
    }
    repo = _StubRepo(job)
    mgr = _manager_with(repo)

    (tmp_path / "a.txt").write_text("x")

    class _Throttle:
        def add(self, *a, **k):
            pass

    mgr._run_batch_import(job, lambda snap: None, _Throttle())

    assert capture_run.get("cancel_cb") is not None, "cancel_cb was not wired"
    assert capture_run.get("pause_cb") is not None, "pause_cb was not wired"
    assert capture_run.get("engine_cb") is not None, "engine_cb was not wired"


def test_batch_import_cancel_flag_is_read_from_the_job(capture_run, tmp_path):
    """The cancel callback must reflect the job's cancellation flag."""
    job = {
        "job_id": "ABC123",
        "job_type": "batch_import",
        "options": {
            "file_paths": [str(tmp_path / "a.txt")],
            "source": "SRC", "side": "SIDE", "processing": {},
        },
        "created_by": "tester",
    }
    repo = _StubRepo(job)
    mgr = _manager_with(repo)
    (tmp_path / "a.txt").write_text("x")

    class _Throttle:
        def add(self, *a, **k):
            pass

    mgr._run_batch_import(job, lambda snap: None, _Throttle())
    cancel_cb = capture_run["cancel_cb"]

    assert cancel_cb() is False
    repo.job["cancellation_requested"] = True
    assert cancel_cb() is True


def test_batch_import_engine_is_registered_and_released(capture_run, tmp_path):
    """The engine must be registered for direct signalling, then released."""
    job = {
        "job_id": "ABC123",
        "job_type": "batch_import",
        "options": {
            "file_paths": [str(tmp_path / "a.txt")],
            "source": "SRC", "side": "SIDE", "processing": {},
        },
        "created_by": "tester",
    }
    repo = _StubRepo(job)
    mgr = _manager_with(repo)
    (tmp_path / "a.txt").write_text("x")

    class _Throttle:
        def add(self, *a, **k):
            pass

    mgr._run_batch_import(job, lambda snap: None, _Throttle())
    # The engine is registered during the run and unregistered afterwards.
    assert "READER" not in list(mgr._engines.values())
