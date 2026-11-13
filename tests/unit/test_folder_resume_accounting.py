"""A resumed folder run must report the whole corpus, not an under-count.

``process_folder`` derived its work total as ``inventory['total'] - skipped``,
but ``_inventory_tree`` already excludes checkpointed files from ``total``
(they are counted as terminal *skipped* work instead). Skipped was subtracted
twice, so a resumed run under-reported its total - and once a checkpoint
covered more than half the corpus the total went *negative*, which:

* printed a negative "N remaining" to the operator,
* fed a negative count to ``add_discovered``, and
* made the "all files already processed" branch unreachable, because a
  negative number is truthy.

These tests pin the corrected behaviour.
"""

from __future__ import annotations

import os

import pytest

TOTAL = 20


class _StubPipeline:
    """Stands in for StoragePipeline (no database in unit tests)."""

    def __init__(self):
        self.stored = []

    def _store_file_sync(self, file_info, result, source_name=None, side_name=None):
        self.stored.append(file_info.get("path"))
        return len(self.stored)

    def get_statistics(self):
        return {"files_stored": len(self.stored)}

    def __getattr__(self, name):
        return lambda *a, **k: None


def _build_reader(folder, checkpoint, workers=1):
    from pipeline.integrated_reader import IntegratedFileReader

    reader = IntegratedFileReader(
        enable_storage=False,      # no DB init; the pipeline is stubbed below
        storage_source="SRC",
        storage_side="SIDE",
        checkpoint_file=checkpoint,
        max_workers=workers,
    )
    reader.enable_storage = True   # take the normal "stored" branch
    reader.storage_pipeline = _StubPipeline()
    return reader


def _make_corpus(root, total):
    os.makedirs(root, exist_ok=True)
    for i in range(total):
        (root / f"f{i:03d}.txt").write_text(f"content {i}\n")
    return sorted(str(p) for p in root.glob("*.txt"))


def _pre_mark(folder, checkpoint, paths, count):
    """Record ``count`` files as already processed, then close cleanly."""
    from core.checkpoint_manager import CheckpointManager
    from core.file_utils import get_standardized_metadata

    mgr = CheckpointManager(str(checkpoint), str(folder), "SRC", "SIDE")
    for path in paths[:count]:
        mgr.mark_processed(get_standardized_metadata(path, compute_hash=False))
    mgr.finalize()
    return mgr


def test_resumed_run_reports_the_full_corpus(tmp_path, capsys):
    """A run resuming over 8 of 20 files reports 20 total, 12 remaining."""
    folder = tmp_path / "corpus"
    paths = _make_corpus(folder, TOTAL)
    checkpoint = tmp_path / "ck" / "test.json"
    done = 8
    _pre_mark(folder, checkpoint, paths, done)

    reader = _build_reader(folder, checkpoint)
    results = reader.process_folder(str(folder)) or []
    capsys.readouterr()

    assert len(results) == TOTAL - done, "checkpointed files must not be redone"

    snap = reader.progress_ledger.snapshot()
    # The whole corpus is the denominator: work done earlier is skipped work,
    # not work that vanished.
    assert snap["total_files"] == TOTAL
    assert snap["files_skipped"] == done
    assert snap["files_pending"] == 0
    assert snap["percent"] == 100


def test_resumed_run_reports_remaining_files_correctly(tmp_path, capsys):
    """The operator-facing message must not under-report what is left."""
    folder = tmp_path / "corpus"
    paths = _make_corpus(folder, TOTAL)
    checkpoint = tmp_path / "ck" / "test.json"
    done = 8
    _pre_mark(folder, checkpoint, paths, done)

    reader = _build_reader(folder, checkpoint)
    reader.process_folder(str(folder))
    out = capsys.readouterr().out

    assert f"{done} files already processed" in out
    # Regression: this used to print "8 remaining" (20 - 8 - 8).
    assert f"{TOTAL - done} remaining" in out


def test_fully_processed_corpus_takes_the_completion_path(tmp_path, capsys):
    """When everything is checkpointed the run says so, instead of going
    negative and falling through into the processing phases."""
    folder = tmp_path / "corpus"
    paths = _make_corpus(folder, TOTAL)
    checkpoint = tmp_path / "ck" / "test.json"
    _pre_mark(folder, checkpoint, paths, TOTAL)

    reader = _build_reader(folder, checkpoint)
    results = reader.process_folder(str(folder)) or []
    out = capsys.readouterr().out

    assert results == []
    # Regression: the total went to -20, which is truthy, so this branch
    # could never run and the completion message was never printed.
    assert "All files already processed" in out
    assert reader.progress_ledger.snapshot()["total_files"] == TOTAL


def test_work_total_is_never_negative(tmp_path, capsys):
    """Guard the invariant directly: a resumed run's total is a real count."""
    folder = tmp_path / "corpus"
    paths = _make_corpus(folder, TOTAL)
    checkpoint = tmp_path / "ck" / "test.json"
    # More than half already done - the case that used to go negative.
    _pre_mark(folder, checkpoint, paths, int(TOTAL * 0.9))

    reader = _build_reader(folder, checkpoint)
    reader.process_folder(str(folder))
    capsys.readouterr()

    total = reader.progress_ledger.snapshot()["total_files"]
    assert total >= 0, f"work total went negative: {total}"
    assert total == TOTAL
