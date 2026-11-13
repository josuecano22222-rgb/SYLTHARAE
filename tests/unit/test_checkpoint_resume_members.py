"""A restarted run must not re-process archive members it already stored.

Archive members are extracted into a folder on every run, so their mtime is
the extraction time and changes each time. The checkpoint used to key on that
mtime, so a restarted run treated every member as new. Members are now keyed by
their position inside the extraction folder and their size, and each container
gets its own stable checkpoint derived from the top-level checkpoint.
"""

from __future__ import annotations

import os
from pathlib import Path
import time

import pytest

from core import checkpoint_scope
from core.checkpoint_manager import CheckpointManager


@pytest.fixture(autouse=True)
def _clear_registry():
    checkpoint_scope.register_checkpoint_base("src", "side", None)
    yield
    checkpoint_scope.register_checkpoint_base("src", "side", None)


def _member(folder, name, data: bytes, mtime: float):
    """Write a member the way extraction does, then set the extraction-time mtime."""
    path = folder / name
    path.write_bytes(data)
    os.utime(path, (mtime, mtime))
    return {"path": str(path), "modified": f"{mtime}", "size_bytes": len(data)}


def test_member_identity_ignores_extraction_mtime(tmp_path):
    folder = tmp_path / "extracted" / "bundle__abc"
    folder.mkdir(parents=True)
    ckpt = tmp_path / "ck.json"

    first = CheckpointManager(str(ckpt), str(folder), "src", "side", stable_members=True)
    first.mark_processed(_member(folder, "m1.txt", b"hello", time.time() - 3600))
    first.finalize()

    # A later run extracts the same member again: new mtime, same content size.
    again = CheckpointManager(str(ckpt), str(folder), "src", "side", stable_members=True)
    member_again = _member(folder, "m1.txt", b"hello", time.time())
    assert again.is_processed(member_again)


def test_member_with_different_size_is_not_skipped(tmp_path):
    folder = tmp_path / "extracted" / "bundle__abc"
    folder.mkdir(parents=True)
    ckpt = tmp_path / "ck.json"

    first = CheckpointManager(str(ckpt), str(folder), "src", "side", stable_members=True)
    first.mark_processed(_member(folder, "m1.txt", b"hello", time.time()))
    first.finalize()

    again = CheckpointManager(str(ckpt), str(folder), "src", "side", stable_members=True)
    assert not again.is_processed(_member(folder, "m1.txt", b"hello, changed", time.time()))


def test_member_identity_survives_moving_extraction_folder(tmp_path):
    # Identity is relative to the extraction folder, so the same member under a
    # different absolute location (e.g. another user profile) still matches.
    a = tmp_path / "one" / "bundle__abc"
    b = tmp_path / "two" / "bundle__abc"
    a.mkdir(parents=True)
    b.mkdir(parents=True)
    ckpt = tmp_path / "ck.json"

    mgr_a = CheckpointManager(str(ckpt), str(a), "src", "side", stable_members=True)
    id_a = mgr_a._get_file_identifier(_member(a, "m1.txt", b"x", 1.0))
    mgr_b = CheckpointManager(str(ckpt), str(b), "src", "side", stable_members=True)
    id_b = mgr_b._get_file_identifier(_member(b, "m1.txt", b"x", 2.0))
    assert id_a == id_b


def test_top_level_identity_still_uses_mtime(tmp_path):
    # Top-level files keep path + mtime, so an edited file is processed again.
    folder = tmp_path / "in"
    folder.mkdir()
    ckpt = tmp_path / "ck.json"
    mgr = CheckpointManager(str(ckpt), str(folder), "src", "side")
    base = _member(folder, "a.txt", b"x", 1000.0)
    assert mgr._get_file_identifier(base) != mgr._get_file_identifier(
        dict(base, modified="2000.0")
    )


def test_member_checkpoint_is_derived_from_registered_base(tmp_path):
    base = tmp_path / "ckpt.json"
    container = tmp_path / "in" / "bundle.zip"
    container.parent.mkdir()
    container.write_bytes(b"zipdata")

    assert checkpoint_scope.member_checkpoint_file("src", "side", str(container)) is None

    checkpoint_scope.register_checkpoint_base("src", "side", str(base))
    derived = checkpoint_scope.member_checkpoint_file("src", "side", str(container))
    assert derived is not None
    assert derived.parent.name == "ckpt.members"
    assert derived == checkpoint_scope.member_checkpoint_file("src", "side", str(container))

    # A run with checkpointing off clears the entry.
    checkpoint_scope.register_checkpoint_base("src", "side", None)
    assert checkpoint_scope.member_checkpoint_file("src", "side", str(container)) is None


def test_container_identity_ignores_extraction_time_for_extracted_copies(tmp_path, monkeypatch):
    base = tmp_path / "extracted"
    copy = base / "bundle__abc" / "inner.zip"
    copy.parent.mkdir(parents=True)
    copy.write_bytes(b"12345")

    import core.path_utils as pu
    monkeypatch.setattr(pu, "get_extraction_base_folder", lambda *a, **k: base)

    first = checkpoint_scope.container_identity(str(copy))
    os.utime(copy, (1.0, 1.0))
    assert checkpoint_scope.container_identity(str(copy)) == first


def test_fresh_policy_clears_snapshot_journal_and_member_checkpoints(tmp_path, monkeypatch):
    """A 'fresh' run must not inherit marks from the journal or member checkpoints."""
    import core.app_paths as app_paths
    from services.ingesting.options import IngestionOptions, IngestionRequest
    from services.ingesting.service import IngestionService

    ckdir = tmp_path / "checkpoints"
    monkeypatch.setattr(app_paths, "get_checkpoints_dir", lambda: ckdir)
    folder = tmp_path / "in"
    folder.mkdir()

    def _request(policy):
        return IngestionRequest(path=str(folder), source="src", side="side",
                                options=IngestionOptions(checkpoint=policy))

    auto_path = Path(IngestionService._checkpoint_file(_request("auto")))
    auto_path.write_text("{}")
    journal = auto_path.with_suffix(auto_path.suffix + ".journal")
    journal.write_text("header\n")
    members = auto_path.with_name(auto_path.stem + ".members")
    members.mkdir()
    (members / "x.json").write_text("{}")

    # Default 'auto' keeps everything, so a restart resumes.
    IngestionService._checkpoint_file(_request("auto"))
    assert auto_path.exists() and journal.exists() and (members / "x.json").exists()

    fresh_path = IngestionService._checkpoint_file(_request("fresh"))
    assert fresh_path == str(auto_path)
    assert not auto_path.exists()
    assert not journal.exists()
    assert not members.exists()
