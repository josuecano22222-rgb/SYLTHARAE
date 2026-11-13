"""PDF text-layer extraction (PDF-01 / PDF-02).

Two defects, both reproduced against the real reader before the fix:

PDF-01  ``detect_pdf_type_early`` divided by ``min(3, len(doc))``. A PDF with
        no pages raised ZeroDivisionError, which surfaced as an opaque
        ``{"error": "division by zero"}`` for the whole document.

PDF-02  Pages whose text layer held <= 30 characters were classified as
        "image" and sent to OCR. When OCR was unavailable, produced nothing,
        or failed, the page's own text layer was DISCARDED and the page was
        stored empty. Measured before the fix:

            chars=  10  extracted=0   methods={'ocr_skipped_tesseract_unavailable': 1}
            chars=  26  extracted=0   methods={'ocr_skipped_tesseract_unavailable': 1}
            chars= 40   extracted=41  methods={'direct_extraction': 1}
            chars= 51   extracted=52  methods={'direct_extraction': 1}

        i.e. a real text PDF lost 100% of its content below ~31 chars/page.

        Note the status string in the measurement above is the historical one.
        Phase 2A added a fallback engine, so the reason a page is not OCR'd is
        no longer necessarily "tesseract is missing"; the current string is
        ``ocr_required_engine_unavailable``, asserted below.
"""

import io
import sys
import tempfile
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import reader_file.readers.read_pdf as reader_module  # noqa: E402
from reader_file.readers.read_pdf import PDFFileReader  # noqa: E402


@pytest.fixture(scope="module")
def reader():
    return PDFFileReader()


@pytest.fixture
def no_ocr_engine(monkeypatch):
    """Force the 'no OCR engine available' path so results are deterministic.

    PHASE 2A added a fallback engine, so disabling tesseract alone no longer
    reaches this path. Both backends are disabled here so these tests keep
    asserting the guarantee they were written for: with no engine at all, the
    page's own text layer is preserved rather than discarded.
    """
    monkeypatch.setattr(
        "reader_file.readers.read_pdf._is_tesseract_available", lambda: False
    )
    monkeypatch.setattr("reader_file.readers.read_pdf.get_ocr_engine", lambda: None)


def build_pdf(text_per_page):
    """Build a real PDF; ``text_per_page`` is a list of strings ('' = blank)."""
    pymupdf = pytest.importorskip("pymupdf")
    doc = pymupdf.open()
    for text in text_per_page:
        page = doc.new_page()
        if text:
            page.insert_text((72, 72), text, fontsize=11)
    data = doc.tobytes()
    doc.close()
    return data


#: A structurally valid PDF whose page tree is empty (Count 0).
ZERO_PAGE_PDF = b"""%PDF-1.4
1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj
2 0 obj<</Type/Pages/Kids[]/Count 0>>endobj
xref
0 3
0000000000 65535 f
0000000009 00000 n
0000000052 00000 n
trailer<</Root 1 0 R/Size 3>>
startxref
101
%%EOF
"""


def read_pdf_bytes(reader, data: bytes):
    """Run the real reader over raw PDF bytes; returns the content dict."""
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as handle:
        handle.write(data)
        path = handle.name
    try:
        return reader.read_pdf_file(path)
    finally:
        Path(path).unlink(missing_ok=True)


class TestZeroPagePdf:
    def test_zero_page_pdf_does_not_raise_division_by_zero(self, reader):
        result = read_pdf_bytes(reader, ZERO_PAGE_PDF)
        assert result.get("error") != "division by zero"
        assert result.get("error_type") != "ZeroDivisionError"

    def test_zero_page_pdf_reports_zero_pages(self, reader):
        result = read_pdf_bytes(reader, ZERO_PAGE_PDF)
        assert result.get("num_pages") == 0
        assert result.get("total_characters") == 0
        assert result.get("pages") == []

    def test_detect_pdf_type_early_tolerates_empty_document(self, reader):
        pymupdf = pytest.importorskip("pymupdf")
        doc = pymupdf.open(stream=ZERO_PAGE_PDF, filetype="pdf")
        try:
            assert len(doc) == 0
            assert reader.detect_pdf_type_early(doc) == "image"
        finally:
            doc.close()


