"""Hypothesis 3: the checkpoint was never written in the first place.

If marks are lost to an I/O error and nothing says so, the operator sees a
run that "started from the beginning" with no explanation. These tests pin
down what must happen when the checkpoint cannot be written: it must be
loud, and it must be attributable.
"""

from __future__ import annotations

import logging
import os
import tempfile

import pytest


def _unwritable_dir():
    """A path that cannot be created as a directory, even for root.

    chmod alone is not enough - tests may run as root, which ignores the
    permission bits. Making the parent a regular file fails for everybody.
    """
    root = tempfile.mkdtemp()
    blocker = os.path.join(root, "i_am_a_regular_file")
    with open(blocker, "w") as fh:
        fh.write("not a directory")
    return root, os.path.join(blocker, "checkpoints")


def test_unusable_checkpoint_dir_disables_resuming_loudly(caplog, monkeypatch):
    """A checkpoint dir that cannot be created must warn, not fail silently.

    Before this fix the OSError propagated out of _make_reader and aborted
    the run's setup. Either way the operator got no warning that resuming
    was disabled, so a later Retry looked like a resume bug.
    """
    from pathlib import Path

    import core.app_paths as app_paths
    from services.ingesting.options import IngestionRequest
    from services.ingesting.service import IngestionService

    _root, bad = _unwritable_dir()
    monkeypatch.setattr(app_paths, "get_checkpoints_dir", lambda: Path(bad))

    with caplog.at_level(logging.WARNING):
        result = IngestionService._checkpoint_file(
            IngestionRequest(path=_root, source="SRC", side="SIDE")
        )

    assert result is None, "no usable checkpoint dir means no checkpoint file"
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings, "an unusable checkpoint dir must be reported, not swallowed"
    text = " ".join(r.getMessage() for r in warnings).lower()
    assert "resum" in text, (
        "the warning must say that resuming is disabled, not just log an OS error"
    )


def test_journal_write_failure_warns_once_and_processing_continues(caplog):
    """A disk-full or permission error on the journal must not spam or abort."""
    from core.checkpoint_manager import CheckpointManager

    folder = tempfile.mkdtemp()
    # Five DISTINCT files: a repeated file is already in the in-memory set and
    # is never written a second time, so it would understate the failure count.
    targets = []
    for i in range(5):
        target = os.path.join(folder, f"f{i}.txt")
        with open(target, "w") as fh:
            fh.write(f"payload {i}")
        targets.append(target)

    mgr = CheckpointManager(
        os.path.join(folder, "ck.json"), folder, "SRC", "SIDE"
    )

    real_fsync = os.fsync

    def exploding_fsync(fd):
        raise OSError(28, "No space left on device")

    os.fsync = exploding_fsync
    try:
        with caplog.at_level(logging.WARNING):
            for target in targets:
                mgr.mark_processed(
                    {"path": target, "size_bytes": 9, "modified": "2026-01-01"}
                )
    finally:
        os.fsync = real_fsync

    # Every mark is still counted in memory, so the run itself is unaffected.
    assert mgr.mark_failures == 5, (
        "each failed mark must be counted so the run can report it"
    )
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1, (
        f"five failures must produce one warning, not {len(warnings)} - "
        "a per-file warning would bury the real problem"
    )
    assert "checkpoint" in warnings[0].getMessage().lower()


def test_failed_marks_are_reported_in_resume_info(caplog):
    """The job summary must state how many files were never checkpointed."""
    from core.checkpoint_manager import CheckpointManager

    folder = tempfile.mkdtemp()
    target = os.path.join(folder, "f.txt")
    with open(target, "w") as fh:
        fh.write("payload")

    mgr = CheckpointManager(os.path.join(folder, "ck.json"), folder, "SRC", "SIDE")
    real_fsync = os.fsync
    os.fsync = lambda fd: (_ for _ in ()).throw(OSError(28, "No space left on device"))
    try:
        with caplog.at_level(logging.WARNING):
            mgr.mark_processed({"path": target, "size_bytes": 7, "modified": "2026-01-01"})
    finally:
        os.fsync = real_fsync

    stats = mgr.get_statistics()
    assert stats["mark_failures"] == 1, (
        "the number of files that were processed but never checkpointed must "
        "appear in the checkpoint statistics the job summary is built from, "
        "not only in a counter nothing reads"
    )
