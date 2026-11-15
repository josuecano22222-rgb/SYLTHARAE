"""The multiprocessing pool path is unreachable from every job entry point.

``_process_with_pool`` ships ``_process_file_worker`` to separate processes, so
each worker holds a *copy* of the CheckpointManager: marks made there are not
reliably reflected in the parent, and concurrent appends to a single journal
can interleave. If that path were live it would be a strong candidate for
"Retry starts from the beginning".

These tests establish, at runtime, that no entry point can reach it. The AST
scan is the regression guard: if anyone ever wires ``use_multiprocessing``
through a config key, a CLI flag or a settings reference, this fails and the
checkpoint-correctness work for the pool path becomes mandatory rather than
deferred.
"""

from __future__ import annotations

import ast
import os
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SKIP_DIRS = {".git", "__pycache__", "node_modules", "offline-bundle", "test-doc"}


def _python_files():
    for dirpath, dirnames, filenames in os.walk(REPO_ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            if name.endswith(".py"):
                yield pathlib.Path(dirpath) / name


# ---------------------------------------------------------------------------
# Runtime: the service never builds a multiprocessing reader
# ---------------------------------------------------------------------------

def test_service_reader_never_uses_multiprocessing(tmp_path, monkeypatch):
    """_make_reader is the single entry point for web jobs and all job types."""
    from services.ingesting.options import IngestionOptions, IngestionRequest
    from services.ingesting.service import IngestionService

    monkeypatch.setenv("INGESTION_ROOTS", str(tmp_path))
    folder = tmp_path / "corpus"
    folder.mkdir()
    (folder / "a.txt").write_text("x")

    for workers in (0, 1, 2, 8, 16):
        request = IngestionRequest(
            path=str(folder), source="SRC", side="SIDE",
            options=IngestionOptions(max_workers=workers),
        )
        reader = IngestionService()._make_reader(_RecordingReader, request)
        assert reader.use_multiprocessing is False, (
            f"max_workers={workers} produced a multiprocessing reader"
        )


def test_run_batch_cannot_reach_the_pool(monkeypatch):
    """With use_multiprocessing False, _run_batch must dispatch to threads."""
    from pipeline.integrated_reader import IntegratedFileReader

    reader = IntegratedFileReader(
        enable_storage=False, max_workers=8,
        storage_source="SRC", storage_side="SIDE",
    )
    assert reader.use_multiprocessing is False

    called = []
    monkeypatch.setattr(IntegratedFileReader, "_process_with_pool",
                        lambda self, files: (called.append("pool"), [])[1])
    monkeypatch.setattr(IntegratedFileReader, "_process_with_threads",
                        lambda self, files: (called.append("threads"), [])[1])

    reader._run_batch([{"path": "/nonexistent/a.txt"}])
    assert called == ["threads"], "the pool path must not be reachable"


# ---------------------------------------------------------------------------
# Source-level guard: nothing anywhere passes use_multiprocessing
# ---------------------------------------------------------------------------

def test_no_call_site_enables_multiprocessing():
    """Regression guard: the pool stays dead until it is made checkpoint-safe."""
    offenders = []
    for path in _python_files():
        if path.parts[-1].startswith("test_"):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for kw in node.keywords:
                if kw.arg == "use_multiprocessing":
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
            # **kwargs dicts that could smuggle the flag in.
            for arg in node.args:
                if isinstance(arg, ast.Dict):
                    for key in arg.keys:
                        if isinstance(key, ast.Constant) and key.value == "use_multiprocessing":
                            offenders.append(
                                f"{path.relative_to(REPO_ROOT)}:{node.lineno} (dict)"
                            )
    assert not offenders, (
        "use_multiprocessing is passed somewhere; the pool path is not "
        "checkpoint-correct and must be fixed (mark in the parent from worker "
        "results) before it is enabled: " + ", ".join(offenders)
    )


def test_no_config_key_or_cli_flag_for_multiprocessing():
    """There is no setting, env var or flag that could switch it on."""
    import re

    hits = []
    for pattern in ("*.json", "*.bat", "*.example", "*.toml", "*.cfg"):
        for path in REPO_ROOT.rglob(pattern):
            if any(part in SKIP_DIRS for part in path.parts):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if re.search(r"use_multiprocessing|use_multiproc", text, re.I):
                hits.append(str(path.relative_to(REPO_ROOT)))
    for name in (".env.example",):
        candidate = REPO_ROOT / name
        if candidate.exists():
            text = candidate.read_text(encoding="utf-8", errors="ignore")
            if re.search(r"use_multiprocessing|use_multiproc", text, re.I):
                hits.append(name)
    assert not hits, f"multiprocessing can be configured via: {hits}"


# ---------------------------------------------------------------------------
# Stand-in reader that records how it was constructed
# ---------------------------------------------------------------------------

class _RecordingReader:
    """Mirrors the constructor surface _make_reader inspects."""

    def __init__(self, enable_storage=None, storage_source=None, storage_side=None,
                 max_workers=None, enable_monitoring=None, issue_registry=None,
                 checkpoint_file=None, use_multiprocessing=False, **kwargs):
        self.max_workers = max_workers or 0
        self.enable_storage = enable_storage
        self.storage_source = storage_source
        self.storage_side = storage_side
        self.checkpoint_file = checkpoint_file
        self.checkpoint_manager = None
        self.use_multiprocessing = bool(use_multiprocessing)
        self.calls = []

    def _set_current(self, file_path=None, phase=None):
        pass

    def _notify_progress(self):
        pass

    def _control_requested(self):
        return False

    def process_folder(self, folder):
        return []
