"""When the upload volume fills up, say so - do not answer 500.

Round-10 disk-full drill: a tmpfs mounted at ``APP_DATA_DIR`` and filled to 100%
turned ``POST /api/input/uploads/chunked/start`` into

    {"correlation_id": "ERR-20261024-000002",
     "error": "An internal error occurred"}       HTTP 500

with an ``OSError`` (ENOSPC) only in the server log. Staging is a plain
filesystem operation, so a storage problem is an expected, reportable
condition - not an internal error. The routes now answer ``507`` with a code the
page can show.

The tests force the same failure class deterministically by making the staging
directory read-only: ``EACCES`` today, ``ENOSPC`` on a full volume, one code
path.
"""

from __future__ import annotations

import os
import pathlib
import sys

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

pytestmark = pytest.mark.integration

FULL_DISK_ANSWER = "STORAGE_UNAVAILABLE"


@pytest.fixture()
def full_uploads(tmp_path, monkeypatch):
    """Point staging at a small tree we can make unwritable, then fill it."""
    from Api.routes import operations_api

    root = tmp_path / "uploads"
    root.mkdir()
    monkeypatch.setattr(operations_api, "_staged_uploads_dir", lambda: root)
    monkeypatch.setattr(operations_api, "_chunked_root", lambda: root / "chunked")
    monkeypatch.setattr(
        operations_api, "_chunked_session_dir",
        lambda upload_id: operations_api._chunked_root() / upload_id,
    )
    os.chmod(root, 0o555)
    try:
        yield root
    finally:
        os.chmod(root, 0o755)


def test_starting_a_chunked_upload_on_a_full_volume_is_a_507(app, admin_client, full_uploads):
    response = admin_client.post(
        "/api/input/uploads/chunked/start",
        json={"filename": "full.txt", "size": 3_000_000},
    )
    body = response.get_json()
    assert response.status_code == 507, response.get_data(as_text=True)
    assert body["error"]["code"] == FULL_DISK_ANSWER
    assert "storage" in body["error"]["message"].lower()
    # No correlation id: this is a reported condition, not an unhandled error.
    assert "correlation_id" not in body


def test_a_chunk_that_cannot_be_stored_is_a_507_not_a_500(app, admin_client, full_uploads):
    from Api.routes import operations_api

    # A session that exists (as it would after a successful /start) whose
    # directory has since become unwritable: exactly the full-volume case.
    upload_id = "a" * 32
    session_dir = full_uploads / "chunked" / upload_id
    os.chmod(full_uploads, 0o755)
    session_dir.mkdir(parents=True)
    (session_dir / "session.json").write_text(
        '{"filename": "late.txt", "size": 32, "sha256": "", "chunk_size": 16,'
        ' "total_chunks": 2, "created_at": "2026-10-24T00:00:00+00:00"}',
        encoding="utf-8",
    )
    os.chmod(session_dir, 0o555)

    response = admin_client.post(
        f"/api/input/uploads/chunked/{upload_id}/chunk/0",
        data={"chunk": (__import__("io").BytesIO(b"x" * 16), "chunk")},
        content_type="multipart/form-data",
    )
    body = response.get_json()
    assert response.status_code == 507, response.get_data(as_text=True)
    assert body["error"]["code"] == FULL_DISK_ANSWER
    assert "correlation_id" not in body
    # A half-written chunk must never be left behind for the assembler.
    assert not (session_dir / "chunk_0.part").exists()
    assert not (session_dir / "chunk_0").exists()