class TestTextLayerNeverDiscarded:
    @pytest.mark.parametrize("char_count", [1, 5, 10, 26, 30])
    def test_sparse_text_page_keeps_its_text(self, reader, no_ocr_engine, char_count):
        """The core regression: sparse pages used to come back empty."""
        marker = "S" * char_count
        result = read_pdf_bytes(reader, build_pdf([marker]))
        assert result.get("total_characters") > 0, result.get("methods_used")
        assert marker in result["pages"][0]["text"]

    def test_sparse_page_method_is_text_layer_fallback(self, reader, no_ocr_engine):
        result = read_pdf_bytes(reader, build_pdf(["SPARSE-7712"]))
        assert result["pages"][0]["method"] == "text_layer_fallback"
        assert result["pages"][0]["ocr_status"] == "ocr_required_engine_unavailable"

    def test_dense_page_uses_direct_extraction(self, reader, no_ocr_engine):
        # A single insert_text run is clipped at the page width (~66 glyphs at
        # 11pt), so use prose rather than a repeated character run.
        prose = (
            "The quick brown fox jumps over the lazy dog while the auditor "
            "reviews the quarterly report for anomalies in the ledger entries."
        )
        result = read_pdf_bytes(reader, build_pdf([prose]))
        assert result["pages"][0]["method"] == "direct_extraction"
        # Comfortably above the 30-char per-page OCR trigger.
        assert result["total_characters"] > 60
        assert "quarterly report" in result["pages"][0]["text"]

    def test_multi_page_preserves_order_and_both_methods(self, reader, no_ocr_engine):
        # The first three pages are sampled to classify the document; keeping
        # them sparse forces the "image" path so both per-page outcomes occur.
        dense = (
            "Invoice summary for the period ending March shows a net variance "
            "that requires reconciliation against the general ledger entries."
        )
        pages = ["one", "two", "three", dense]
        result = read_pdf_bytes(reader, build_pdf(pages))
        assert result["num_pages"] == 4
        numbers = [p["page_number"] for p in result["pages"]]
        assert numbers == [1, 2, 3, 4], "page order must be preserved"
        methods = [p["method"] for p in result["pages"]]
        assert methods == [
            "text_layer_fallback",
            "text_layer_fallback",
            "text_layer_fallback",
            "direct_extraction",
        ], methods
        assert result["pages"][0]["text"] == "one"
        assert "Invoice summary" in result["pages"][3]["text"]

    def test_blank_page_is_recorded_with_a_reason(self, reader, no_ocr_engine):
        """A failed extractor must explain itself, not vanish."""
        result = read_pdf_bytes(reader, build_pdf([""]))
        assert len(result["pages"]) == 1
        page = result["pages"][0]
        assert page["page_number"] == 1
        assert page["text"] == ""
        assert page["method"] == "ocr_required_engine_unavailable"

    def test_no_ocr_engine_skips_rasterisation_entirely(self, reader, no_ocr_engine):
        result = read_pdf_bytes(reader, build_pdf(["sparse text"]))
        assert result.get("ocr_used") is False
        assert result.get("ocr_status") == "ocr_required_engine_unavailable"


class TestProcessPageOptimized:
    """Unit-level checks on the OCR worker's fallback behaviour."""

    def test_seven_tuple_falls_back_to_native_text(self, reader, no_ocr_engine):
        page_data = (0, b"not-a-real-png", "eng", "", True, ["eng"], "NATIVE-3312")
        result = reader.process_page_optimized(page_data)
        assert result["page_number"] == 1
        assert result["text"] == "NATIVE-3312"
        assert result["method"] == "text_layer_fallback"

    def test_seven_tuple_with_no_native_text_is_empty_but_labelled(self, reader, no_ocr_engine):
        page_data = (2, b"not-a-real-png", "eng", "", True, ["eng"], "")
        result = reader.process_page_optimized(page_data)
        assert result["page_number"] == 3
        assert result["text"] == ""
        assert result["method"] == "ocr_required_engine_unavailable"

    def test_legacy_six_tuple_still_unpacks(self, reader, no_ocr_engine):
        """Backward compatibility for the previous page_data shape."""
        page_data = (1, b"not-a-real-png", "eng", "", True, ["eng"])
        result = reader.process_page_optimized(page_data)
        assert result["page_number"] == 2
        assert result["method"] == "ocr_required_engine_unavailable"

    def test_needs_ocr_false_short_circuits(self, reader, no_ocr_engine):
        page_data = (0, b"", "eng", "", False, ["eng"], "ignored")
        result = reader.process_page_optimized(page_data)
        assert result["method"] == "skipped_text_based_pdf"


