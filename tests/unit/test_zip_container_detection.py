"""Regression: a ZIP container must not be mistaken for a document inside it.

``[Content_Types].xml`` and ``word/`` are markers of an OOXML *package*. A plain
ZIP that merely contains such a package carries the same bytes - ``zipfile``
defaults to ZIP_STORED, so the nested document lands in the outer archive
verbatim - and the old substring test declared the outer archive a ``.docx``.

The consequence reached straight into progress accounting: the OfficeFileReader
then failed with "There is no item named '[Content_Types].xml' in the archive",
the archive was never expanded, and every member below it silently disappeared
from the processing workload and from the progress denominator.

The formats are defined by their entry *names*, so detection now enumerates the
container instead of scanning raw bytes.
"""

import io
import os
import sys
import tempfile
import zipfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.detect_binanry_utils import (  # noqa: E402
    CONFIDENCE_STRONG,
    detect_file_type_with_confidence,
    sniff_file_type,
)
from reader_file.services.file_reader_service import FileReaderService  # noqa: E402


def _zip_bytes(entries, compression=zipfile.ZIP_STORED):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression) as zf:
        for name, payload in entries.items():
            zf.writestr(name, payload)
    return buf.getvalue()


def _docx_payload():
    """A real (minimal) DOCX package."""
    docx = pytest.importorskip("docx")
    document = docx.Document()
    document.add_paragraph("nested document body")
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


def _xlsx_payload():
    openpyxl = pytest.importorskip("openpyxl")
    workbook = openpyxl.Workbook()
    workbook.active["A1"] = "value"
    buf = io.BytesIO()
    workbook.save(buf)
    return buf.getvalue()


def _odt_payload():
    pytest.importorskip("odf")
    from odf.opendocument import OpenDocumentText
    from odf.text import P

    document = OpenDocumentText()
    document.text.addElement(P(text="hello"))
    # odfpy appends the mimetype suffix when its second argument is truthy, so
    # save to a real path and read the bytes back.
    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "doc")
    document.save(path)
    produced = path if os.path.exists(path) else path + ".odt"
    with open(produced, "rb") as fh:
        return fh.read()


def _write(tmpdir, name, payload):
    path = os.path.join(tmpdir, name)
    with open(path, "wb") as fh:
        fh.write(payload)
    return path


class TestRealDocumentsStillRouteToTheDocumentReader:
    def test_docx_is_docx(self):
        ext, confidence = sniff_file_type(
            _write(tempfile.mkdtemp(), "a.docx", _docx_payload())
        )
        assert ext == ".docx"
        assert confidence == CONFIDENCE_STRONG

    def test_xlsx_is_xlsx_even_though_content_types_is_not_first(self):
        """openpyxl writes docProps/app.xml first, so entry order is not a test."""
        payload = _xlsx_payload()
        with zipfile.ZipFile(io.BytesIO(payload)) as zf:
            assert zf.namelist()[0].lower() != "[content_types].xml"
        ext, confidence = sniff_file_type(
            _write(tempfile.mkdtemp(), "a.xlsx", payload)
        )
        assert ext == ".xlsx"
        assert confidence == CONFIDENCE_STRONG

    def test_odt_is_odt(self):
        ext, confidence = sniff_file_type(
            _write(tempfile.mkdtemp(), "a.odt", _odt_payload())
        )
        assert ext == ".odt"
        assert confidence == CONFIDENCE_STRONG


