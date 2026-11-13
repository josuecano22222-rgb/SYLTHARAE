"""Mixed PDFs: a text document whose later pages are scans.

The reader decides once per document whether to OCR, from a three-page sample.
A report with a born-digital cover, a born-digital contents page and scanned
annexes is therefore stored as: three pages of text, then empty strings, with no
error anywhere. Nothing in the result says the later pages were never read.

These tests pin the per-page decision: a page of a text document that carries an
image and either almost no text or a text layer that is not language is OCR'd,
and the better of the two readings is kept. The OCR engine itself is stubbed
where the test is about wiring, so the tests do not depend on tesseract or
RapidOCR being installed.
"""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

fitz = pytest.importorskip("pymupdf", reason="PyMuPDF not installed")

from reader_file.readers import read_pdf as read_pdf_module  # noqa: E402
from reader_file.readers.read_pdf import PDFFileReader  # noqa: E402


def _content(result):
    if isinstance(result.get("content"), dict) and "elements" not in result:
        return result["content"]
    return result


def _mixed_pdf(tmp_path, second_page_text="SCANNED ANNEX", garbage_layer=False):
    """Page 1 born-digital, page 2 an image of text (a scan)."""
    doc = fitz.open()
    page = doc.new_page(width=400, height=300)
    page.insert_text((40, 60), "Born digital cover page", fontsize=14)
    # enough text that the document-level sample classifies it as a text PDF,
    # which is the case this module exists for
    for i in range(10):
        page.insert_text((40, 90 + i * 16),
                         f"Body line {i} of a born-digital page with plenty of words.",
                         fontsize=9)

    scan = fitz.open()
    src = scan.new_page(width=400, height=300)
    src.insert_text((40, 60), second_page_text, fontsize=16)
    pix = src.get_pixmap(dpi=150)
    scan.close()

    page2 = doc.new_page(width=400, height=300)
    page2.insert_image(fitz.Rect(0, 0, 400, 300), pixmap=pix)
    if garbage_layer:
        # invisible text drawn under the image: the residue of a failed OCR pass
        page2.insert_text((40, 200), "\u00b7 4 \u00b7 7 \u00b7", fontsize=8)

    path = tmp_path / "mixed.pdf"
    doc.save(str(path))
    doc.close()
    return path


class _StubReader(PDFFileReader):
    """Records the fallback and returns a canned recognition."""

    def __init__(self, text="RECOGNISED ANNEX", fail=False):
        super().__init__()
        self.calls = []
        self._text = text
        self._fail = fail

    def _page_ocr_fallback(self, page, page_num, fitz_module, filepath,
                           native_text, ocr_languages, ocr_available):
        self.calls.append({"page": page_num, "native": native_text})
        if self._fail:
            return None
        return {"text": self._text, "method": "ocr_stub",
                "ocr_engine": "stub", "ocr_confidence": 0.9,
                "ocr_render_dpi": 150, "ocr_trigger": "page_scan_or_broken_text_layer"}


def test_text_pages_carry_geometry_and_blocks(tmp_path):
    path = _mixed_pdf(tmp_path)
    content = _content(PDFFileReader().read_file({"path": str(path), "extension": ".pdf"}))

    page1 = content["pages"][0]
    assert page1["method"] in ("direct_extraction", "ocr_layout_fallback")
    assert page1["width"] == pytest.approx(400, abs=1)
    assert page1["height"] == pytest.approx(300, abs=1)
    assert page1["blocks"], "no block geometry recorded"
    block = page1["blocks"][0]
    assert block["type"] == "text" and len(block["bbox"]) == 4
    assert page1["layout"]["chars"] > 0
    assert "Born digital cover page" in page1["text"]


def test_broken_text_layer_page_is_replaced_by_ocr(tmp_path):
    path = _mixed_pdf(tmp_path, garbage_layer=True)
    reader = _StubReader()
    content = _content(reader.read_file({"path": str(path), "extension": ".pdf"}))

    assert [c["page"] for c in reader.calls] == [1], reader.calls
    annex = content["pages"][1]
    assert annex["method"] == "ocr_layout_fallback"
    assert annex["text"] == "RECOGNISED ANNEX"
    assert annex["ocr_fallback"]["trigger"] == "page_scan_or_broken_text_layer"
    assert content["ocr_used"] is True


def test_corrupt_layer_is_not_kept_when_ocr_finds_nothing(tmp_path):
    """A failed OCR must not destroy the little the layer had."""
    path = _mixed_pdf(tmp_path, garbage_layer=True)
    reader = _StubReader(fail=True)
    content = _content(reader.read_file({"path": str(path), "extension": ".pdf"}))
    assert reader.calls, "fallback was never attempted"
    text = content["pages"][1]["text"]
    assert text.strip(), "the layer was thrown away even though OCR failed"
    assert "4" in text


