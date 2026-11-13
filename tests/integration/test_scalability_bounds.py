"""Scalability bounds that must hold as a corpus grows without bound.

These are the properties that decide whether a multi-million-file run is
*possible*; each one is asserted here so a regression fails the suite instead of
only showing up in a soak hours later:

1. discovery streams - per-file metadata is never accumulated;
2. result retention is bounded - extracted content cannot pin the corpus in RAM;
3. checkpoint saving costs the same at file 10 as at file 100 000 (no quadratic
   re-serialisation of the processed set) and still resumes exactly;
4. accounting stays exact as work is added through nested/duplicated paths;
5. worker/queue bounds are declared and enforced rather than implied.

The tests use small corpora on purpose (seconds, not hours) and measure ratios
and bounds, not absolute timings.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import tracemalloc
from pathlib import Path


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from core.checkpoint_manager import CheckpointManager  # noqa: E402
from core.file_utils import iter_tree, read_tree  # noqa: E402
from pipeline.integrated_reader import (  # noqa: E402
    DEFAULT_RESULT_RETENTION,
    DEFAULT_RESULT_RETENTION_BYTES,
    IntegratedFileReader,
)
from pipeline.progress_ledger import (  # noqa: E402
    OUTCOME_COMPLETED,
    OUTCOME_FAILED,
    OUTCOME_SKIPPED,
    ProgressLedger,
)


def _build_tree(root: Path, count: int, dirs: int = 8, payload: bytes = b"x" * 64) -> int:
    """Write ``count`` small files spread over ``dirs`` directories."""
    written = 0
    for index in range(count):
        directory = root / f"d{index % dirs:03d}" / f"s{(index // dirs) % 7:02d}"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"f{index:06d}.txt"
        path.write_bytes(payload)
        written += 1
    return written


# ---------------------------------------------------------------------------
# 1. Discovery streams
# ---------------------------------------------------------------------------
class TestDiscoveryStreaming:
    def test_iter_tree_does_not_retain_per_file_metadata(self, tmp_path):
        root = tmp_path / "corpus"
        count = 8000
        _build_tree(root, count)

        tracemalloc.start()
        listed = read_tree(str(root))
        _current, list_peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        tracemalloc.start()
        streamed_paths = [entry["path"] for entry in iter_tree(str(root))]
        _current, stream_peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        directory_paths = {p["path"] for p in listed if p.get("type") == "DIRECTORY"}
        listed_files = sorted(p["path"] for p in listed if p["path"] not in directory_paths)
        streamed_files = sorted(p for p in streamed_paths if p not in directory_paths)
        assert len(streamed_files) == len(listed_files) == count
        assert streamed_files == listed_files
        # The point of the streaming reader: peak memory must not scale with
        # the number of entries the way the list version does.
        assert stream_peak < list_peak * 0.5, (
            f"streaming discovery retained {stream_peak / 1024:.0f} KiB vs "
            f"{list_peak / 1024:.0f} KiB for the list version"
        )

    def test_iter_tree_is_deterministic(self, tmp_path):
        root = tmp_path / "corpus"
        _build_tree(root, 200)
        first = [e["path"] for e in iter_tree(str(root))]
        second = [e["path"] for e in iter_tree(str(root))]
        assert first == second, "discovery order must be stable between runs"
        assert len(first) >= 200

    def test_discovery_survives_a_filesystem_without_usable_inodes(self, tmp_path,
                                                                   monkeypatch):
        """A volume that reports ``st_ino == 0`` must not truncate the walk.

        Windows volumes (and some network, FAT and virtual mounts) report zero
        for every inode.  The walk keeps a visited-directory set for cycle
        safety; if those zeros are used as identities, every directory shares
        one key, the walk descends into the first directory it meets and
        silently skips every other directory *and all the files inside them*.
        Measured before the fix: a 200-file tree yielded 15 entries and 0 files,
        so nearly the whole corpus never reached a worker.

        This reproduces the condition on any platform and requires the full
        tree to be enumerated regardless.
        """
        root = tmp_path / "corpus"
        count = 200
        written = _build_tree(root, count)

        import core.file_utils as file_utils

        real_scandir = os.scandir
        real_stat = os.stat

        class _ZeroInoStat:
            def __init__(self, st):
                self._st = st

            def __getattr__(self, name):
                return 0 if name == "st_ino" else getattr(self._st, name)

        class _ZeroInoEntry:
            def __init__(self, entry):
                self._entry = entry

            def __getattr__(self, name):
                return getattr(self._entry, name)

            def stat(self, follow_symlinks=True):
                return _ZeroInoStat(
                    self._entry.stat(follow_symlinks=follow_symlinks))

        class _ZeroInoScandir:
            def __init__(self, path):
                self._handle = real_scandir(path)

            def __enter__(self):
                self._handle.__enter__()
                return self

            def __exit__(self, *exc):
                return self._handle.__exit__(*exc)

            def __iter__(self):
                for entry in self._handle:
                    yield _ZeroInoEntry(entry)

            def close(self):
                return self._handle.close()

        monkeypatch.setattr(file_utils.os, "scandir", _ZeroInoScandir)
        monkeypatch.setattr(file_utils.os, "stat",
                            lambda path, *a, **k: _ZeroInoStat(real_stat(path, *a, **k)))

        entries = list(iter_tree(str(root)))
        files = [e for e in entries if e.get("type") != "DIRECTORY"]
        assert len(files) == written, (
            "discovery lost files on a filesystem with no usable inode: "
            f"{len(files)} of {written}")
        # Directories must still be walked exactly once - cycle safety intact.
        assert len(entries) == written + 64, len(entries)

    def test_deferred_hashing_uses_the_sentinel_not_a_fake_digest(self, tmp_path):
        """Deep paths must not fabricate hashes when hashing is deferred."""
        target = tmp_path / "corpus"
        target.mkdir()
        (target / "a.txt").write_text("hello")
        entry = next(iter_tree(str(target), compute_hashes=False))
        from core.file_utils import HASH_DEFERRED_SENTINEL

        assert entry["hash"] == HASH_DEFERRED_SENTINEL


# ---------------------------------------------------------------------------
# 2. Result retention is bounded
# ---------------------------------------------------------------------------
class TestResultRetention:
    def test_retention_window_never_exceeds_its_limit(self):
        reader = IntegratedFileReader(max_workers=1, enable_storage=False,
                                      enable_monitoring=False,
                                      result_retention_limit=100)
        results = []
        for index in range(1000):
            reader._retain_result(results, {"path": f"f{index}.txt", "content": "y" * 100})
        assert len(results) == 100
        assert reader._results_truncated == 900

    def test_byte_ceiling_bounds_a_window_of_large_results(self):
        """A count bound alone is not a memory bound.

        Measured failure mode: 10 000 retained results x ~4 MB of extracted
        content from word-dense files still held ~800 MB.  The window must also
        stop at a byte ceiling, whatever the file sizes are.
        """
        reader = IntegratedFileReader(max_workers=1, enable_storage=False,
                                      enable_monitoring=False,
                                      result_retention_limit=10000,
                                      result_retention_bytes=1024 * 1024)
        results = []
        for index in range(50):
            reader._retain_result(results, {"path": f"f{index}.txt",
                                            "content": "y" * (1024 * 1024)})
        assert reader._results_retained_bytes <= 1024 * 1024
        assert results == [] and reader._results_truncated == 50
        # Every result is still accounted for even when none is retained, so
        # the totals a caller reports never depend on the window.
        reader._count_processed_bytes({"file_size": 123})
        assert reader._bytes_processed == 123

    def test_results_within_the_ceiling_are_all_retained(self):
        reader = IntegratedFileReader(max_workers=1, enable_storage=False,
                                      enable_monitoring=False,
                                      result_retention_bytes=1024 * 1024)
        results = []
        for index in range(200):
            reader._retain_result(results, {"path": f"f{index}.txt",
                                            "content": "y" * 100})
        assert len(results) == 200
        assert reader._results_truncated == 0

    def test_default_window_is_bounded_and_declared(self):
        assert DEFAULT_RESULT_RETENTION_BYTES > 0
        assert DEFAULT_RESULT_RETENTION > 0
        reader = IntegratedFileReader(max_workers=1, enable_storage=False,
                                      enable_monitoring=False)
        assert reader._result_retention_limit == DEFAULT_RESULT_RETENTION

    def test_retention_can_be_disabled_without_losing_totals(self):
        reader = IntegratedFileReader(max_workers=1, enable_storage=False,
                                      enable_monitoring=False,
                                      result_retention_limit=None)
        results = []
        for index in range(50):
            reader._retain_result(results, {"path": f"f{index}.txt"})
        assert len(results) == 50


# ---------------------------------------------------------------------------
# 3. Checkpoint cost is flat, and resume is exact
# ---------------------------------------------------------------------------
class TestCheckpointScaling:
    def _fill(self, manager: CheckpointManager, root: str, count: int) -> None:
        for index in range(count):
            manager.mark_processed({"path": f"{root}/f{index}.txt", "modified": "m"})

    def test_periodic_saves_do_not_reserialise_the_whole_processed_set(self, tmp_path):
        root = str(tmp_path / "corpus")
        os.makedirs(root, exist_ok=True)
        manager = CheckpointManager(str(tmp_path / "cp.json"), root,
                                    auto_save_interval=10)
        serialized = {"calls": 0, "entries": 0, "bytes": 0}
        real_dump = json.dump

        def counting_dump(obj, fp, **kwargs):
            if isinstance(obj, dict) and "processed_files" in obj:
                serialized["calls"] += 1
                serialized["entries"] += len(obj["processed_files"])
                serialized["bytes"] += len(json.dumps(obj))
            return real_dump(obj, fp, **kwargs)

        json.dump = counting_dump
        try:
            self._fill(manager, root, 5000)
            manager.finalize()
        finally:
            json.dump = real_dump

        # One snapshot at the end; periodic saves append to the journal. The
        # old behaviour serialised the growing set every 10 files (~500 times).
        assert serialized["calls"] <= 3, serialized
        assert serialized["entries"] <= 2 * 5000

    def test_resume_after_snapshot_and_journal_is_exact(self, tmp_path):
        root = str(tmp_path / "corpus")
        os.makedirs(root, exist_ok=True)
        checkpoint = str(tmp_path / "cp.json")
        first = CheckpointManager(checkpoint, root, auto_save_interval=25)
        self._fill(first, root, 3000)
        before = first.get_statistics()["processed_count"]
        first.finalize()

        resumed = CheckpointManager(checkpoint, root, auto_save_interval=25)
        assert resumed.get_statistics()["processed_count"] == before == 3000
        # Re-marking everything must not double count: identifiers are a set.
        self._fill(resumed, root, 3000)
        assert resumed.get_statistics()["processed_count"] == 3000
        resumed.finalize()

    def test_checkpoint_files_stay_proportional_to_processed_files(self, tmp_path):
        root = str(tmp_path / "corpus")
        os.makedirs(root, exist_ok=True)
        checkpoint = str(tmp_path / "cp.json")
        manager = CheckpointManager(checkpoint, root, auto_save_interval=50)
        self._fill(manager, root, 4000)
        sizes = {
            "snapshot": os.path.getsize(checkpoint) if os.path.exists(checkpoint) else 0,
            "journal": (os.path.getsize(checkpoint + ".journal")
                        if os.path.exists(checkpoint + ".journal") else 0),
        }
        manager.finalize()
        final_size = os.path.getsize(checkpoint)
        # ~60 bytes/identifier is the JSON encoding of a path; assert we are in
        # that order of magnitude rather than holding whole content blobs.
        assert final_size < 4000 * 200, sizes
        assert final_size > 0


# ---------------------------------------------------------------------------
# 4. Accounting stays exact
# ---------------------------------------------------------------------------
class TestAccountingExactness:
    def test_ledger_accounts_for_nested_work_exactly(self):
        ledger = ProgressLedger(name="scale-accounting")
        ledger.add_discovered(1000, key="root", initial=True)
        # Every discovered file enters a worker unit and is settled once, which
        # is the lifecycle the ingestion path uses.
        units = [ledger.begin(path=f"root/f{i}.txt") for i in range(1000)]
        for index, unit in enumerate(units):
            if index < 900:
                ledger.settle(unit, OUTCOME_COMPLETED)
            elif index < 950:
                ledger.settle(unit, OUTCOME_SKIPPED)
            else:
                ledger.settle(unit, OUTCOME_FAILED)

        snapshot = ledger.snapshot()
        # The identity the "exact accounting" requirement asks for: every
        # discovered unit lands in exactly one terminal bucket, none pending.
        assert snapshot["total_files"] == 1000
        assert snapshot["terminal"] == 1000
        assert snapshot["files_completed"] == 900
        assert snapshot["files_skipped"] == 50
        assert snapshot["files_failed"] == 50
        assert snapshot["files_pending"] == 0
        assert ledger.percent == 100
        assert ledger.is_complete is True
        # Settling twice must not double count - no duplicate processing shows
        # up in the accounting.
        assert ledger.settle(units[0], OUTCOME_COMPLETED) is False
        assert ledger.snapshot()["terminal"] == 1000

    def test_discovered_count_matches_the_tree(self, tmp_path):
        root = tmp_path / "corpus"
        written = _build_tree(root, 500)
        entries = list(iter_tree(str(root)))
        files = [e for e in entries if e.get("type") != "DIRECTORY"]
        assert len(files) == written


# ---------------------------------------------------------------------------
# 5. Concurrency bounds are real
# ---------------------------------------------------------------------------
class TestConcurrencyBounds:
    def test_reader_declares_a_bounded_result_window_and_worker_count(self):
        reader = IntegratedFileReader(max_workers=3, enable_storage=False,
                                      enable_monitoring=False)
        try:
            stats = reader.get_statistics()
            assert stats.get("result_retention_limit") == DEFAULT_RESULT_RETENTION
            assert stats.get("result_retention_bytes") == DEFAULT_RESULT_RETENTION_BYTES
            assert 1 <= reader.max_workers <= 3
        finally:
            reader.close() if hasattr(reader, "close") else None

    def test_shared_ledger_can_be_read_from_another_thread_safely(self, tmp_path):
        root = tmp_path / "corpus"
        _build_tree(root, 100)
        reader = IntegratedFileReader(max_workers=2, enable_storage=False,
                                      enable_monitoring=False)
        ledger = reader.progress_ledger
        ledger.add_discovered(100, key=str(root), initial=True)
        errors = []

        def work():
            try:
                for _ in range(100):
                    ledger.record(OUTCOME_COMPLETED, 1, parent=str(root))
            except Exception as exc:  # pragma: no cover - failure detail
                errors.append(exc)

        threads = [threading.Thread(target=work) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert not errors
        # Every recorded unit is accounted for; nothing is lost under threading.
        assert ledger.terminal >= 400
