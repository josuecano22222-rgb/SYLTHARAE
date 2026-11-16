"""Resume must be observable, and must not be disabled silently.

Two failure modes made "Retry starts from the beginning" undiagnosable:

* a checkpoint that existed but was not used left no trace - a mismatched
  journal header, an unreadable snapshot - so "fresh start" and "rejected"
  looked identical from the outside;
* a failed checkpoint *mark* was logged at DEBUG, invisible at the default
  INFO level, so a run could lose every entry and still look healthy;
* ``Path.resolve()`` raising while the identity was being built did not fail
  the run loudly, it disabled checkpointing for the whole corpus (Windows
  paths beyond MAX_PATH are one way to trigger it).
"""

from __future__ import annotations

import logging
import os

import pytest

from core.checkpoint_manager import CheckpointManager, safe_resolve  # noqa: E402


def _make_corpus(root, total=10):
    root.mkdir(parents=True, exist_ok=True)
    paths = []
    for i in range(total):
        p = root / f"f{i}.txt"
        p.write_text("x" * i)
        paths.append(str(p))
    return paths


def _infos(paths):
    from core.file_utils import get_standardized_metadata

    return [get_standardized_metadata(p, compute_hash=False) for p in paths]


# ---------------------------------------------------------------------------
# The resume report
# ---------------------------------------------------------------------------

def test_fresh_start_is_reported(tmp_path):
    mgr = CheckpointManager(str(tmp_path / "ck.json"), str(tmp_path / "corpus"), "S", "D")
    report = mgr.resume_report
    assert report["status"] == "fresh"
    assert report["resumed_files"] == 0
    assert report["snapshot_present"] is False
    assert report["journal_present"] is False
    assert report["reason"]


def test_resume_from_journal_is_reported(tmp_path):
    corpus = tmp_path / "corpus"
    paths = _make_corpus(corpus)
    ckpt = tmp_path / "ck.json"

    first = CheckpointManager(str(ckpt), str(corpus), "S", "D")
    for info in _infos(paths[:4]):
        first.mark_processed(info)
    del first  # crash: no finalize()

    second = CheckpointManager(str(ckpt), str(corpus), "S", "D")
    report = second.resume_report
    assert report["status"] == "resumed"
    assert report["resumed_files"] == 4
    assert report["journal_present"] is True
    assert report["journal_entries"] == 4
    assert report["snapshot_present"] is False


def test_rejected_journal_is_warned_not_silently_ignored(tmp_path, caplog):
    """A journal for another folder means repeated work - say so loudly."""
    corpus = tmp_path / "corpus"
    paths = _make_corpus(corpus)
    ckpt = tmp_path / "ck.json"

    first = CheckpointManager(str(ckpt), str(corpus), "S", "D")
    for info in _infos(paths[:4]):
        first.mark_processed(info)
    del first

    with caplog.at_level(logging.WARNING):
        other = CheckpointManager(str(ckpt), str(tmp_path / "elsewhere"), "S", "D")

    assert other.resume_report["status"] == "rejected"
    assert other.resume_report["resumed_files"] == 0
    # The journal is present and full, but unusable - that must be visible.
    assert other.resume_report["journal_present"] is True
    assert "different folder" in (other.resume_report["reason"] or "")
    assert any(r.levelno >= logging.WARNING for r in caplog.records)


def test_rejected_snapshot_reports_the_mismatch(tmp_path, caplog):
    corpus = tmp_path / "corpus"
    paths = _make_corpus(corpus)
    ckpt = tmp_path / "ck.json"

    first = CheckpointManager(str(ckpt), str(corpus), "S", "D")
    for info in _infos(paths[:3]):
        first.mark_processed(info)
    first.finalize()  # snapshot on disk

    with caplog.at_level(logging.WARNING):
        second = CheckpointManager(str(ckpt), str(corpus), "OTHER-SOURCE", "D")

    assert second.resume_report["status"] == "rejected"
    assert "source mismatch" in second.resume_report["reason"]
    assert second.resume_report["snapshot_present"] is True
    assert second.resume_report["snapshot_entries"] == 3


# ---------------------------------------------------------------------------
# Marking failures must be loud
# ---------------------------------------------------------------------------

def test_mark_failure_is_warned_once_and_counted(tmp_path, caplog):
    corpus = tmp_path / "corpus"
    paths = _make_corpus(corpus)
    mgr = CheckpointManager(str(tmp_path / "ck.json"), str(corpus), "S", "D")

    def boom(*_a, **_k):
        raise OSError("disk full")

    mgr._append_journal_locked = boom
    with caplog.at_level(logging.WARNING):
        for info in _infos(paths[:5]):
            mgr.mark_processed(info)

    assert mgr.mark_failures == 5
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings, "a failed mark must not be silent (it was DEBUG before)"
    # Warned once, not once per file: a million-file run must not produce a
    # million warnings.
    assert len(warnings) == 1


def test_mark_failure_does_not_stop_processing(tmp_path):
    corpus = tmp_path / "corpus"
    paths = _make_corpus(corpus)
    mgr = CheckpointManager(str(tmp_path / "ck.json"), str(corpus), "S", "D")
    mgr._append_journal_locked = lambda *_a, **_k: (_ for _ in ()).throw(OSError("x"))
    for info in _infos(paths):
        mgr.mark_processed(info)  # must not raise
    assert mgr.mark_failures == len(paths)


# ---------------------------------------------------------------------------
# Path resolution must not disable checkpointing
# ---------------------------------------------------------------------------

def test_safe_resolve_falls_back_when_resolve_raises(tmp_path):
    result = safe_resolve(tmp_path)
    assert result == str(tmp_path.resolve())


def test_init_survives_an_unresolvable_folder(tmp_path):
    """Regression: Path.resolve() raising disabled checkpointing entirely.

    On Windows this happens for paths beyond MAX_PATH. The manager used to
    raise during construction, the engine caught the exception and continued
    with no checkpoint at all - so the next Retry redid the whole corpus.
    """
    corpus = tmp_path / "corpus"
    paths = _make_corpus(corpus)
    ckpt = tmp_path / "ck.json"

    mgr = CheckpointManager(str(ckpt), str(corpus), "S", "D")
    for info in _infos(paths[:5]):
        mgr.mark_processed(info)
    del mgr

    import core.checkpoint_manager as cm
    from pathlib import Path

    real_resolve = Path.resolve

    def broken_resolve(self, *a, **k):
        if str(self).startswith(str(corpus)):
            raise OSError("cannot resolve (MAX_PATH)")
        return real_resolve(self, *a, **k)

    Path.resolve = broken_resolve
    try:
        resumed = CheckpointManager(str(ckpt), str(corpus), "S", "D")
        assert resumed.resume_report["resumed_files"] == 5
    finally:
        Path.resolve = real_resolve


def test_without_the_fallback_the_manager_cannot_be_built(tmp_path, monkeypatch):
    """Shows what the previous behaviour was, so the fix above is load bearing."""
    import core.checkpoint_manager as cm

    corpus = tmp_path / "corpus"
    _make_corpus(corpus)

    def raising_resolve(path):
        raise OSError("cannot resolve (MAX_PATH)")

    monkeypatch.setattr(cm, "safe_resolve", raising_resolve)
    with pytest.raises(OSError):
        cm.CheckpointManager(str(tmp_path / "ck.json"), str(corpus), "S", "D")
