"""Integration: the original/source file can be viewed beside the extracted text.

Feature under test - "show me the file this text was extracted from":

* ``GET /api/file/<id>/original`` describes the source object: is it still on
  disk, which viewer fits it, and the URLs that serve the bytes;
* ``GET /api/file/<id>/original/content`` serves those bytes with a
  disposition and content type that are safe for the app's own origin.

The rules the tests pin, because getting them wrong is a security bug rather
than a display bug:

* an uploaded HTML/JS/XML file is served as ``text/plain`` - the app must
  never execute a document it ingested (stored XSS);
* an SVG renders in ``<img>`` but is sent as an attachment, so opening the
  URL directly cannot run its scripts;
* a format the browser cannot render is an attachment, not an inline blob;
* a source file that has disappeared from disk is reported as such - the
  extracted text survives it, so this is a state, not an error;
* the bytes served are the bytes stored (identity matters for verification).
"""

import datetime
import hashlib
import os
import sys
from pathlib import Path

import psycopg2
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

pytestmark = pytest.mark.integration

_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6360000002000154a24f5f0000000049454e44ae42"
    "6082"
)

_TEXTS = {
    "plain": ("notes.txt", b"line one\nline two\n", "text/plain"),
    "html": ("page.html", b"<script>alert(1)</script><h1>Hi</h1>", "text/plain"),
    "svg": ("logo.svg", b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>', "image/svg+xml"),
    "png": ("scan.png", _PNG, "image/png"),
    "pdf": ("report.pdf", b"%PDF-1.4\n%mock\n", "application/pdf"),
    "docx": ("letter.docx", b"PK\x03\x04mock-docx", "application/octet-stream"),
}


@pytest.fixture(scope="module")
def originals(pg_db, tmp_path_factory):
    """Real files on disk, each registered as a stored object."""
    root = tmp_path_factory.mktemp("original_files")
    tag = f"_orig_{os.getpid()}_{hashlib.sha256(str(root).encode()).hexdigest()[:8]}"

    written = {}
    for key, (name, blob, _mime) in _TEXTS.items():
        path = root / f"{tag}_{name}"
        path.write_bytes(blob)
        written[key] = (path, name, blob)

    # An object whose source file is gone: the row survives, the bytes do not.
    missing_path = root / f"{tag}_gone.pdf"
    missing_path.write_bytes(b"%PDF-1.4\n")
    missing_path.unlink()

    conn = psycopg2.connect(
        host=pg_db["host"], port=pg_db["port"], user=pg_db["user"],
        password=pg_db["password"], dbname=pg_db["database"],
    )
    today = datetime.date.today()
    ids = {}
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO sides (name, importance, date_creation)"
                " VALUES (%s, 0.5, %s) ON CONFLICT (name) DO UPDATE"
                " SET name = EXCLUDED.name RETURNING id", (f"{tag}_side", today))
            side_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO sources (name, job, importance, country, date_creation)"
                " VALUES (%s, 't', 0.5, 't', %s) ON CONFLICT (name) DO UPDATE"
                " SET name = EXCLUDED.name RETURNING id", (f"{tag}_src", today))
            source_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO hashs (hash) VALUES (%s) RETURNING id",
                (hashlib.sha256(tag.encode()).hexdigest(),))
            hash_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO hash_contexts (hash_id, source_id, side_id)"
                " VALUES (%s, %s, %s) RETURNING id",
                (hash_id, source_id, side_id))
            context_id = cur.fetchone()[0]

            def add_path(name, path_value, size):
                cur.execute(
                    "INSERT INTO paths (file_name, file_path, file_size, file_type,"
                    " file_status, file_date, date_creation, context_id)"
                    " VALUES (%s, %s, %s, 'FILE', 'Read', %s, %s, %s) RETURNING id",
                    (name, str(path_value), size, today, today, context_id))
                return cur.fetchone()[0]

            for key, (path, name, blob) in written.items():
                ids[key] = add_path(name, path, len(blob))
            ids["missing"] = add_path("gone.pdf", missing_path, 9)
            ids["no-path"] = add_path("no-path.txt", "", 0)

            # The reader page needs stored extracted text ("no content" is a
            # different state, redirected away): give the plain file the text
            # it produced, so the two sides of the comparison both exist.
            extracted = written["plain"][2].decode("utf-8")
            cur.execute(
                "INSERT INTO contents_raw (hash_id, chunk_seq, content, char_count)"
                " VALUES (%s, 0, %s, %s) ON CONFLICT (hash_id, chunk_seq)"
                " DO UPDATE SET content = EXCLUDED.content",
                (hash_id, extracted, len(extracted)))
    finally:
        conn.commit()
        conn.close()

    return {"ids": ids, "files": {k: v for k, v in written.items()}, "tag": tag}


