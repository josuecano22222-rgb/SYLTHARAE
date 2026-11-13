"""A pause request must not hold its HTTP worker while the engine finishes.

The engine stops at its next checkpoint, which can take minutes on a large
file. The old pause() polled for up to 30 s per request, so every click on the
job page held a server thread for that long (seen as 30 s POST /pause entries).
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from services.jobs import job_state
from services.jobs.manager import JobManager


class _Repo:
    def __init__(self, status):
        self.job = {"job_id": "J1", "status": status}
        self.updates = []

    def get(self, job_id):
        return dict(self.job)

    def update_fields(self, job_id, fields):
        self.updates.append(fields)


def _stub(status):
    # _require sees the status at request time; later repo.get reads may differ
    # (the engine moves the job to PAUSED after the request returns).
    repo = _Repo(status)
    stub = SimpleNamespace(repo=repo, _signal_engine=lambda *a: None)
    stub._require = lambda job_id: {"job_id": "J1", "status": status}
    return stub, repo


def test_pause_returns_immediately_while_the_engine_is_still_running():
    stub, repo = _stub(job_state.RUNNING)
    started = time.time()
    job = JobManager.pause(stub, "J1")
    assert time.time() - started < 1.0
    assert repo.updates == [{"pause_requested": True}]
    assert job["status"] == job_state.RUNNING  # the job reaches PAUSED later


def test_a_bounded_wait_returns_as_soon_as_the_job_is_paused():
    stub, repo = _stub(job_state.RUNNING)
    stub.repo.job["status"] = job_state.PAUSED  # engine reached a checkpoint
    started = time.time()
    job = JobManager.pause(stub, "J1", wait_seconds=5)
    assert time.time() - started < 1.0
    assert job["status"] == job_state.PAUSED


def test_only_running_jobs_can_be_paused():
    stub, _ = _stub(job_state.PAUSED)
    with pytest.raises(ValueError):
        JobManager.pause(stub, "J1")
