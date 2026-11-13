"""Unit tests for StoragePipeline._build_extraction_provenance (task 1).

The builder's contract matters more than its shape: it must return None when
there is no provenance rather than an empty dict, because an empty dict would
claim provenance was recorded when it was not.
"""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from pipeline.storage_pipeline import StoragePipeline  # noqa: E402


@pytest.fixture
def pipeline():
    return StoragePipeline.__new__(StoragePipeline)


class TestEmptyInputs:
    @pytest.mark.parametrize("content", [None, {}, "not a dict", 42, []])
    def test_returns_none_not_empty_dict(self, pipeline, content):
        """An empty dict would be a false claim that provenance was recorded."""
        assert pipeline._build_extraction_provenance(content) is None

    def test_content_with_no_provenance_at_all(self, pipeline):
        assert pipeline._build_extraction_provenance(
            {"text": "hello", "word_count": 1}
        ) is None


class TestImageOcr:
    CONTENT = {
        "text": "SCAN 4417",
        "ocr_attempted": True,
        "ocr_successful": True,
        "ocr_engine": "rapidocr",
        "ocr_engine_version": "1.4.4 (PP-OCRv4 onnx)",
        "ocr_derived": True,
        "ocr_confidence": 0.98,
        "ocr_language": "latn+chi",
        "ocr_input_variant": "original",
    }

    def test_records_engine_and_derived(self, pipeline):
        ocr = pipeline._build_extraction_provenance(self.CONTENT)["ocr"]
        assert ocr["derived"] is True
        assert ocr["engine"] == "rapidocr"
        assert ocr["engine_version"] == "1.4.4 (PP-OCRv4 onnx)"
        assert ocr["confidence"] == pytest.approx(0.98)
        assert ocr["input_variant"] == "original"

    def test_failed_ocr_is_not_marked_derived(self, pipeline):
        content = dict(self.CONTENT, ocr_successful=False, ocr_derived=False,
                       ocr_confidence=None, text="")
        ocr = pipeline._build_extraction_provenance(content)["ocr"]
        assert ocr["derived"] is False
        assert ocr["attempted"] is True
        assert ocr["successful"] is False
        assert ocr["confidence"] is None


class TestPdfPages:
    def test_aggregates_across_pages(self, pipeline):
        content = {"pages": [
            {"page_number": 1, "method": "direct_extraction", "text": "native"},
            {"page_number": 2, "method": "ocr_rapidocr", "ocr_engine": "rapidocr",
             "ocr_engine_version": "1.4.4", "ocr_derived": True,
             "ocr_confidence": 0.90, "ocr_language": "latn+chi"},
            {"page_number": 3, "method": "ocr_rapidocr", "ocr_engine": "rapidocr",
             "ocr_engine_version": "1.4.4", "ocr_derived": True,
             "ocr_confidence": 0.80},
        ]}
        ocr = pipeline._build_extraction_provenance(content)["ocr"]
        assert ocr["ocr_pages"] == 2, "only OCR'd pages count"
        assert ocr["total_pages"] == 3
        assert ocr["confidence"] == pytest.approx(0.85)
        assert ocr["derived"] is True

    def test_all_native_pdf_reports_no_ocr(self, pipeline):
        """A text PDF must not claim OCR provenance."""
        content = {"pages": [
            {"page_number": 1, "method": "direct_extraction", "text": "native"},
        ]}
        assert "ocr" not in (pipeline._build_extraction_provenance(content) or {})


class TestDiagnostics:
    def test_engine_error_is_preserved(self, pipeline):
        content = {
            "ocr_attempted": True,
            "extraction_info": {"engine_error": "model exploded"},
        }
        out = pipeline._build_extraction_provenance(content)
        assert out["diagnostics"] == {"engine_error": "model exploded"}

    def test_skip_reason_is_preserved(self, pipeline):
        content = {"extraction_info": {"skipped": True, "skip_reason": "too_small"}}
        out = pipeline._build_extraction_provenance(content)
        assert out["diagnostics"]["skip_reason"] == "too_small"

    def test_uninteresting_extraction_info_yields_no_diagnostics(self, pipeline):
        content = {"extraction_info": {"text_length": 10, "word_count": 2}}
        assert pipeline._build_extraction_provenance(content) is None


class TestDetection:
    def test_detection_fields_are_copied(self, pipeline):
        content = {"type_detection": {
            "declared_extension": ".txt",
            "detected_extension": ".png",
            "detection_method": "magic-bytes",
            "detection_confidence": "strong",
            "extension_mismatch": True,
            "irrelevant": "dropped",
        }}
        detection = pipeline._build_extraction_provenance(content)["detection"]
        assert detection["extension_mismatch"] is True
        assert "irrelevant" not in detection

    def test_empty_detection_is_dropped(self, pipeline):
        content = {"type_detection": {"declared_extension": None}}
        assert pipeline._build_extraction_provenance(content) is None
