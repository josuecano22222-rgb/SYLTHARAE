"""Content-based file type identification (DETECT-01).

The audit rule: *an extension alone must not determine the processing path
when a stronger identification method is available.*

These tests exercise the real detector, the real resolver and the real router
against files whose bytes deliberately disagree with their names.
"""

import io
import os
import sys
import zipfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.detect_binanry_utils import (  # noqa: E402
    CONFIDENCE_NONE,
    CONFIDENCE_STRONG,
    CONFIDENCE_WEAK,
    SNIFF_HEADER_SIZE,
    detect_file_type_with_confidence,
    read_file_header,
    sniff_file_type,
)
from reader_file.services.file_reader_service import FileReaderService  # noqa: E402
from reader_file.services.file_router_service import FileRouterService  # noqa: E402

# Minimal 1x1 PNG.
PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6260000002000100ffff03000006000557bfabd4"
    "0000000049454e44ae426082"
)
PDF_BYTES = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"


def _build_pdf(text: str) -> bytes:
    """Build a real single-page PDF containing ``text`` in its text layer."""
    pymupdf = pytest.importorskip("pymupdf")
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), text, fontsize=14)
    return doc.tobytes()


def make_zip(path: Path, members: dict) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return path


@pytest.fixture(scope="module")
def reader_service():
    return FileReaderService()


@pytest.fixture(scope="module")
def router():
    return FileRouterService()


# ----------------------------------------------------------------------
# Detector
# ----------------------------------------------------------------------
class TestDetector:
    def test_png_is_strong(self):
        ext, confidence = detect_file_type_with_confidence(PNG_BYTES)
        assert ext == ".png"
        assert confidence == CONFIDENCE_STRONG

    def test_pdf_is_strong(self):
        ext, confidence = detect_file_type_with_confidence(PDF_BYTES)
        assert ext == ".pdf"
        assert confidence == CONFIDENCE_STRONG

    def test_plain_text_is_not_a_signature(self):
        """Plain text carries no magic bytes; the extension must stay in charge."""
        ext, confidence = detect_file_type_with_confidence(b"just some csv,a,b\n1,2,3\n")
        assert confidence == CONFIDENCE_NONE
        assert ext == ".bin"

    def test_empty_input_is_inconclusive(self):
        assert detect_file_type_with_confidence(b"") == (".bin", CONFIDENCE_NONE)

    def test_html_heuristic_is_weak_not_strong(self):
        """A text heuristic may fill a gap but must never override an extension."""
        ext, confidence = detect_file_type_with_confidence(b"<html><body>x</body></html>")
        assert ext == ".html"
        assert confidence == CONFIDENCE_WEAK

    def test_unknown_riff_subtype_is_not_claimed_as_webp(self):
        """A RIFF payload we cannot name must not be reported as .webp."""
        payload = b"RIFF" + (100).to_bytes(4, "little") + b"CDAO" + b"\x00" * 64
        ext, confidence = detect_file_type_with_confidence(payload)
        assert ext != ".webp"
        assert ext == ".riff"
        assert confidence == CONFIDENCE_STRONG

    def test_riff_wave_is_wav(self):
        payload = b"RIFF" + (100).to_bytes(4, "little") + b"WAVE" + b"\x00" * 64
        assert detect_file_type_with_confidence(payload)[0] == ".wav"

    def test_odf_container_is_not_reported_as_plain_zip(self):
        """OpenDocument is a ZIP; naming it .zip would lose the document text."""
        body = (
            b"PK\x03\x04" + b"\x00" * 200
            + b"mimetype" + b"application/vnd.oasis.opendocument.text" + b"\x00" * 400
        )
        ext, confidence = detect_file_type_with_confidence(body)
        assert ext == ".odt"
        assert confidence == CONFIDENCE_STRONG

    def test_zip_with_word_folder_but_no_content_types_stays_zip(self):
        """Guard against misreading an ordinary ZIP that contains a word/ dir."""
        body = b"PK\x03\x04" + b"\x00" * 200 + b"word/document.xml" + b"\x00" * 400
        ext, _ = detect_file_type_with_confidence(body)
        assert ext == ".zip"

    def test_ole_directory_names_are_matched_as_utf16le(self):
        """OLE stores entry names as UTF-16LE; an ASCII-only probe never fires."""
        blob = (
            b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1"
            + b"\x00" * 512
            + "WordDocument".encode("utf-16-le")
            + b"\x00" * 64
        )
        assert detect_file_type_with_confidence(blob)[0] == ".doc"