def _info(client, file_id):
    resp = client.get(f"/api/file/{file_id}/original")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["success"] is True, body
    return body["original"]


def _content(client, file_id, **params):
    query = "?" + "&".join(f"{k}={v}" for k, v in params.items()) if params else ""
    return client.get(f"/api/file/{file_id}/original/content{query}")


# ---------------------------------------------------------------------------
# Description
# ---------------------------------------------------------------------------

def test_image_is_described_for_the_image_viewer(admin_client, originals):
    info = _info(admin_client, originals["ids"]["png"])
    assert info["available"] is True, info
    assert info["kind"] == "image"
    assert info["viewer"] == "img"
    assert info["mime_type"] == "image/png"
    assert info["size"] == len(_PNG)
    assert info["serve_url"].endswith("/original/content")
    assert "download=1" in info["download_url"]


def test_pdf_is_described_for_the_activex_frame_viewer(admin_client, originals):
    info = _info(admin_client, originals["ids"]["pdf"])
    assert (info["kind"], info["viewer"]) == ("pdf", "iframe"), info
    assert info["mime_type"] == "application/pdf"


def test_text_file_is_described_as_source_text(admin_client, originals):
    info = _info(admin_client, originals["ids"]["plain"])
    assert (info["kind"], info["viewer"]) == ("text", "text"), info
    assert info["mime_type"] == "text/plain"


def test_format_without_a_viewer_is_a_download(admin_client, originals):
    """A .docx cannot be rendered by a browser - say so instead of pretending."""
    info = _info(admin_client, originals["ids"]["docx"])
    assert (info["kind"], info["viewer"]) == ("download", "none"), info
    assert info["available"] is True, info


def test_missing_source_file_is_reported_not_hidden(admin_client, originals):
    """Extraction outlived the file: the operator must be told."""
    info = _info(admin_client, originals["ids"]["missing"])
    assert info["available"] is False, info
    assert info["reason"] == "missing-on-disk", info
    assert "no longer" in info["message"].lower(), info["message"]
    # The extracted text this object produced is unaffected by that.
    assert info["stored_size"] == 9


def test_object_without_a_path_is_reported(admin_client, originals):
    info = _info(admin_client, originals["ids"]["no-path"])
    assert info["available"] is False
    assert info["reason"] == "no-path", info


def test_unknown_object_is_a_404(admin_client):
    resp = admin_client.get("/api/file/99999999/original")
    assert resp.status_code == 404
    assert resp.get_json()["success"] is False


# ---------------------------------------------------------------------------
# Serving the bytes
# ---------------------------------------------------------------------------

def test_image_bytes_are_served_inline_and_identical(admin_client, originals):
    resp = _content(admin_client, originals["ids"]["png"])
    assert resp.status_code == 200
    assert resp.data == _PNG, "the viewer must show the bytes that were stored"
    assert resp.mimetype == "image/png"
    assert "attachment" not in (resp.headers.get("Content-Disposition") or "")


def test_html_is_served_as_plain_text_never_as_markup(admin_client, originals):
    """Stored HTML is data: serving it as text/html would run it in our origin."""
    resp = _content(admin_client, originals["ids"]["html"])
    assert resp.status_code == 200
    assert resp.mimetype == "text/plain", resp.headers.get("Content-Type")
    assert resp.data == _TEXTS["html"][1]
    assert "<script" in resp.get_data(as_text=True)  # shown, not executed


def test_svg_is_displayable_but_not_runnable(admin_client, originals):
    """<img> shows it; a direct navigation must not execute its script."""
    resp = _content(admin_client, originals["ids"]["svg"])
    assert resp.status_code == 200
    assert resp.mimetype == "image/svg+xml"
    assert "attachment" in (resp.headers.get("Content-Disposition") or ""), (
        resp.headers.get("Content-Disposition"))


def test_unrenderable_format_is_an_attachment(admin_client, originals):
    resp = _content(admin_client, originals["ids"]["docx"])
    assert resp.status_code == 200
    assert resp.mimetype == "application/octet-stream"
    assert "attachment" in (resp.headers.get("Content-Disposition") or "")


