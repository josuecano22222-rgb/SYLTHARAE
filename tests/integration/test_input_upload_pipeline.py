"""Integration: the ingestion page's staging API, end to end over HTTP.

The consolidation turned the upload surface into one pipeline, and large files
- which the single-request endpoint cannot carry - into a real chunked staging
API. Before this, the browser half of that feature posted to
``/upload/chunked/complete``, an endpoint that did not exist: every large-file
upload failed at the end of the transfer. These tests exercise the API as the
page uses it, including the failure paths, because "it uploaded" is not the
claim being made - the claim is that what lands on disk is exactly what the
operator selected, or nothing lands at all.

Nothing here starts an ingestion job: staging is deliberately separate from
processing, and the job API has its own tests.
"""

from __future__ import annotations

import hashlib
import io
import json
import pathlib
import shutil
import sys
import uuid

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

pytestmark = pytest.mark.integration


@pytest.fixture()
def ops_api():
    """The module holding the upload routes and their limits."""
    from Api.routes import operations_api

    return operations_api


@pytest.fixture()
def staged_cleanup(ops_api):
    """Track staged/upload directories this test creates and remove them.

    Staging lives under APP_DATA_DIR; the test database is disposable but the
    filesystem is the developer's checkout, so the test cleans up after itself.
    """
    tracked = []
    yield tracked
    for path in tracked:
        shutil.rmtree(path, ignore_errors=True)


def _tidy(entry: str, staged_cleanup):
    """Remove the batch directory of a staged path, keeping the tree honest."""
    path = pathlib.Path(entry)
    for parent in path.parents:
        if parent.name == "uploads":
            break
        batch = parent
    else:  # pragma: no cover - defensive
        return
    staged_cleanup.append(batch)


# ---------------------------------------------------------------------------
# Direct upload (small files, kept in one request)
# ---------------------------------------------------------------------------


