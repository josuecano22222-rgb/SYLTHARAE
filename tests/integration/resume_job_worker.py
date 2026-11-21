"""Subprocess worker for job-level crash-recovery tests.

The existing ``resume_worker.py`` constructs ``IntegratedFileReader`` itself
and hands it a ``checkpoint_file`` explicitly. That bypasses
``IngestionService._make_reader``, which is where the resume bug actually
lived: the service never passed the checkpoint file on, so **no ingestion
started through the job manager was ever checkpointed** - even though the
reader-level tests all passed.

This worker drives the real operator path instead: create a job through
``JobManager``, crash, then click Retry and run again.

* ``KILL_AFTER_STORE=k``: ``os._exit(9)`` after the k-th store has committed.
  ``os._exit`` skips every cleanup handler, so the process dies the way a
  power loss or a killed service does.

Usage: resume_job_worker.py <create|retry> <folder> [job_id]
Environment: DB_HOST, DB_PORT, DB_NAME, DB_USER (read by the application);
INGESTION_ROOTS (server-path allowlist); APP_DATA_DIR (checkpoint location).

Prints JOB_ID, STORE_CALLS, ROUTER_CALLS, TOP_LEVEL_SKIPPED and
CHECKPOINT_FILES on normal exit.
"""

from __future__ import annotations

import json
import logging
import os
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, REPO)


def main() -> int:
    logging.disable(logging.CRITICAL)
    mode, folder = sys.argv[1], sys.argv[2]

    from pipeline import storage_pipeline as sp

    kill_after = int(os.environ.get("KILL_AFTER_STORE", "0") or 0)
    original_store = sp.StoragePipeline._store_file_sync
    calls = {"n": 0}

    def counting_store(self, *args, **kwargs):
        calls["n"] += 1
        result = original_store(self, *args, **kwargs)
        if kill_after and calls["n"] == kill_after:
            os._exit(9)
        return result

    sp.StoragePipeline._store_file_sync = counting_store

    # Count every file the router is asked to read, including archive members.
    # A file skipped at discovery never reaches the router.
    from reader_file.services.file_router_service import FileRouterService

    original_route = FileRouterService.process_file
    routed = {"n": 0}

    def counting_route(self, *args, **kwargs):
        routed["n"] += 1
        return original_route(self, *args, **kwargs)

    FileRouterService.process_file = counting_route

    # Record what discovery reported for the top-level walk: how many files
    # were recognised as already processed and left out of the work queue.
    from pipeline import integrated_reader as ir

    original_inventory = ir.IntegratedFileReader._inventory_tree
    inventory = {"top_level_skipped": None}

    def recording_inventory(self, *args, **kwargs):
        result = original_inventory(self, *args, **kwargs)
        if isinstance(result, dict) and inventory["top_level_skipped"] is None:
            inventory["top_level_skipped"] = int(result.get("skipped") or 0)
        return result

    ir.IntegratedFileReader._inventory_tree = recording_inventory

    import time

    from services.jobs import job_state
    from services.jobs.manager import JobManager

    manager = JobManager(synchronous=False)
    options = {
        "path": folder,
        "file_paths": [],
        "source": "resume-test",
        "side": "resume-test",
        "recursive": True,
        # One worker by default so a store count maps to a file; a concurrent
        # run is opt-in so the crash can land while several files are in flight.
        "processing": {"max_workers": int(os.environ.get("WORKERS", "1") or 1),
                       "checkpoint": "auto"},
    }

    if mode == "create":
        job = manager.create_job("ingestion", folder, options, created_by="resume-test")
    else:
        # A crashed job is reclaimed as FAILED on the next startup, exactly as
        # the running application does it.
        manager.recover_stale_jobs(stale_seconds=0)
        job = manager.retry(sys.argv[3], created_by="resume-test")

    print(f"JOB_ID={job['job_id']}", flush=True)

    terminal = {job_state.COMPLETED, job_state.FAILED,
                job_state.CANCELLED, job_state.COMPLETED_WITH_WARNINGS}
    deadline = time.time() + 600
    record = {}
    while time.time() < deadline:
        record = manager.repo.get(job["job_id"]) or {}
        if record.get("status") in terminal:
            break
        time.sleep(0.2)

    from core.app_paths import get_checkpoints_dir

    checkpoints = sorted(
        str(p.relative_to(get_checkpoints_dir()))
        for p in get_checkpoints_dir().rglob("*") if p.is_file()
    )

    print(f"STATUS={record.get('status')}", flush=True)
    print(f"STORE_CALLS={calls['n']}", flush=True)
    print(f"ROUTER_CALLS={routed['n']}", flush=True)
    print(f"TOP_LEVEL_SKIPPED={inventory['top_level_skipped']}", flush=True)
    print("CHECKPOINT_FILES=" + json.dumps(checkpoints), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
