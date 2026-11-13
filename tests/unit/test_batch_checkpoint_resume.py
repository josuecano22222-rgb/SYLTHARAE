"""Batch (file-list) ingestions must survive a crash and resume on Retry.

A folder ingestion checkpoints itself, but an ingestion submitted as a *list*
of files - which is what ``batch_import`` builds - used to get no checkpoint at
all: ``_checkpoint_file()`` returned ``None`` whenever ``request.path`` was
falsy, and ``_run_file_list()`` never consulted a checkpoint. An interrupted
batch therefore restarted at file #1 every time Retry was clicked.

The tests below pin the behaviour that fixes that, and - just as importantly -
the invariants that make the fix safe:

* the checkpoint identity is stable for the same selection of files;
* the identifier computed when *checking* a file matches the one computed when
  *marking* it (a mismatch would silently skip nothing);
* ``checkpoint: off`` still means "no checkpoint".
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class _StubReader:
    """Stands in for ``IntegratedFileReader``.

    Only the contract ``_run_file_list`` and ``process_single_file`` rely on is
    implemented. ``process_single_file`` marks the checkpoint with metadata from
    ``get_standardized_metadata(path)`` - exactly as the real engine does - so
    the test fails if the two sides ever drift apart again.
    """

    def __init__(self, enable_storage=None, storage_source=None, storage_side=None,
                 max_workers=None, enable_monitoring=None, issue_registry=None,
                 checkpoint_file=None, crash_after=None):
        self.checkpoint_file = checkpoint_file
        self.checkpoint_manager = None
        self.crash_after = crash_after
        self.processed = 0
        from pipeline.progress_ledger import ProgressLedger

        self.progress_ledger = ProgressLedger()

    def _set_current(self, file_path=None, phase=None):
        self.progress_ledger.current_file = file_path

    def _notify_progress(self):
        pass

    def _control_requested(self):
        return False

    def process_single_file(self, file_path):
        from core.file_utils import get_standardized_metadata

        # Mirror the real engine's ledger bookkeeping: register one unit, open
        # it, settle it (see IntegratedFileReader.process_single_file).
        self.progress_ledger.add_discovered(1, key=f"single::{file_path}")
        unit = self.progress_ledger.begin(
            path=file_path, phase="Processing file", depth=0, parent=None
        )
        info = get_standardized_metadata(file_path)
        if self.checkpoint_manager:
            self.checkpoint_manager.mark_processed(info)
        self.processed += 1
        self.progress_ledger.settle(unit, "completed")
        if self.crash_after and self.processed >= self.crash_after:
            # Hard kill: no ``finally``, no atexit, no flushing of Python
            # buffers - the same guarantees a power loss or SIGKILL gives.
            os._exit(9)
        return {"path": file_path, "database_path_id": 1}


@pytest.fixture
def checkpoints_in_tmp(tmp_path, monkeypatch):
    """Point the checkpoint directory at a temp dir."""
    from core import app_paths

    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "appdata"))
    monkeypatch.setattr(app_paths, "_cached_root", None, raising=False)
    return tmp_path / "appdata" / "checkpoints"


def _make_corpus(tmp_path, count=40):
    folder = tmp_path / "corpus"
    folder.mkdir()
    paths = []
    for i in range(count):
        p = folder / f"f{i:03d}.txt"
        p.write_text(f"content {i}\n")
        paths.append(str(p))
    return paths


def _request(paths):
    from services.ingesting.options import IngestionRequest

    return IngestionRequest(file_paths=paths, source="SRC", side="SIDE")


# ---------------------------------------------------------------------------
# The bug itself
# ---------------------------------------------------------------------------

def test_file_list_gets_a_checkpoint(checkpoints_in_tmp, tmp_path):
    """A batch of files is checkpointed (previously: always None)."""
    from services.ingesting.service import IngestionService

    paths = _make_corpus(tmp_path, 10)
    result = IngestionService._checkpoint_file(_request(paths))
    assert result is not None, "file-list ingestion must not disable checkpointing"


def test_checkpoint_identity_is_stable_across_retry(checkpoints_in_tmp, tmp_path):
    """The identical selection reaches the identical checkpoint on Retry."""
    from services.ingesting.service import IngestionService

    paths = _make_corpus(tmp_path, 10)
    first = IngestionService._checkpoint_file(_request(paths))
    retried = IngestionService._checkpoint_file(_request(paths))
    assert first == retried


def test_checkpoint_identity_ignores_submission_order(checkpoints_in_tmp, tmp_path):
    """Re-ordering the same files must not invalidate the checkpoint."""
    from services.ingesting.service import IngestionService

    paths = _make_corpus(tmp_path, 10)
    first = IngestionService._checkpoint_file(_request(paths))
    shuffled = IngestionService._checkpoint_file(_request(list(reversed(paths))))
    assert first == shuffled


def test_a_different_batch_is_a_different_checkpoint(checkpoints_in_tmp, tmp_path):
    from services.ingesting.service import IngestionService

    paths = _make_corpus(tmp_path, 10)
    assert (IngestionService._checkpoint_file(_request(paths))
            != IngestionService._checkpoint_file(_request(paths[:5])))


def test_policy_off_still_disables_checkpointing(checkpoints_in_tmp, tmp_path):
    from services.ingesting.options import IngestionOptions, IngestionRequest
    from services.ingesting.service import IngestionService

    paths = _make_corpus(tmp_path, 10)
    req = IngestionRequest(file_paths=paths, source="SRC", side="SIDE",
                           options=IngestionOptions(checkpoint="off"))
    assert IngestionService._checkpoint_file(req) is None


def test_folder_ingestion_checkpoint_is_unchanged(checkpoints_in_tmp, tmp_path):
    """Guard the folder path: it must keep hashing the root folder."""
    from services.ingesting.options import IngestionRequest
    from services.ingesting.service import IngestionService

    paths = _make_corpus(tmp_path, 5)
    folder = os.path.dirname(paths[0])
    folder_ckpt = IngestionService._checkpoint_file(
        IngestionRequest(path=folder, source="SRC", side="SIDE"))
    assert folder_ckpt is not None
    # A folder run and a batch of the same files are different work sets.
    assert folder_ckpt != IngestionService._checkpoint_file(_request(paths))


# ---------------------------------------------------------------------------
# The invariant that makes marking and checking agree
# ---------------------------------------------------------------------------

def test_check_side_and_mark_side_identifiers_match(tmp_path):
    """The skip check and the engine's mark must hash to the same value.

    ``_run_file_list`` builds metadata with ``compute_hash=False`` (cheap, as
    ``iter_tree`` does); ``process_single_file`` builds it with the default. If
    those two ever produce different checkpoint identifiers, every retry would
    skip nothing and the bug would silently return.
    """
    from core.checkpoint_manager import CheckpointManager
    from core.file_utils import get_standardized_metadata

    paths = _make_corpus(tmp_path, 5)
    mgr = CheckpointManager(str(tmp_path / "ck.json"), os.path.dirname(paths[0]),
                            "SRC", "SIDE")
    for path in paths:
        check_side = get_standardized_metadata(path, compute_hash=False)
        mark_side = get_standardized_metadata(path)
        assert mgr._get_file_identifier(check_side) == mgr._get_file_identifier(mark_side)


# ---------------------------------------------------------------------------
# End to end: crash, then Retry
# ---------------------------------------------------------------------------

def test_batch_resumes_after_hard_kill(checkpoints_in_tmp, tmp_path):
    """The reported scenario: process a batch, die mid-way, click Retry.

    The first run happens in a subprocess that exits with ``os._exit`` after N
    files, so no cleanup code runs at all. The retry must skip exactly those N
    files instead of redoing them.
    """
    from services.ingesting.service import IngestionService

    total, crash_after = 40, 25
    paths = _make_corpus(tmp_path, total)
    paths_file = tmp_path / "paths.json"
    paths_file.write_text(json.dumps(paths))

    child = textwrap.dedent(
        """
        import json, os, sys
        sys.path.insert(0, {repo!r})
        from services.ingesting.service import IngestionService
        from services.ingesting.options import IngestionRequest
        from tests.unit.test_batch_checkpoint_resume import _StubReader

        paths = json.load(open({paths!r}))
        req = IngestionRequest(file_paths=paths, source="SRC", side="SIDE")
        svc = IngestionService()
        reader = svc._make_reader(_StubReader, req)
        reader.crash_after = {crash_after}
        svc._run_file_list(reader, req)
        """
    ).format(repo=REPO_ROOT, paths=str(paths_file), crash_after=crash_after)

    env = dict(os.environ, APP_DATA_DIR=str(tmp_path / "appdata"))
    proc = subprocess.run([sys.executable, "-c", child], env=env,
                          capture_output=True, text=True, timeout=300)
    assert proc.returncode == 9, f"child should die mid-run, got {proc.returncode}: {proc.stderr}"

    # --- Retry: a brand-new job, in this process. ---
    reader = IngestionService()._make_reader(_StubReader, _request(paths))
    assert reader.checkpoint_file is not None
    IngestionService()._run_file_list(reader, _request(paths))

    assert reader.processed == total - crash_after, (
        f"retry redid completed work: processed {reader.processed}, "
        f"expected {total - crash_after} (of {total}, {crash_after} already done)"
    )


def test_batch_resume_progress_accounting_is_honest(checkpoints_in_tmp, tmp_path):
    """A resumed batch settles every unit, so the bar can still reach 100%.

    Note: ``files_total`` is *not* asserted here. ``_run_file_list`` registers
    the batch up front and ``process_single_file`` registers each file again,
    which inflates the denominator on every file-list run - a pre-existing
    issue, independent of checkpointing, and not what this test is about.
    """
    from pipeline.progress_ledger import OUTCOME_SKIPPED
    from services.ingesting.service import IngestionService

    total, first_run = 40, 15
    paths = _make_corpus(tmp_path, total)

    svc = IngestionService()

    # Run 1: process the first slice only (as an interrupted run would).
    reader1 = svc._make_reader(_StubReader, _request(paths))
    svc._run_file_list(reader1, _request(paths[:first_run]))
    assert reader1.processed == first_run

    # Run 2: the retry over the whole batch.
    reader2 = svc._make_reader(_StubReader, _request(paths))
    svc._run_file_list(reader2, _request(paths))

    # The fix: completed work is not redone.
    assert reader2.processed == total - first_run

    snapshot = reader2.progress_ledger.snapshot()
    # The skipped files were recorded as terminal work, not left pending.
    assert reader2.progress_ledger.counts[OUTCOME_SKIPPED] >= first_run
    # Nothing is stranded, so the run can report an honest 100%.
    assert snapshot["files_pending"] == 0
    assert snapshot["percent"] == 100
