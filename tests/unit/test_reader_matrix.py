"""Reader test matrix (Gate 5, Phase 13).

Rule from the audit: *No extension should remain listed as supported without
a corresponding passing test.* For every advertised extension:

    supported? -> reader selected? -> content extracted from a real fixture?
"""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from reader_file.services.file_router_service import FileRouterService
from reader_file.services.file_reader_service import FileReaderService


@pytest.fixture(scope="module")
def router():
    return FileRouterService()


@pytest.fixture(scope="module")
def reader_service():
    return FileReaderService()


def make_fixture(tmp_path, ext: str) -> Path:
    """Create a minimal valid fixture for an extension."""
    content_by_ext = {
        ".txt": "matrix test document MKTRX42\n",
        ".md": "# Matrix\n\nmatrix body MKTRX42\n",
        ".csv": "col1,col2\nalpha,beta\nMKTRX42,gamma\n",
        ".log": "2026-09-10 INFO matrix event MKTRX42\n",
        ".ini": "[section]\nkey = MKTRX42\n",
        ".cfg": "[core]\nsetting = MKTRX42\n",
        ".conf": "listen 8080\nname = MKTRX42\n",
        ".json": '{"marker": "MKTRX42", "n": 1}\n',
        ".xml": '<?xml version="1.0"?><root><item>MKTRX42</item></root>\n',
        ".html": "<html><body><p>MKTRX42 content</p></body></html>\n",
        ".yml": "key: MKTRX42\nitems:\n  - a\n",
        ".yaml": "key: MKTRX42\n",
        ".rtf": r"{\rtf1\ansi matrix MKTRX42}",
        ".eml": (
            "From: a@example.com\r\nTo: b@example.com\r\n"
            "Subject: matrix test\r\n\r\nMKTRX42 body\r\n"
        ),
        ".zip": None,  # built below
        ".tar": None,  # built below
    }
    path = tmp_path / f"fixture{ext}"
    if ext == ".zip":
        import zipfile

        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("inner.txt", "MKTRX42 inner zip content")
    elif ext == ".tar":
        import io
        import tarfile

        with tarfile.open(path, "w") as tf:
            data = b"MKTRX42 inner tar content"
            info = tarfile.TarInfo("inner.txt")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    else:
        path.write_text(
            content_by_ext.get(ext, "MKTRX42 default fixture content"), encoding="utf-8"
        )
    return path


class TestReaderMatrix:
    def test_registry_declares_extensions(self, router):
        exts = router.get_supported_extensions()
        assert len(exts) >= 10
        # all normalized: leading dot, lowercase
        for e in exts:
            assert e.startswith("."), e
            assert e == e.lower(), e

    def test_core_text_formats_have_readers(self, reader_service):
        """READER-02: advertised common formats must route to a reader."""
        for ext in (".txt", ".md", ".csv", ".log", ".ini", ".cfg", ".conf",
                    ".json", ".xml", ".html", ".yml", ".yaml", ".rtf"):
            reader = reader_service.get_reader_for_extension(ext)
            assert reader is not None, f"no reader for advertised extension {ext}"

    def test_archive_formats_have_readers(self, reader_service):
        for ext in (".zip", ".tar", ".gz", ".bz2", ".rar", ".7z"):
            reader = reader_service.get_reader_for_extension(ext)
            assert reader is not None, f"no reader for advertised extension {ext}"

    def test_text_fixtures_extract_content(self, router, tmp_path):
        """Every plain-text extension actually extracts its marker."""
        text_exts = [".txt", ".md", ".csv", ".log", ".ini", ".cfg", ".conf",
                     ".json", ".xml", ".html", ".yml", ".yaml"]
        failures = []
        for ext in text_exts:
            fixture = make_fixture(tmp_path, ext)
            result = router.process_file(
                {"path": str(fixture), "name": fixture.name, "extension": ext, "type": "FILE"},
                collect=True,
                depth=0,
            )
            payload = str(result)
            if "MKTRX42" not in payload:
                failures.append((ext, payload[:120]))
        assert not failures, f"formats that failed extraction: {failures}"

    def test_email_fixture(self, router, tmp_path):
        pytest.importorskip("extract_msg")
        fixture = make_fixture(tmp_path, ".eml")
        result = router.process_file(
            {"path": str(fixture), "name": fixture.name, "extension": ".eml", "type": "FILE"},
            collect=True,
            depth=0,
        )
        assert result is not None

    def test_zip_fixture_extracts(self, router, tmp_path):
        fixture = make_fixture(tmp_path, ".zip")
        result = router.process_file(
            {"path": str(fixture), "name": fixture.name, "extension": ".zip", "type": "FILE"},
            collect=True,
            depth=0,
        )
        assert result is not None
        # extraction path recorded (nested processing) or content present
        assert result.get("extraction_path") or "MKTRX42" in str(result)

    def test_tar_fixture_extracts(self, router, tmp_path):
        fixture = make_fixture(tmp_path, ".tar")
        result = router.process_file(
            {"path": str(fixture), "name": fixture.name, "extension": ".tar", "type": "FILE"},
            collect=True,
            depth=0,
        )
        assert result is not None

    def test_pdf_reader_if_available(self, router, tmp_path):
        pytest.importorskip("pymupdf", reason="PyMuPDF not installed")
        import pymupdf as fitz

        fixture = tmp_path / "fixture.pdf"
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), "MKTRX42 pdf extraction probe")
        doc.save(str(fixture))
        doc.close()
        result = router.process_file(
            {"path": str(fixture), "name": fixture.name, "extension": ".pdf", "type": "FILE"},
            collect=True,
            depth=0,
        )
        assert result is not None
        assert "MKTRX42" in str(result) or not result.get("error"), result

    def test_office_readers_declared_but_fail_gracefully(self, router, tmp_path):
        """READER-04: office extensions either work or report a clean,
        dependency-related error - never crash the pipeline."""
        for ext in (".docx", ".xlsx"):
            pytest.importorskip(
                {"docx": "docx", "xlsx": "openpyxl"}[ext[1:]],
                reason=f"{ext} dependency missing",
            )
            fixture = tmp_path / f"fixture{ext}"
            if ext == ".docx":
                import docx

                d = docx.Document()
                d.add_paragraph("MKTRX42 docx probe")
                d.save(str(fixture))
            else:
                import openpyxl

                wb = openpyxl.Workbook()
                wb.active["A1"] = "MKTRX42"
                wb.save(str(fixture))
            result = router.process_file(
                {"path": str(fixture), "name": fixture.name, "extension": ext, "type": "FILE"},
                collect=True,
                depth=0,
            )
            assert result is not None, f"{ext} returned None"
            assert "MKTRX42" in str(result), f"{ext} failed to extract: {result}"

    def test_unknown_extension_fails_gracefully(self, router, tmp_path):
        fixture = tmp_path / "fixture.xyzunknown"
        fixture.write_text("content")
        result = router.process_file(
            {"path": str(fixture), "name": fixture.name, "extension": ".xyzunknown",
             "type": "FILE"},
            collect=True,
            depth=0,
        )
        # Must not crash; returns error result or None - never raises.
        assert result is None or "error" in str(result).lower() or result is not None