# ----------------------------------------------------------------------
# Header reader (bounded + failure-safe)
# ----------------------------------------------------------------------
class TestHeaderReader:
    def test_missing_file_returns_empty_not_raise(self, tmp_path):
        assert read_file_header(str(tmp_path / "nope.bin")) == b""

    def test_directory_returns_empty_not_raise(self, tmp_path):
        assert read_file_header(str(tmp_path)) == b""

    def test_read_is_bounded(self, tmp_path):
        big = tmp_path / "big.bin"
        big.write_bytes(b"\x00" * (SNIFF_HEADER_SIZE * 4))
        assert len(read_file_header(str(big))) == SNIFF_HEADER_SIZE

    def test_sniff_reads_from_disk(self, tmp_path):
        target = tmp_path / "misnamed.bin"
        target.write_bytes(PNG_BYTES)
        assert sniff_file_type(str(target)) == (".png", CONFIDENCE_STRONG)


# ----------------------------------------------------------------------
# Resolver
# ----------------------------------------------------------------------
class TestResolver:
    def test_misnamed_pdf_resolves_to_pdf(self, reader_service, tmp_path):
        target = tmp_path / "report.docx"
        target.write_bytes(PDF_BYTES)
        d = reader_service.resolve_type_for_file(str(target))
        assert d["effective_extension"] == ".pdf"
        assert d["detection_method"] == "magic-bytes"
        assert d["extension_mismatch"] is True
        assert "contradicted" in d["detection_note"]

    def test_extensionless_zip_is_identified(self, reader_service, tmp_path):
        target = make_zip(tmp_path / "bundle", {"inner.txt": "hello"})
        d = reader_service.resolve_type_for_file(str(target))
        assert d["effective_extension"] == ".zip"
        assert d["detection_method"] == "magic-bytes"

    def test_plain_text_keeps_its_extension(self, reader_service, tmp_path):
        """No regression: a .txt file must not be hijacked by a heuristic."""
        target = tmp_path / "notes.txt"
        target.write_text("col1,col2\n1,2\n", encoding="utf-8")
        d = reader_service.resolve_type_for_file(str(target))
        assert d["effective_extension"] == ".txt"
        assert d["detection_method"] == "extension"
        assert d["extension_mismatch"] is False

    def test_compound_tar_gz_outlives_single_layer_sniff(self, reader_service, tmp_path):
        import tarfile

        target = tmp_path / "bundle.tar.gz"
        with tarfile.open(target, "w:gz") as tf:
            data = b"payload"
            info = tarfile.TarInfo("payload.txt")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        d = reader_service.resolve_type_for_file(str(target))
        assert d["effective_extension"] == ".tar.gz"
        assert d["detected_extension"] == ".gz"

    def test_strong_signature_without_a_reader_falls_back(self, reader_service, tmp_path):
        """An ELF binary sniffs strongly but has no reader; do not mis-route."""
        target = tmp_path / "tool.bin"
        target.write_bytes(b"\x7FELF" + b"\x00" * 64)
        d = reader_service.resolve_type_for_file(str(target))
        assert d["detected_extension"] == ".elf"
        assert d["detection_method"] != "magic-bytes"
        assert d["effective_extension"] in (".bin", "")

    def test_extensionless_unknown_content_routes_to_binary(self, reader_service, tmp_path):
        """No extension + no signature must still produce a processable type."""
        target = tmp_path / "mystery"
        target.write_bytes(b"\x01\x02\x03\x04" * 32)
        d = reader_service.resolve_type_for_file(str(target))
        assert d["effective_extension"] == ".bin"
        assert d["detection_method"] == "fallback-binary"

    def test_declared_none_is_treated_as_no_extension(self, reader_service, tmp_path):
        """read_tree() reports 'none' for extensionless files."""
        assert reader_service.normalize_extension("none") == ""
        target = tmp_path / "blob"
        target.write_bytes(PNG_BYTES)
        d = reader_service.resolve_type_for_file(str(target), "none")
        assert d["effective_extension"] == ".png"

    def test_resolve_reader_picks_pdf_reader_for_misnamed_pdf(self, reader_service, tmp_path):
        target = tmp_path / "report.docx"
        target.write_bytes(PDF_BYTES)
        reader, decision = reader_service.resolve_reader_for_file(str(target))
        assert decision["effective_extension"] == ".pdf"
        assert reader is reader_service.pdf_reader

    # Both spellings of one format are the same type: the resolver must not
    # report a content/extension contradiction (which the router logs as a
    # warning) for a .jpeg, .tif or .mpg file, and must still route it to a
    # registered reader.
    @pytest.mark.parametrize(
        "name, head",
        [
            ("photo.jpeg", b"\xFF\xD8\xFF\xE0\x00\x10JFIF"),
            ("scan.tif", b"II*\x00"),
            ("clip.mpg", b"\x00\x00\x01\xBA"),
        ],
    )
    def test_extension_alias_is_not_a_contradiction(
        self, reader_service, tmp_path, name, head
    ):
        target = tmp_path / name
        target.write_bytes(head + b"\x00" * 256)
        d = reader_service.resolve_type_for_file(str(target))
        assert d["extension_mismatch"] is False
        assert d["detection_note"] is None
        assert reader_service.is_supported(d["effective_extension"])

    def test_canonical_spelling_is_used_without_a_declared_extension(
        self, reader_service, tmp_path
    ):
        """Without a name to consult, the signature's own spelling is reported."""
        target = tmp_path / "nameless"
        target.write_bytes(b"\xFF\xD8\xFF\xDB" + b"\x00" * 64)
        d = reader_service.resolve_type_for_file(str(target))
        assert d["detected_extension"] == ".jpg"
        assert d["effective_extension"] == ".jpg"


