"""A paused run must not report the files it never started as failures.

A large run was paused and then reported "N file(s) could not be processed",
because the files left in the queue were settled as FAILED. A pause is a
request to stop, not a failure, so those files are skipped and the run is
marked incomplete. Cancel keeps its own terminal state (see the cancel test).
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from pipeline.integrated_reader import IntegratedFileReader


def _write_files(root: Path, count: int) -> None:
    for i in range(count):
        (root / f"doc{i}.txt").write_text(f"document {i} text", encoding="utf-8")


def test_unstarted_files_are_not_failures_after_a_pause():
    root = Path(tempfile.mkdtemp(prefix="pause_"))
    try:
        _write_files(root, 6)
        reader = IntegratedFileReader(
            max_workers=1, enable_monitoring=False, enable_storage=False
        )
        reader.request_pause()
        reader.process_folder(str(root))

        snap = reader.get_live_progress()
        assert snap["files_pending"] == 0, snap
        assert snap["in_progress"] == 0, snap
        assert snap["files_failed"] == 0, snap
        assert snap["files_cancelled"] == 0, snap
        assert snap["files_skipped"] > 0, snap
        assert reader.get_statistics().get("partial_run") is True
    finally:
        shutil.rmtree(root, ignore_errors=True)