def test_folder_upload_keeps_structure_and_never_overwrites(
    app, admin_client, staged_cleanup
):
    """A folder selection keeps its paths; two same-named files both survive."""
    payload = {
        "files": [
            (io.BytesIO(b"first"), "report.txt"),
            (io.BytesIO(b"second"), "report.txt"),
            (io.BytesIO(b"deep"), "notes.txt"),
        ],
        "relative_paths": json.dumps([
            "batch/report.txt", "batch/report.txt", "batch/sub/deep/notes.txt",
        ]),
    }
    resp = admin_client.post(
        "/api/input/uploads", data=payload, content_type="multipart/form-data",
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["success"] is True

    staged = body["staged"]
    assert len(staged) == 3
    names = sorted(entry["name"] for entry in staged)
    _tidy(staged[0]["path"], staged_cleanup)

    # Folder structure preserved, nesting preserved, duplicate name preserved
    # under a different final name instead of being overwritten.
    assert "batch/sub/deep/notes.txt" in names
    assert sum(1 for name in names if name.startswith("batch/report")) == 2

    report_paths = [entry["path"] for entry in staged if entry["name"].startswith("batch/report")]
    contents = sorted(pathlib.Path(p).read_bytes() for p in report_paths)
    assert contents == [b"first", b"second"]

    deep = next(entry for entry in staged if entry["name"].endswith("notes.txt"))
    assert pathlib.Path(deep["path"]).read_bytes() == b"deep"


def test_a_single_file_needs_no_relative_path(app, admin_client, staged_cleanup):
    """Choosing one file sends the file and nothing else."""
    resp = admin_client.post(
        "/api/input/uploads",
        data={"files": [(io.BytesIO(b"just one"), "notes.txt")]},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    staged = resp.get_json()["staged"]
    _tidy(staged[0]["path"], staged_cleanup)
    assert [entry["name"] for entry in staged] == ["notes.txt"]
    assert pathlib.Path(staged[0]["path"]).read_bytes() == b"just one"


def test_folder_selection_from_windows_keeps_the_tree(
    app, admin_client, staged_cleanup
):
    """A Windows folder pick sends either separator, or a drive-qualified path.

    All three are the operator's own selection; the stored tree is the tree
    they chose, minus the parts that are the filesystem's (the drive) rather
    than theirs.
    """
    payload = {
        "files": [
            (io.BytesIO(b"a"), "report.txt"),
            (io.BytesIO(b"b"), "page1.jpg"),
            (io.BytesIO(b"c"), "deep.txt"),
        ],
        "relative_paths": json.dumps([
            "Evidence\\2026\\Case 1\\report.txt",   # backslashes (Windows)
            "Evidence/2026/Case 1/page1.jpg",         # forward slashes (all OSes)
            r"C:\Evidence\2026\Case 1\sub\deep.txt",  # drive-qualified
        ]),
    }
    resp = admin_client.post(
        "/api/input/uploads", data=payload, content_type="multipart/form-data",
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    staged = resp.get_json()["staged"]
    _tidy(staged[0]["path"], staged_cleanup)

    names = sorted(entry["name"] for entry in staged)
    assert names == [
        "Evidence/2026/Case 1/page1.jpg",
        "Evidence/2026/Case 1/report.txt",
        "Evidence/2026/Case 1/sub/deep.txt",
    ]
    # Every file landed one batch directory below the staging root.
    for entry in staged:
        path = pathlib.Path(entry["path"])
        assert _staging_root() in path.parents
        assert path.is_file()


def test_windows_awkward_names_are_staged_under_writable_names(
    app, admin_client, staged_cleanup
):
    """Reserved device names and trailing dots are defused, never dropped.

    A folder collected on macOS or Linux can contain names Windows refuses;
    the files must still arrive, and the response must say what they were
    stored as (the page shows it next to the file).
    """
    payload = {
        "files": [
            (io.BytesIO(b"1"), "CON.txt"),
            (io.BytesIO(b"2"), "notes."),
            (io.BytesIO(b"3"), ".env"),
        ],
        "relative_paths": json.dumps(["Case/NUL/CON.txt", "Case/notes.", "Case/.env"]),
    }
    resp = admin_client.post(
        "/api/input/uploads", data=payload, content_type="multipart/form-data",
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    staged = resp.get_json()["staged"]
    _tidy(staged[0]["path"], staged_cleanup)

    names = sorted(entry["name"] for entry in staged)
    assert names == ["Case/.env", "Case/_NUL/_CON.txt", "Case/notes"]
    for entry in staged:
        assert pathlib.Path(entry["path"]).is_file()


def test_one_impossible_file_does_not_sink_the_selection(
    app, admin_client, staged_cleanup
):
    """Per-file isolation: the good files stage, the bad one is reported."""
    payload = {
        "files": [
            (io.BytesIO(b"good"), "good.txt"),
            (io.BytesIO(b"bad"), "bad.txt"),
        ],
        # The second name is not a name a filesystem can hold (NUL byte).
        "relative_paths": json.dumps(["good.txt", "bad\x00.txt"]),
    }
    resp = admin_client.post(
        "/api/input/uploads", data=payload, content_type="multipart/form-data",
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    body = resp.get_json()
    staged = body["staged"]
    _tidy(staged[0]["path"], staged_cleanup)

    assert [entry["name"] for entry in staged] == ["good.txt"]
    assert pathlib.Path(staged[0]["path"]).read_bytes() == b"good"
    assert len(body["failed"]) == 1
    assert body["failed"][0]["code"] == "BAD_NAME"


def test_a_batch_where_everything_fails_reports_the_reason(app, admin_client):
    payload = {
        "files": [(io.BytesIO(b"x"), "x.txt")],
        "relative_paths": json.dumps(["bad\x00.txt"]),
    }
    resp = admin_client.post(
        "/api/input/uploads", data=payload, content_type="multipart/form-data",
    )
    assert resp.status_code == 400
    error = resp.get_json()["error"]
    assert error["code"] == "BAD_NAME"
    assert error["details"]["failed"][0]["name"] == "bad\x00.txt"


def test_a_name_collision_costs_one_file_not_the_batch(
    app, admin_client, staged_cleanup
):
    """`Case/a.txt` (file) and `Case/a.txt/inner.txt` (needs a.txt to be a
    folder) in one selection: the impossible one is reported by name and the
    others still stage - never a 500 that loses everything the operator chose.
    """
    payload = {
        "files": [
            (io.BytesIO(b"a file"), "a.txt"),
            (io.BytesIO(b"inside what would have to be a folder"), "inner.txt"),
            (io.BytesIO(b"unrelated"), "other.txt"),
        ],
        "relative_paths": json.dumps([
            "Case/a.txt", "Case/a.txt/inner.txt", "Case/other.txt",
        ]),
    }
    resp = admin_client.post(
        "/api/input/uploads", data=payload, content_type="multipart/form-data",
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    body = resp.get_json()

    assert body["success"] is True
    names = sorted(entry["name"] for entry in body["staged"])
    assert names == ["Case/a.txt", "Case/other.txt"], names
    _tidy(body["staged"][0]["path"], staged_cleanup)

    assert len(body["failed"]) == 1, body["failed"]
    failure = body["failed"][0]
    assert failure["code"] == "NAME_CONFLICT"
    assert "a.txt" in failure["name"]
    assert "rename" in failure["message"].lower()

    contents = sorted(
        pathlib.Path(entry["path"]).read_bytes() for entry in body["staged"]
    )
    assert contents == [b"a file", b"unrelated"]


def test_upload_without_files_is_refused(app, admin_client):
    resp = admin_client.post(
        "/api/input/uploads", data={}, content_type="multipart/form-data",
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == "NO_FILES"


def test_traversal_in_the_client_name_cannot_escape_the_batch(
    app, admin_client, staged_cleanup
):
    """A hostile relative path is contained, not trusted."""
    payload = {
        "files": [(io.BytesIO(b"x"), "evil.txt")],
        "relative_paths": json.dumps(["../../../../etc/evil.txt"]),
    }
    resp = admin_client.post(
        "/api/input/uploads", data=payload, content_type="multipart/form-data",
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    staged = resp.get_json()["staged"]
    _tidy(staged[0]["path"], staged_cleanup)
    # It landed under the staging root, one batch directory deep - not in /etc.
    assert _staging_root() in pathlib.Path(staged[0]["path"]).parents
    assert ".." not in staged[0]["path"]
    assert staged[0]["name"] == "etc/evil.txt"


# ---------------------------------------------------------------------------
# Chunked upload (files larger than one request may carry)
# ---------------------------------------------------------------------------


@pytest.fixture()
def small_chunks(monkeypatch, ops_api):
    """Tiny chunk size so multi-chunk behaviour is testable without gigabytes."""
    monkeypatch.setattr(ops_api, "CHUNK_SIZE_BYTES", 16)
    return 16


def _start(admin_client, filename, size, sha256=None):
    payload = {"filename": filename, "size": size}
    if sha256:
        payload["sha256"] = sha256
    return admin_client.post("/api/input/uploads/chunked/start", json=payload)


def _chunk(admin_client, upload_id, index, data, sha256=None):
    form = {"chunk": (io.BytesIO(data), "chunk")}
    if sha256 is not None:
        form["sha256"] = sha256
    return admin_client.post(
        f"/api/input/uploads/chunked/{upload_id}/chunk/{index}",
        data=form, content_type="multipart/form-data",
    )


def test_chunked_upload_assembles_exactly_what_was_sent(
    app, admin_client, small_chunks, staged_cleanup
):
    content = b"0123456789abcdef" * 3  # 48 bytes -> 3 chunks of 16
    digest = hashlib.sha256(content).hexdigest()

    start = _start(admin_client, "big/report.bin", len(content), digest)
    assert start.status_code == 201, start.get_data(as_text=True)
    plan = start.get_json()
    assert plan["total_chunks"] == 3
    assert plan["chunk_size"] == 16
    upload_id = plan["upload_id"]

    # Status before anything is sent lists every chunk as missing.
    status = admin_client.get(f"/api/input/uploads/chunked/{upload_id}").get_json()
    assert status["bytes_received"] == 0
    assert status["missing_chunks"] == [0, 1, 2]

    for index in range(3):
        part = content[index * 16:(index + 1) * 16]
        resp = _chunk(admin_client, upload_id, index, part,
                      hashlib.sha256(part).hexdigest())
        assert resp.status_code == 200, resp.get_data(as_text=True)

    status = admin_client.get(f"/api/input/uploads/chunked/{upload_id}").get_json()
    assert status["bytes_received"] == len(content)
    assert status["missing_chunks"] == []

    done = admin_client.post(f"/api/input/uploads/chunked/{upload_id}/complete")
    assert done.status_code == 201, done.get_data(as_text=True)
    staged = done.get_json()
    _tidy(staged["staged_path"], staged_cleanup)

    assert staged["bytes"] == len(content)
    assert staged["sha256"] == digest
    assert staged["name"] == "big/report.bin"
    assert pathlib.Path(staged["staged_path"]).read_bytes() == content

    # The session is gone once the file is staged - nothing is left to resume.
    assert admin_client.get(
        f"/api/input/uploads/chunked/{upload_id}"
    ).status_code == 404


def test_chunked_upload_keeps_the_folder_path_from_a_windows_client(
    app, admin_client, small_chunks, staged_cleanup
):
    """A large file selected inside a folder keeps its place in the tree."""
    content = b"y" * 32
    upload_id = _start(
        admin_client, r"C:\Evidence\Case 1\large.bin", len(content),
    ).get_json()["upload_id"]
    staged_cleanup.append(pathlib.Path(_upload_dir(upload_id)))

    assert _chunk(admin_client, upload_id, 0, content[:16]).status_code == 200
    assert _chunk(admin_client, upload_id, 1, content[16:]).status_code == 200
    done = admin_client.post(f"/api/input/uploads/chunked/{upload_id}/complete")
    assert done.status_code == 201, done.get_data(as_text=True)
    staged = done.get_json()
    _tidy(staged["staged_path"], staged_cleanup)

    assert staged["name"] == "Evidence/Case 1/large.bin"
    assert pathlib.Path(staged["staged_path"]).read_bytes() == content


def test_incomplete_upload_cannot_be_completed(
    app, admin_client, small_chunks, staged_cleanup
):
    content = b"a" * 40
    upload_id = _start(admin_client, "partial.bin", len(content)).get_json()["upload_id"]
    staged_cleanup.append(pathlib.Path(_upload_dir(upload_id)))

    assert _chunk(admin_client, upload_id, 0, content[:16]).status_code == 200
    resp = admin_client.post(f"/api/input/uploads/chunked/{upload_id}/complete")
    assert resp.status_code == 409
    error = resp.get_json()["error"]
    assert error["code"] == "UPLOAD_INCOMPLETE"
    assert error["details"]["missing_chunks"] == [1, 2]

    # Resuming is possible: send the missing chunk, then complete.
    assert _chunk(admin_client, upload_id, 1, content[16:32]).status_code == 200
    assert _chunk(admin_client, upload_id, 2, content[32:]).status_code == 200
    done = admin_client.post(f"/api/input/uploads/chunked/{upload_id}/complete")
    assert done.status_code == 201
    staged = done.get_json()
    _tidy(staged["staged_path"], staged_cleanup)
    assert pathlib.Path(staged["staged_path"]).read_bytes() == content


def test_a_corrupted_chunk_is_rejected_and_discarded(
    app, admin_client, small_chunks, staged_cleanup
):
    """Transit corruption is caught at the chunk that suffered it."""
    content = b"b" * 32
    upload_id = _start(admin_client, "corrupt.bin", len(content)).get_json()["upload_id"]
    staged_cleanup.append(pathlib.Path(_upload_dir(upload_id)))

    wrong_hash = hashlib.sha256(b"something else").hexdigest()
    resp = _chunk(admin_client, upload_id, 0, content[:16], wrong_hash)
    assert resp.status_code == 422
    assert resp.get_json()["error"]["code"] == "CHUNK_HASH_MISMATCH"

    # The rejected chunk was not kept: the upload still reports it missing, so
    # a retry is the only way forward (no silent acceptance of bad bytes).
    status = admin_client.get(f"/api/input/uploads/chunked/{upload_id}").get_json()
    assert status["bytes_received"] == 0
    assert status["missing_chunks"] == [0, 1]


def test_a_file_that_does_not_match_its_declared_hash_is_refused(
    app, admin_client, small_chunks, staged_cleanup
):
    content = b"c" * 32
    declared = hashlib.sha256(b"a different file entirely").hexdigest()
    upload_id = _start(
        admin_client, "mismatch.bin", len(content), declared,
    ).get_json()["upload_id"]
    staged_cleanup.append(pathlib.Path(_upload_dir(upload_id)))

    assert _chunk(admin_client, upload_id, 0, content[:16]).status_code == 200
    assert _chunk(admin_client, upload_id, 1, content[16:]).status_code == 200

    resp = admin_client.post(f"/api/input/uploads/chunked/{upload_id}/complete")
    assert resp.status_code == 422
    assert resp.get_json()["error"]["code"] == "HASH_MISMATCH"
    # Nothing was staged anywhere: a failed upload must not leave a half file
    # for a later job to pick up.
    assert list(_staging_root().glob("*/mismatch.bin")) == []


def test_size_shortfall_is_reported_against_the_plan(
    app, admin_client, small_chunks, staged_cleanup
):
    """A chunk that is smaller than planned cannot silently shrink the file."""
    content = b"d" * 40
    upload_id = _start(admin_client, "short.bin", len(content)).get_json()["upload_id"]
    staged_cleanup.append(pathlib.Path(_upload_dir(upload_id)))

    assert _chunk(admin_client, upload_id, 0, content[:16]).status_code == 200
    assert _chunk(admin_client, upload_id, 1, content[16:32]).status_code == 200
    assert _chunk(admin_client, upload_id, 2, content[32:38]).status_code == 200  # 2 bytes short

    resp = admin_client.post(f"/api/input/uploads/chunked/{upload_id}/complete")
    assert resp.status_code == 422
    assert resp.get_json()["error"]["code"] == "SIZE_MISMATCH"


def test_chunk_out_of_range_and_unknown_session_are_refused(app, admin_client, small_chunks):
    upload_id = _start(admin_client, "range.bin", 32).get_json()["upload_id"]
    try:
        assert _chunk(admin_client, upload_id, 9, b"x" * 16).status_code == 400
        assert _chunk(admin_client, "f" * 32, 0, b"x").status_code == 404
        assert admin_client.get("/api/input/uploads/chunked/nope").status_code == 404
        assert admin_client.post(
            "/api/input/uploads/chunked/" + "f" * 32 + "/complete"
        ).status_code == 404
    finally:
        admin_client.delete(f"/api/input/uploads/chunked/{upload_id}")


def test_start_rejects_impossible_plans(app, admin_client, ops_api, monkeypatch):
    assert _start(admin_client, "x.bin", 0).status_code == 400
    assert _start(admin_client, "", 10).status_code == 400
    assert _start(admin_client, "x.bin", 10, "not-a-hash").status_code == 400

    monkeypatch.setattr(ops_api, "MAX_CHUNKED_UPLOAD_BYTES", 1024)
    resp = _start(admin_client, "huge.bin", 2048)
    assert resp.status_code == 413
    assert resp.get_json()["error"]["code"] == "UPLOAD_TOO_LARGE"


def test_cancelling_an_upload_removes_its_session(
    app, admin_client, small_chunks, staged_cleanup
):
    content = b"e" * 32
    upload_id = _start(admin_client, "cancel.bin", len(content)).get_json()["upload_id"]
    assert _chunk(admin_client, upload_id, 0, content[:16]).status_code == 200

    assert admin_client.delete(f"/api/input/uploads/chunked/{upload_id}").status_code == 200
    assert admin_client.get(f"/api/input/uploads/chunked/{upload_id}").status_code == 404
    assert admin_client.delete(f"/api/input/uploads/chunked/{upload_id}").status_code == 404


def test_options_info_reports_the_limits_the_page_renders(app, admin_client):
    body = admin_client.get("/api/input/options-info").get_json()
    for key in ("max_direct_upload_mb", "max_chunked_upload_mb", "chunk_size_mb",
                "ingestion_roots", "server_path_import_available",
                "max_staged_path_chars", "platform"):
        assert key in body, key
    assert body["max_direct_upload_mb"] > 0
    assert body["max_chunked_upload_mb"] >= body["max_direct_upload_mb"]
    assert body["chunk_size_mb"] >= 1


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _upload_dir(upload_id):
    return _staging_root() / "chunked" / upload_id


def _staging_root():
    from core.app_paths import get_data_root

    return pathlib.Path(get_data_root()) / "uploads"
