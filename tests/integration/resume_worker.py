"""Subprocess worker for the crash-recovery tests.

Runs one ingestion pass over a folder against a real PostgreSQL database and
can terminate itself at a chosen storage boundary:

* ``KILL_BEFORE_STORE=k``: exit before the k-th store call starts (nothing of
  store k is written).
* ``KILL_AFTER_STORE=k``: exit after the k-th store call has returned, which is
  after its database commit and before the checkpoint mark for that file.

``os._exit`` skips every cleanup handler, so the process dies the way a power
loss or a killed service does.  On normal exit prints ``STORE_CALLS``, ``ROUTER_CALLS`` (files the reader was
asked to process) and ``LEDGER`` (terminal counts from the progress ledger).

Usage: resume_worker.py <folder> <checkpoint_file>
Environment: DB_HOST, DB_PORT, DB_NAME, DB_USER (read by the application).
"""

from __future__ import annotations

import logging
import os
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, REPO)


def main() -> int:
    logging.disable(logging.CRITICAL)
    folder, checkpoint = sys.argv[1], sys.argv[2]

    from pipeline import storage_pipeline as sp
    from pipeline import integrated_reader as ir

    kill_before = int(os.environ.get("KILL_BEFORE_STORE", "0") or 0)
    kill_after = int(os.environ.get("KILL_AFTER_STORE", "0") or 0)
    original = sp.StoragePipeline._store_file_sync
    calls = {"n": 0}

    def counting_store(self, *args, **kwargs):
        calls["n"] += 1
        if kill_before and calls["n"] == kill_before:
            os._exit(9)
        result = original(self, *args, **kwargs)
        if kill_after and calls["n"] == kill_after:
            os._exit(9)
        return result

    sp.StoragePipeline._store_file_sync = counting_store

    # Count every file the router is asked to read, including archive members.
    # A skipped (already committed) file never reaches the router.
    from reader_file.services.file_router_service import FileRouterService
    original_route = FileRouterService.process_file
    routed = {"n": 0}

    def counting_route(self, *args, **kwargs):
        routed["n"] += 1
        return original_route(self, *args, **kwargs)

    FileRouterService.process_file = counting_route

    reader = ir.IntegratedFileReader(
        max_workers=1,
        enable_monitoring=False,
        enable_storage=True,
        storage_source="resume-test",
        storage_side="resume-test",
        checkpoint_file=checkpoint,
    )
    reader.process_folder(folder)
    import json
    print(f"STORE_CALLS={calls['n']}", flush=True)
    print(f"ROUTER_CALLS={routed['n']}", flush=True)
    print("LEDGER=" + json.dumps(dict(reader.progress_ledger.counts)), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
