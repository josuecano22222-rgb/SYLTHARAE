"""DOMAIN_MODEL.md lists exactly the OCR provenance fields the pipeline stores.

The paragraph is the contract an analyst or integrator reads to interpret
``extraction_provenance.ocr``; it is checked against StoragePipeline's own
output for an image and a PDF, in both directions.
"""
import re
from pathlib import Path

from pipeline.storage_pipeline import StoragePipeline

DOC = Path(__file__).resolve().parents[2] / "docs" / "DOMAIN_MODEL.md"
#: Backticked words in the paragraph that are values or the container, not fields.
NOT_FIELDS = {"extraction_provenance", "extraction_provenance.ocr", "true", "null",
              "preprocessed", "original"}


def _documented_fields():
    text = DOC.read_text(encoding="utf-8")
    start = text.index("For OCR, `extraction_provenance.ocr` holds")
    paragraph = text[start:text.index("\n## ", start)]
    return {t.split(":")[0] for t in re.findall(r"`([^`]+)`", paragraph)} - NOT_FIELDS


def _stored_fields():
    image = StoragePipeline._ocr_provenance({
        "ocr_attempted": True, "ocr_successful": True, "ocr_derived": True,
        "ocr_engine": "tesseract", "ocr_engine_version": "5.3.4", "ocr_confidence": None,
        "ocr_language": "eng", "ocr_input_variant": "original", "ocr_rotation": 90,
        "extraction_info": {"ocr_blocks_error": "image_to_data failed: stub",
                            "missing_ocr_languages": ["ara"]},
    })
    pdf = StoragePipeline._ocr_provenance({"pages": [
        {"page_number": 1, "method": "text"},
        {"page_number": 2, "method": "ocr_tesseract", "ocr_engine": "tesseract",
         "ocr_engine_version": "5.3.4", "ocr_confidence": 0.9, "ocr_language": "eng",
         "ocr_input_variant": "preprocessed", "ocr_rotation": 180,
         "missing_ocr_languages": ["ara"]},
    ]})
    return set(image) | set(pdf)


def test_every_stored_ocr_field_is_documented():
    missing = _stored_fields() - _documented_fields()
    assert not missing, f"stored but not described in DOMAIN_MODEL.md: {sorted(missing)}"


def test_every_documented_ocr_field_is_stored():
    invented = _documented_fields() - _stored_fields()
    assert not invented, f"described in DOMAIN_MODEL.md but never stored: {sorted(invented)}"