# ----------------------------------------------------------------------
# Router end-to-end (the real processing path)
# ----------------------------------------------------------------------
class TestRouterEndToEnd:
    @staticmethod
    def _process(router, path: Path):
        file_info = {
            "path": str(path),
            "name": path.name,
            "extension": os.path.splitext(path.name)[1].lower(),
            "type": "FILE",
            "size": path.stat().st_size,
        }
        result = router.process_file(file_info, collect=False, store_result=False)
        assert result is not None, "router returned no result"
        return result["Content"]

    def test_extensionless_zip_is_actually_extracted(self, router, tmp_path):
        """The headline defect: an extensionless ZIP used to be dropped."""
        target = make_zip(tmp_path / "container", {"inner.txt": "EXTRACTED-4412"})
        content = self._process(router, target)
        assert not content.get("error"), content.get("error")
        assert "archive_info" in content
        assert content["archive_info"]["total_files"] >= 1

    def test_zip_named_bin_is_actually_extracted(self, router, tmp_path):
        target = make_zip(tmp_path / "container.bin", {"inner.txt": "EXTRACTED-4412"})
        content = self._process(router, target)
        assert not content.get("error"), content.get("error")
        assert "archive_info" in content

    def test_misnamed_png_reaches_the_image_reader_not_the_text_reader(self, router, tmp_path):
        """A PNG named .txt used to be stored as mojibake text."""
        target = tmp_path / "picture.txt"
        target.write_bytes(PNG_BYTES)
        content = self._process(router, target)
        detection = content.get("type_detection")
        assert detection["effective_extension"] == ".png"
        assert detection["extension_mismatch"] is True
        # Image reader markers, not the text reader's encoding report.
        assert "ocr_attempted" in content
        assert "encoding_used" not in content

    def test_misnamed_pdf_reaches_the_pdf_reader(self, router, tmp_path):
        target = tmp_path / "report.docx"
        target.write_bytes(_build_pdf("MARKER PDF7741"))
        content = self._process(router, target)
        assert content["type_detection"]["effective_extension"] == ".pdf"
        # PDF reader output shape (num_pages), not python-docx's error.
        assert "num_pages" in content
        assert "install_command" not in content

    def test_extensionless_png_is_processed_not_rejected(self, router, tmp_path):
        target = tmp_path / "picture"
        target.write_bytes(PNG_BYTES)
        content = self._process(router, target)
        assert content.get("error") != "No file extension found"
        assert content["type_detection"]["effective_extension"] == ".png"

    def test_plain_text_control_unchanged(self, router, tmp_path):
        target = tmp_path / "notes.txt"
        target.write_text("plain text CONTROL-9981\n", encoding="utf-8")
        content = self._process(router, target)
        assert not content.get("error"), content.get("error")
        assert "CONTROL-9981" in str(content.get("content", ""))
        assert content["type_detection"]["detection_method"] == "extension"

    def test_detection_provenance_is_attached_to_every_result(self, router, tmp_path):
        target = tmp_path / "data.csv"
        target.write_text("a,b\n1,2\n", encoding="utf-8")
        content = self._process(router, target)
        detection = content.get("type_detection")
        assert detection is not None, "identification provenance missing"
        for key in (
            "declared_extension",
            "detected_extension",
            "detection_confidence",
            "detection_method",
            "effective_extension",
            "extension_mismatch",
        ):
            assert key in detection