def test_download_parameter_forces_an_attachment(admin_client, originals, pg_db):
    resp = _content(admin_client, originals["ids"]["png"], download=1)
    assert resp.status_code == 200
    assert "attachment" in (resp.headers.get("Content-Disposition") or "")
    assert resp.data == _PNG
    # The download is a disclosure: one DATA_EXPORTED record for this file.
    import hashlib
    import psycopg2
    audit_id = int(resp.headers["X-Disclosure-Audit-Id"])
    conn = psycopg2.connect(host=pg_db["host"], port=pg_db["port"], user=pg_db["user"],
                            password=pg_db["password"], dbname=pg_db["database"])
    with conn.cursor() as cur:
        cur.execute("SELECT action, detail FROM audit_log WHERE id = %s", (audit_id,))
        action, detail = cur.fetchone()
    conn.close()
    file_id = originals["ids"]["png"]
    assert action == "DATA_EXPORTED"
    assert detail["kind"] == "original_file" and detail["scope"] == f"file:{file_id}"
    assert detail["row_count"] == 1 and detail["format"] == "png"
    assert detail["artifact"]["sha256"] == hashlib.sha256(_PNG).hexdigest()


def test_inline_display_is_a_preview_not_a_disclosure(admin_client, originals):
    resp = _content(admin_client, originals["ids"]["png"])
    assert resp.status_code == 200
    assert "X-Disclosure-Audit-Id" not in resp.headers


def test_pdf_is_frameable_by_this_app_only(admin_client, originals):
    """The PDF viewer is an iframe of this same origin - and nothing else."""
    resp = _content(admin_client, originals["ids"]["pdf"])
    assert resp.status_code == 200
    assert resp.headers.get("X-Frame-Options") == "SAMEORIGIN", resp.headers
    assert "frame-ancestors 'self'" in resp.headers.get("Content-Security-Policy", "")


def test_page_headers_still_deny_framing(app, admin_client):
    """The relaxation is per-response: pages keep DENY."""
    resp = admin_client.get("/archives")
    assert resp.status_code == 200
    assert resp.headers.get("X-Frame-Options") == "DENY", resp.headers


def test_missing_source_file_cannot_be_served(admin_client, originals):
    resp = _content(admin_client, originals["ids"]["missing"])
    assert resp.status_code == 404
    body = resp.get_json()
    assert body["success"] is False
    assert body["reason"] == "missing-on-disk", body


def test_the_path_is_never_taken_from_the_request(admin_client, originals, tmp_path):
    """Path traversal is impossible by construction: only the stored row is used."""
    secret = tmp_path / "secret.txt"
    secret.write_text("not yours")
    # A query parameter that the old /api/file/serve endpoint would have honoured.
    resp = admin_client.get(
        f"/api/file/{originals['ids']['plain']}/original/content",
        query_string={"path": str(secret)},
    )
    assert resp.status_code == 200
    assert b"not yours" not in resp.data
    assert resp.data == _TEXTS["plain"][1]


# ---------------------------------------------------------------------------
# The UI the operator actually uses
# ---------------------------------------------------------------------------

def test_archives_page_offers_the_original_file_view(admin_client):
    """The file-details modal must expose the comparison: text AND source."""
    resp = admin_client.get("/archives")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'id="originalFileTab"' in html, "no Original File tab in the modal"
    assert 'id="originalFileSection"' in html, "no pane to render it into"
    assert "switchModalContentTab('original')" in html
    assert 'id="extractedContentTab"' in html


def test_reader_page_offers_the_original_file_in_place(admin_client, originals):
    """The reader exposes the original tab and its lazy viewer module."""
    file_id = originals["ids"]["plain"]
    resp = admin_client.get(f"/file/{file_id}/full-content")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    html = resp.get_data(as_text=True)
    assert 'id="extractedContentTab"' in html
    assert 'id="originalFileTab"' in html
    assert 'data-content-pane="original"' in html
    assert "js/pages/full-content-page.js" in html

    page_module = (PROJECT_ROOT / "static/js/pages/full-content-page.js").read_text(
        encoding="utf-8")
    tab_module = (PROJECT_ROOT / "static/js/modules/original-content-tab.js").read_text(
        encoding="utf-8")
    assert "initOriginalContentTab(fileId)" in page_module
    assert "showOriginalFile(fileId, originalPane)" in tab_module

    # The viewer resolves its URLs from the server's descriptor, rather than
    # baking a source path into the HTML returned for a Reader page.
    descriptor = admin_client.get(f"/api/file/{file_id}/original")
    assert descriptor.status_code == 200
    original = descriptor.get_json()["original"]
    assert original["serve_url"].endswith(
        f"/api/file/{file_id}/original/content")
    assert original["download_url"].endswith("?download=1")