class TestEndToEndThroughRouter:
    """The full processing path, not just the reader in isolation."""

    def test_sparse_pdf_content_survives_the_router(self, no_ocr_engine):
        from reader_file.services.file_router_service import FileRouterService

        router = FileRouterService()
        data = build_pdf(["ROUTER-SPARSE-8823"])
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as handle:
            handle.write(data)
            path = handle.name
        try:
            file_info = {
                "path": path,
                "name": Path(path).name,
                "extension": ".pdf",
                "type": "FILE",
                "size": len(data),
            }
            result = router.process_file(file_info, collect=False, store_result=False)
        finally:
            Path(path).unlink(missing_ok=True)

        content = result["Content"]
        assert not content.get("error"), content.get("error")
        assert content["total_characters"] > 0
        assert "ROUTER-SPARSE-8823" in content["pages"][0]["text"]


def build_scanned_pdf(marker, font_path="/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
    """A real scanned document: text rasterised to a bitmap, no text layer."""
    pytest.importorskip("pymupdf")
    Image = pytest.importorskip("PIL.Image")
    ImageDraw = pytest.importorskip("PIL.ImageDraw")
    ImageFont = pytest.importorskip("PIL.ImageFont")

    img = Image.new("RGB", (1100, 300), "white")
    ImageDraw.Draw(img).text((60, 120), marker,
                             font=ImageFont.truetype(font_path, 44), fill="black")
    png = io.BytesIO()
    img.save(png, format="PNG")

    import pymupdf
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_image(pymupdf.Rect(0, 0, 595, 162), stream=png.getvalue())
    data = doc.tobytes()
    doc.close()
    return data


class TestScannedPdfUsesFallbackEngine:
    """PHASE 2A: OCR no longer depends on the tesseract binary.

    Before this change a scanned PDF on a host without tesseract came back with
    zero characters - the whole document's content was lost, because a scanned
    page has no text layer to fall back to.
    """

    @pytest.fixture
    def tesseract_absent(self, monkeypatch):
        """Simulate the environment this sandbox actually runs in."""
        monkeypatch.setattr(
            "reader_file.readers.read_pdf._is_tesseract_available", lambda: False
        )

    def test_engine_exists_so_ocr_is_not_skipped(self, reader, tesseract_absent):
        engine = reader_module.get_ocr_engine()
        assert engine is not None, (
            "no OCR engine installed; this test proves the fallback path and "
            "needs rapidocr-onnxruntime (or tesseract) available"
        )

    def test_scanned_page_yields_recognised_text_with_provenance(
        self, reader, tesseract_absent
    ):
        marker = "SCANNED INVOICE 8842"
        result = read_pdf_bytes(reader, build_scanned_pdf(marker))
        page = result["pages"][0]

        assert page["text_length"] > 0, page
        assert page["method"].startswith("ocr_"), page["method"]
        # Provenance: the page must say how its text was derived.
        assert page["ocr_derived"] is True
        assert page["ocr_engine"] in ("tesseract", "rapidocr")
        assert page["ocr_engine_version"]
        assert page.get("ocr_confidence") is not None
        assert 0.0 <= page["ocr_confidence"] <= 1.0
        # The digits and a word must survive; allow spacing variation.
        compact = page["text"].replace(" ", "")
        assert "8842" in compact, page["text"]

    def test_document_is_not_flagged_as_no_engine(self, reader, tesseract_absent):
        result = read_pdf_bytes(reader, build_scanned_pdf("ANYTHING 1234"))
        assert result.get("ocr_status") != "ocr_required_engine_unavailable"
        assert result.get("ocr_used") is True

    def test_blank_scanned_page_falls_back_without_hallucinating(
        self, reader, tesseract_absent
    ):
        """A scanned page with no text must not invent content."""
        import pymupdf
        Image = pytest.importorskip("PIL.Image")

        png = io.BytesIO()
        Image.new("RGB", (800, 400), "white").save(png, format="PNG")
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_image(pymupdf.Rect(0, 0, 595, 296), stream=png.getvalue())
        data = doc.tobytes()
        doc.close()

        result = read_pdf_bytes(reader, data)
        page_result = result["pages"][0]
        assert page_result["text"] == ""
        assert page_result["ocr_status"] == "ocr_no_text"

    def test_text_layer_wins_when_page_already_has_text(self, reader, tesseract_absent):
        """A real text PDF must not be routed through OCR at all."""
        prose = (
            "The quarterly ledger reconciliation identified a variance that "
            "requires review by the auditor before the period is closed out."
        )
        result = read_pdf_bytes(reader, build_pdf([prose]))
        assert result["pages"][0]["method"] == "direct_extraction"
        assert result["pages"][0].get("ocr_derived") is not True