def test_good_page_is_not_sent_to_ocr(tmp_path):
    """A logo must not trigger OCR on a page that already reads perfectly."""
    doc = fitz.open()
    page = doc.new_page(width=400, height=300)
    page.insert_text((40, 60), "A page of perfectly good prose with many words on it.",
                     fontsize=12)
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 10, 10), 0)
    pix.clear_with(120)
    page.insert_image(fitz.Rect(300, 20, 380, 100), pixmap=pix)
    path = tmp_path / "logo.pdf"
    doc.save(str(path))
    doc.close()

    reader = _StubReader()
    content = _content(reader.read_file({"path": str(path), "extension": ".pdf"}))
    assert reader.calls == [], reader.calls
    assert content["pages"][0]["method"] == "direct_extraction"


def test_scanned_annex_with_real_engine_produces_text(tmp_path):
    """End-to-end, when an OCR engine is actually installed."""
    from core.ocr import get_ocr_engine

    if get_ocr_engine() is None:
        pytest.skip("no OCR engine installed in this environment")

    path = _mixed_pdf(tmp_path)
    content = _content(PDFFileReader().read_file({"path": str(path), "extension": ".pdf"}))
    annex = content["pages"][1]
    assert annex["method"] == "ocr_layout_fallback", annex.get("method")
    assert "ANNEX" in annex["text"].upper(), annex["text"]
    assert annex["ocr_fallback"]["engine"]


class _ImageBranchReader(PDFFileReader):
    """Stands in for the OCR engine on a document typed ``image``."""

    def __init__(self, recognised):
        super().__init__()
        self.recognised = recognised
        self.calls = 0

    def process_page_optimized(self, page_data):
        self.calls += 1
        return {
            "page_number": page_data[0] + 1,
            "text": self.recognised,
            "text_length": len(self.recognised),
            "method": "ocr_stub",
            "ocr_derived": True,
            "ocr_engine": "stub",
            "ocr_confidence": 0.9,
        }


def _short_layer_pdf(tmp_path):
    """One page: a real, short text layer plus an image.

    Its 24 characters are below the 50-character average the document-level
    sample uses, so the document is typed "image" and the page is routed to OCR
    even though nothing about its text layer is broken.
    """
    doc = fitz.open()
    page = doc.new_page(width=400, height=300)
    page.insert_text((40, 60), "This is page one.", fontsize=12)
    page.insert_text((40, 80), "1", fontsize=10)
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 10, 10), 0)
    pix.clear_with(200)
    page.insert_image(fitz.Rect(300, 20, 380, 100), pixmap=pix)
    path = tmp_path / "short_layer.pdf"
    doc.save(str(path))
    doc.close()
    return path


def test_a_short_but_real_text_layer_is_not_replaced_by_a_shorter_ocr_reading(
        tmp_path, monkeypatch):
    monkeypatch.setattr(read_pdf_module, "get_ocr_engine", lambda: object())
    path = _short_layer_pdf(tmp_path)
    reader = _ImageBranchReader("This is page one.")
    content = _content(reader.read_file({"path": str(path), "extension": ".pdf"}))

    page = content["pages"][0]
    assert reader.calls == 1, "the page should still be offered to OCR"
    assert page["method"] == "text_layer_preferred"
    assert "1" in page["text"], page["text"]
    assert page["ocr_text"] == "This is page one."
    assert page["ocr_rejected"]["reason"] == "text_layer_reads_as_language"


def test_a_scanned_page_still_uses_recognition(tmp_path, monkeypatch):
    monkeypatch.setattr(read_pdf_module, "get_ocr_engine", lambda: object())
    path = _mixed_pdf(tmp_path)   # page 1 is born-digital, so build a pure scan
    doc = fitz.open()
    src = doc.new_page(width=400, height=300)
    src.insert_text((40, 60), "SCANNED PAGE", fontsize=18)
    pix = src.get_pixmap(dpi=150)
    scan_path = tmp_path / "scan.pdf"
    page = doc.new_page(width=400, height=300)
    doc.save(str(scan_path))
    doc.close()
    doc = fitz.open()
    only = doc.new_page(width=400, height=300)
    only.insert_image(fitz.Rect(0, 0, 400, 300), pixmap=pix)
    doc.save(str(scan_path))
    doc.close()

    reader = _ImageBranchReader("SCANNED PAGE")
    content = _content(reader.read_file({"path": str(scan_path), "extension": ".pdf"}))
    assert content["pages"][0]["method"] == "ocr_stub"
    assert content["pages"][0]["text"] == "SCANNED PAGE"
