"""A job left RUNNING by a killed process must be cleared at the next start.

Found by the round-10 process-kill drill. A batch import was SIGKILLed at 16%
(400 files); the ``jobs`` row stayed

    job_id 6E05E2184FFE | status RUNNING | progress 16 | completed_at NULL

and ``GET /api/import/jobs`` kept listing it as in progress after the app was
started again. The startup recovery path existed but was ineffective for this
case: it only reclaims jobs whose ``updated_at`` is older than
``JobsConfig.stale_seconds()`` - six hours by default - so the Import Center
showed a phantom running job until the window expired (two such rows were
measured in the drill).

The application runs exactly one process per database, with imports as threads
inside it (README, "Import jobs are process-local"; run_web.py says the same).
At startup, therefore, no job can legitimately be running: every RUNNING row
belongs to a process that is gone. ``_recover_jobs`` now asks for
``stale_seconds=0``. The manager's default stays as it is, because the same
function is the runtime guard against stealing a newer job.
"""

from __future__ import annotations

import datetime
import sys
import uuid
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

pytestmark = pytest.mark.integration


def _running_job(pg_db) -> str:
    """Insert a RUNNING job row whose updated_at is *now* (just crashed)."""
    from services.jobs.repository import JobRepository

    job_id = uuid.uuid4().hex[:12].upper()
    now = datetime.datetime.now(datetime.timezone.utc)
    record = {
        "job_id": job_id, "job_type": "batch_import", "status": "RUNNING",
        "progress": 16, "source": "kill-drill", "created_by": "admin",
    }
    repo = JobRepository()
    repo.create(record)
    # update_fields stamps updated_at = now() itself, which is exactly the
    # "just crashed" state that the six-hour window fails to reclaim.
    repo.update_fields(job_id, {"started_at": now})
    return job_id


def _status(pg_db, job_id: str) -> str:
    from services.jobs.repository import JobRepository

    return JobRepository().get(job_id)["status"]


def test_a_fresh_running_job_survives_the_runtime_default(pg_db):
    """The 6-hour window is deliberate at runtime - do not change it."""
    from services.jobs.manager import JobManager

    job_id = _running_job(pg_db)
    out = JobManager(synchronous=True).recover_stale_jobs()
    assert job_id not in (out.get("recovered") or [])
    assert _status(pg_db, job_id) == "RUNNING"


def test_startup_reclaims_every_running_job(pg_db):
    """At startup nothing can be running: stale_seconds=0 reclaims it."""
    from services.jobs.manager import JobManager

    job_id = _running_job(pg_db)
    out = JobManager(synchronous=True).recover_stale_jobs(stale_seconds=0)
    assert job_id in out["recovered"]
    job = JobManager(synchronous=True).get(job_id)
    assert job["status"] == "FAILED"
    assert job["completed_at"] is not None
    assert any("restarted" in line for line in job["errors"]), job["errors"]


def test_the_startup_hook_asks_for_zero_staleness(monkeypatch):
    """The wiring, not just the manager: apps/web/app.py must pass 0."""
    import apps.web.app as web_app

    seen = {}

    class _Recorder:
        def recover_stale_jobs(self, **kwargs):
            seen.update(kwargs)
            return {"recovered": [], "count": 0}

    class _Manager:
        @staticmethod
        def get_instance():
            return _Recorder()

    import services.jobs.manager as manager_module
    monkeypatch.setattr(manager_module, "JobManager", _Manager)
    web_app._recover_jobs()

    assert seen.get("stale_seconds") == 0, (
        "the startup recovery still waits for the 6-hour window, so a killed "
        "process leaves a phantom RUNNING job in the Import Center")
