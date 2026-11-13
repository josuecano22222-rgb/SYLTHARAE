"""OCR engine layer.

Exposes the pluggable engine abstraction used by the image and PDF readers.
See :mod:`core.ocr.engines` for engine preference and provenance rules.
"""

from .engines import (
    ENGINE_PREFERENCE,
    LOW_CONFIDENCE_RETRY_THRESHOLD,
    BaseOcrEngine,
    OcrBlock,
    OcrResult,
    RapidOcrEngine,
    TesseractEngine,
    get_ocr_engine,
    ocr_available,
    ocr_engine_name,
    recognize_best,
    recognize_image,
    reset_engine_cache,
)

__all__ = [
    "ENGINE_PREFERENCE",
    "LOW_CONFIDENCE_RETRY_THRESHOLD",
    "BaseOcrEngine",
    "OcrBlock",
    "OcrResult",
    "RapidOcrEngine",
    "TesseractEngine",
    "get_ocr_engine",
    "ocr_available",
    "ocr_engine_name",
    "recognize_best",
    "recognize_image",
    "reset_engine_cache",
]