class TestArchivesContainingDocumentsStayArchives:
    @pytest.mark.parametrize("compression", [zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED])
    def test_zip_containing_docx_is_a_zip(self, compression):
        """The regression: stored inline, the nested markers used to leak out."""
        payload = _zip_bytes({"inner.docx": _docx_payload()}, compression)
        if compression == zipfile.ZIP_STORED:
            # This is the case that used to misroute: the nested document's own
            # OOXML markers sit verbatim in the outer archive's bytes.
            assert b"[Content_Types].xml" in payload
        else:
            # Deflated members hide their markers, so the old substring test
            # happened to pass; it must keep passing for the right reason.
            assert b"[Content_Types].xml" not in payload

        path = _write(tempfile.mkdtemp(), "with_docx.zip", payload)
        ext, confidence = sniff_file_type(path)
        assert ext == ".zip", "an archive containing a document is still an archive"
        assert confidence == CONFIDENCE_STRONG

    def test_zip_containing_xlsx_is_a_zip(self):
        payload = _zip_bytes({"inner.xlsx": _xlsx_payload()})
        path = _write(tempfile.mkdtemp(), "with_xlsx.zip", payload)
        assert sniff_file_type(path)[0] == ".zip"

    def test_zip_containing_several_documents_is_a_zip(self):
        payload = _zip_bytes({
            "docs/report.docx": _docx_payload(),
            "docs/sheet.xlsx": _xlsx_payload(),
            "readme.txt": b"plain",
        })
        path = _write(tempfile.mkdtemp(), "bundle.zip", payload)
        assert sniff_file_type(path)[0] == ".zip"

    def test_plain_zip_is_a_zip(self):
        payload = _zip_bytes({"a.txt": b"hello" * 200, "b.txt": b"world" * 200})
        path = _write(tempfile.mkdtemp(), "plain.zip", payload)
        assert sniff_file_type(path)[0] == ".zip"


class TestRouterReachTheArchiveReader:
    """Detection only matters if the router then expands the container."""

    def setup_method(self):
        self.service = FileReaderService()

    def _resolve(self, path):
        reader, decision = self.service.resolve_reader_for_file(
            path, os.path.splitext(path)[1]
        )
        return type(reader).__name__ if reader else None, decision

    def test_zip_containing_docx_reaches_the_archive_reader(self):
        payload = _zip_bytes({"inner.docx": _docx_payload()})
        path = _write(tempfile.mkdtemp(), "with_docx.zip", payload)
        name, decision = self._resolve(path)
        assert name == "ArchiveFileReader"
        assert decision["effective_extension"] == ".zip"

    def test_real_docx_reaches_the_office_reader(self):
        path = _write(tempfile.mkdtemp(), "report.docx", _docx_payload())
        name, decision = self._resolve(path)
        assert name == "OfficeFileReader"
        assert decision["effective_extension"] == ".docx"


class TestUnparseableContainerFallsBackToPayloadEvidence:
    """A synthetic/truncated header must not lose ODF or EPUB recognition."""

    def test_odf_markers_without_a_parseable_header(self):
        body = (
            b"PK\x03\x04" + b"\x00" * 200
            + b"mimetype" + b"application/vnd.oasis.opendocument.text"
            + b"\x00" * 400
        )
        assert detect_file_type_with_confidence(body)[0] == ".odt"

    def test_epub_markers_without_a_parseable_header(self):
        body = (
            b"PK\x03\x04" + b"\x00" * 200
            + b"mimetype" + b"application/epub+zip" + b"\x00" * 400
        )
        assert detect_file_type_with_confidence(body)[0] == ".epub"

    def test_real_epub_container(self):
        payload = _zip_bytes({
            "mimetype": b"application/epub+zip",
            "META-INF/container.xml": b"<container/>",
            "OEBPS/content.xhtml": b"<html>" + b"x" * 2000 + b"</html>",
        })
        path = _write(tempfile.mkdtemp(), "book.epub", payload)
        assert sniff_file_type(path)[0] == ".epub"


class TestNestedSubtreeIsActuallyExpanded:
    """The point of the fix: the children must reach the workload."""

    def test_zip_containing_docx_expands_rather_than_failing(self):
        from pipeline.integrated_reader import IntegratedFileReader

        root = tempfile.mkdtemp(prefix="expand_")
        try:
            payload = _zip_bytes({
                "inner.docx": _docx_payload(),
                "side.txt": b"sidecar text " * 100,
            })
            _write(root, "with_docx.zip", payload)

            reader = IntegratedFileReader(
                max_workers=2, enable_monitoring=False, enable_storage=False
            )
            reader.process_folder(root)
            snap = reader.get_live_progress()

            # 1 zip + 1 docx + 1 sidecar = 3, and the zip must not have failed.
            assert snap["total_files"] == 3
            assert snap["files_done"] == 3
            assert snap["files_pending"] == 0
            assert snap["files_failed"] == 0, (
                "the archive was misrouted to the document reader and failed"
            )
        finally:
            import shutil
            shutil.rmtree(root, ignore_errors=True)
